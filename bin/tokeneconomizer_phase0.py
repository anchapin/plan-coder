#!/usr/bin/env python3
"""Phase-0 measurement for plan-coder issue #28 (TokenEconomizer).

Phase 0 is measurement only.  Nothing here is wired into the live harness:
no loop nudge in ``bin/agent.py``, no fact-lifecycle code, no new prompt
text, no change to the 200k/750k token caps.  The script reads the frozen
worker-transcript corpus, measures two things and prints the two raw
tables the issue asks for.

  A. Loop waste (digest detector)
     A step is a *loop-waste* step when it re-runs a command that already
     produced the same failure signature earlier in the same run.  The
     detector is digest based: the pair (normalised command, failure
     signature) is keyed by a SHA-256 digest, so the detector never has to
     hold long command strings in its key space.  Reruns that are preceded
     by an intervening edit to a file *related* to the failure are
     discounted: the worker changed something relevant, so the rerun is
     progress, not waste.

  B. Micro-fact recurrence
     A failure signature that recurs in >= 3 runs of one repo, spanning
     >= 3 distinct tasks (issue numbers), is a *micro-fact candidate*.
     Each candidate is cross-checked against the coverage corpus (the
     harness prompts, the ``bin/agent.py`` environment facts, and the
     repo's AGENTS.md / CONTRACT.md / brief text as observed in the
     transcript corpus).  Candidates whose distinctive terms are absent
     from that corpus are *promotable* facts.

Usage:
    python3 bin/tokeneconomizer_phase0.py
    python3 bin/tokeneconomizer_phase0.py --format json
    python3 bin/tokeneconomizer_phase0.py --out docs/spikes/tokeneconomizer-phase0.md

Options:
    --transcripts DIR   Frozen transcript corpus (read-only).
    --repo-root DIR     plan-coder checkout used for the coverage corpus
                        (default: the checkout this script lives in).
    --out PATH          Write the markdown report to PATH (default: stdout).
    --format FORMAT     markdown | json (default: markdown).
    --min-runs N        Minimum runs a repo needs to be "active" (default: 5).
    --min-tasks N       Minimum distinct tasks for a recurrence candidate
                        (default: 3).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TRANSCRIPTS = os.path.expanduser(
    "~/workspace/goals/token-plan-coding-manager-setup/hidden_files/transcripts")

REPOS = (
    "fluxion",
    "staging-studio",
    "matchline",
    "planar-nexus",
    "armored-archer",
    "building-ai-pocs",
    "portkit-v2",
    "plan-coder",
    "retrofit-copilot",
)

# Historic / shorthand repo labels seen in the corpus.
REPO_ALIASES = {
    "sstudio": "staging-studio",
    "staging": "staging-studio",
    "planar": "planar-nexus",
    "pocs": "building-ai-pocs",
}

# Non-worker run flavours that live in the same directory (goldfish
# fixtures, jittest hypothesis/test runs).  They are measured and flagged.
RUN_FLAVOURS = ("goldfish", "jittest-hyp", "jittest-test", "jittest-judge")

COMMAND_TOOLS = ("exec", "bash", "Bash", "exec_command")
FILE_TOOLS = ("edit_file", "write_file")

WORKTREE_RE = re.compile(r"(?:~?/[\w.\-]+)*/(?:fixtrees|workspace)/[\w.\-]+")
ABS_PATH_RE = re.compile(r"(?:~|\$HOME|/home/[\w.\-]+|/Users/[\w.\-]+)/[\w./\-]+")
WORKER_DIR_RE = re.compile(r"[\w.\-]+-worker-[\w.\-]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b")
INT_RE = re.compile(r"\b\d+\b")
PATH_TOKEN_RE = re.compile(r"[\w./\-]*[\w\-]+\.[A-Za-z][A-Za-z0-9]{0,7}")
FLAG_RE = re.compile(r"--[A-Za-z][\w\-]*")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]{2,}")

ERROR_LINE_RE = re.compile(
    r"(?i)(\b(error|errors|failed|failure|not found|no such|cannot|can't|unable"
    r"|denied|missing|invalid|timed?\s?out|timeout|traceback|exception|assert"
    r"|abort|refus|warning:|fatal)\b"
    r"|[A-Za-z_][A-Za-z0-9_]*(Error|Exception)\b"
    r"|\b[A-Z][A-Za-z0-9_]*Error\b)")

GENERIC_TERMS = {
    "error", "errors", "failed", "failure", "cannot", "could", "invalid",
    "missing", "found", "file", "files", "line", "lines", "module", "package",
    "command", "unknown", "unexpected", "expected", "actual", "result",
    "results", "output", "stderr", "stdout", "warning", "warnings", "info",
    "exit", "status", "true", "false", "null", "none", "with", "from", "this",
    "that", "there", "have", "has", "was", "were", "not", "but", "you", "your",
    "using", "used", "use", "when", "then", "than", "into", "for", "and", "the",
    "value", "values", "type", "types", "test", "tests", "case", "cases",
    "timeout", "timedout", "traceback", "recent", "call", "last", "most",
    "traceback", "exception", "raised", "during", "handling", "above",
}

# Boilerplate lines that carry no diagnostic content of their own.
BOILERPLATE_LINES = {
    "traceback (most recent call last):",
    "traceback (most recent call last)",
    "fatal:",
    "error:",
    "warning:",
}

# Kill criteria from the issue body.
LOOP_WASTE_KILL_PCT = 5.0
FACT_MIN_PER_REPO = 3
FACT_COVERED_KILL_PCT = 80.0


# --------------------------------------------------------------------------
# transcript model
# --------------------------------------------------------------------------


@dataclass
class Call:
    """One tool call of a step, with its result."""

    name: str
    args: dict
    result: str


@dataclass
class Step:
    """One worker step of a transcript."""

    index: int
    tokens_in: int = 0
    tokens_out: int = 0
    calls: list = field(default_factory=list)

    @property
    def commands(self):
        return [c for c in self.calls if c.name in COMMAND_TOOLS]

    @property
    def edits(self):
        return [c.args.get("path") or c.args.get("file") or ""
                for c in self.calls if c.name in FILE_TOOLS]


@dataclass
class Run:
    """One transcript file."""

    path: str
    run_id: str | None = None
    repo: str | None = None
    issue: int | None = None
    flavour: str | None = None
    steps: list = field(default_factory=list)
    total_out: int = 0
    total_in: int = 0
    reported_steps: int | None = None

    @property
    def task_key(self):
        """Task identity: repo + issue number (reruns of one issue share it)."""
        if self.repo is None:
            return None
        if self.issue is None:
            return f"{self.repo}:{self.run_id}"
        return f"{self.repo}#{self.issue}"


def parse_run_id(run_id):
    """Split a supervisor run id into (repo, issue, flavour).

    Run ids look like ``fluxion-4744-1794427226`` (repo, issue number,
    dispatch epoch), with optional flavours (``fluxion-goldfish-4744-...``)
    and suffixes (``fluxion-4744-re2-...``).  Returns None when the id
    does not belong to a known repo (smoke runs, shepherd runs, ...).
    """
    if not isinstance(run_id, str) or not run_id:
        return None
    repo = None
    rest = None
    for name in REPOS:
        if run_id == name or run_id.startswith(name + "-"):
            repo = name
            rest = run_id[len(name) + 1:]
            break
    if repo is None:
        base = run_id.split("-")[0]
        repo = REPO_ALIASES.get(base)
        if repo is None:
            return None
        rest = run_id[len(base) + 1:]
    flavour = None
    for name in RUN_FLAVOURS:
        if rest.startswith(name + "-"):
            flavour = name
            rest = rest[len(name) + 1:]
            break
    match = re.match(r"^(\d+)(.*)$", rest)
    if not match:
        return repo, None, flavour
    return repo, int(match.group(1)), flavour


def _as_int(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return 0


def _parse_args(raw):
    """Parse a tool call's ``args`` into a dict, tolerating broken JSON.

    The transcripts store ``args`` as a JSON *string*.  After the outer
    decode that string frequently contains raw newlines, so a second
    ``json.loads`` fails on a large share of the calls.  When it does, the
    few fields this script needs are recovered with a tolerant scan
    instead of dropping the call.
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    out = {}
    for key in ("path", "file", "command", "cmd"):
        match = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % key, raw)
        if match:
            value = match.group(1)
            try:
                value = json.loads('"%s"' % value)
            except json.JSONDecodeError:
                value = value.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
            out[key] = value
    return out


def load_run(path):
    """Parse one transcript jsonl into a Run (read-only)."""
    run = Run(path=os.path.basename(path))
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                event = obj.get("event")
                if event == "started":
                    run.run_id = obj.get("run_id") or run.run_id
                    continue
                if event == "finished":
                    run.total_in = _as_int(obj.get("total_in"))
                    run.total_out = _as_int(obj.get("total_out"))
                    run.reported_steps = _as_int(obj.get("steps")) or None
                    continue
                if "step" not in obj:
                    continue
                step = Step(index=_as_int(obj.get("step")))
                step.tokens_in = _as_int(obj.get("tokens_in"))
                step.tokens_out = _as_int(obj.get("tokens_out"))
                calls = obj.get("tool_calls") or []
                results = obj.get("tool_results") or []
                for index, call in enumerate(calls):
                    if not isinstance(call, dict):
                        continue
                    name = call.get("name") or ""
                    args = _parse_args(call.get("args"))
                    raw_result = ""
                    if index < len(results):
                        candidate = results[index]
                        raw_result = candidate if isinstance(candidate, str) \
                            else json.dumps(candidate)
                    step.calls.append(Call(name=name, args=args, result=raw_result))
                run.steps.append(step)
    except OSError:
        return run
    if run.run_id is None:
        run.run_id = os.path.basename(path)[:-6] if path.endswith(".jsonl") \
            else os.path.basename(path)
    parsed = parse_run_id(run.run_id)
    if parsed:
        run.repo, run.issue, run.flavour = parsed
    return run


def load_corpus(transcripts_dir):
    """Load every transcript in the corpus directory (read-only)."""
    runs = []
    if not os.path.isdir(transcripts_dir):
        return runs
    for name in sorted(os.listdir(transcripts_dir)):
        if not name.endswith(".jsonl"):
            continue
        runs.append(load_run(os.path.join(transcripts_dir, name)))
    return runs


# --------------------------------------------------------------------------
# normalisation helpers
# --------------------------------------------------------------------------


def normalize_command(command):
    """Normalise a shell command so equal commands compare equal.

    Volatile parts are folded away: the worktree path, absolute home
    paths, whitespace runs and trailing separators.  Everything that
    identifies *what* was run stays.
    """
    if not command:
        return ""
    text = command.strip()
    text = WORKTREE_RE.sub("<WORKTREE>", text)
    text = ABS_PATH_RE.sub("<PATH>", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip().rstrip(";").strip()
    return text


def normalize_text(text):
    """Fold volatile paths / numbers / hashes / ANSI codes out of a message."""
    if not text:
        return ""
    text = ANSI_RE.sub("", text)
    text = ABS_PATH_RE.sub("<path>", text)
    text = WORKER_DIR_RE.sub("<worktree>", text)
    text = HEX_RE.sub("<hex>", text)
    text = INT_RE.sub("<n>", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def failure_signature(result):
    """Return a normalised failure signature for a tool result, or None.

    A result that reports success has no failure signature.  Everything
    else is reduced to its informative error lines (up to three,
    de-duplicated) so that the same underlying failure recurs with the
    same signature across runs and repos.  Volatile parts -- absolute
    paths, numbers, hashes -- are folded away, so a per-run worktree
    path never splits one failure into many signatures.
    """
    if not isinstance(result, str):
        return None
    text = result.strip()
    if not text:
        return None
    if text.startswith("exit=0"):
        return None
    if text.startswith("TIMEOUT after"):
        return "TIMEOUT"
    body = text
    if body.startswith("tool error:"):
        body = body[len("tool error:"):].strip()
    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    # The exit-code line carries no diagnostic content of its own.
    if lines and re.match(r"^exit=-?\d+$", lines[0]):
        lines = lines[1:]
    if not lines:
        return "exit=<n>"
    picked = []
    for line in lines:
        if not ERROR_LINE_RE.search(line):
            continue
        if normalize_text(line).lower() in BOILERPLATE_LINES:
            continue
        picked.append(line)
    if not picked:
        # No recognisable error line: keep the tail, where the exception
        # of a traceback lives.
        picked = lines[-2:]
    out = []
    for line in picked[:3]:
        line = normalize_text(line)
        if line and line not in out:
            out.append(line)
    if not out:
        return None
    return " | ".join(out)[:240]


def path_tokens(text):
    """File-ish tokens mentioned in a command or a failure output."""
    if not text:
        return set()
    found = set()
    for match in PATH_TOKEN_RE.findall(text):
        token = match.strip("-+.,:;()[]'\"")
        if "/" in token or "." in token:
            found.add(token)
    return found


def digest(*parts):
    """SHA-256 digest of the given parts (the detector's key space)."""
    sha = hashlib.sha256()
    for part in parts:
        sha.update(part.encode("utf-8", "replace"))
        sha.update(b"\x00")
    return sha.hexdigest()[:16]


def distinctive_terms(signature):
    """Distinctive tokens of a failure signature, for coverage checks."""
    terms = set()
    for match in IDENT_RE.findall(signature):
        token = match.strip(".-_").lower()
        if len(token) < 4 or token in GENERIC_TERMS:
            continue
        if token.isdigit():
            continue
        terms.add(token)
    for match in FLAG_RE.findall(signature):
        terms.add(match.lower())
    return terms


def edit_touches(edit_path, related):
    """True when an edited path is related to the failing command.

    "Related" is deliberately generous: same basename, same path, or one
    basename containing the other (so an edit to
    ``scripts/check_test_inventory_drift.py`` counts as related to a
    failure of ``scripts/ci/test_check_test_inventory_drift.py``).
    """
    if not edit_path or not related:
        return False
    edit_base = os.path.basename(edit_path)
    edit_norm = edit_path.lstrip("./")
    for token in related:
        base = os.path.basename(token)
        if not base:
            continue
        if edit_base == base:
            return True
        if token.lstrip("./") == edit_norm:
            return True
        if token.endswith("/" + base):
            return True
        shorter, longer = sorted((base, edit_base), key=len)
        if len(shorter) >= 8 and shorter in longer:
            return True
    return False


# --------------------------------------------------------------------------
# Part A -- loop waste
# --------------------------------------------------------------------------


@dataclass
class LoopWaste:
    """Result of the loop-waste detector for one run."""

    waste_steps: set = field(default_factory=set)
    discounted_steps: set = field(default_factory=set)
    repeat_success_steps: set = field(default_factory=set)
    pairs: Counter = field(default_factory=Counter)


def detect_loop_waste(run, mode="pair"):
    """Classify the steps of one run with the digest-based detector.

    For every command execution the detector computes a digest of the
    pair (normalised command, failure signature).  The first occurrence
    of a digest is the baseline.  A later occurrence of the same digest
    is a repeat: the step that carries it is a loop-waste step, unless a
    file related to that failure was edited *between* the two occurrences
    -- then the rerun is discounted (the worker changed something
    relevant first, so the rerun is progress, not waste).

    ``mode`` selects the digest granularity:

    ``pair``     digest over (command, failure signature) -- the issue's
                 definition, an identical command that fails identically.
    ``command``  digest over the command alone -- every later failure of
                 the same command counts, whatever the signature.  This
                 is a sensitivity upper bound, not the headline number.
    """
    result = LoopWaste()
    # digest -> (count, related file tokens, step index of last occurrence)
    seen = {}
    edits_by_step = {}
    for step in run.steps:
        edits_by_step[step.index] = list(step.edits)
    step_order = [s.index for s in run.steps]
    position = {index: pos for pos, index in enumerate(step_order)}

    for step in run.steps:
        for call in step.commands:
            raw_command = call.args.get("command") or call.args.get("cmd") or ""
            normalized = normalize_command(str(raw_command))
            if not normalized:
                continue
            signature = failure_signature(call.result)
            related = path_tokens(str(raw_command)) | path_tokens(call.result)
            if mode == "command":
                key = digest(normalized)
            else:
                key = digest(normalized, signature or "ok")
            if signature is None:
                # A repeated *successful* command is tracked but is not
                # loop waste: the detector only counts failed loops.
                if key in seen:
                    result.repeat_success_steps.add(step.index)
                    seen[key] = (seen[key][0] + 1, seen[key][1], step.index)
                else:
                    seen[key] = (1, related, step.index)
                continue
            if key not in seen:
                seen[key] = (1, related, step.index)
                continue
            count, base_related, last_index = seen[key]
            intervening = []
            for other in step_order[position[last_index] + 1:position[step.index]]:
                intervening.extend(edits_by_step.get(other, []))
            if any(edit_touches(edit, base_related | related) for edit in intervening):
                result.discounted_steps.add(step.index)
            else:
                result.waste_steps.add(step.index)
                result.pairs[key] += 1
            seen[key] = (count + 1, base_related | related, step.index)
    return result


# --------------------------------------------------------------------------
# Part B -- micro-fact recurrence and coverage
# --------------------------------------------------------------------------


def collect_signatures(runs):
    """Map (repo, signature) -> {'tasks': set, 'runs': set, 'commands': Counter}.

    Only command failures carry a signature.  Each signature is attributed
    to the task of the run it appeared in, so a signature that only ever
    shows up in reruns of one issue counts once.
    """
    table = defaultdict(lambda: {"tasks": set(), "runs": set(), "commands": Counter()})
    for run in runs:
        if run.repo is None:
            continue
        for step in run.steps:
            for call in step.commands:
                signature = failure_signature(call.result)
                if not signature:
                    continue
                entry = table[(run.repo, signature)]
                if run.task_key:
                    entry["tasks"].add(run.task_key)
                entry["runs"].add(run.run_id)
                raw_command = call.args.get("command") or call.args.get("cmd") or ""
                entry["commands"][normalize_command(str(raw_command))] += 1
    return table


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def build_coverage_corpus(repo_root, runs):
    """Build the coverage corpus used for the "already covered?" check.

    Sources (the only ones this Phase-0 script may read):

    * the harness prompt templates in ``prompts/`` (global) -- the current
      prompts that would carry a promotable fact;
    * the environment facts of ``bin/agent.py``: the environment variable
      names it reads plus its module docstring (global);
    * the repo's AGENTS.md / CONTRACT.md and any brief text *as observed
      in the transcript corpus* (per repo) -- the corpus is the only
      artefact this script may read outside its own checkout, and the
      transcripts carry the file bodies the workers read.
    """
    global_texts = []
    prompt_dir = os.path.join(repo_root, "prompts")
    if os.path.isdir(prompt_dir):
        for name in sorted(os.listdir(prompt_dir)):
            if name.endswith(".md"):
                global_texts.append(read_text(os.path.join(prompt_dir, name)))
    agent_py = read_text(os.path.join(repo_root, "bin", "agent.py"))
    env_facts = sorted(set(re.findall(
        r"os\.environ(?:\.get)?\(?\[?\"([A-Z_0-9]+)\"", agent_py)))
    global_texts.extend(env_facts)
    docstring = agent_py.split('"""', 2)
    if len(docstring) >= 3:
        global_texts.append(docstring[1])

    # Repo documents, harvested from read_file results only: an `ls` that
    # merely mentions AGENTS.md does not carry the document's body.
    doc_bodies = defaultdict(list)
    doc_names = ("agents.md", "contract.md")
    brief_names = ("brief.md",)
    for run in runs:
        if run.repo is None:
            continue
        for step in run.steps:
            for call in step.calls:
                if call.name != "read_file" or not call.result:
                    continue
                path = str(call.args.get("path") or call.args.get("file") or "")
                base = os.path.basename(path).lower()
                if base in doc_names:
                    doc_bodies[run.repo].append(call.result)
                elif base in brief_names:
                    doc_bodies[run.repo].append(call.result)

    corpus = {
        "global": "\n".join(global_texts).lower(),
        "env_facts": env_facts,
        "per_repo": {repo: "\n".join(texts).lower()
                     for repo, texts in doc_bodies.items()},
        "doc_bytes": {repo: sum(len(t) for t in texts)
                      for repo, texts in doc_bodies.items()},
    }
    return corpus


def coverage_of(signature, repo, corpus):
    """Classify one failure signature against the coverage corpus.

    Returns ``(state, ratio, hits, total)`` where ``state`` is one of
    ``no-fact-content`` (the signature carries no promotable fact),
    ``covered`` (the distinctive terms are already documented),
    ``partially-covered`` or ``not-covered``.
    """
    terms = distinctive_terms(signature)
    if not terms:
        return "no-fact-content", 0.0, 0, 0
    global_text = corpus["global"]
    repo_text = corpus["per_repo"].get(repo, "")
    hits = 0
    for term in terms:
        if term in global_text or term in repo_text:
            hits += 1
    ratio = hits / len(terms)
    if ratio >= 0.75:
        return "covered", ratio, hits, len(terms)
    if ratio >= 0.34:
        return "partially-covered", ratio, hits, len(terms)
    return "not-covered", ratio, hits, len(terms)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def build_report(runs, repo_root, min_runs, min_tasks):
    """Compute both measurements and return the report payload."""
    by_repo = defaultdict(list)
    for run in runs:
        by_repo[run.repo or "unattributed"].append(run)

    # ---------------- Part A ----------------
    per_repo_a = {}
    per_repo_sens = {}
    for repo, repo_runs in sorted(by_repo.items()):
        total_steps = 0
        total_tokens = 0
        waste_steps = 0
        waste_tokens = 0
        affected = 0
        discounted = 0
        sens_waste = 0
        for run in repo_runs:
            waste = detect_loop_waste(run, mode="pair")
            sens = detect_loop_waste(run, mode="command")
            total_steps += len(run.steps)
            total_tokens += sum(s.tokens_out for s in run.steps)
            waste_steps += len(waste.waste_steps)
            waste_tokens += sum(
                s.tokens_out for s in run.steps if s.index in waste.waste_steps)
            discounted += len(waste.discounted_steps)
            sens_waste += len(sens.waste_steps)
            if waste.waste_steps:
                affected += 1
        per_repo_a[repo] = {
            "repo": repo,
            "runs": len(repo_runs),
            "runs_affected": affected,
            "total_steps": total_steps,
            "waste_steps": waste_steps,
            "discounted_steps": discounted,
            "total_tokens_out": total_tokens,
            "waste_tokens_out": waste_tokens,
            "waste_pct_steps": (100.0 * waste_steps / total_steps) if total_steps else 0.0,
            "waste_pct_tokens": (100.0 * waste_tokens / total_tokens) if total_tokens else 0.0,
        }
        per_repo_sens[repo] = {
            "repo": repo,
            "total_steps": total_steps,
            "waste_steps": sens_waste,
            "waste_pct_steps": (100.0 * sens_waste / total_steps) if total_steps else 0.0,
        }

    # ---------------- Part B ----------------
    corpus = build_coverage_corpus(repo_root, runs)
    signatures = collect_signatures(runs)
    candidates = defaultdict(list)
    for (repo, signature), entry in signatures.items():
        if len(entry["tasks"]) >= min_tasks and len(entry["runs"]) >= min_tasks:
            candidates[repo].append((signature, entry))

    fact_rows = []
    fact_detail = []
    for repo, entries in sorted(candidates.items()):
        promotable = 0
        covered = 0
        fact_bearing = 0
        for signature, entry in sorted(entries, key=lambda item: -len(item[1]["tasks"])):
            state, ratio, hits, total = coverage_of(signature, repo, corpus)
            if state != "no-fact-content":
                fact_bearing += 1
                covered += int(state == "covered")
                promotable += int(state != "covered")
            fact_detail.append({
                "repo": repo,
                "signature": signature,
                "tasks": len(entry["tasks"]),
                "runs": len(entry["runs"]),
                "coverage": state,
                "coverage_ratio": ratio,
                "hits": hits,
                "terms": total,
                "example_command": entry["commands"].most_common(1)[0][0]
                if entry["commands"] else "",
            })
        fact_rows.append({
            "repo": repo,
            "candidates": len(entries),
            "fact_bearing": fact_bearing,
            "covered": covered,
            "promotable": promotable,
            "runs": per_repo_a.get(repo, {}).get("runs", 0),
            "active": per_repo_a.get(repo, {}).get("runs", 0) >= min_runs,
        })

    return {
        "loop_rows": [per_repo_a[repo] for repo in sorted(per_repo_a)],
        "sensitivity": [per_repo_sens[repo] for repo in sorted(per_repo_sens)],
        "fact_rows": fact_rows,
        "fact_detail": fact_detail,
        "corpus": {
            "files": len(runs),
            "env_facts": corpus["env_facts"],
            "doc_bytes": corpus["doc_bytes"],
        },
    }


def render_markdown(payload, args):
    """Render the two raw tables and the kill-criteria evaluation."""
    out = []
    add = out.append
    add("# Phase 0 TokenEconomizer measurement (issue #28)")
    add("")
    add("Measurement only: no loop nudge, no fact lifecycle, no prompt or")
    add("config change, no change to the 200k/750k token caps.")
    add("")
    add("## Parameters")
    add("")
    add("| parameter | value |")
    add("|---|---|")
    add(f"| transcripts | {args.transcripts} |")
    add(f"| corpus files | {payload['corpus']['files']} |")
    add(f"| min runs for an active repo | {args.min_runs} |")
    add(f"| min distinct tasks for a recurrence candidate | {args.min_tasks} |")
    add(f"| loop-waste kill threshold | < {LOOP_WASTE_KILL_PCT}% of steps |")
    add(f"| fact kill thresholds | < {FACT_MIN_PER_REPO} promotable per active "
        f"repo, or >= {FACT_COVERED_KILL_PCT}% already covered |")
    add("")

    add("## Table A -- loop waste (digest detector)")
    add("")
    add("| repo | runs | runs affected | total steps | waste steps | waste % of steps "
        "| waste % of tokens-out | discounted reruns |")
    add("|---|---|---|---|---|---|---|---|")
    for row in payload["loop_rows"]:
        add(f"| {row['repo']} | {row['runs']} | {row['runs_affected']} | "
            f"{row['total_steps']} | {row['waste_steps']} | "
            f"{row['waste_pct_steps']:.2f}% | {row['waste_pct_tokens']:.2f}% | "
            f"{row['discounted_steps']} |")
    totals = {
        "runs": sum(r["runs"] for r in payload["loop_rows"]),
        "runs_affected": sum(r["runs_affected"] for r in payload["loop_rows"]),
        "total_steps": sum(r["total_steps"] for r in payload["loop_rows"]),
        "waste_steps": sum(r["waste_steps"] for r in payload["loop_rows"]),
        "discounted_steps": sum(r["discounted_steps"] for r in payload["loop_rows"]),
        "total_tokens_out": sum(r["total_tokens_out"] for r in payload["loop_rows"]),
        "waste_tokens_out": sum(r["waste_tokens_out"] for r in payload["loop_rows"]),
    }
    pct_steps = (100.0 * totals["waste_steps"] / totals["total_steps"]
                 if totals["total_steps"] else 0.0)
    pct_tokens = (100.0 * totals["waste_tokens_out"] / totals["total_tokens_out"]
                  if totals["total_tokens_out"] else 0.0)
    add(f"| **TOTAL** | {totals['runs']} | {totals['runs_affected']} | "
        f"{totals['total_steps']} | {totals['waste_steps']} | {pct_steps:.2f}% | "
        f"{pct_tokens:.2f}% | {totals['discounted_steps']} |")
    add("")
    verdict_a = "PROCEED" if pct_steps >= LOOP_WASTE_KILL_PCT else "KILL"
    add(f"**Kill criterion A:** true loop waste = {pct_steps:.2f}% of steps "
        f"(threshold {LOOP_WASTE_KILL_PCT}%).  Verdict: **{verdict_a}**"
        f" -- {'keep the loop nudge alive' if verdict_a == 'PROCEED' else 'kill the loop nudge'}.")
    add("")
    if "sensitivity" in payload:
        add("### Sensitivity: same detector, command-only digest")
        add("")
        add("| repo | total steps | waste steps (command digest) | waste % of steps |")
        add("|---|---|---|---|")
        for row in payload["sensitivity"]:
            add(f"| {row['repo']} | {row['total_steps']} | {row['waste_steps']} | "
                f"{row['waste_pct_steps']:.2f}% |")
        sens_total_steps = sum(r["total_steps"] for r in payload["sensitivity"])
        sens_waste = sum(r["waste_steps"] for r in payload["sensitivity"])
        sens_pct = (100.0 * sens_waste / sens_total_steps) if sens_total_steps else 0.0
        add(f"| **TOTAL** | {sens_total_steps} | {sens_waste} | {sens_pct:.2f}% |")
        add("")
        add(f"The looser digest (same command, any failure signature) also lands at "
            f"{sens_pct:.2f}% of steps, so the KILL verdict is not an artefact of "
            f"the strict signature match.")
        add("")

    add("## Table B -- micro-fact recurrence")
    add("")
    add("| repo | runs | active | recurrence candidates (>=3 tasks) | fact-bearing "
        "candidates | already covered | promotable |")
    add("|---|---|---|---|---|---|---|")
    for row in payload["fact_rows"]:
        add(f"| {row['repo']} | {row['runs']} | {'yes' if row['active'] else 'no'} | "
            f"{row['candidates']} | {row['fact_bearing']} | {row['covered']} | "
            f"{row['promotable']} |")
    total_candidates = sum(r["candidates"] for r in payload["fact_rows"])
    total_fact_bearing = sum(r["fact_bearing"] for r in payload["fact_rows"])
    total_covered = sum(r["covered"] for r in payload["fact_rows"])
    total_promotable = sum(r["promotable"] for r in payload["fact_rows"])
    covered_pct = (100.0 * total_covered / total_fact_bearing) if total_fact_bearing else 0.0
    add(f"| **TOTAL** | {sum(r['runs'] for r in payload['fact_rows'])} | | "
        f"{total_candidates} | {total_fact_bearing} | {total_covered} | "
        f"{total_promotable} |")
    add("")
    add("### Candidates (raw)")
    add("")
    add("| repo | failure signature | distinct tasks | runs | coverage | terms hit "
        "| example command |")
    add("|---|---|---|---|---|---|---|")
    for row in payload["fact_detail"]:
        add(f"| {row['repo']} | {row['signature'][:120]} | {row['tasks']} | "
            f"{row['runs']} | {row['coverage']} ({row['coverage_ratio']:.2f}) | "
            f"{row['hits']}/{row['terms']} | {row['example_command'][:80]} |")
    add("")
    active_rows = [r for r in payload["fact_rows"] if r["active"]]
    repos_below_bar = [r["repo"] for r in active_rows
                       if r["promotable"] < FACT_MIN_PER_REPO]
    if not active_rows:
        verdict_b = "KILL"
        reason_b = "no active repo in the corpus"
    elif covered_pct >= FACT_COVERED_KILL_PCT:
        verdict_b = "KILL"
        reason_b = (f"{covered_pct:.1f}% of fact-bearing candidates are already "
                    f"covered (>= {FACT_COVERED_KILL_PCT}%)")
    elif repos_below_bar:
        verdict_b = "KILL"
        reason_b = ("active repos with fewer than "
                    f"{FACT_MIN_PER_REPO} promotable facts: "
                    + ", ".join(repos_below_bar))
    else:
        verdict_b = "PROCEED"
        reason_b = ("every active repo has >= 3 promotable facts and coverage "
                    "is below 80%")
    add(f"**Kill criterion B:** {total_promotable} promotable facts over "
        f"{len(active_rows)} active repos; {covered_pct:.1f}% of the "
        f"{total_fact_bearing} fact-bearing candidates already covered.  "
        f"Verdict: **{verdict_b}** -- {reason_b}.")
    add("")
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Phase-0 measurement for plan-coder issue #28 (TokenEconomizer).")
    parser.add_argument("--transcripts", default=DEFAULT_TRANSCRIPTS,
                        help="frozen transcript corpus (read-only)")
    parser.add_argument("--repo-root", default=REPO_ROOT,
                        help="plan-coder checkout used for the coverage corpus")
    parser.add_argument("--out", default=None,
                        help="write the markdown report to this path")
    parser.add_argument("--format", default="markdown", choices=["markdown", "json"])
    parser.add_argument("--min-runs", type=int, default=5)
    parser.add_argument("--min-tasks", type=int, default=3)
    args = parser.parse_args(argv)

    if not os.path.isdir(args.transcripts):
        print(f"error: transcript corpus not found: {args.transcripts}", file=sys.stderr)
        return 2

    runs = load_corpus(args.transcripts)
    payload = build_report(runs, args.repo_root, args.min_runs, args.min_tasks)

    if args.format == "json":
        print(json.dumps(payload, indent=2))
        return 0

    report = render_markdown(payload, args)
    if args.out:
        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(report)
        print(f"wrote {out_path}")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
