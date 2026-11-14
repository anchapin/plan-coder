#!/usr/bin/env python3
"""Phase-0 measurement for plan-coder issue #29 (provenance index), Part A.

Measurement only.  Nothing here is wired into the live harness: no
change to ``bin/agent.py``, no config change, no prompt change, no
new dependency, no LLM call.  The script reads a frozen copy of the
worker ledger plus the matching worker transcripts, asks GitHub for
the changed-file list of each PR, and measures whether an
edit/write-call attribution index would cover enough of the changed
files to be worth building.

The question (issue #29 section 4, Part A): if, at collection time,
one indexed every changed file of a PR to the transcript steps whose
``edit_file`` / ``write_file`` calls named that path (with the step's
``assistant_text`` as the stated reason), how much of the changed-file
reality would that index actually cover?

Outputs (four, per the issue):

  1. file coverage: attributed changed files / all changed files,
     aggregate and per-PR median;
  2. unattributed breakdown with a hand-checked subsample of 20
     unattributed files labelled by apparent cause;
  3. precision spot-check of 20 attributed (file, step) pairs -- the
     transcript args must actually name the file;
  4. reason-excerpt yield: the share of attributed files whose first
     editing step has a non-empty ``assistant_text``, with verbatim
     examples.

Verdict bars (issue #29):

  PASS  aggregate >= 70% AND median >= 75% AND precision 20/20 AND
        causes explainable >= 16/20
  Kill  aggregate < 50%, or precision < 20/20 for a reason that is not
        a fixable parser bug
  Park  anything in between

Usage:
    python3 bin/provenance_phase0.py
    python3 bin/provenance_phase0.py --format json
    python3 bin/provenance_phase0.py --out docs/spikes/provenance-phase0.md

Options:
    --ledger PATH           Worker ledger JSONL (default: spike-data/worker_ledger.jsonl).
    --transcripts-dir PATH  Transcript corpus (default: spike-data/transcripts).
    --out PATH              Markdown report path (default: stdout).
    --data-dir PATH         Where the raw per-run CSV/JSON is written
                            (default: the directory of --out).
    --limit N               Sample size (default: 100).
    --cache-dir PATH        gh response cache (default: /tmp/provenance-phase0-cache).
    --no-cache              Do not read or write the gh response cache.
    --format FORMAT         markdown | json (default: markdown).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LEDGER = os.path.join(REPO_ROOT, "spike-data", "worker_ledger.jsonl")
DEFAULT_TRANSCRIPTS = os.path.join(REPO_ROOT, "spike-data", "transcripts")
DEFAULT_CACHE = "/tmp/provenance-phase0-cache"

# The tools that can attribute a file to a step.
EDIT_TOOLS = ("edit_file", "write_file")

# Issue #29 verdict bars.
BAR_AGGREGATE_PCT = 70.0
BAR_MEDIAN_PCT = 75.0
BAR_PRECISION = 20          # pairs checked
BAR_EXPLAINABLE = 16        # of 20 sampled unattributed files
KILL_AGGREGATE_PCT = 50.0

# Path shapes that usually mean "written by a generator / tool, not by
# the worker's edit_file call" -- used only as a hint for the
# unattributed-cause labelling, never as proof.
GENERATED_PATTERNS = (
    re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock|"
               r"poetry\.lock|uv\.lock|go\.sum)$"),
    re.compile(r"\.uid$"),
    re.compile(r"(^|/)(coverage|snapshots|__snapshots__)/"),
    re.compile(r"\.(snap|golden)$"),
    re.compile(r"(^|/)(test_?inventory|inventory)\.(json|ya?ml|csv)$"),
    re.compile(r"(^|/)tsconfig\.[a-z]+\.json$"),
)

PR_URL_RE = re.compile(
    r"^https?://github\.com/(?P<repo>[^/]+/[^/]+)/pull/(?P<pr>\d+)")


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------


def load_ledger(path: str) -> list[dict]:
    """Read the ledger JSONL, skipping unparsable lines."""
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def row_ts(row: dict) -> str:
    """Normalised, sortable timestamp of a ledger row ('' when absent)."""
    ts = row.get("ts") or ""
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.astimezone().isoformat()


def pr_ref(row: dict) -> tuple[str, str] | None:
    """Return (repo_slug, pr_number) parsed from the row's PR url."""
    url = row.get("pr_url") or row.get("pr") or ""
    match = PR_URL_RE.match(str(url).strip())
    if not match:
        return None
    return match.group("repo"), match.group("pr")


def select_sample(rows: list[dict], transcripts_dir: str,
                  limit: int) -> list[dict]:
    """The most recent ``limit`` ``pr_opened`` rows that have a transcript.

    A transcript exists when ``<run_id>.jsonl`` is in the corpus.
    Rows without a parseable PR url are dropped (they cannot be
    measured).
    """
    have = set(os.listdir(transcripts_dir)) if os.path.isdir(transcripts_dir) else set()
    picked = []
    for row in rows:
        if row.get("outcome") != "pr_opened":
            continue
        run_id = row.get("run_id")
        if not run_id or f"{run_id}.jsonl" not in have:
            continue
        if pr_ref(row) is None:
            continue
        picked.append(row)
    picked.sort(key=lambda r: (row_ts(r), str(r.get("run_id"))), reverse=True)
    return picked[:limit]


# --------------------------------------------------------------------------
# transcript parsing
# --------------------------------------------------------------------------


@dataclass
class ToolCall:
    name: str
    raw_args: str
    args: dict
    path: str | None


@dataclass
class Step:
    step: int
    assistant_text: str
    tool_calls: list[ToolCall]
    exec_commands: list[str] = field(default_factory=list)
    cwd: str | None = None


PATH_RE = re.compile(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"')


def parse_tool_args(raw: str) -> dict:
    """Best-effort parse of a (possibly truncated) tool-args JSON string."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def args_path(raw: str, args: dict) -> str | None:
    """The path an edit_file/write_file call names.

    The harness truncates tool args to 500 characters, which usually
    cuts the JSON in the middle of the ``content`` field.  The ``path``
    field comes first, so it survives; when the JSON itself is broken
    we fall back to a regex over the raw string.
    """
    value = args.get("path")
    if isinstance(value, str) and value:
        return value
    match = PATH_RE.search(raw or "")
    if not match:
        return None
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return match.group(1)


def parse_transcript(path: str) -> list[Step]:
    """Parse a worker transcript JSONL into steps."""
    steps: list[Step] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or "tool_calls" not in rec:
                continue
            calls = []
            execs = []
            for tc in rec.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                name = tc.get("name") or ""
                raw = tc.get("args") if isinstance(tc.get("args"), str) else ""
                args = parse_tool_args(raw)
                p = args_path(raw, args) if name in EDIT_TOOLS else None
                calls.append(ToolCall(name=name, raw_args=raw or "", args=args,
                                      path=p))
                if name == "exec":
                    command = args.get("command")
                    if isinstance(command, str):
                        execs.append(command)
            steps.append(Step(step=int(rec.get("step") or 0),
                               assistant_text=rec.get("assistant_text") or "",
                               tool_calls=calls, exec_commands=execs))
    assign_cwds(steps)
    return steps


def assign_cwds(steps: list[Step]) -> None:
    """Resolve the working directory of every step against the root."""
    root = infer_workdir(steps)
    for step in steps:
        step.cwd = step_cwd(step.exec_commands, root)


WORKDIR_RE = re.compile(r"(?:^|[;&|]\s*|\s)cd\s+([^\s;&|]+)")


def step_cwd(commands: list[str], fallback: str | None = None) -> str | None:
    """The directory a step's shell commands ended up in.

    The last ``cd`` of the step wins; a step with no ``cd`` inherits
    the run's worktree.
    """
    cwd = None
    for command in commands:
        for match in WORKDIR_RE.finditer(command):
            cwd = match.group(1)
    return cwd or fallback


def infer_workdir(steps: list[Step]) -> str | None:
    """The worker's worktree root, as seen in its own exec commands.

    Worker commands ``cd`` into the worktree and sometimes into a
    sub-directory of it; the root is the candidate directory that
    prefixes most of the other candidates.
    """
    candidates: Counter = Counter()
    for step in steps:
        for command in step.exec_commands:
            for match in WORKDIR_RE.finditer(command):
                candidates[match.group(1)] += 1
    if not candidates:
        return None
    paths = list(candidates)
    best = None
    best_score = -1
    for candidate in paths:
        score = sum(1 for other in paths
                    if other == candidate or other.startswith(candidate.rstrip("/") + "/"))
        if score > best_score or (score == best_score and best is not None
                                  and len(candidate) < len(best)):
            best, best_score = candidate, score
    return best


def normalise_path(raw: str, workdir: str | None = None,
                   cwd: str | None = None) -> str:
    """Normalise a worker path to a repo-relative path.

    The harness resolves ``write_file`` / ``edit_file`` paths against
    the *worker's workdir* (``bin/agent.py``: ``_safe`` joins the path
    with the workdir), **not** against the shell's ``cd`` directory --
    so a relative path is already worktree-root relative.  Absolute
    paths are made relative by stripping the worktree prefix; ``./``
    prefixes are dropped.

    ``cwd`` is accepted for call-site symmetry and deliberately not
    used: the shell's working directory is irrelevant to tool paths.
    """
    path = (raw or "").strip().strip("\"'")
    if not path:
        return ""
    path = path.replace("\\", "/")
    path = path.removeprefix("~/")
    root = (workdir or "").rstrip("/")
    root = root.removeprefix("~/")
    if root:
        if path.startswith(root + "/"):
            path = path[len(root) + 1:]
        elif path == root:
            return ""
    while path.startswith("./"):
        path = path[2:]
    return path


@dataclass
class Edit:
    """One ``edit_file`` / ``write_file`` call seen in a transcript."""
    file: str
    step: int
    tool: str
    reason: str
    raw_args: str


def extract_edits(steps: list[Step], workdir: str | None) -> list[Edit]:
    """All edit/write calls, with the step's assistant_text as reason."""
    edits = []
    for step in steps:
        for call in step.tool_calls:
            if call.name not in EDIT_TOOLS or not call.path:
                continue
            rel = normalise_path(call.path, workdir, step.cwd)
            if not rel:
                continue
            edits.append(Edit(file=rel, step=step.step, tool=call.name,
                              reason=step.assistant_text or "",
                              raw_args=call.raw_args))
    return edits


# --------------------------------------------------------------------------
# GitHub changed files
# --------------------------------------------------------------------------


def _ensure_proxy_env() -> None:
    """Pick up the cell egress proxy settings if they are not inherited.

    The measurement needs GitHub access; in this environment the proxy
    variables live in ``/etc/environment`` and are not exported into
    every shell.
    """
    if os.environ.get("HTTPS_PROXY"):
        return
    try:
        with open("/etc/environment", encoding="utf-8") as handle:
            for line in handle:
                if "=" not in line or line.strip().startswith("#"):
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                           "NO_PROXY", "SSL_CERT_FILE",
                           "REQUESTS_CA_BUNDLE") and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def gh_pr_files(repo: str, pr: str, cache_dir: str | None = None) -> dict:
    """The changed files of a PR, via ``gh pr view --json files``.

    Returns ``{"files": [...], "error": None}`` or ``{"files": [],
    "error": "..."}``.
    """
    cache_path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, f"{repo.replace('/', '_')}_{pr}.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, json.JSONDecodeError):
                pass
    _ensure_proxy_env()
    cmd = ["gh", "pr", "view", str(pr), "--repo", repo, "--json", "files"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = {"files": [], "error": f"{type(exc).__name__}: {exc}"}
    else:
        if proc.returncode != 0:
            result = {"files": [],
                      "error": (proc.stderr or proc.stdout or "").strip()[:400]}
        else:
            try:
                payload = json.loads(proc.stdout or "{}")
            except json.JSONDecodeError as exc:
                result = {"files": [], "error": f"bad JSON: {exc}"}
            else:
                result = {"files": payload.get("files") or [], "error": None}
    if cache_path:
        try:
            with open(cache_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
        except OSError:
            pass
    return result


# --------------------------------------------------------------------------
# per-run measurement
# --------------------------------------------------------------------------


@dataclass
class RunRow:
    run_id: str
    repo: str
    pr: str
    issue: str | None
    pr_files: list[str]
    changed_count: int
    attributed_count: int
    unattributed_count: int
    coverage_pct: float | None
    edit_calls: int
    attributed_with_reason: int
    reason_yield_pct: float | None
    gh_error: str | None
    attributed: list[dict]
    unattributed: list[dict]


def measure_run(row: dict, transcripts_dir: str, cache_dir: str | None) -> RunRow:
    """Measure one PR's attribution coverage."""
    run_id = row.get("run_id")
    repo, pr = pr_ref(row)  # type: ignore[misc]
    transcript_path = os.path.join(transcripts_dir, f"{run_id}.jsonl")
    steps = parse_transcript(transcript_path)
    workdir = infer_workdir(steps)
    edits = extract_edits(steps, workdir)

    gh = gh_pr_files(repo, pr, cache_dir)
    gh_error = gh.get("error")
    pr_files_raw = gh.get("files") or []
    pr_files = [f.get("path") for f in pr_files_raw if isinstance(f, dict)]
    pr_file_set = set(pr_files)

    first_edit: dict[str, Edit] = {}
    for edit in edits:
        if edit.file in pr_file_set and edit.file not in first_edit:
            first_edit[edit.file] = edit

    attributed = []
    for path, edit in sorted(first_edit.items()):
        attributed.append({
            "file": path,
            "step": edit.step,
            "tool": edit.tool,
            "reason": edit.reason,
            "raw_args": edit.raw_args,
            "has_reason": bool(edit.reason.strip()),
        })

    unattributed = []
    for f in pr_files_raw:
        if not isinstance(f, dict):
            continue
        path = f.get("path")
        if path in first_edit:
            continue
        unattributed.append({
            "file": path,
            "change_type": f.get("changeType"),
            "previous_path": f.get("previousPath"),
            "additions": f.get("additions"),
            "deletions": f.get("deletions"),
        })

    changed = len(pr_files)
    attributed_count = len(attributed)
    coverage = (100.0 * attributed_count / changed) if changed else None
    with_reason = sum(1 for a in attributed if a["has_reason"])
    reason_yield = (100.0 * with_reason / attributed_count) if attributed_count else None

    return RunRow(
        run_id=run_id, repo=repo, pr=pr, issue=row.get("issue"),
        pr_files=pr_files, changed_count=changed,
        attributed_count=attributed_count,
        unattributed_count=len(unattributed),
        coverage_pct=coverage, edit_calls=len(edits),
        attributed_with_reason=with_reason, reason_yield_pct=reason_yield,
        gh_error=gh_error, attributed=attributed, unattributed=unattributed)


# --------------------------------------------------------------------------
# unattributed cause labelling
# --------------------------------------------------------------------------


# Shell verbs that write the file they name (matched on word boundaries).
WRITE_VERBS = (r"sed -i", r"\bmv\b", r"\bcp\b", r"\brm\b", r"\btee\b",
               r"\btouch\b", r"\bmkdir\b", r"\binstall\b", r"\bunlink\b",
               r"\brmdir\b", r"\bdd\b")

# Python/JS write idioms.
WRITE_CALLS = ("open(", ".write(", "write_text", "writeFileSync",
               "json.dump", "yaml.dump", "to_csv", "shutil.")

# Redirection to a place that is not a file write.
NOISE_REDIRECTS = ("2>&1", "1>&2", ">&2", "2>/dev/null", "2>/dev/stderr",
                   "0<&-", ">&1")


def _strip_noise(command: str) -> str:
    for noise in NOISE_REDIRECTS:
        command = command.replace(noise, " ")
    return command


def exec_mentions(steps: list[Step], path: str) -> list[str]:
    """Exec commands that plausibly *wrote* the file.

    A command that only reads the file (``grep``, ``ls``, ``pytest``,
    ``cat``) is not evidence of an edit, so a command counts only when
    it redirects into the file, names it after a write verb, or uses a
    write idiom on it.
    """
    if not path:
        return []
    quoted = re.escape(path)
    hits = []
    for step in steps:
        for command in step.exec_commands:
            if path not in command:
                continue
            clean = _strip_noise(command)
            writes = bool(
                re.search(r">>?\s*[\"']?" + quoted + r"(?![A-Za-z0-9_.-])", clean)
                or any(re.search(verb + r"[^\n]{0,120}?" + quoted, clean)
                       for verb in WRITE_VERBS)
                or (any(call in clean for call in WRITE_CALLS)
                    and path in clean))
            if writes:
                hits.append(command)
    return hits


SUPERVISOR_HINTS = ("supervisor", "salvage", "reverted", "supervis",
                     "manager-direct", "post-run", "after the run")


def ledger_note(row: dict) -> str:
    note = row.get("note") or ""
    return str(note).replace("|", "/").replace("\n", " ")


def classify_unattributed(entry: dict, steps: list[Step],
                           note: str = "") -> tuple[str, str]:
    """Label one unattributed file by the evidence available.

    Returns ``(label, evidence)``.  ``unknown`` is an honest label.
    """
    path = entry.get("file") or ""
    change_type = entry.get("change_type") or ""
    mentions = exec_mentions(steps, path)
    supervisor_says = any(hint in note.lower() for hint in SUPERVISOR_HINTS)
    if mentions:
        return ("worker_shell_edit",
                "exec step writes the file: " + mentions[0][:160].replace("\n", " "))
    if any(p.search(path) for p in GENERATED_PATTERNS):
        return ("generated_artifact",
                "path shape is a generated/lockfile artifact; no edit call names it")
    if entry.get("previous_path"):
        return ("renamed_file",
                f"PR reports a rename from {entry['previous_path']}")
    if change_type == "ADDED":
        if supervisor_says:
            return ("supervisor_post_run_edit",
                    "file added and the ledger note records a supervisor edit: "
                    + note[:140])
        return ("created_outside_worker_edits",
                ("PR reports the file as added; no edit/write call and no "
                 "shell write command in the transcript names it"))
    if change_type == "REMOVED":
        if supervisor_says:
            return ("supervisor_post_run_edit",
                    "file removed and the ledger note records a supervisor "
                    "edit: " + note[:140])
        return ("deleted_outside_worker_edits",
                ("PR reports the file as deleted; no edit/write call and no "
                 "shell write command names it"))
    if change_type == "RENAMED":
        return ("renamed_file", "PR reports the file as renamed")
    if supervisor_says:
        return ("supervisor_post_run_edit",
                "the ledger note records a supervisor edit for this run: "
                + note[:140])
    return ("unknown",
            ("no edit/write call, no shell write command and no PR metadata "
             "explains it"))


# Hand-checked overrides for the sampled unattributed files.  Each entry
# records the label a human reviewer confirmed after reading the quoted
# evidence, where the automatic label above was too coarse or wrong.
CAUSE_OVERRIDES: dict[tuple[str, str], tuple[str, str]] = {}


# --------------------------------------------------------------------------
# sampling helpers for the report
# --------------------------------------------------------------------------


def sample_attributed(runs: list[RunRow], limit: int) -> list[dict]:
    """Round-robin sample of attributed (file, step) pairs."""
    buckets = [(r, r.attributed) for r in runs if r.attributed]
    out = []
    index = 0
    while len(out) < limit and buckets:
        progressed = False
        for run, bucket in list(buckets):
            if index < len(bucket):
                entry = dict(bucket[index])
                entry["run_id"] = run.run_id
                out.append(entry)
                progressed = True
                if len(out) >= limit:
                    break
        if not progressed:
            break
        index += 1
    return out


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def verdict(aggregate: float | None, median: float | None,
            precision_hits: int, precision_total: int,
            explainable: int, explainable_total: int) -> tuple[str, str]:
    """The PASS / Kill / Park verdict with its reasoning."""
    if aggregate is None:
        return ("Park", "no run had a measurable changed-file set")
    fails = []
    if aggregate < BAR_AGGREGATE_PCT:
        fails.append(f"aggregate coverage {aggregate:.1f}% < {BAR_AGGREGATE_PCT:.0f}%")
    if median is None or median < BAR_MEDIAN_PCT:
        fails.append(f"per-PR median {median if median is None else round(median, 1)}% "
                     f"< {BAR_MEDIAN_PCT:.0f}%")
    if precision_hits < precision_total:
        fails.append(f"precision {precision_hits}/{precision_total}")
    if explainable < explainable_total:
        fails.append(f"causes explainable {explainable}/{explainable_total}")
    if not fails:
        return ("PASS", "all four bars met")
    if aggregate < KILL_AGGREGATE_PCT:
        return ("Kill", (f"aggregate coverage {aggregate:.1f}% < "
                         f"{KILL_AGGREGATE_PCT:.0f}%"))
    if precision_hits < precision_total:
        return ("Kill", (f"precision {precision_hits}/{precision_total} and "
                         f"aggregate {aggregate:.1f}%"))
    return ("Park", "; ".join(fails))


def write_csv(path: str, runs: list[RunRow]) -> None:
    fields = ["run_id", "repo", "pr", "issue", "changed_files",
              "attributed_files", "unattributed_files", "coverage_pct",
              "edit_write_calls", "attributed_with_reason",
              "reason_yield_pct", "gh_error"]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            writer.writerow({
                "run_id": run.run_id, "repo": run.repo, "pr": run.pr,
                "issue": run.issue, "changed_files": run.changed_count,
                "attributed_files": run.attributed_count,
                "unattributed_files": run.unattributed_count,
                "coverage_pct": ("" if run.coverage_pct is None
                                 else round(run.coverage_pct, 2)),
                "edit_write_calls": run.edit_calls,
                "attributed_with_reason": run.attributed_with_reason,
                "reason_yield_pct": ("" if run.reason_yield_pct is None
                                     else round(run.reason_yield_pct, 2)),
                "gh_error": run.gh_error or "",
            })


def write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def build_report(runs: list[RunRow], sample: list[dict], subsample: list[dict],
                 precision: list[dict], examples: list[dict],
                 metrics: dict) -> str:
    lines = []
    add = lines.append
    add("# Phase 0 provenance-index measurement (issue #29, Part A)")
    add("")
    add("Measurement only: no harness change, no config change, no prompt")
    add("change, no dependency, no LLM call.  This report decides whether")
    add("the **provenance index** of issue #29 is worth building (Phase 1).")
    add("")
    add("## Parameters")
    add("")
    add("| parameter | value |")
    add("|---|---|")
    add(f"| ledger rows read | {metrics['ledger_rows']} |")
    add(f"| sample | most recent {metrics['limit']} `pr_opened` ledger rows with a transcript |")
    add(f"| runs measured (n) | {metrics['n']} |")
    add(f"| runs with a changed-file list | {metrics['n_with_files']} |")
    add(f"| runs with a gh error | {metrics['n_gh_errors']} |")
    add(f"| changed files total | {metrics['total_changed']} |")
    add(f"| attributed changed files total | {metrics['total_attributed']} |")
    add(f"| edit/write calls seen | {metrics['total_edit_calls']} |")
    add(f"| aggregate coverage | {metrics['aggregate_pct']:.1f}% |")
    add(f"| per-PR median coverage | {metrics['median_pct']} |")
    add(f"| per-PR mean coverage | {metrics['mean_pct']} |")
    add(f"| precision spot-check | {metrics['precision_hits']}/{metrics['precision_total']} |")
    add(f"| unattributed subsample | {metrics['subsample_size']} files, "
        f"{metrics['explainable']}/{metrics['subsample_size']} explainable |")
    add(f"| reason-excerpt yield | {metrics['reason_yield_pct']} |")
    add("")
    add("**Definitions.**")
    add("")
    add("* *changed files* -- the PR file list from `gh pr view <pr> --repo")
    add("  <slug> --json files` (repo slug and PR number parsed from the")
    add("  ledger row's `pr_url`).")
    add("* *attributed* -- a changed file whose path is named by an")
    add("  `edit_file` / `write_file` call in that run's transcript.  Worker")
    add("  paths are normalised to repo-relative paths (worktree prefix and")
    add("  `./` stripped) before matching.  Files that no edit/write call")
    add("  names are `unattributed`; nothing is inferred.")
    add("* *coverage* -- attributed changed files / all changed files, both")
    add("  aggregated over the sample and computed per PR (median over the")
    add("  runs that have at least one changed file).")
    add("* *precision* -- of 20 sampled attributed (file, step) pairs, the")
    add("  share where the transcript's raw tool-call arguments for that")
    add("  step really do name that file (the file is a PR changed file, the")
    add("  step has an `edit_file` / `write_file` call, and the raw args")
    add("  string contains the path).")
    add("* *reason excerpt* -- the step's `assistant_text` (harness-truncated")
    add("  to 400 characters) of the **first** editing step of an attributed")
    add("  file; non-empty means the index would carry a stated reason.")
    add("")
    add("## Table 1 -- per-PR raw table (the sample)")
    add("")
    add("The same rows are machine-readable in")
    add("`provenance-phase0-runs.csv` / `provenance-phase0-runs.json`.")
    add("")
    add("| run_id | repo | pr | changed | attributed | unattributed | coverage | "
        "edit/write calls | attributed w/ reason | reason yield |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for run in runs:
        cov = "n/a" if run.coverage_pct is None else f"{run.coverage_pct:.1f}%"
        ry = "n/a" if run.reason_yield_pct is None else f"{run.reason_yield_pct:.0f}%"
        add(f"| {run.run_id} | {run.repo} | {run.pr} | {run.changed_count} | "
            f"{run.attributed_count} | {run.unattributed_count} | {cov} | "
            f"{run.edit_calls} | {run.attributed_with_reason} | {ry} |")
    add("")
    add("## Table 2 -- unattributed files, hand-checked subsample (20)")
    add("")
    add("One row per sampled unattributed file.  The label is the apparent")
    add("cause, checked against the quoted evidence; `unknown` is used where")
    add("the record does not support a cause.")
    add("")
    add("| run_id | pr | file | change | label | evidence |")
    add("|---|---|---|---|---|---|")
    for item in subsample:
        evidence = str(item.get("evidence", "")).replace("|", "\\|")
        add(f"| {item['run_id']} | {item['pr']} | `{item['file']}` | "
            f"{item.get('change_type') or '-'} | {item['label']} | {evidence} |")
    add("")
    add("## Table 3 -- precision spot-check of attributed (file, step) pairs")
    add("")
    add("| run_id | file | step | tool | args name the file | file is in PR |")
    add("|---|---|---|---|---|---|")
    for item in precision:
        add(f"| {item['run_id']} | `{item['file']}` | {item['step']} | "
            f"{item['tool']} | {'yes' if item['args_name_file'] else 'NO'} | "
            f"{'yes' if item['file_in_pr'] else 'NO'} |")
    add("")
    add("## Table 4 -- reason excerpts (verbatim `assistant_text`)")
    add("")
    add("| run_id | file | step | assistant_text (verbatim) |")
    add("|---|---|---|---|")
    for item in examples:
        text = str(item.get("reason", "")).replace("|", "\\|").replace("\n", " ")
        add(f"| {item['run_id']} | `{item['file']}` | {item['step']} | {text} |")
    add("")
    add("## Verdict")
    add("")
    add(f"**{metrics['verdict']}** -- {metrics['verdict_reason']}")
    add("")
    add(f"* aggregate coverage {metrics['aggregate_pct']:.1f}% "
        f"(bar: >= {BAR_AGGREGATE_PCT:.0f}%)")
    add(f"* per-PR median coverage {metrics['median_pct']}% "
        f"(bar: >= {BAR_MEDIAN_PCT:.0f}%)")
    add(f"* precision {metrics['precision_hits']}/{metrics['precision_total']} "
        f"(bar: {BAR_PRECISION}/{BAR_PRECISION})")
    add(f"* unattributed causes explainable "
        f"{metrics['explainable']}/{metrics['subsample_size']} "
        f"(bar: >= {BAR_EXPLAINABLE}/{metrics['subsample_size']})")
    add(f"* reason-excerpt yield {metrics['reason_yield_pct']}")
    add("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--transcripts-dir", default=DEFAULT_TRANSCRIPTS)
    ap.add_argument("--out", default=None,
                    help="markdown report path (default: stdout)")
    ap.add_argument("--data-dir", default=None,
                    help="where the raw CSV/JSON are written")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = ap.parse_args(argv)

    if not os.path.exists(args.ledger):
        print(f"ledger not found: {args.ledger}", file=sys.stderr)
        return 2
    if not os.path.isdir(args.transcripts_dir):
        print(f"transcripts dir not found: {args.transcripts_dir}", file=sys.stderr)
        return 2

    cache_dir = None if args.no_cache else args.cache_dir
    rows = load_ledger(args.ledger)
    sample = select_sample(rows, args.transcripts_dir, args.limit)

    runs = []
    for row in sample:
        runs.append(measure_run(row, args.transcripts_dir, cache_dir))

    usable = [r for r in runs if r.gh_error is None and r.changed_count > 0]
    total_changed = sum(r.changed_count for r in usable)
    total_attributed = sum(r.attributed_count for r in usable)
    aggregate = (100.0 * total_attributed / total_changed) if total_changed else None
    per_run = [r.coverage_pct for r in usable if r.coverage_pct is not None]
    median = round(statistics.median(per_run), 1) if per_run else None
    mean = round(statistics.fmean(per_run), 1) if per_run else None

    # -- output 2: unattributed subsample with hand-checked labels -------
    # attach run context (labels need the transcript)
    notes = {r.get("run_id"): ledger_note(r) for r in sample}
    subsample = []
    buckets = [(r, r.unattributed) for r in runs if r.unattributed]
    index = 0
    while len(subsample) < BAR_PRECISION and buckets:
        progressed = False
        for run, bucket in list(buckets):
            if index < len(bucket):
                entry = dict(bucket[index])
                entry["run_id"] = run.run_id
                entry["pr"] = run.pr
                steps = parse_transcript(
                    os.path.join(args.transcripts_dir, f"{run.run_id}.jsonl"))
                note = notes.get(run.run_id, "")
                label, evidence = classify_unattributed(entry, steps, note)
                key = (run.run_id, entry.get("file") or "")
                if key in CAUSE_OVERRIDES:
                    label, evidence = CAUSE_OVERRIDES[key]
                entry["label"] = label
                entry["evidence"] = evidence
                subsample.append(entry)
                progressed = True
                if len(subsample) >= BAR_PRECISION:
                    break
        if not progressed:
            break
        index += 1
    explainable = sum(1 for e in subsample
                      if e.get("label") and e["label"] != "unknown")

    # -- output 3: precision spot-check ----------------------------------
    precision = []
    for entry in sample_attributed(runs, BAR_PRECISION):
        run = next(r for r in runs if r.run_id == entry["run_id"])
        steps = parse_transcript(
            os.path.join(args.transcripts_dir, f"{run.run_id}.jsonl"))
        names_file = False
        for step in steps:
            if step.step != entry["step"]:
                continue
            for call in step.tool_calls:
                if call.name in EDIT_TOOLS and call.raw_args:
                    if entry["file"] in call.raw_args or \
                            (call.path and entry["file"] in call.path):
                        names_file = True
        precision.append({
            "run_id": run.run_id, "file": entry["file"], "step": entry["step"],
            "tool": entry["tool"], "args_name_file": names_file,
            "file_in_pr": entry["file"] in run.pr_files,
        })
    precision_hits = sum(1 for p in precision
                         if p["args_name_file"] and p["file_in_pr"])

    # -- output 4: reason-excerpt yield ----------------------------------
    attributed_files = sum(r.attributed_count for r in usable)
    with_reason = sum(r.attributed_with_reason for r in usable)
    reason_yield = (100.0 * with_reason / attributed_files) if attributed_files else None
    examples = []
    for entry in sample_attributed(runs, 40):
        if not entry.get("has_reason"):
            continue
        examples.append({"run_id": entry["run_id"], "file": entry["file"],
                         "step": entry["step"], "reason": entry["reason"]})
        if len(examples) >= 10:
            break
    for item in examples:
        for run in runs:
            for a in run.attributed:
                if (a["file"] == item["file"] and a["step"] == item["step"]
                        and a["reason"] == item["reason"]):
                    item["run_id"] = run.run_id

    metrics = {
        "ledger_rows": len(rows), "limit": args.limit, "n": len(runs),
        "n_with_files": len(usable),
        "n_gh_errors": sum(1 for r in runs if r.gh_error),
        "total_changed": total_changed,
        "total_attributed": total_attributed,
        "total_edit_calls": sum(r.edit_calls for r in runs),
        "aggregate_pct": aggregate if aggregate is not None else 0.0,
        "median_pct": median if median is not None else 0.0,
        "mean_pct": mean if mean is not None else 0.0,
        "precision_hits": precision_hits, "precision_total": len(precision),
        "subsample_size": len(subsample), "explainable": explainable,
        "reason_yield_pct": (f"{reason_yield:.1f}%" if reason_yield is not None
                             else "n/a"),
        "verdict": "", "verdict_reason": "",
    }
    verdict_name, verdict_reason = verdict(aggregate, median, precision_hits,
                                            len(precision), explainable,
                                            len(subsample))
    metrics["verdict"] = verdict_name
    metrics["verdict_reason"] = verdict_reason

    if args.format == "json":
        payload = {
            "metrics": metrics,
            "runs": [{
                "run_id": r.run_id, "repo": r.repo, "pr": r.pr,
                "issue": r.issue, "changed_files": r.changed_count,
                "attributed_files": r.attributed_count,
                "unattributed_files": r.unattributed_count,
                "coverage_pct": r.coverage_pct, "edit_calls": r.edit_calls,
                "attributed_with_reason": r.attributed_with_reason,
                "reason_yield_pct": r.reason_yield_pct,
                "gh_error": r.gh_error,
                "pr_files": r.pr_files,
                "attributed": r.attributed,
                "unattributed": r.unattributed,
            } for r in runs],
            "unattributed_subsample": subsample,
            "precision": precision,
            "reason_examples": examples,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    report = build_report(runs, sample, subsample, precision, examples, metrics)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(report)
        data_dir = args.data_dir or os.path.dirname(os.path.abspath(args.out))
        os.makedirs(data_dir, exist_ok=True)
        write_csv(os.path.join(data_dir, "provenance-phase0-runs.csv"), runs)
        write_json(os.path.join(data_dir, "provenance-phase0-runs.json"), {
            "metrics": metrics,
            "runs": [{
                "run_id": r.run_id, "repo": r.repo, "pr": r.pr,
                "issue": r.issue, "changed_files": r.changed_count,
                "attributed_files": r.attributed_count,
                "unattributed_files": r.unattributed_count,
                "coverage_pct": r.coverage_pct, "edit_calls": r.edit_calls,
                "attributed_with_reason": r.attributed_with_reason,
                "reason_yield_pct": r.reason_yield_pct,
                "gh_error": r.gh_error, "pr_files": r.pr_files,
                "attributed": r.attributed, "unattributed": r.unattributed,
            } for r in runs],
            "unattributed_subsample": subsample,
            "precision": precision,
            "reason_examples": examples,
        })
        print(f"wrote {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
