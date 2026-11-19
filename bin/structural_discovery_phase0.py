#!/usr/bin/env python3
"""Phase-0 measurement for plan-coder issue #79 (CodeGraph spike).

Measurement only.  Nothing here is wired into the live harness: no
change to ``bin/agent.py``, no prompt change, no config change, no
install, no LLM call, no network access.  The script reads existing
worker transcripts and counts how much of the workers' tool traffic is
*structural discovery* -- the "where is this symbol defined, who calls
it, what depends on it" work that a pre-built code graph could
plausibly replace.

Every tool call in every sampled run is assigned exactly one class,
mechanically, from the tool name and its arguments (for ``exec``, from
the command string).  There is no LLM judge.  Where a call is
ambiguous it is classified as ``other_exec`` and never as ``struct_*``:
the bias is deliberately against the spike's own premise, so a Proceed
verdict cannot be an artefact of a generous classifier.

The classes (issue #79 section 3):

  struct_search  exec commands whose primary purpose is locating
                 structure (grep/rg/find/ast-grep/ctags/ls-style file
                 location) and list_dir calls made before the run's
                 first edit.
  struct_read    read_file calls (and read-via-exec) on a file that is
                 not a steering/task document, was not read before in
                 this run, and is never edited in this run.
  task_read      read_file / list_dir of steering or task documents, of
                 files already read in this run, or of files that are
                 edited later in the run.
  edit           write_file / edit_file.
  verify         exec commands running tests, linters, type checks or
                 builds.
  vcs            exec commands running git / gh.
  other_exec     every other exec command.

The rules are printed with the tables so the numbers can be re-derived
by hand; ``RULES`` below is the single source of truth for them.

Usage:
    python3 bin/structural_discovery_phase0.py
    python3 bin/structural_discovery_phase0.py --limit 60 --format json
    python3 bin/structural_discovery_phase0.py --out docs/codegraph-phase0-report.md

Options:
    --transcripts-dir DIR   Transcript directory (default: resolved the
                            same way bin/agent.py resolves it -- deployment
                            config ``state.transcripts_dir``, else
                            ~/.plan-coder/transcripts, else the live
                            supervisor directory when it exists).
    --limit N               Most recent transcripts to sample (default 60).
    --exclude-run-id ID     Drop this run id from the sample (used to
                            keep the measuring run out of its own corpus).
    --ledger PATH           Worker ledger jsonl used for repo/outcome
                            joins (default: the manager-setup ledger).
    --repos-root DIR        Root holding the supervisor clones, used for
                            the steering-document check (default:
                            ~/workspace).
    --min-steps N           Minimum steps for a run to qualify (default 5).
    --format FORMAT         markdown | json (default: markdown).
    --out PATH              Also write the rendered output here.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shlex
import statistics
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fallback transcript directory of the live supervisor on this host.  It
# is only used when neither the CLI flag nor the deployment config
# resolves to an existing directory.
SUPERVISOR_TRANSCRIPTS = os.path.expanduser(
    "~/workspace/goals/token-plan-coding-manager-setup/hidden_files/transcripts")

DEFAULT_LEDGER = os.path.expanduser(
    "~/workspace/goals/token-plan-coding-manager-setup/hidden_files/"
    "worker_ledger.jsonl")

CLASSES = (
    "struct_search",
    "struct_read",
    "task_read",
    "edit",
    "verify",
    "vcs",
    "other_exec",
)

# --- the rules, printed with the results --------------------------------
RULES = [
    ("edit",
     "tool name write_file or edit_file"),
    ("vcs",
     "exec whose first command segment starts with git or gh"),
    ("verify",
     ("exec with a test/lint/typecheck/build segment (pytest, ruff, mypy, "
      "pyright, tsc, eslint, npm/pnpm/yarn test|build|lint, cargo test|check, "
      "go test, make test|check, gradlew, ctest, vitest, jest, bun test, "
      "dotnet test, a repo test script such as test_*.py / run_tests / "
      "verify*.sh)")),
    ("struct_search",
     ("exec with a strong file-location segment (grep, egrep, rg, ag, ack, "
      "find, fd, tree, ast-grep, sg, ctags) anywhere in the command; an "
      "exec whose only file-location segment is a plain ls/dir, which "
      "carries no test/lint/build segment, and which was issued before the "
      "run's first edit; plus every list_dir call made before the run's "
      "first edit")),
    ("struct_read",
     ("read_file (or a cat/head/tail/sed -n/wc read via exec) on a file "
      "that is not a steering/task document, was not read earlier in the "
      "run, and is not edited anywhere in the run")),
    ("task_read",
     ("read_file / list_dir of a steering or task document, of a file "
      "already read in the run, or of a file edited in the run; "
      "list_dir calls after the run's first edit")),
    ("other_exec",
     "every other exec command (installs, script runs, downloads, ...)"),
]

STEERING_DOC_RE = re.compile(
    r"^(agents|claude|contract|context|contributing|readme|architecture|"
    r"brief|issue|task|instructions)\b", re.I)

VCS_HEADS = frozenset({"git", "gh", "glab", "hub"})

# Strong searches: locating structure is the command's point.
STRONG_SEARCH_HEADS = frozenset({
    "grep", "egrep", "fgrep", "rgrep", "rg", "ag", "ack",
    "find", "fd", "fdfind", "locate", "tree", "ast-grep", "sg", "ctags",
    "mdfind",
})

# Weak searches: a directory listing is only structural discovery when
# nothing else in the command is (an ``ls`` in front of a test run is
# part of the test run).
WEAK_SEARCH_HEADS = frozenset({"ls", "dir"})

READ_HEADS = frozenset({"cat", "head", "tail", "sed", "wc", "bat", "less"})

VERIFY_RE = re.compile(
    r"(pytest|ruff\b|mypy\b|pyright\b|tsc\b|eslint\b|flake8\b|black\b"
    r"|prettier\b|cargo\s+(test|check|build|clippy)|go\s+test\b"
    r"|npm\s+(run\s+)?(test|build|lint|typecheck)|pnpm\s+(test|build|lint)"
    r"|yarn\s+(test|build|lint)|make\s+(test|check)|gradlew|ctest\b"
    r"|vitest\b|jest\b|bun\s+test|dotnet\s+test|flutter\s+test"
    r"|python3?\s+-m\s+(unittest|pytest)"
    r"|python3?\s+\S*(test_\w+|run_tests|verify\w*)\.(py|sh))")

HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?.*?\n.*?\n\1",
                        re.DOTALL)

LEAD_NOISE = frozenset({"sudo", "time", "env", "nohup", "command",
                        "builtin", "exec"})


# --- helpers ------------------------------------------------------------
def _pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile (the convention used by the other
    phase0 tools in this repo).

    Nearest rank: the smallest value at or above the q-th position,
    i.e. index ``ceil(q * n) - 1`` of the sorted values.  For
    q=0.90 over 100 values that is the 90th value (index 89).
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(q * len(ordered))
    idx = min(len(ordered) - 1, max(0, rank - 1))
    return ordered[idx]


def _summary(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": statistics.median(values),
        "p90": _pct(values, 0.90),
        "min": min(values),
        "max": max(values),
    }


def _norm_path(path: str) -> str:
    path = (path or "").strip().strip("'\"")
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/")


def _is_task_doc(path: str) -> bool:
    """Steering or task document (read for orientation, not discovery)."""
    base = os.path.basename(path)
    if not base:
        return False
    if STEERING_DOC_RE.match(base):
        return True
    return "brief" in base.lower() or "issue" in base.lower()


def _strip_heredocs(cmd: str) -> str:
    return HEREDOC_RE.sub(" ", cmd or "")


def _segments(cmd: str) -> list[str]:
    """Split a shell command into its segments.

    The split is textual, not a shell parse: ``;``, ``&&``, ``||`` and
    ``|`` are separators, and a leading ``cd DIR`` is removed from every
    segment so that the head command is the first thing the segment
    actually runs.
    """
    cmd = _strip_heredocs(cmd or "")
    raw = re.split(r"\|\||&&|;|\||\n", cmd)
    out = []
    for part in raw:
        part = part.strip()
        while part.startswith("cd "):
            rest = part[3:].strip()
            bits = rest.split(None, 1)
            part = bits[1].strip() if len(bits) > 1 else ""
        if part:
            out.append(part)
    return out


def _head(segment: str) -> str:
    """The command name of a segment, with env assignments and wrappers
    removed."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    while tokens:
        tok = tokens[0]
        if "=" in tok and not tok.startswith("-"):
            tokens = tokens[1:]
            continue
        if tok in LEAD_NOISE or re.match(r"^timeout$", tok):
            tokens = tokens[1:]
            continue
        if re.match(r"^\d+$", tok):
            tokens = tokens[1:]
            continue
        break
    if not tokens:
        return ""
    return os.path.basename(tokens[0])


def _read_target(segment: str) -> str:
    """The file a read-style segment (``cat``/``head``/``sed -n``/``wc``)
    points at.  Best effort: the last non-flag, non-redirection token of
    the segment."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    tokens = [t for t in tokens[1:]
              if not t.startswith("-") and ">" not in t]
    if not tokens:
        return ""
    return _norm_path(tokens[-1])


def _search_subject(segment: str) -> str:
    """The symbol or path a search segment looks for (mechanical: the
    first non-flag argument of a grep-like command, else the path
    argument of find/ls)."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    head = _head(segment)
    args = [t for t in tokens[1:] if not t.startswith("-")]
    if head in {"grep", "egrep", "fgrep", "rgrep", "rg", "ag", "ack"}:
        for tok in args:
            if not tok.startswith("-"):
                return tok
        return ""
    if args:
        return args[0]
    return ""


# --- transcript discovery ------------------------------------------------
def _load_deployment_config() -> dict:
    """Deployment config, searched in bin/agent.py's order."""
    candidates = []
    env_path = os.environ.get("PLAN_CODER_CONFIG")
    if env_path:
        candidates.append(env_path)
    candidates.append(os.path.join(REPO_ROOT, "config",
                                    "deployment.local.json"))
    candidates.append(os.path.expanduser("~/.plan-coder/deployment.json"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:  # noqa: BLE001, S112 - config is a convenience
            continue
        if isinstance(data, dict):
            return data
    return {}


def resolve_transcripts_dir(explicit: str | None = None) -> tuple[str, str]:
    """Resolve the transcript directory the way bin/agent.py does.

    Returns (path, how-it-was-resolved).  Search order:
    CLI flag, $PLAN_CODER_TRANSCRIPTS_DIR, deployment config
    ``state.transcripts_dir``, ~/.plan-coder/transcripts, and finally the
    live supervisor directory when the earlier candidates do not exist.
    """
    if explicit:
        return os.path.expanduser(explicit), "--transcripts-dir"
    env_dir = (os.environ.get("PLAN_CODER_TRANSCRIPT_DIR")
               or os.environ.get("PLAN_CODER_TRANSCRIPTS_DIR"))
    if env_dir:
        return os.path.expanduser(env_dir), "$PLAN_CODER_TRANSCRIPT_DIR"
    cfg = _load_deployment_config()
    state = cfg.get("state") if isinstance(cfg, dict) else None
    if isinstance(state, dict) and state.get("transcripts_dir"):
        path = os.path.expanduser(state["transcripts_dir"])
        if os.path.isdir(path):
            return path, "deployment config state.transcripts_dir"
    default = os.path.expanduser("~/.plan-coder/transcripts")
    if os.path.isdir(default):
        return default, "agent.py default (~/.plan-coder/transcripts)"
    if os.path.isdir(SUPERVISOR_TRANSCRIPTS):
        return SUPERVISOR_TRANSCRIPTS, (
            "live supervisor transcript dir (config-resolved dir absent)")
    return default, "agent.py default (~/.plan-coder/transcripts)"


def sample_transcripts(transcripts_dir: str, limit: int,
                       exclude_run_ids: set[str] | None = None) -> list[str]:
    """The most recent transcripts, newest first.

    Ordering is by file mtime (descending), ties broken by file name
    (ascending) so the sample is reproducible for a given corpus.
    """
    exclude = exclude_run_ids or set()
    try:
        names = sorted(os.listdir(transcripts_dir))
    except OSError:
        return []
    paths = [os.path.join(transcripts_dir, n) for n in names
             if n.endswith(".jsonl")]
    paths.sort(key=lambda p: (-os.path.getmtime(p), os.path.basename(p)))
    out = []
    for path in paths:
        run_id = os.path.basename(path)[:-len(".jsonl")]
        if run_id in exclude:
            continue
        out.append(path)
        if limit and len(out) >= limit:
            break
    return out


def load_events(path: str) -> list[dict]:
    events = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return events


def _tool_args(call: dict) -> dict:
    raw = call.get("args")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    if "path" not in parsed and isinstance(raw, str):
        match = re.search(r'"path"\s*:\s*"([^"]+)"', raw)
        if match:
            parsed["path"] = match.group(1)
    return parsed


# --- classification ------------------------------------------------------
def classify_exec(command: str, first_edit_seen: bool) -> tuple[str, str]:
    """Classify one exec command.

    Returns (class, subject) where subject is the searched symbol/path
    for structural searches and the empty string otherwise.
    """
    segments = _segments(command)
    if not segments:
        return "other_exec", ""
    heads = [_head(s) for s in segments]
    if heads[0] in VCS_HEADS:
        return "vcs", ""
    strong_idx = next((i for i, h in enumerate(heads)
                       if h in STRONG_SEARCH_HEADS), None)
    weak_idx = next((i for i, h in enumerate(heads)
                     if h in WEAK_SEARCH_HEADS), None)
    verify_idx = next((i for i, s in enumerate(segments)
                       if VERIFY_RE.search(s)), None)
    search_idx = strong_idx
    if (search_idx is None and weak_idx is not None and verify_idx is None
            and not first_edit_seen):
        # A plain directory listing only counts as structural discovery
        # while the worker is still looking for the files it will edit
        # (the same rule the list_dir calls follow).
        search_idx = weak_idx
    if search_idx is not None:
        subject = ""
        for i in range(search_idx, len(segments)):
            candidate = _search_subject(segments[i])
            if candidate:
                subject = candidate
                break
        return "struct_search", subject
    if verify_idx is not None:
        return "verify", ""
    if heads[0] in READ_HEADS:
        return "read_exec", _read_target(segments[0])
    return "other_exec", ""


def transcript_disposition(events: list[dict]) -> str:
    """The run's mechanical disposition, read from its own transcript.

    The supervisor ledger only knows the outcome of the runs it
    launched; a transcript always knows how the run ended:

      no_finished_event  the transcript has no ``finished`` record (the
                         run was cut off before it reported)
      token_cap          the run stopped on the output-token guard
      no_edit            the run finished without changing a file
      edited             the run finished having changed at least one file
    """
    finished = None
    for event in events:
        if isinstance(event, dict) and event.get("event") == "finished":
            finished = event
    if finished is None:
        return "no_finished_event"
    if finished.get("token_cap"):
        return "token_cap"
    if not finished.get("files_changed"):
        return "no_edit"
    return "edited"


def classify_run(events: list[dict]) -> dict:
    """Classify every tool call of one run.

    The rules are applied in two passes: the first collects the files
    edited anywhere in the run (needed by the struct_read rule), the
    second assigns one class to every tool call in step order.
    """
    step_events = sorted((e for e in events if isinstance(e.get("step"), int)
                          and e.get("tool_calls")),
                          key=lambda e: e["step"])
    edited: set[str] = set()
    for event in step_events:
        for call in event["tool_calls"]:
            name = call.get("name")
            if name in ("write_file", "edit_file"):
                path = _norm_path(str(_tool_args(call).get("path", "")))
                if path:
                    edited.add(path)

    counts = Counter()
    pre_counts = Counter()
    read_paths: set[str] = set()
    chars = Counter()
    subjects: Counter = Counter()
    others: list[str] = []
    struct_steps: set[int] = set()
    first_edit_step = None
    total_calls = 0
    step_call_index: list[tuple[int, str, str]] = []
    for event in step_events:
        results = event.get("tool_results") or []
        for index, call in enumerate(event["tool_calls"]):
            name = call.get("name") or ""
            args = _tool_args(call)
            result = ""
            if index < len(results) and isinstance(results[index], str):
                result = results[index]
            subject = ""
            if name in ("write_file", "edit_file"):
                klass = "edit"
                if first_edit_step is None:
                    first_edit_step = event["step"]
            elif name == "list_dir":
                path = _norm_path(str(args.get("path", "")))
                if first_edit_step is None:
                    klass = "struct_search"
                else:
                    klass = "task_read"
                subject = path
            elif name == "read_file":
                path = _norm_path(str(args.get("path", "")))
                subject = path
                if (path and not _is_task_doc(path) and path not in read_paths
                        and path not in edited):
                    klass = "struct_read"
                else:
                    klass = "task_read"
                if path:
                    read_paths.add(path)
            elif name == "exec":
                command = str(args.get("command", ""))
                klass, subject = classify_exec(command,
                                               first_edit_step is None)
                if klass == "read_exec":
                    path = subject
                    subject = path
                    if (path and not _is_task_doc(path)
                            and path not in read_paths
                            and path not in edited):
                        klass = "struct_read"
                    else:
                        klass = "task_read"
                    if path:
                        read_paths.add(path)
            else:
                klass = "other_exec"
            if klass == "other_exec" and name == "exec":
                command = str(args.get("command", ""))
                if command and len(others) < 8:
                    others.append(command[:200])
            counts[klass] += 1
            total_calls += 1
            chars[klass] += len(result)
            if klass in ("struct_search", "struct_read"):
                struct_steps.add(event["step"])
            if klass == "struct_search" and subject:
                subjects[subject] += 1
            step_call_index.append((event["step"], klass, subject))
            if first_edit_step is None or event["step"] < first_edit_step:
                pre_counts[klass] += 1

    pre_total = sum(pre_counts.values())
    struct_calls = counts["struct_search"] + counts["struct_read"]
    pre_struct = pre_counts["struct_search"] + pre_counts["struct_read"]
    struct_chars = chars["struct_search"] + chars["struct_read"]
    total_chars = sum(chars.values())
    # Every class is always present (0 when unused) so that the tables
    # and the tests can read counts["class"] without a KeyError.
    full_counts = {klass: counts.get(klass, 0) for klass in CLASSES}
    full_chars = {klass: chars.get(klass, 0) for klass in CLASSES}
    return {
        "steps": len(step_events),
        "tool_calls": total_calls,
        "counts": full_counts,
        "chars": full_chars,
        "struct_calls": struct_calls,
        "struct_share": (struct_calls / total_calls) if total_calls else 0.0,
        "struct_steps": len(struct_steps),
        "struct_step_share": (len(struct_steps) / len(step_events))
        if step_events else 0.0,
        "pre_calls": pre_total,
        "pre_struct_calls": pre_struct,
        "pre_struct_share": (pre_struct / pre_total) if pre_total else 0.0,
        "first_edit_step": first_edit_step,
        "steps_to_first_edit": (first_edit_step - 1
                                if first_edit_step is not None else None),
        "struct_char_share": (struct_chars / total_chars) if total_chars
        else 0.0,
        "subjects": subjects.most_common(5),
        "other_exec_samples": others,
    }


def ceiling_shares(run: dict) -> tuple[float, float]:
    """The two ceiling cuts from issue #79 section 3, per run.

    A perfect graph replaces 100% of struct_search and 50% of
    struct_read; a realistic graph replaces 50% and 25%.
    """
    counts = run["counts"]
    total = run["tool_calls"]
    if not total:
        return 0.0, 0.0
    search = counts.get("struct_search", 0)
    read = counts.get("struct_read", 0)
    perfect = (search + 0.5 * read) / total
    realistic = (0.5 * search + 0.25 * read) / total
    return perfect, realistic


# --- ledger -------------------------------------------------------------
def load_ledger(path: str) -> dict:
    """run_id -> ledger row (last row wins)."""
    rows: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                run_id = row.get("run_id")
                if run_id:
                    rows[run_id] = row
    except OSError:
        return {}
    return rows


def outcome_class(outcome: str | None) -> str:
    if not outcome:
        return "unknown"
    outcome = outcome.lower()
    if outcome.startswith("pr_opened") or outcome in ("pr_merged",
                                                     "ci_fix_pushed"):
        return "pr_opened"
    if "empty-diff" in outcome or "empty_diff" in outcome:
        return "empty_diff"
    if outcome.startswith("failed"):
        return "failed"
    if "cap" in outcome or "aborted" in outcome or "stopped" in outcome:
        return "stopped"
    return "other"


ROLE_SUFFIXES = frozenset({"goldfish", "jittest", "cifix", "hyp", "test",
                           "fix", "phase0"})


def repo_from_run_id(run_id: str) -> str:
    """Repo name from a run id of the form ``<repo>-<issue>-<epoch>``.

    Calibration runs carry a role word between the repo and the issue
    (``<repo>-goldfish-<n>-<epoch>``); those role words are stripped so
    the per-repo table groups by the real repository.
    """
    parts = run_id.split("-")
    while parts and parts[-1].isdigit():
        parts = parts[:-1]
    while len(parts) > 1 and parts[-1] in ROLE_SUFFIXES:
        parts = parts[:-1]
    if len(parts) > 1 and parts[-1].isdigit():
        parts = parts[:-1]
    return "-".join(parts)


# --- steering documents -------------------------------------------------
ENTRY_POINT_KEYWORDS = (
    "entry point", "entry-point", "entrypoint", "entry points",
    "public api", "do not edit", "don't edit", "do-not-touch",
    "do not touch", "architecture", "repository layout", "repo layout",
    "directory structure", "module layout", "key modules",
    "main entry", "cli entry", "starting point",
)


def steering_check(repo: str, repos_root: str) -> dict:
    """Mechanical steering-document check for one repo (issue #79
    section 4): does the repo root carry AGENTS.md / CONTRACT.md /
    CLAUDE.md, and does any of them name entry points?"""
    candidates = [os.path.join(repos_root, repo)]
    if repo == "plan-coder":
        candidates.append(os.path.join(repos_root, "plan-coder-repo"))
    root = next((c for c in candidates if os.path.isdir(c)), None)
    docs = {}
    if root:
        for name in ("AGENTS.md", "CONTRACT.md", "CLAUDE.md", "CONTEXT.md"):
            path = os.path.join(root, name)
            docs[name] = os.path.isfile(path)
    else:
        docs = {name: False for name in ("AGENTS.md", "CONTRACT.md",
                                         "CLAUDE.md", "CONTEXT.md")}
    named = []
    hits: list[str] = []
    if root:
        for name, present in docs.items():
            if not present:
                continue
            try:
                with open(os.path.join(root, name), encoding="utf-8",
                          errors="replace") as fh:
                    text = fh.read().lower()
            except OSError:
                continue
            found = [kw for kw in ENTRY_POINT_KEYWORDS if kw in text]
            if found:
                named.append(name)
                hits.extend(found)
    return {
        "repo": repo,
        "root": root or "",
        "docs": docs,
        "entry_point_docs": named,
        "names_entry_points": bool(named),
        "keywords": sorted(set(hits)),
    }


# --- report -------------------------------------------------------------
def aggregate_runs(runs: list[dict]) -> dict:
    """Every aggregate number the report prints, for one population."""
    shares = [r["struct_share"] for r in runs]
    pre_shares = [r["pre_struct_share"] for r in runs]
    by_repo: dict[str, list[float]] = {}
    by_outcome: dict[str, list[float]] = {}
    by_repo_pre: dict[str, list[float]] = {}
    class_totals_by_outcome: dict[str, dict[str, int]] = {}
    by_disposition: dict[str, list[float]] = {}
    for run in runs:
        by_repo.setdefault(run.get("repo", "(unknown)"), []).append(
            run["struct_share"])
        by_outcome.setdefault(run.get("outcome_class", "unknown"), []).append(
            run["struct_share"])
        by_repo_pre.setdefault(run.get("repo", "(unknown)"), []).append(
            run["pre_struct_share"])
        outcome = run.get("outcome_class", "unknown")
        bucket = class_totals_by_outcome.setdefault(
            outcome, {klass: 0 for klass in CLASSES})
        for klass in CLASSES:
            bucket[klass] += run["counts"].get(klass, 0)
        by_disposition.setdefault(run.get("disposition", "unknown"), []).append(
            run["struct_share"])
    return {
        "share": _summary(shares),
        "pre_share": _summary(pre_shares),
        "step_share": _summary([r.get("struct_step_share", 0.0)
                                for r in runs]),
        "by_repo": {k: _summary(v) for k, v in by_repo.items()},
        "by_repo_pre": {k: _summary(v) for k, v in by_repo_pre.items()},
        "by_outcome": {k: _summary(v) for k, v in by_outcome.items()},
        "by_disposition": {k: _summary(v) for k, v in by_disposition.items()},
        "ceiling_perfect": _summary([r["ceiling_perfect"] for r in runs]),
        "ceiling_realistic": _summary([r["ceiling_realistic"]
                                       for r in runs]),
        "char_share": _summary([r["struct_char_share"] for r in runs]),
        "steps_to_first_edit": _summary(
            [float(r["steps_to_first_edit"]) for r in runs
             if r["steps_to_first_edit"] is not None]),
        "calls": sum(r["tool_calls"] for r in runs),
        "class_totals": {cls: sum(r["counts"].get(cls, 0) for r in runs)
                         for cls in CLASSES},
        "class_totals_by_outcome": class_totals_by_outcome,
    }


def verdict_of(aggregate: dict) -> dict:
    """The issue #79 section 5 gate, computed mechanically.

    Kill bar: median structural share < 10% AND p90 < 20% AND the
    pre-first-edit window shows the same pattern (median < 10% and
    p90 < 20%).
    """
    share = aggregate["share"]
    pre = aggregate["pre_share"]
    median = share["median"] if share["n"] else 0.0
    p90 = share["p90"] if share["n"] else 0.0
    pre_median = pre["median"] if pre["n"] else 0.0
    pre_p90 = pre["p90"] if pre["n"] else 0.0
    kill_bar = bool(share["n"]) and median < 0.10 and p90 < 0.20 \
        and pre_median < 0.10 and pre_p90 < 0.20
    return {
        "n": share["n"],
        "median_share": median,
        "p90_share": p90,
        "pre_median_share": pre_median,
        "pre_p90_share": pre_p90,
        "kill_bar_met": kill_bar,
        "result": "KILL" if kill_bar else "PROCEED",
    }


def build_report(transcripts_dir: str, resolved_by: str, limit: int,
                 min_steps: int, ledger_path: str, repos_root: str,
                 exclude_run_ids: set[str]) -> dict:
    paths = sample_transcripts(transcripts_dir, limit, exclude_run_ids)
    ledger = load_ledger(ledger_path)
    runs = []
    excluded = []
    for path in paths:
        run_id = os.path.basename(path)[:-len(".jsonl")]
        events = load_events(path)
        if not events:
            excluded.append({"run_id": run_id, "reason": "unreadable/empty"})
            continue
        result = classify_run(events)
        if result["steps"] < min_steps:
            excluded.append({"run_id": run_id,
                             "reason": f"fewer than {min_steps} steps",
                             "steps": result["steps"]})
            continue
        row = ledger.get(run_id) or {}
        result["run_id"] = run_id
        result["ledger_joined"] = bool(row)
        result["repo"] = row.get("repo") or repo_from_run_id(run_id)
        result["outcome"] = row.get("outcome")
        result["outcome_class"] = outcome_class(row.get("outcome"))
        result["tokens_in"] = row.get("tokens_in")
        result["tokens_out"] = row.get("tokens_out")
        result["issue"] = row.get("issue")
        result["disposition"] = transcript_disposition(events)
        result["mtime"] = os.path.getmtime(path)
        perfect, realistic = ceiling_shares(result)
        result["ceiling_perfect"] = perfect
        result["ceiling_realistic"] = realistic
        runs.append(result)

    top = sorted(runs, key=lambda r: (-r["struct_calls"],
                                      r["run_id"]))[:10]
    repos = sorted({r["repo"] for r in runs})
    steering = [steering_check(repo, repos_root) for repo in repos]

    aggregate = aggregate_runs(runs)
    ledger_runs = [r for r in runs if r["ledger_joined"]]
    aggregate_ledger = aggregate_runs(ledger_runs)

    # A deterministic sample of the commands that landed in other_exec,
    # so a human can audit the taxonomy's leakage (issue #79 section 3).
    all_others = [cmd for run in runs for cmd in run["other_exec_samples"]]
    if len(all_others) > 30:
        rng = random.Random(79)
        audit_sample = sorted(rng.sample(all_others, 30))
    else:
        audit_sample = sorted(all_others)

    return {
        "transcripts_dir": transcripts_dir,
        "resolved_by": resolved_by,
        "limit": limit,
        "min_steps": min_steps,
        "excluded_run_ids": sorted(exclude_run_ids),
        "corpus_size": len(runs),
        "sampled_files": len(paths),
        "excluded": excluded,
        "runs": runs,
        "aggregate": aggregate,
        "aggregate_ledger": aggregate_ledger,
        "ledger_joined_runs": len(ledger_runs),
        "top_runs": [
            {"run_id": r["run_id"], "repo": r["repo"],
             "outcome": r["outcome_class"], "struct_calls": r["struct_calls"],
             "tool_calls": r["tool_calls"],
             "subject": r["subjects"][0][0] if r["subjects"] else ""}
            for r in top],
        "steering": steering,
        "other_exec_sample": audit_sample,
        "other_exec_total": sum(r["counts"].get("other_exec", 0)
                                for r in runs),
        "verdict": verdict_of(aggregate),
        "verdict_ledger": verdict_of(aggregate_ledger),
    }


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(report: dict) -> str:
    out = []
    agg = report["aggregate"]
    verdict = report["verdict"]
    out.append("# plan-coder #79 Phase 0 — structural-discovery measurement")
    out.append("")
    out.append("Mechanical classifier over existing worker transcripts. "
               "No installs, no harness changes, no LLM judge.")
    out.append("")
    out.append(f"- Transcript dir: `{report['transcripts_dir']}` "
               f"(resolved: {report['resolved_by']})")
    out.append(f"- Sampling rule: the {report['limit']} most recent "
               f"`*.jsonl` transcripts by file mtime (descending, ties by "
               f"name), minus the excluded run ids "
               f"{report['excluded_run_ids'] or '(none)'}; runs with fewer "
               f"than {report['min_steps']} steps are dropped.")
    out.append(f"- Corpus: **{report['corpus_size']} qualifying runs** "
               f"({report['sampled_files']} files sampled, "
               f"{len(report['excluded'])} excluded).")
    out.append("")
    out.append("## Verdict")
    out.append("")
    out.append(f"- Kill bar (median < 10% AND p90 < 20% AND the same "
               f"pattern pre-first-edit): "
               f"**{'MET' if verdict['kill_bar_met'] else 'NOT MET'}**")
    out.append(f"- Median structural share: **{_fmt_pct(verdict['median_share'])}**"
               f" · p90 **{_fmt_pct(verdict['p90_share'])}**")
    out.append(f"- Pre-first-edit window: median "
               f"**{_fmt_pct(verdict['pre_median_share'])}** · p90 "
               f"**{_fmt_pct(verdict['pre_p90_share'])}**")
    out.append(f"- Result: **{verdict['result']}**")
    out.append("")
    ledger_verdict = report["verdict_ledger"]
    out.append(f"Sensitivity cut — the {report['ledger_joined_runs']} runs "
               f"that join the worker ledger (real supervisor worker runs; "
               f"the rest are calibration runs of the same agent loop): "
               f"median **{_fmt_pct(ledger_verdict['median_share'])}**, p90 "
               f"**{_fmt_pct(ledger_verdict['p90_share'])}**, pre-edit "
               f"median **{_fmt_pct(ledger_verdict['pre_median_share'])}** "
               f"→ **{ledger_verdict['result']}**.")
    out.append("")
    out.append("## Classifier rules (mechanical, ambiguous → other_exec)")
    out.append("")
    out.append("| class | rule |")
    out.append("|---|---|")
    for klass, rule in RULES:
        out.append(f"| `{klass}` | {rule} |")
    out.append("")
    out.append("## Class totals")
    out.append("")
    out.append("| class | calls |")
    out.append("|---|---|")
    for klass in CLASSES:
        out.append(f"| `{klass}` | {agg['class_totals'].get(klass, 0)} |")
    out.append(f"| **total** | **{agg['calls']}** |")
    out.append("")
    out.append("## Aggregate")
    out.append("")
    out.append("| cut | n | median | p90 |")
    out.append("|---|---|---|---|")
    out.append(f"| all tool calls | {agg['share']['n']} | "
               f"{_fmt_pct(agg['share']['median'])} | "
               f"{_fmt_pct(agg['share']['p90'])} |")
    out.append(f"| pre-first-edit window | {agg['pre_share']['n']} | "
               f"{_fmt_pct(agg['pre_share']['median'])} | "
               f"{_fmt_pct(agg['pre_share']['p90'])} |")
    out.append(f"| share of steps carrying a discovery call | "
               f"{agg['step_share']['n']} | "
               f"{_fmt_pct(agg['step_share']['median'])} | "
               f"{_fmt_pct(agg['step_share']['p90'])} |")
    out.append(f"| share of tool-result chars | {agg['char_share']['n']} | "
               f"{_fmt_pct(agg['char_share']['median'])} | "
               f"{_fmt_pct(agg['char_share']['p90'])} |")
    out.append(f"| ceiling, perfect graph (100%/50%) | "
               f"{agg['ceiling_perfect']['n']} | "
               f"{_fmt_pct(agg['ceiling_perfect']['median'])} | "
               f"{_fmt_pct(agg['ceiling_perfect']['p90'])} |")
    out.append(f"| ceiling, realistic graph (50%/25%) | "
               f"{agg['ceiling_realistic']['n']} | "
               f"{_fmt_pct(agg['ceiling_realistic']['median'])} | "
               f"{_fmt_pct(agg['ceiling_realistic']['p90'])} |")
    out.append("")
    out.append("### By repo")
    out.append("")
    out.append("| repo | runs | median share | p90 | median pre-edit share |")
    out.append("|---|---|---|---|---|")
    for repo in sorted(agg["by_repo"]):
        row = agg["by_repo"][repo]
        pre = agg["by_repo_pre"].get(repo, {})
        out.append(f"| {repo} | {row['n']} | {_fmt_pct(row['median'])} | "
                   f"{_fmt_pct(row['p90'])} | "
                   f"{_fmt_pct(pre.get('median', 0.0))} |")
    out.append("")
    out.append("### By outcome class")
    out.append("")
    out.append("| outcome class | runs | median share | p90 |")
    out.append("|---|---|---|---|")
    for name in sorted(agg["by_outcome"]):
        row = agg["by_outcome"][name]
        out.append(f"| {name} | {row['n']} | {_fmt_pct(row['median'])} | "
                   f"{_fmt_pct(row['p90'])} |")
    out.append("")
    out.append("### By transcript disposition")
    out.append("")
    out.append("| disposition | runs | median share | p90 |")
    out.append("|---|---|---|---|")
    for name in sorted(agg["by_disposition"]):
        row = agg["by_disposition"][name]
        out.append(f"| {name} | {row['n']} | {_fmt_pct(row['median'])} | "
                   f"{_fmt_pct(row['p90'])} |")
    out.append("")
    out.append("### Tool calls by class and run outcome")
    out.append("")
    outcomes = sorted(agg["class_totals_by_outcome"])
    out.append("| class | " + " | ".join(outcomes) + " | total |")
    out.append("|---" * (len(outcomes) + 2) + "|")
    for klass in CLASSES:
        cells = [str(agg["class_totals_by_outcome"].get(o, {})
                     .get(klass, 0)) for o in outcomes]
        out.append(f"| `{klass}` | " + " | ".join(cells)
                   + f" | {agg['class_totals'].get(klass, 0)} |")
    out.append("")
    out.append("### Steps to first edit")
    out.append("")
    out.append(f"- median **{agg['steps_to_first_edit'].get('median', 0):.0f}**"
               f" · p90 **{agg['steps_to_first_edit'].get('p90', 0):.0f}** "
               f"(n={agg['steps_to_first_edit'].get('n', 0)})")
    out.append("")
    out.append("### Ledger-joined sensitivity cut")
    out.append("")
    out.append("| cut | n | median | p90 |")
    out.append("|---|---|---|---|")
    lagg = report["aggregate_ledger"]
    out.append(f"| all tool calls | {lagg['share']['n']} | "
               f"{_fmt_pct(lagg['share']['median'])} | "
               f"{_fmt_pct(lagg['share']['p90'])} |")
    out.append(f"| pre-first-edit window | {lagg['pre_share']['n']} | "
               f"{_fmt_pct(lagg['pre_share']['median'])} | "
               f"{_fmt_pct(lagg['pre_share']['p90'])} |")
    out.append(f"| ceiling, perfect graph (100%/50%) | "
               f"{lagg['ceiling_perfect']['n']} | "
               f"{_fmt_pct(lagg['ceiling_perfect']['median'])} | "
               f"{_fmt_pct(lagg['ceiling_perfect']['p90'])} |")
    out.append(f"| ceiling, realistic graph (50%/25%) | "
               f"{lagg['ceiling_realistic']['n']} | "
               f"{_fmt_pct(lagg['ceiling_realistic']['median'])} | "
               f"{_fmt_pct(lagg['ceiling_realistic']['p90'])} |")
    out.append("")
    out.append("## `other_exec` audit sample")
    out.append("")
    out.append(f"{report['other_exec_total']} calls landed in `other_exec`. "
               f"Deterministic sample of {len(report['other_exec_sample'])} "
               f"commands (seed 79) for a human to audit the taxonomy's "
               f"leakage:")
    out.append("")
    for cmd in report["other_exec_sample"]:
        out.append(f"- `{cmd}`")
    out.append("")
    out.append("## Top 10 runs by absolute structural-discovery calls")
    out.append("")
    out.append("| run | repo | outcome | structural calls | tool calls | "
               "most-searched subject |")
    out.append("|---|---|---|---|---|---|")
    for row in report["top_runs"]:
        out.append(f"| {row['run_id']} | {row['repo']} | {row['outcome']} | "
                   f"{row['struct_calls']} | {row['tool_calls']} | "
                   f"`{row['subject']}` |")
    out.append("")
    out.append("## Steering-document check (issue #79 section 4)")
    out.append("")
    out.append("| repo | clone root | AGENTS.md | CONTRACT.md | CLAUDE.md | "
               "CONTEXT.md | names entry points |")
    out.append("|---|---|---|---|---|---|---|")
    for row in report["steering"]:
        docs = row["docs"]
        out.append(f"| {row['repo']} | `{row['root']}` | "
                   f"{'yes' if docs.get('AGENTS.md') else 'no'} | "
                   f"{'yes' if docs.get('CONTRACT.md') else 'no'} | "
                   f"{'yes' if docs.get('CLAUDE.md') else 'no'} | "
                   f"{'yes' if docs.get('CONTEXT.md') else 'no'} | "
                   f"{'yes (' + ', '.join(row['entry_point_docs']) + ')' if row['names_entry_points'] else 'no'} |")
    out.append("")
    out.append("## Per-run table")
    out.append("")
    out.append("| run | repo | outcome | disposition | ledger | steps | "
               "tool calls | struct_search | struct_read | share | "
               "discovery steps | pre-edit calls | pre-edit share | "
               "steps to first edit |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for run in report["runs"]:
        counts = run["counts"]
        out.append(
            f"| {run['run_id']} | {run['repo']} | {run['outcome_class']} | "
            f"{run.get('disposition', '')} | "
            f"{'yes' if run['ledger_joined'] else 'no'} | "
            f"{run['steps']} | {run['tool_calls']} | "
            f"{counts.get('struct_search', 0)} | "
            f"{counts.get('struct_read', 0)} | "
            f"{_fmt_pct(run['struct_share'])} | "
            f"{run.get('struct_steps', 0)} | {run['pre_calls']} | "
            f"{_fmt_pct(run['pre_struct_share'])} | "
            f"{run['steps_to_first_edit'] if run['steps_to_first_edit'] is not None else '-'} |")
    out.append("")
    if report["excluded"]:
        out.append("### Excluded from the corpus")
        out.append("")
        for row in report["excluded"]:
            out.append(f"- `{row['run_id']}` — {row['reason']}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase-0 structural-discovery measurement "
                    "(plan-coder issue #79).")
    parser.add_argument("--transcripts-dir", default=None,
                        help="transcript directory (default: resolved like "
                             "bin/agent.py)")
    parser.add_argument("--limit", type=int, default=60,
                        help="most recent transcripts to sample")
    parser.add_argument("--exclude-run-id", action="append", default=[],
                        help="run id to drop from the sample (repeatable)")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER,
                        help="worker ledger jsonl (repo/outcome joins)")
    parser.add_argument("--repos-root",
                        default=os.path.expanduser("~/workspace"),
                        help="root holding the supervisor clones")
    parser.add_argument("--min-steps", type=int, default=5,
                        help="minimum steps for a run to qualify")
    parser.add_argument("--format", choices=("markdown", "json"),
                        default="markdown")
    parser.add_argument("--out", default=None,
                        help="also write the rendered output here")
    args = parser.parse_args(argv)

    transcripts_dir, resolved_by = resolve_transcripts_dir(
        args.transcripts_dir)
    if not os.path.isdir(transcripts_dir):
        print(f"transcript directory not found: {transcripts_dir}",
              file=sys.stderr)
        return 2
    report = build_report(transcripts_dir, resolved_by, args.limit,
                          args.min_steps, args.ledger, args.repos_root,
                          set(args.exclude_run_id))
    if args.format == "json":
        rendered = json.dumps(report, indent=2, sort_keys=True,
                              default=str)
    else:
        rendered = render_markdown(report)
    print(rendered)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)),
                    exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
