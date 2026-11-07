#!/usr/bin/env python3
"""Mechanical trajectory grader for one plan-coder worker run (trace-grader
spike, 2026-11-07).

Scores a run 0-100 from its transcript JSONL (+ the ledger row for
context, + WORKER_REPORT.md / the worktree for the claim-vs-diff check
when run live at collection time). Mechanical signals only — no
LLM-as-judge, no model prose in the score:

  first_edit      30  first write_file/edit_file step (PR-opened runs
                      first edit at median step 14 in the last 50 runs;
                      failed runs at median 32)
  exploration     10  (first_edit - 1) / steps_used
  test_evidence   10  test command run and exit=0; run-but-failing scores
                      low, no test command scores neutral-low. Measured
                      2026-11-07: this signal does NOT separate outcomes
                      (failed runs run tests too) — weight stays small.
  empty_diff_gate 15  empty_diff completion gate fired (33/65 failed
                      rows vs 22/234 PR-opened rows in the ledger)
  completion      15  finished before the step cap vs hit cap vs aborted
                      (61% of PR-opened runs hit the cap, so hitting it
                      costs little)
  midrun_gate      5  mid-run no-edit gate fired (weak separator)
  provider         5  single provider, no abort (usually neutral)
  claim_vs_diff   10  WORKER_REPORT "Files changed" vs the git diff
                      (live only — worktrees are removed after
                      collection, so historical runs score this as
                      unknown / neutral 5)

Verdicts: pass >= 70, flag 50-69, review < 50. Thresholds calibrated
2026-11-07 on the last 50 ledger runs; see the spike report for the
separation numbers. The grade never uses the run's outcome — it is
meant to be compared against the outcome, not derived from it.

Usage:
    python3 bin/grade_run.py --run-id <id> [--transcripts-dir DIR]
                             [--ledger PATH] [--report WORKER_REPORT.md]
                             [--workdir DIR] [--json]
    python3 bin/grade_run.py --transcript <run_id.jsonl> [...]
Exit 0 with a grade on stdout; exit 2 when the transcript cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

EDIT_TOOLS = ("write_file", "edit_file")
TEST_MARKERS = (
    "pytest", "cargo test", "npm test", "go test", "ctest", "make test",
    "vitest", "jest", "python -m unittest", "python3 -m unittest",
    "scripts/check_", "test_inventory", "bun test",
)
VERDICT_PASS = 70
VERDICT_FLAG = 50


def _parse_steps(value):
    if not isinstance(value, str) or "/" not in value:
        return None, None
    used, _, maximum = value.partition("/")
    try:
        return int(used), int(maximum)
    except ValueError:
        return None, None


def _parse_gates(value) -> dict:
    out = {}
    if not isinstance(value, str):
        return out
    for part in value.split(","):
        if "=" not in part:
            continue
        key, _, val = part.strip().partition("=")
        try:
            out[key.strip()] = int(val.strip())
        except ValueError:
            out[key.strip()] = val.strip()
    return out


def load_transcript(path: str) -> list:
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def find_ledger_row(ledger_path: str | None, run_id: str) -> dict | None:
    if not ledger_path or not os.path.exists(ledger_path):
        return None
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("run_id") == run_id:
                return row
    return None


def _report_files_changed(report_path: str) -> set | None:
    """The worker's own 'Files changed' line from WORKER_REPORT.md."""
    try:
        with open(report_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.search(r"^- Files changed: (.+)$", text, re.MULTILINE)
    if not m:
        return None
    value = m.group(1).strip()
    if value == "(none)":
        return set()
    return {p.strip() for p in value.split(",") if p.strip()}


def _manifest_files_changed(report_path: str) -> set | None:
    """The harness-generated files_changed_git line (git truth)."""
    try:
        with open(report_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.search(r"^- files_changed_git: (.+)$", text, re.MULTILINE)
    if not m:
        return None
    value = m.group(1).strip()
    if value == "(none)":
        return set()
    return {p.strip() for p in value.split(",") if p.strip()}


def _git_changed(workdir: str, base: str | None) -> set | None:
    """Tracked + untracked names vs base, mirroring agent._worktree_changed."""
    if base is None:
        return None
    try:
        diff = subprocess.run(
            ["git", "-C", workdir, "diff", "--name-only", base],
            capture_output=True, text=True, timeout=30)
        untracked = subprocess.run(
            ["git", "-C", workdir, "ls-files", "--others",
             "--exclude-standard"],
            capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001 - git unavailable: unknown, not a fail
        return None
    if diff.returncode != 0 or untracked.returncode != 0:
        return None
    names = [n for n in diff.stdout.split() + untracked.stdout.split()
             if n != "WORKER_REPORT.md"]
    return set(names)


def claim_vs_diff(report_path: str | None, workdir: str | None) -> dict:
    """Compare the worker's claimed files against git truth.

    Returns {"status": "match"|"mismatch"|"unknown", "points": int,
    "detail": str}. Unknown (no report/worktree — the normal case for
    historical runs, whose worktrees were removed at collection) scores
    neutral 5/10 and is flagged, never silently treated as a match."""
    if not report_path:
        return {"status": "unknown", "points": 5,
                "detail": "no WORKER_REPORT.md supplied (historical run)"}
    claimed = _report_files_changed(report_path)
    manifest = _manifest_files_changed(report_path)
    if claimed is None or manifest is None:
        return {"status": "unknown", "points": 5,
                "detail": "report has no files-changed / manifest lines"}
    if claimed != manifest:
        return {"status": "mismatch", "points": 0,
                "detail": f"report claims {sorted(claimed)}; "
                          f"manifest shows {sorted(manifest)}"}
    if workdir:
        base = None
        try:
            with open(report_path, encoding="utf-8") as fh:
                m = re.search(r"^- base_commit: (\S+)$", fh.read(),
                              re.MULTILINE)
            base = m.group(1) if m else None
        except OSError:
            base = None
        git_names = _git_changed(workdir, base)
        if git_names is not None and git_names != manifest:
            return {"status": "mismatch", "points": 0,
                    "detail": f"manifest shows {sorted(manifest)}; "
                              f"live git diff shows {sorted(git_names)}"}
    return {"status": "match", "points": 10,
            "detail": f"report, manifest and git agree on "
                      f"{len(claimed)} file(s)"}


def grade(events: list, ledger_row: dict | None = None,
          report_path: str | None = None,
          workdir: str | None = None) -> dict:
    step_events = [e for e in events
                   if isinstance(e.get("step"), int)]
    step_events.sort(key=lambda e: e["step"])
    finished = next((e for e in events if e.get("event") == "finished"),
                    None)
    steps_used = None
    steps_max = None
    if finished is not None:
        steps_used = finished.get("steps")
    if not steps_used and step_events:
        steps_used = max(e["step"] for e in step_events)
    if ledger_row:
        _, steps_max = _parse_steps(ledger_row.get("steps"))
    if steps_max is None:
        steps_max = 60  # harness default; only used for the hit-cap check

    # --- first edit ------------------------------------------------------
    first_edit = None
    for e in step_events:
        for tc in e.get("tool_calls") or []:
            if tc.get("name") in EDIT_TOOLS:
                first_edit = e["step"]
                break
        if first_edit is not None:
            break

    # --- test evidence ---------------------------------------------------
    test_runs = 0
    test_passed = 0
    for e in step_events:
        for tc, res in zip(e.get("tool_calls") or [],
                           e.get("tool_results") or []):
            if tc.get("name") != "exec":
                continue
            args = tc.get("args") or ""
            if any(marker in args for marker in TEST_MARKERS):
                test_runs += 1
                if str(res).startswith("exit=0"):
                    test_passed += 1

    # --- gates / completion / provider -----------------------------------
    gate_events = [e for e in events if e.get("event") == "gate_trigger"]
    empty_diff_fired = any(e.get("gate") == "empty_diff"
                           for e in gate_events)
    midrun_fired = any(e.get("gate") == "midrun_no_edit"
                       for e in gate_events)
    if not gate_events and ledger_row:
        gates = _parse_gates(ledger_row.get("gate_triggers"))
        empty_diff_fired = bool(gates.get("empty_diff"))
        midrun_fired = bool(gates.get("midrun_no_edit"))
    aborted = any(e.get("event") == "aborted" for e in events)
    token_cap = any(e.get("event") == "token_cap" for e in events)
    providers = {e.get("provider") for e in step_events if e.get("provider")}
    hit_cap = bool(steps_used is not None and steps_max
                   and steps_used >= steps_max)

    signals = {}
    if first_edit is None:
        signals["first_edit"] = {"value": None, "points": 0, "max": 30,
                                 "note": "no write/edit tool call in transcript"}
    elif first_edit <= 15:
        signals["first_edit"] = {"value": first_edit, "points": 30, "max": 30,
                                 "note": "first edit by step 15"}
    elif first_edit <= 25:
        signals["first_edit"] = {"value": first_edit, "points": 20, "max": 30,
                                 "note": "first edit steps 16-25"}
    elif first_edit <= 40:
        signals["first_edit"] = {"value": first_edit, "points": 8, "max": 30,
                                 "note": "first edit steps 26-40"}
    else:
        signals["first_edit"] = {"value": first_edit, "points": 2, "max": 30,
                                 "note": "first edit after step 40"}

    if first_edit is None or not steps_used:
        signals["exploration_ratio"] = {"value": None, "points": 0,
                                        "max": 10,
                                        "note": "no edit to measure against"}
    else:
        ratio = (first_edit - 1) / steps_used
        if ratio <= 0.30:
            pts, note = 10, "exploration <= 30% of steps"
        elif ratio <= 0.50:
            pts, note = 7, "exploration 30-50% of steps"
        elif ratio <= 0.75:
            pts, note = 3, "exploration 50-75% of steps"
        else:
            pts, note = 0, "exploration > 75% of steps"
        signals["exploration_ratio"] = {"value": round(ratio, 2),
                                        "points": pts, "max": 10, "note": note}

    if test_runs and test_passed:
        signals["test_evidence"] = {"value": f"{test_passed}/{test_runs} passed",
                                    "points": 10, "max": 10,
                                    "note": "test command ran with exit=0"}
    elif test_runs:
        signals["test_evidence"] = {"value": f"0/{test_runs} passed",
                                    "points": 3, "max": 10,
                                    "note": "test commands ran, none exit=0"}
    else:
        signals["test_evidence"] = {"value": "no test command",
                                    "points": 4, "max": 10,
                                    "note": "no test command in transcript"}

    signals["empty_diff_gate"] = {
        "value": empty_diff_fired, "points": 0 if empty_diff_fired else 15,
        "max": 15,
        "note": "empty-diff gate fired" if empty_diff_fired
                else "empty-diff gate never fired"}

    if aborted:
        signals["completion"] = {"value": "aborted", "points": 0, "max": 15,
                                 "note": "run aborted (providers exhausted)"}
    elif token_cap:
        signals["completion"] = {"value": "token_cap", "points": 0,
                                 "max": 15,
                                 "note": "stopped by the token-out guard"}
    elif hit_cap:
        signals["completion"] = {"value": "hit_cap", "points": 8, "max": 15,
                                 "note": "reached the step cap"}
    else:
        signals["completion"] = {"value": "finished_early", "points": 15,
                                 "max": 15,
                                 "note": "finished before the step cap"}

    signals["midrun_gate"] = {
        "value": midrun_fired, "points": 2 if midrun_fired else 5, "max": 5,
        "note": "mid-run no-edit gate fired" if midrun_fired
                else "mid-run no-edit gate never fired"}

    if aborted or len(providers) > 1:
        signals["provider_stability"] = {
            "value": sorted(providers), "points": 0, "max": 5,
            "note": "aborted or provider switch during run"}
    else:
        signals["provider_stability"] = {
            "value": sorted(providers), "points": 5, "max": 5,
            "note": "single provider, no abort"}

    cvd = claim_vs_diff(report_path, workdir)
    signals["claim_vs_diff"] = {"value": cvd["status"],
                                "points": cvd["points"], "max": 10,
                                "note": cvd["detail"]}

    score = sum(s["points"] for s in signals.values())
    if score >= VERDICT_PASS:
        verdict = "pass"
    elif score >= VERDICT_FLAG:
        verdict = "flag"
    else:
        verdict = "review"
    return {
        "score": score,
        "verdict": verdict,
        "signals": signals,
        "steps_used": steps_used,
        "first_edit_step": first_edit,
        "test_runs": test_runs,
        "test_passed": test_passed,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--transcript", default=None)
    ap.add_argument("--transcripts-dir", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--report", default=None,
                    help="WORKER_REPORT.md path (live runs only)")
    ap.add_argument("--workdir", default=None,
                    help="worker worktree (live runs only, for git check)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    path = args.transcript
    if not path and args.run_id:
        tdir = args.transcripts_dir or os.path.expanduser(
            "~/.plan-coder/transcripts")
        path = os.path.join(tdir, f"{args.run_id}.jsonl")
    if not path or not os.path.exists(path):
        print(f"transcript not found: {path}", file=sys.stderr)
        return 2
    try:
        events = load_transcript(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read transcript {path}: {exc}", file=sys.stderr)
        return 2
    row = None
    if args.run_id and args.ledger:
        row = find_ledger_row(args.ledger, args.run_id)
    result = grade(events, ledger_row=row, report_path=args.report,
                   workdir=args.workdir)
    result["run_id"] = args.run_id
    result["transcript"] = path
    if row:
        result["ledger_outcome"] = row.get("outcome")
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        print(f"grade {result['score']}/100 ({result['verdict']}) "
              f"for {args.run_id or path}")
        for name, sig in result["signals"].items():
            print(f"  {name}: {sig['points']}/{sig['max']} "
                  f"({sig['value']}) — {sig['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
