#!/usr/bin/env python3
"""Role-based failover + cross-agent reflection for the backlog
supervisor (MA-Mesh failover spike, approved by the operator 2026-11-07;
pattern from MA-Mesh, arXiv:2611.06389).

ledger_check.py answers "may this issue be re-briefed unchanged?"
(no). This script answers the next question — "with what tier, and
carrying what memory of the failures?" — mechanically, from the
metrics ledger:

  reflection   Every failed/escalated run produces a short structured
               reflection (failure category in MAST terms + the
               mechanical signals behind it + one imperative for the
               next attempt). Reflections attach to the next brief, so
               the substitute worker inherits what the failed role
               learned instead of starting from a clean brief.
  failover     After >= --failure-threshold graded failures on one
               issue (default 2), the next dispatch MUST change tier:
               one rung up the ladder from the highest tier tried
               (cheap -> balanced -> flagship; already-flagship means
               flagship again but the brief must be narrowed/split).
               Serial substitution, never parallel redundancy
               (ParallACT, arXiv:2611.00314: parallel agents on
               interdependent work do not reconcile by talking).
  flag         After >= --flag-after graded failures (default 3), no
               further automatic dispatch: exit 1 and the supervisor
               flags the issue for the operator in its report, per the
               monitoring contract.

A "graded failure" is a ledger row with outcome failed:* or
escalated, or a non-PR row whose recorded grade is < 50 (the
trajectory grader's review band). stopped:token-cap rows are the
burn guard's kill, not a worker failure — they earn a reflection but
do not increment the failure count (the supervisor classifies them
the same way). No majority voting over worker outputs is introduced
anywhere: voting protects against the weakest model but rarely beats
the best one, and a 5%-error aggregator erases the edge
(arXiv:2611.07140).

Thresholds and the tier ladder are policy-as-data: they are read from
the deployment config (workers.failover) when present, so the weekly
harness-tune pass can adjust the resilience policy without editing
this code or the supervisor prompt. CLI flags override the config.

Usage:
    python3 bin/failover.py <repo> <issue> [--ledger PATH] [--json]
                            [--failure-threshold N] [--flag-after N]
    --ledger defaults to the deployment config's state dir (same
    search order as agent.py). Exit 0 = dispatch (see action field),
    exit 1 = flag_for_operator, exit 2 = ledger missing / failover
    disabled in config.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import default_ledger_path, _load_deployment_config  # noqa: E402

DEFAULT_LADDER = ["cheap", "balanced", "flagship"]
REFLECTION_MAX_CHARS = 1200  # ~300 tokens per reflection record


def _parse_steps(value):
    if not isinstance(value, str) or "/" not in value:
        return None, None
    used, _, maximum = value.partition("/")
    try:
        return int(used), int(maximum)
    except ValueError:
        return None, None


def _gates(value) -> dict:
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


def is_graded_failure(row: dict) -> bool:
    outcome = str(row.get("outcome") or "")
    if outcome == "pr_opened":
        return False
    if outcome.startswith("failed") or outcome == "escalated":
        return True
    grade = row.get("grade")
    return isinstance(grade, (int, float)) and grade < 50


def reflection_for(row: dict) -> dict:
    """One mechanical reflection record for an unsuccessful run.

    Categories follow the MAST failure taxonomy names used in the
    spike brief; every claim is derived from ledger fields only."""
    outcome = str(row.get("outcome") or "")
    gates = _gates(row.get("gate_triggers"))
    used, maximum = _parse_steps(row.get("steps"))
    hit_cap = bool(used is not None and maximum and used >= maximum)
    if outcome == "stopped:token-cap":
        category, imperative = (
            "budget-exhaustion (burn guard, not a worker failure)",
            "Shrink the brief's scope or raise the tier's efficiency; "
            "do not simply re-run the same brief at the same cap.")
    elif gates.get("empty_diff") or "empty-diff" in outcome:
        category, imperative = (
            "no-output (empty diff behind a finished run)",
            "Name the first target file in the brief and require a "
            "first scoped edit by about step 15.")
    elif "bad-diff" in outcome:
        category, imperative = (
            "reasoning-action mismatch (diff did not meet the brief)",
            "Restate the acceptance criteria as checkable commands in "
            "the brief; the substitute must run them before finishing.")
    elif "test" in outcome:
        category, imperative = (
            "verification failure (tests red at collection)",
            "Require the brief's test commands in the report; a test "
            "that passed before the change and fails after is the "
            "worker's regression, per the stale-test protocol.")
    elif "worker-error" in outcome or outcome == "escalated":
        category, imperative = (
            "execution failure (worker/provider error or stuck run)",
            "Substitute tier/provider per the failover ladder; keep "
            "the brief, attach this reflection, and cap exploration.")
    elif hit_cap:
        category, imperative = (
            "step repetition / exploration without completion "
            "(hit the step cap)",
            "Narrow or split the brief; an unchanged re-brief is "
            "expected to hit the cap again.")
    else:
        category, imperative = (
            "unclassified failure",
            "Read the transcript before re-briefing; do not re-brief "
            "unchanged.")
    grade = row.get("grade")
    text = (
        f"REFLECTION {row.get('repo')}#{row.get('issue')} "
        f"run {row.get('run_id') or '?'} tier {row.get('tier') or '?'} "
        f"outcome {outcome or '?'} "
        f"grade {grade if grade is not None else 'n/a'}: {category}. "
        f"Signals: steps {row.get('steps') or '?'}, "
        f"tokens_out {row.get('tokens_out') or 0}, "
        f"gates {row.get('gate_triggers') or 'none recorded'}. "
        f"Next attempt: {imperative}")
    return {"run_id": row.get("run_id"), "outcome": outcome,
            "category": category, "text": text[:REFLECTION_MAX_CHARS]}


def decide(rows: list, repo: str, issue, failure_threshold: int = 2,
           flag_after: int = 3, ladder: list | None = None) -> dict:
    ladder = ladder or DEFAULT_LADDER
    mine = [r for r in rows
            if r.get("repo") == repo and str(r.get("issue")) == str(issue)]
    pr_opened = any(str(r.get("outcome") or "") == "pr_opened"
                    for r in mine)
    failures = [r for r in mine if is_graded_failure(r)]
    reflections = [reflection_for(r) for r in mine
                   if is_graded_failure(r)
                   or str(r.get("outcome") or "") == "stopped:token-cap"]
    tried = [r.get("tier") for r in mine if r.get("tier") in ladder]
    highest = max(tried, key=ladder.index) if tried else None
    base = {"repo": repo, "issue": issue, "attempts": len(mine),
            "graded_failures": len(failures), "pr_opened": pr_opened,
            "tiers_tried": sorted(set(tried), key=ladder.index),
            "failure_threshold": failure_threshold,
            "flag_after": flag_after, "reflections": reflections}
    if pr_opened:
        return {**base, "action": "none_pr_opened", "next_tier": None,
                "requires_narrowed_brief": False}
    if len(failures) >= flag_after:
        return {**base, "action": "flag_for_operator", "next_tier": None,
                "requires_narrowed_brief": False}
    if len(failures) >= failure_threshold:
        if highest is None or ladder.index(highest) >= len(ladder) - 1:
            next_tier = ladder[-1]
            narrowed = True
        else:
            next_tier = ladder[ladder.index(highest) + 1]
            narrowed = False
        return {**base, "action": "failover", "next_tier": next_tier,
                "requires_narrowed_brief": narrowed}
    if failures:
        return {**base, "action": "dispatch_with_reflection",
                "next_tier": highest or "balanced",
                "requires_narrowed_brief": False}
    return {**base, "action": "dispatch", "next_tier": "balanced",
            "requires_narrowed_brief": False}


def load_rows(ledger_path: str) -> list:
    rows = []
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def policy_from_config() -> dict:
    cfg = _load_deployment_config() or {}
    workers = cfg.get("workers") or {}
    return workers.get("failover") or {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo")
    ap.add_argument("issue")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--failure-threshold", type=int, default=None)
    ap.add_argument("--flag-after", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    policy = policy_from_config()
    if policy.get("enabled") is False:
        print("failover disabled in deployment config "
              "(workers.failover.enabled=false)", file=sys.stderr)
        return 2
    failure_threshold = (args.failure_threshold
                         if args.failure_threshold is not None
                         else int(policy.get("failure_threshold", 2)))
    flag_after = (args.flag_after if args.flag_after is not None
                  else int(policy.get("flag_after", 3)))
    ladder = policy.get("tier_ladder") or DEFAULT_LADDER
    path = args.ledger or default_ledger_path()
    if not path or not os.path.exists(path):
        print(f"ledger not found: {path}", file=sys.stderr)
        return 2
    result = decide(load_rows(path), args.repo, args.issue,
                    failure_threshold, flag_after, ladder)
    if args.json:
        print(json.dumps(result, indent=1))
    elif result["action"] == "flag_for_operator":
        print(f"FLAG FOR OPERATOR: {args.repo}#{args.issue} has "
              f"{result['graded_failures']} graded failure(s) — no "
              f"further automatic dispatch; flag it in the report.")
        for ref in result["reflections"]:
            print(f"  {ref['text']}")
    elif result["action"] == "failover":
        extra = (" (already at top tier — narrowed/split brief "
                 "required)" if result["requires_narrowed_brief"]
                 else "")
        print(f"FAILOVER: {args.repo}#{args.issue} — "
              f"{result['graded_failures']} graded failure(s); next "
              f"dispatch must use tier {result['next_tier']}{extra} "
              f"and carry the reflection(s) below in the brief.")
        for ref in result["reflections"]:
            print(f"  {ref['text']}")
    elif result["action"] == "dispatch_with_reflection":
        print(f"DISPATCH WITH REFLECTION: {args.repo}#{args.issue} — "
              f"attach the reflection(s) below to the brief.")
        for ref in result["reflections"]:
            print(f"  {ref['text']}")
    elif result["action"] == "none_pr_opened":
        print(f"NO ACTION: {args.repo}#{args.issue} already opened a "
              f"PR; failover does not apply.")
    else:
        print(f"DISPATCH: {args.repo}#{args.issue} — no graded "
              f"failures on record; default tier "
              f"{result['next_tier']}.")
    return 1 if result["action"] == "flag_for_operator" else 0


if __name__ == "__main__":
    raise SystemExit(main())
