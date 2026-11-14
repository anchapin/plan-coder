#!/usr/bin/env python3
"""Phase-0 measurement for plan-coder issue #22 (Headroom).

Phase 0 is measurement only.  Nothing here is wired into the live
harness: no change to ``bin/agent.py``, no config change, no prompt
change, no new dependency (no Headroom, no Context Mode).  The script
reads the frozen worker-transcript corpus and measures how much of the
worker's context is *tool output* -- the raw text returned by ``exec``,
``read_file`` and ``list_dir``.

The measurement answers one question: is there enough tool-output bloat
in the transcripts to justify a Phase 1 probe (a Headroom / Context-Mode
style tool-output compressor), or is the slice too small and the idea
should be killed?

Fields measured per run (issue #22 section 3):

  * steps                       -- number of step records in the transcript
  * final tokens_in             -- tokens_in of the last step (the final
                                   prompt the worker was billed for)
  * tokens_out                  -- sum of tokens_out over all steps
  * tool-result chars by kind   -- exec / read_file / list_dir / other,
                                   taken from the tool results paired
                                   with the tool calls of the same step
  * tool-output share           -- estimated tool-output tokens divided
                                   by the final tokens_in (the share of
                                   the final context that is tool output)
  * truncation-cap hits         -- number of ``...[truncated]`` markers
  * end-to-end tokens_in        -- sum of tokens_in over all steps (the
                                   whole prompt bill of the run)
  * ceiling savings             -- tokens_in that could be saved if the
                                   measured tool-output slice were
                                   compressed by 50% / 80%

Usage:
    python3 bin/headroom_phase0.py
    python3 bin/headroom_phase0.py --format json
    python3 bin/headroom_phase0.py --out docs/spikes/headroom-phase0.md

Options:
    --transcripts-dir DIR   Frozen transcript corpus (read-only).
    --out PATH              Write the markdown report to PATH (default: stdout).
    --format FORMAT         markdown | json (default: markdown).
    --days N                Size of the measurement window in days (default: 14).
    --as-of TS              ISO timestamp used as the end of the window
                            (default: newest transcript mtime in the corpus).
    --data-dir DIR          Where the full per-run CSV/JSON is written
                            (default: the directory of --out).
    --verification-note F   Text file embedded verbatim in the report.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TRANSCRIPTS = os.path.expanduser(
    "~/workspace/goals/token-plan-coding-manager-setup/hidden_files/transcripts")

# Repo labels seen in the corpus (same list the tokeneconomizer spike uses).
REPOS = (
    "armored-archer",
    "building-ai-pocs",
    "fluxion",
    "matchline",
    "plan-coder",
    "planar-nexus",
    "portkit-v2",
    "retrofit-copilot",
    "staging-studio",
)
REPO_ALIASES = {
    "sstudio": "staging-studio",
    "staging": "staging-studio",
    "planar": "planar-nexus",
    "pocs": "building-ai-pocs",
}

# Tool kinds measured (issue #22 section 3 names exactly these four).
EXEC_TOOLS = frozenset({"exec", "bash", "Bash", "exec_command"})
READ_TOOLS = frozenset({"read_file"})
LIST_TOOLS = frozenset({"list_dir"})
TOOL_KINDS = ("exec", "read_file", "list_dir", "other")

# The harness truncates oversized tool output with a "...[truncated]" marker.
TRUNCATION_MARKER = "[truncated]"
TRUNCATION_RE = re.compile(r"\.\.\.\[truncated\]")

# Rough English/JSON token ratio used to turn characters into tokens.
CHARS_PER_TOKEN = 4.0

# The harness caps the worker context at 200k tokens; runs whose final
# tokens_in sits at that cap may have had older tool output trimmed out
# of the final context, so their measured share is a lower bound.
CONTEXT_CAP_PROBE = 195000

# Kill / eligibility thresholds from the issue body.
KILL_MEDIAN_SHARE_PCT = 10.0
TAIL_P90_SHARE_PCT = 20.0

# Compression levels of the ceiling statement.
COMPRESSION_LEVELS = (0.5, 0.8)


# --------------------------------------------------------------------------
# transcript model
# --------------------------------------------------------------------------


@dataclass
class Step:
    """One worker step of a transcript."""

    index: int
    tokens_in: int = 0
    tokens_out: int = 0
    tool_names: list = field(default_factory=list)
    tool_result_chars: list = field(default_factory=list)
    tool_result_kinds: list = field(default_factory=list)
    kinds_typed: bool = False


@dataclass
class Run:
    """One transcript file."""

    path: str
    file: str
    mtime: datetime
    run_id: str | None = None
    repo: str | None = None
    issue: int | None = None
    flavour: str | None = None
    steps: list = field(default_factory=list)
    total_in_event: int = 0
    total_out_event: int = 0
    reported_steps: int | None = None
    ts_steps: int = 0
    typed_steps: int = 0


def _as_int(value):
    """Coerce a JSON value to int (0 when it is not a number)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return 0


def parse_run_id(run_id):
    """Split a supervisor run id into (repo, issue, flavour).

    Run ids look like ``fluxion-4744-1794427226`` (repo, issue number,
    dispatch epoch) with optional flavours (``fluxion-goldfish-...``).
    """
    if not isinstance(run_id, str) or not run_id:
        return None
    repo = None
    rest = None
    for name in sorted(REPOS, key=len, reverse=True):
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
    for name in ("goldfish", "jittest-hyp", "jittest-test", "jittest-judge"):
        if rest.startswith(name + "-"):
            flavour = name
            rest = rest[len(name) + 1:]
            break
    match = re.match(r"^(\d+)(.*)$", rest)
    if not match:
        return repo, None, flavour
    return repo, int(match.group(1)), flavour


def tool_kind(name):
    """Map a tool-call name onto one of the four measured kinds."""
    if name in EXEC_TOOLS:
        return "exec"
    if name in READ_TOOLS:
        return "read_file"
    if name in LIST_TOOLS:
        return "list_dir"
    return "other"


def load_run(path):
    """Parse one transcript jsonl into a Run (read-only)."""
    run = Run(path=path, file=os.path.basename(path),
              mtime=datetime.fromtimestamp(os.path.getmtime(path), UTC))
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
                run.total_in_event = _as_int(obj.get("total_in"))
                run.total_out_event = _as_int(obj.get("total_out"))
                run.reported_steps = _as_int(obj.get("steps")) or None
                continue
            if "event" in obj:
                # Non-step events (gate_trigger, aborted, ...) carry a
                # "step" counter but no step payload.
                continue
            if "step" not in obj:
                continue
            step = Step(index=_as_int(obj.get("step")))
            step.tokens_in = _as_int(obj.get("tokens_in"))
            step.tokens_out = _as_int(obj.get("tokens_out"))
            if obj.get("ts"):
                run.ts_steps += 1
            calls = obj.get("tool_calls") or []
            results = obj.get("tool_results") or []
            kinds = obj.get("tool_result_kinds")
            for index, call in enumerate(calls):
                if not isinstance(call, dict):
                    step.tool_names.append("")
                    step.tool_result_chars.append(0)
                    continue
                step.tool_names.append(call.get("name") or "")
                if index < len(results):
                    candidate = results[index]
                    text = candidate if isinstance(candidate, str) \
                        else json.dumps(candidate)
                else:
                    text = ""
                step.tool_result_chars.append(len(text))
            if isinstance(kinds, list) and kinds:
                step.tool_result_kinds = [str(k) for k in kinds]
                step.kinds_typed = True
                run.typed_steps += 1
            run.steps.append(step)
    if run.run_id is None:
        run.run_id = os.path.splitext(run.file)[0]
    parsed = parse_run_id(run.run_id)
    if parsed:
        run.repo, run.issue, run.flavour = parsed
    return run


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------


def measure_run(run):
    """Compute the issue #22 section 3 fields for one run."""
    chars = {kind: 0 for kind in TOOL_KINDS}
    trunc_hits = 0
    kind_calls = Counter()
    end_to_end_in = 0
    tokens_out = 0
    # Cumulative tool-output tokens injected into the context *before*
    # each step (that is what a compressor would shrink).
    cum_tool_tokens = 0.0
    savings = {level: 0.0 for level in COMPRESSION_LEVELS}
    steps_seen = 0
    for step in run.steps:
        steps_seen += 1
        end_to_end_in += step.tokens_in
        tokens_out += step.tokens_out
        for level in COMPRESSION_LEVELS:
            # A step's prompt can never shrink by more than the prompt
            # itself, so the saving of a step is capped at tokens_in.
            saving = min(level * cum_tool_tokens, float(step.tokens_in))
            savings[level] += saving
        step_chars = 0
        for name, length in zip(step.tool_names, step.tool_result_chars):
            kind = tool_kind(name)
            chars[kind] += length
            kind_calls[kind] += 1
            step_chars += length
        cum_tool_tokens += step_chars / CHARS_PER_TOKEN

    # Truncation markers live in the tool-result text, so they are counted
    # in a second pass over the raw step payloads.
    trunc_hits = count_truncation_markers(run)

    final_tokens_in = run.steps[-1].tokens_in if run.steps else 0
    tool_chars_total = sum(chars.values())
    tool_tokens = tool_chars_total / CHARS_PER_TOKEN
    if final_tokens_in > 0:
        share = 100.0 * tool_tokens / final_tokens_in
    else:
        share = None
    top_kind = None
    if kind_calls:
        top_kind = max(TOOL_KINDS, key=lambda k: (chars[k], k))
    saving_pct = {}
    for level in COMPRESSION_LEVELS:
        saving_pct[level] = (
            100.0 * savings[level] / end_to_end_in) if end_to_end_in else None
    return {
        "file": run.file,
        "run_id": run.run_id,
        "repo": run.repo or "unattributed",
        "issue": run.issue,
        "flavour": run.flavour,
        "mtime": run.mtime.isoformat(),
        "steps": steps_seen,
        "reported_steps": run.reported_steps,
        "final_tokens_in": final_tokens_in,
        "end_to_end_tokens_in": end_to_end_in,
        "tokens_out": tokens_out,
        "total_in_event": run.total_in_event,
        "total_out_event": run.total_out_event,
        "chars_exec": chars["exec"],
        "chars_read_file": chars["read_file"],
        "chars_list_dir": chars["list_dir"],
        "chars_other": chars["other"],
        "tool_chars_total": tool_chars_total,
        "tool_tokens": tool_tokens,
        "share_final_pct": share,
        "trunc_hits": trunc_hits,
        "top_tool": top_kind,
        "typed_steps": run.typed_steps,
        "ts_steps": run.ts_steps,
        "saving_50_tokens": savings[0.5],
        "saving_80_tokens": savings[0.8],
        "saving_50_pct": saving_pct[0.5],
        "saving_80_pct": saving_pct[0.8],
    }


def count_truncation_markers(run):
    """Count "...[truncated]" markers in the tool results of a run."""
    count = 0
    with open(run.path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line or TRUNCATION_MARKER not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            for result in obj.get("tool_results") or []:
                if isinstance(result, str) and TRUNCATION_MARKER in result:
                    count += len(TRUNCATION_RE.findall(result))
    return count


def percentile(values, pct):
    """Nearest-rank percentile (inclusive).  Returns None for empty input."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def median(values):
    if not values:
        return None
    return statistics.median(values)


def _fmt(value, digits=2):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def select_window(runs, days, as_of=None):
    """Return (selected, window_end, window_start).

    The window is anchored on the newest transcript of the corpus, so the
    slice is the last N days *of the corpus* and re-running the script on
    the same corpus snapshot reproduces the same numbers.  ``as_of``
    overrides the anchor (used by the tests for determinism).
    """
    newest = max((run.mtime for run in runs), default=None)
    if as_of is None:
        end = newest if newest is not None else datetime.now(UTC)
    else:
        end = as_of
    start = end - timedelta(days=days)
    selected = [run for run in runs if start <= run.mtime <= end]
    return selected, start, end


def build_report(rows, window_start, window_end, corpus_files, corpus_steps,
                 verification_note, days):
    """Aggregate the per-run rows into the report payload."""
    shares = [r["share_final_pct"] for r in rows if r["share_final_pct"] is not None]
    by_repo = defaultdict(list)
    for row in rows:
        by_repo[row["repo"]].append(row)

    repo_rows = []
    for repo in sorted(by_repo):
        repo_runs = by_repo[repo]
        repo_shares = [r["share_final_pct"] for r in repo_runs
                       if r["share_final_pct"] is not None]
        repo_rows.append({
            "repo": repo,
            "runs": len(repo_runs),
            "steps": sum(r["steps"] for r in repo_runs),
            "end_to_end_tokens_in": sum(r["end_to_end_tokens_in"] for r in repo_runs),
            "tool_tokens": sum(r["tool_tokens"] for r in repo_runs),
            "median_share_pct": median(repo_shares),
            "p90_share_pct": percentile(repo_shares, 90),
            "p99_share_pct": percentile(repo_shares, 99),
            "mean_share_pct": statistics.fmean(repo_shares) if repo_shares else None,
            "min_share_pct": min(repo_shares) if repo_shares else None,
            "max_share_pct": max(repo_shares) if repo_shares else None,
            "trunc_hits": sum(r["trunc_hits"] for r in repo_runs),
            "runs_typed_kinds": sum(1 for r in repo_runs if r["typed_steps"] > 0),
            "runs_missing_kinds": sum(1 for r in repo_runs if r["typed_steps"] == 0),
            "runs_with_ts": sum(1 for r in repo_runs if r["ts_steps"] > 0),
        })

    top_absolute = sorted(rows, key=lambda r: -r["tool_tokens"])[:10]
    top_share = sorted([r for r in rows if r["share_final_pct"] is not None],
                       key=lambda r: -r["share_final_pct"])[:10]

    ceiling = {}
    for level in COMPRESSION_LEVELS:
        tokens = [r[f"saving_{int(level * 100)}_tokens"] for r in rows]
        pcts = [r[f"saving_{int(level * 100)}_pct"] for r in rows
                if r[f"saving_{int(level * 100)}_pct"] is not None]
        ceiling[int(level * 100)] = {
            "median_tokens": median(tokens),
            "p90_tokens": percentile(tokens, 90),
            "median_pct": median(pcts),
            "p90_pct": percentile(pcts, 90),
            "total_tokens": sum(tokens),
        }

    total_e2e = sum(r["end_to_end_tokens_in"] for r in rows)
    total_tool_tokens = sum(r["tool_tokens"] for r in rows)
    for level in COMPRESSION_LEVELS:
        key = int(level * 100)
        ceiling[key]["corpus_pct"] = (
            100.0 * ceiling[key]["total_tokens"] / total_e2e) if total_e2e else None

    median_share = median(shares)
    p90_share = percentile(shares, 90)
    p99_share = percentile(shares, 99)

    # Tail population: the runs at or above the p90 share.  It is only a
    # population when the distribution has a spread (p90 above the
    # median); a flat distribution has no distinct tail.
    tail = []
    if (p90_share is not None and median_share is not None
            and p90_share > median_share):
        tail = [r for r in rows if r["share_final_pct"] is not None
                and r["share_final_pct"] >= p90_share]
    tail_repos = Counter(r["repo"] for r in tail)
    tail_tools = Counter(r["top_tool"] or "none" for r in tail)

    verdict = "KILL"
    reason = "median tool-output share below the 10% kill threshold"
    if median_share is not None and median_share >= KILL_MEDIAN_SHARE_PCT:
        verdict = "PHASE-1-ELIGIBLE"
        reason = ("median tool-output share at or above the 10% kill "
                  "threshold")
    elif p90_share is not None and p90_share >= TAIL_P90_SHARE_PCT:
        verdict = "PHASE-1-ELIGIBLE"
        reason = ("median share below 10% but the p90 tail is at or above "
                  "20%, so a narrow tail population still justifies a probe")

    data_quality = {
        "runs": len(rows),
        "runs_without_typed_kinds": sum(1 for r in rows if r["typed_steps"] == 0),
        "runs_with_typed_kinds": sum(1 for r in rows if r["typed_steps"] > 0),
        "runs_without_any_ts": sum(1 for r in rows if r["ts_steps"] == 0),
        "runs_with_any_ts": sum(1 for r in rows if r["ts_steps"] > 0),
        "runs_with_zero_tool_output": sum(1 for r in rows if r["tool_chars_total"] == 0),
        "runs_without_steps": sum(1 for r in rows if r["steps"] == 0),
        "runs_without_final_tokens_in": sum(
            1 for r in rows if r["final_tokens_in"] == 0),
        "steps_typed_kinds": sum(r["typed_steps"] for r in rows),
        "steps_total": sum(r["steps"] for r in rows),
        "trunc_hits_total": sum(r["trunc_hits"] for r in rows),
        "runs_with_trunc_hits": sum(1 for r in rows if r["trunc_hits"] > 0),
        "runs_at_context_cap": sum(
            1 for r in rows if r["final_tokens_in"] >= CONTEXT_CAP_PROBE),
        "runs_with_share": sum(1 for r in rows if r["share_final_pct"] is not None),
    }
    incomplete = [r for r in rows
                  if r["steps"] == 0 or r["typed_steps"] == 0
                  or r["final_tokens_in"] == 0 or r["ts_steps"] == 0]

    return {
        "parameters": {
            "corpus_files": corpus_files,
            "corpus_steps": corpus_steps,
            "window_days": days,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "chars_per_token": CHARS_PER_TOKEN,
            "kill_median_share_pct": KILL_MEDIAN_SHARE_PCT,
            "tail_p90_share_pct": TAIL_P90_SHARE_PCT,
        },
        "overall": {
            "runs": len(rows),
            "steps": sum(r["steps"] for r in rows),
            "end_to_end_tokens_in": total_e2e,
            "tool_tokens": total_tool_tokens,
            "median_share_pct": median_share,
            "p90_share_pct": p90_share,
            "p99_share_pct": p99_share,
            "mean_share_pct": statistics.fmean(shares) if shares else None,
            "min_share_pct": min(shares) if shares else None,
            "max_share_pct": max(shares) if shares else None,
        },
        "repo_rows": repo_rows,
        "top_absolute": top_absolute,
        "top_share": top_share,
        "ceiling": ceiling,
        "verdict": {
            "verdict": verdict,
            "reason": reason,
            "median_share_pct": median_share,
            "p90_share_pct": p90_share,
            "p99_share_pct": p99_share,
            "tail_runs": len(tail),
            "tail_repos": tail_repos.most_common(),
            "tail_tools": tail_tools.most_common(),
        },
        "data_quality": data_quality,
        "incomplete_rows": incomplete,
        "rows": rows,
        "verification_note": verification_note,
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def render_markdown(payload):
    """Render the raw tables, the ceiling statement and the gate verdict."""
    out = []
    add = out.append
    params = payload["parameters"]
    overall = payload["overall"]
    verdict = payload["verdict"]
    quality = payload["data_quality"]

    add("# Phase 0 Headroom measurement (issue #22)")
    add("")
    add("Measurement only: no harness change, no config change, no prompt")
    add("change, no dependency installed (no Headroom, no Context Mode), no")
    add("Phase 1 compatibility probe.  This report decides **Kill vs")
    add("Phase 1** for the tool-output-compression idea.")
    add("")
    add("## Parameters")
    add("")
    add("| parameter | value |")
    add("|---|---|")
    add(f"| transcripts dir | `{params['corpus_dir']}` |")
    add(f"| corpus files (all time) | {params['corpus_files']} |")
    add(f"| corpus steps (all time) | {params['corpus_steps']} |")
    add(f"| window | last {params['window_days']} days |")
    add(f"| window start (UTC) | {params['window_start']} |")
    add(f"| window end (UTC) | {params['window_end']} |")
    add(f"| runs in window | {overall['runs']} |")
    add(f"| chars per token (estimate) | {params['chars_per_token']:.1f} |")
    add(f"| kill threshold (median share) | < {params['kill_median_share_pct']:.1f}% |")
    add(f"| tail threshold (p90 share) | >= {params['tail_p90_share_pct']:.1f}% |")
    add("")
    add("**Timestamp source: file mtime.**  A per-step `ts` field exists in")
    add("only part of the corpus (it is absent from every step of the older")
    add("transcripts), and the epoch suffix of a run id is a dispatch stamp,")
    add("not a wall clock, so neither can date a run on its own.  File mtime")
    add("is present for every transcript, so the window is applied on mtime")
    add("for all runs.  The window end is the newest transcript mtime in")
    add("the corpus, so the slice is the last days *of the corpus* and a")
    add(f"frozen corpus still yields a full {params['window_days']}-day slice,")
    add("reproducibly.")
    add("")
    add("**Definitions.**")
    add("")
    add("* *final tokens_in* -- `tokens_in` of the last step of the run: the")
    add("  final prompt the worker was billed for.")
    add("* *end-to-end tokens_in* -- sum of `tokens_in` over all steps of the")
    add("  run: the whole prompt bill of the run.")
    add("* *tool-result chars* -- characters of the `tool_results` text,")
    add("  paired by index with the `tool_calls` of the same step and")
    add("  classified as exec / read_file / list_dir / other.")
    add("* *tool-output share* -- (tool-result chars / 4) / final tokens_in,")
    add("  i.e. the estimated share of the final context that is tool output.")
    add("* *truncation-cap hits* -- number of `...[truncated]` markers in")
    add("  tool results (the harness output cap already fired).")
    add("* *ceiling saving* -- for every step, the tool-output tokens")
    add("  injected into the context before that step, times the compression")
    add("  level, capped at the step's own `tokens_in`; summed over the run.")
    add("  This is the maximum plausible end-to-end `tokens_in` saving of")
    add("  compressing the measured slice; it assumes no other growth.")
    add("")

    add("## Table 1 -- per-run raw table (all runs in window)")
    add("")
    add("Every run in the window, one row each.  The same rows are written")
    add("as machine-readable data next to this report")
    add("(`headroom-phase0-runs.csv`, `headroom-phase0-runs.json`).")
    add("")
    add("| run_id | repo | issue | steps | final tokens_in | tokens_out | "
        "chars exec | chars read_file | chars list_dir | chars other | "
        "tool tokens | tool share of final ctx | trunc hits | "
        "save50 tokens | save80 tokens |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for row in payload["rows"]:
        add("| {run_id} | {repo} | {issue} | {steps} | {final_tokens_in} | "
            "{tokens_out} | {chars_exec} | {chars_read_file} | {chars_list_dir} | "
            "{chars_other} | {tool_tokens} | {share} | {trunc_hits} | "
            "{saving_50_tokens} | {saving_80_tokens} |".format(
                run_id=row["run_id"],
                repo=row["repo"],
                issue=row["issue"] if row["issue"] is not None else "n/a",
                steps=row["steps"],
                final_tokens_in=row["final_tokens_in"],
                tokens_out=row["tokens_out"],
                chars_exec=row["chars_exec"],
                chars_read_file=row["chars_read_file"],
                chars_list_dir=row["chars_list_dir"],
                chars_other=row["chars_other"],
                tool_tokens=f"{row['tool_tokens']:.0f}",
                share=_fmt(row["share_final_pct"]) + "%",
                trunc_hits=row["trunc_hits"],
                saving_50_tokens=f"{row['saving_50_tokens']:.0f}",
                saving_80_tokens=f"{row['saving_80_tokens']:.0f}"))
    add("")

    add("## Table 2 -- tool-output share distribution")
    add("")
    add("| scope | runs | median share | p90 share | p99 share | mean share | "
        "min share | max share |")
    add("|---|---|---|---|---|---|---|---|")
    add("| **overall** | {runs} | {median} | {p90} | {p99} | {mean} | {min} | "
        "{max} |".format(
            runs=overall["runs"],
            median=_fmt(overall["median_share_pct"]) + "%",
            p90=_fmt(overall["p90_share_pct"]) + "%",
            p99=_fmt(overall["p99_share_pct"]) + "%",
            mean=_fmt(overall["mean_share_pct"]) + "%",
            min=_fmt(overall["min_share_pct"]) + "%",
            max=_fmt(overall["max_share_pct"]) + "%"))
    for row in payload["repo_rows"]:
        add("| {repo} | {runs} | {median} | {p90} | {p99} | {mean} | {min} | "
            "{max} |".format(
                repo=row["repo"],
                runs=row["runs"],
                median=_fmt(row["median_share_pct"]) + "%",
                p90=_fmt(row["p90_share_pct"]) + "%",
                p99=_fmt(row["p99_share_pct"]) + "%",
                mean=_fmt(row["mean_share_pct"]) + "%",
                min=_fmt(row["min_share_pct"]) + "%",
                max=_fmt(row["max_share_pct"]) + "%"))
    add("")
    add("Per-repo min / max are the extremes of the per-run table above; the")
    add("full per-run data is in the CSV / JSON next to this report.")
    add("")

    add("## Table 3 -- top 10 runs by absolute tool-output tokens")
    add("")
    add("| # | run_id | repo | steps | tool tokens | tool share of final ctx | "
        "producing tool | end-to-end tokens_in | trunc hits |")
    add("|---|---|---|---|---|---|---|---|---|")
    for index, row in enumerate(payload["top_absolute"], start=1):
        add("| {i} | {run_id} | {repo} | {steps} | {tool_tokens} | {share} | "
            "{tool} | {e2e} | {trunc} |".format(
                i=index,
                run_id=row["run_id"],
                repo=row["repo"],
                steps=row["steps"],
                tool_tokens=f"{row['tool_tokens']:.0f}",
                share=_fmt(row["share_final_pct"]) + "%",
                tool=row["top_tool"] or "none",
                e2e=row["end_to_end_tokens_in"],
                trunc=row["trunc_hits"]))
    add("")
    add("### Top 10 runs by tool-output share of the final context")
    add("")
    add("| # | run_id | repo | steps | tool tokens | tool share of final ctx | "
        "producing tool | end-to-end tokens_in |")
    add("|---|---|---|---|---|---|---|---|")
    for index, row in enumerate(payload["top_share"], start=1):
        add("| {i} | {run_id} | {repo} | {steps} | {tool_tokens} | {share} | "
            "{tool} | {e2e} |".format(
                i=index,
                run_id=row["run_id"],
                repo=row["repo"],
                steps=row["steps"],
                tool_tokens=f"{row['tool_tokens']:.0f}",
                share=_fmt(row["share_final_pct"]) + "%",
                tool=row["top_tool"] or "none",
                e2e=row["end_to_end_tokens_in"]))
    add("")

    add("## Table 4 -- ceiling statement")
    add("")
    add("Maximum plausible end-to-end `tokens_in` saving if the measured")
    add("tool-output slice were compressed.  Absolute numbers are tokens per")
    add("run; percentages are of that run's end-to-end `tokens_in`.")
    add("")
    add("| compression | median saving (tokens/run) | p90 saving "
        "(tokens/run) | median saving (% of run tokens_in) | p90 saving "
        "(% of run tokens_in) | corpus-wide saving (% of all tokens_in) |")
    add("|---|---|---|---|---|---|")
    for level in sorted(payload["ceiling"]):
        entry = payload["ceiling"][level]
        add("| {level}% | {median_tok} | {p90_tok} | {median_pct} | "
            "{p90_pct} | {corpus_pct} |".format(
                level=level,
                median_tok=f"{entry['median_tokens']:.0f}",
                p90_tok=f"{entry['p90_tokens']:.0f}",
                median_pct=_fmt(entry["median_pct"]) + "%",
                p90_pct=_fmt(entry["p90_pct"]) + "%",
                corpus_pct=_fmt(entry["corpus_pct"]) + "%"))
    add("")
    add("Corpus totals for the window:")
    add("")
    add("| corpus total (window) | value |")
    add("|---|---|")
    add(f"| end-to-end tokens_in | {overall['end_to_end_tokens_in']} |")
    add(f"| tool-output tokens (chars / 4) | {overall['tool_tokens']:.0f} |")
    add(f"| tool-output share of the end-to-end bill | "
        f"{_fmt(100.0 * overall['tool_tokens'] / overall['end_to_end_tokens_in'])}% |")
    add(f"| steps | {overall['steps']} |")
    add("")

    add("## Table 5 -- truncation-cap hits and data quality")
    add("")
    add("| measure | value |")
    add("|---|---|")
    add(f"| truncation markers in tool results (window) | {quality['trunc_hits_total']} |")
    add(f"| runs with at least one truncation marker | {quality['runs_with_trunc_hits']} |")
    add(f"| runs with typed `tool_result_kinds` | {quality['runs_with_typed_kinds']} |")
    add(f"| runs WITHOUT typed `tool_result_kinds` | {quality['runs_without_typed_kinds']} |")
    add(f"| steps with typed `tool_result_kinds` | {quality['steps_typed_kinds']} / {quality['steps_total']} |")
    add(f"| runs with a per-step `ts` | {quality['runs_with_any_ts']} |")
    add(f"| runs without any per-step `ts` | {quality['runs_without_any_ts']} |")
    add(f"| runs with zero tool output | {quality['runs_with_zero_tool_output']} |")
    add(f"| runs with zero steps | {quality['runs_without_steps']} |")
    add(f"| runs with zero final tokens_in | {quality['runs_without_final_tokens_in']} |")
    add(f"| runs whose share is measurable | {quality['runs_with_share']} |")
    add(f"| runs at the context cap (final tokens_in >= {CONTEXT_CAP_PROBE}) "
        f"| {quality['runs_at_context_cap']} |")
    add("")
    add("`tool_result_kinds` is the typed result-status field of a step")
    add("(values such as `ok`, `exec_nonzero`, `exec_timeout`).  It is not")
    add("present on every transcript: the runs listed as *WITHOUT* above")
    add("carry no typed field at all.  Their tool-result text is measured")
    add("exactly like the typed runs -- the field is only used to type the")
    add("result status, so its absence changes no length measurement -- but")
    add("they are counted here so no run is silently dropped or inferred.")
    add("")
    add("### Table 6 -- runs with a missing or empty field")
    add("")
    if payload["incomplete_rows"]:
        add("| run_id | repo | steps | typed kinds steps | ts steps | "
            "final tokens_in | tool chars |")
        add("|---|---|---|---|---|---|---|")
        for row in payload["incomplete_rows"]:
            add("| {run_id} | {repo} | {steps} | {typed} | {ts} | {final} | "
                "{chars} |".format(
                    run_id=row["run_id"],
                    repo=row["repo"],
                    steps=row["steps"],
                    typed=row["typed_steps"],
                    ts=row["ts_steps"],
                    final=row["final_tokens_in"],
                    chars=row["tool_chars_total"]))
    else:
        add("Every run in the window carries steps, typed `tool_result_kinds`,")
        add("a per-step `ts` and a non-zero `tokens_in`.")
    add("")
    add("**Caveats on the measured slice.**")
    add("")
    add("* The tool-result text measured here is what the transcript stores,")
    add("  i.e. *after* the harness output cap already truncated it")
    add(f"  ({quality['trunc_hits_total']} `...[truncated]` markers across "
        f"{quality['runs_with_trunc_hits']} runs).")
    add(f"* {quality['runs_at_context_cap']} runs sit at the context cap, so")
    add("  tool output older than the cap window is not part of their final")
    add("  context; their share is a lower bound.")
    add("* The tool-output share is measured against the *final* context")
    add("  (last step `tokens_in`); the ceiling statement is measured")
    add("  against the *end-to-end* bill (sum of `tokens_in` over all")
    add("  steps).  The two denominators differ on purpose: the share")
    add("  answers \"how full is the context\", the ceiling answers \"how")
    add("  many tokens does the run cost\".")
    add("")
    add("Per-repo truncation totals:")
    add("")
    add("| repo | runs | truncation markers |")
    add("|---|---|---|")
    for row in payload["repo_rows"]:
        add(f"| {row['repo']} | {row['runs']} | {row['trunc_hits']} |")
    add("")

    add("## Gate verdict")
    add("")
    add("| gate input | value |")
    add("|---|---|")
    add(f"| median tool-output share (overall) | {_fmt(verdict['median_share_pct'])}% |")
    add(f"| p90 tool-output share (overall) | {_fmt(verdict['p90_share_pct'])}% |")
    add(f"| p99 tool-output share (overall) | {_fmt(verdict['p99_share_pct'])}% |")
    add(f"| kill threshold (median) | < {params['kill_median_share_pct']:.1f}% |")
    add(f"| tail threshold (p90) | >= {params['tail_p90_share_pct']:.1f}% |")
    add(f"| runs in the tail (share >= p90) | {verdict['tail_runs']} |")
    add("")
    add("The tail population is the set of runs at or above the p90 share;")
    add("it is only defined when the p90 share is above the median share (a")
    add("flat distribution has no distinct tail).")
    add(f"**Verdict: {verdict['verdict']}** -- {verdict['reason']}.")
    add("")
    if verdict["tail_runs"]:
        add("Tail population (runs at or above the p90 share):")
        add("")
        add("| repo | tail runs |")
        add("|---|---|")
        for repo, count in verdict["tail_repos"]:
            add(f"| {repo} | {count} |")
        add("")
        add("| producing tool | tail runs |")
        add("|---|---|")
        for tool, count in verdict["tail_tools"]:
            add(f"| {tool} | {count} |")
        add("")
    add("The numbers above are the raw measurement; they are not softened.")
    add("")

    add("## Reproduction")
    add("")
    add("```")
    add("python3 bin/headroom_phase0.py \\")
    add("    --transcripts-dir ~/workspace/goals/token-plan-coding-manager-setup/"
        "hidden_files/transcripts \\")
    add("    --out docs/spikes/headroom-phase0.md")
    add("```")
    add("")
    if payload.get("verification_note"):
        add("### Verification output")
        add("")
        add("```")
        add(payload["verification_note"].rstrip())
        add("```")
        add("")
    return "\n".join(out) + "\n"


def write_csv(path, rows):
    """Write the full per-run table as CSV."""
    fields = [
        "file", "run_id", "repo", "issue", "flavour", "mtime", "steps",
        "final_tokens_in", "end_to_end_tokens_in", "tokens_out", "chars_exec",
        "chars_read_file", "chars_list_dir", "chars_other", "tool_chars_total",
        "tool_tokens", "share_final_pct", "trunc_hits", "top_tool",
        "typed_steps", "ts_steps", "saving_50_tokens", "saving_80_tokens",
        "saving_50_pct", "saving_80_pct",
    ]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Phase-0 measurement for plan-coder issue #22 (Headroom).")
    parser.add_argument("--transcripts-dir", "--transcripts", dest="transcripts_dir",
                        default=DEFAULT_TRANSCRIPTS,
                        help="frozen transcript corpus (read-only)")
    parser.add_argument("--out", default=None,
                        help="write the markdown report to this path")
    parser.add_argument("--format", default="markdown", choices=["markdown", "json"])
    parser.add_argument("--days", type=int, default=14,
                        help="size of the measurement window in days")
    parser.add_argument("--as-of", default=None,
                        help="ISO timestamp used as the end of the window")
    parser.add_argument("--data-dir", default=None,
                        help="directory for the per-run CSV/JSON "
                             "(default: directory of --out)")
    parser.add_argument("--verification-note", default=os.path.join(
        REPO_ROOT, "docs", "spikes", "headroom-phase0-verification.txt"),
        help="text file embedded verbatim in the report")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.transcripts_dir):
        print(f"error: transcript corpus not found: {args.transcripts_dir}",
              file=sys.stderr)
        return 2

    corpus = []
    for name in sorted(os.listdir(args.transcripts_dir)):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(args.transcripts_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            corpus.append(load_run(path))
        except OSError:
            continue

    as_of = None
    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)

    selected, window_start, window_end = select_window(corpus, args.days, as_of)
    rows = [measure_run(run) for run in selected]
    rows.sort(key=lambda r: (r["repo"], r["file"]))

    verification_note = ""
    note_path = args.verification_note
    if note_path and os.path.isfile(note_path):
        with open(note_path, encoding="utf-8", errors="replace") as handle:
            verification_note = handle.read()

    payload = build_report(
        rows=rows,
        window_start=window_start,
        window_end=window_end,
        corpus_files=len(corpus),
        corpus_steps=sum(len(run.steps) for run in corpus),
        verification_note=verification_note,
        days=args.days,
    )
    payload["parameters"]["corpus_dir"] = os.path.abspath(args.transcripts_dir)

    if args.format == "json":
        print(json.dumps(payload, indent=2))
        return 0

    report = render_markdown(payload)
    if args.out:
        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(report)
        data_dir = args.data_dir or os.path.dirname(out_path)
        os.makedirs(data_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(out_path))[0]
        csv_path = os.path.join(data_dir, stem + "-runs.csv")
        json_path = os.path.join(data_dir, stem + "-runs.json")
        write_csv(csv_path, payload["rows"])
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"wrote {out_path}")
        print(f"wrote {csv_path}")
        print(f"wrote {json_path}")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
