#!/usr/bin/env python3
"""Append-only action ledger for the PR shepherd (loop audit, 2026-11-08).

The supervisor's runs are graded and ledgered (worker_ledger.jsonl),
but the shepherd's actions — CI reruns, mechanical fix attempts,
merges (including UNSTABLE merges on pre-existing failures), and
operator flags — used to live only as counters inside shepherd.json
(ci_fix_attempts, rerun_count), which the weekly harness-tune pass
never reads. Tuning therefore could not learn which fix-attempt
classes waste cycles or how shepherd actions turn out.

This script gives the shepherd the same treatment as the supervisor:
one JSON object per action, appended (never rewritten), fsync'd, in
shepherd_ledger.jsonl beside the worker ledger. `append` is called by
the shepherd prompt per action; `summary` is called by harness-tune
step 1 alongside trace_summary.py. A failed append must never block a
merge — the shepherd note says so, and append stays a one-line,
no-dependency operation.

Usage:
    python3 bin/shepherd_ledger.py append --repo <slug> --pr <n>
        --action rerun|fix_attempt|merged|merged_by_shepherd|needs_operator|
                 closed_unmerged|merge_conflicts|trigger_requested|
                 checked|snapshot|other
        [--url <pr url>] [--outcome <text>] [--ci <summary>]
        [--merge-state <status>] [--detail <short text>]
        [--head-sha <sha>] [--ci-checks <compact rollup>]
        [--files <comma-separated paths>] [--merged-by <login>]
        [--ledger PATH]
    python3 bin/shepherd_ledger.py summary [--since YYYY-MM-DD]
        [--ledger PATH] [--json]

--ledger defaults to shepherd_ledger.jsonl in the deployment config's
state dir (same search order as trace_summary.py).
Exit 0 on success; exit 2 when the ledger cannot be read (summary).

Decision-time packet capture (issue #130, from the #109 spike's
Phase 0 PROCEED): a shepherd decision cannot be reconstructed after
the fact — GitHub returns mergeStateStatus UNKNOWN for a merged PR,
and the head SHA / CI rollup / file list of an open PR all move — so
the packet the shepherd decided on is captured at append time. Every
packet field is optional: when an argument is absent, its key is
ABSENT from the row (never null, never an empty string), so a row
written with the pre-#130 argument set carries no packet keys from
those arguments and every existing reader is unaffected.

  --head-sha     the PR's head SHA when the shepherd acted.
  --ci-checks    the statusCheckRollup in compact form: one
                 `name=conclusion` entry per check, separated by
                 semicolons (an empty value means the PR ran no
                 checks). Stored capped: total / success / failure /
                 pending counts plus the full {name, conclusion}
                 entries for every NON-success check; successful
                 checks are counted, not listed.
  --files        comma-separated changed paths. The script computes
                 judging_touch / contract_touch from them with the
                 path classification in bin/judging_files.py
                 (imported, never copied).
  --merged-by    the merging login for an observed merge, recorded
                 as data. It does NOT discriminate who performed the
                 merge (the operator's account merges both ways);
                 `actor` below does.

Derived fields — computed by this script at append time, never
passed on the command line:

  actor  derived from the action class:
             merged_by_shepherd, fix_attempt, rerun,
             merge_conflicts, trigger_requested,
             needs_operator ............ "shepherd"
             merged, closed_unmerged ... "observed"
             checked, snapshot, other .. (key absent: no basis
                                          to claim an actor)
         For `merged` rows the shepherd observed a merge performed
         by the operator or the supervisor; for `closed_unmerged`
         it observed the closure (who closed the PR is not exposed
         by `gh pr view`, so the fallback is the action class).
  known_failures_hash
         sha256 of the deployment config's merge_policy.known_failures
         string, computed here from the config this script already
         resolves. If the config or the key is unresolvable the key
         is absent and the append still succeeds: a failed capture
         field never blocks a merge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from judging_files import judge_class, parse_changed_files  # noqa: E402
from trace_summary import (  # noqa: E402
    _load_deployment_config,
    default_ledger_path,
)

ACTIONS = ("rerun", "fix_attempt", "merged", "merged_by_shepherd",
           "needs_operator", "closed_unmerged", "merge_conflicts",
           "trigger_requested", "checked", "snapshot", "other")
MERGE_ACTIONS = ("merged", "merged_by_shepherd")


def validate_ci(action: str, ci: str) -> str | None:
    """Return an error message when a merge row has no CI evidence.

    A merge row (action `merged` or `merged_by_shepherd`) with an empty
    --ci records a merge without any evidence about the checks that ran
    on the head SHA, which is exactly the row the weekly harness-tune
    pass needs. Every other action may omit --ci.
    """
    if action not in MERGE_ACTIONS:
        return None
    if ci and ci.strip():
        return None
    return ("shepherd_ledger: --ci is required for merge actions "
            "(merged, merged_by_shepherd): the CI summary of the checks "
            "that ran on the merged SHA, or the local verify command and "
            "its result counts when the repo has no CI checks")

ACTOR_BY_ACTION = {
    "merged_by_shepherd": "shepherd",
    "fix_attempt": "shepherd",
    "rerun": "shepherd",
    "merge_conflicts": "shepherd",
    "trigger_requested": "shepherd",
    "needs_operator": "shepherd",
    "merged": "observed",
    "closed_unmerged": "observed",
}

SUCCESS_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
FAILURE_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE",
                       "CANCELLED", "ACTION_REQUIRED"}
PACKET_FIELDS = ("head_sha", "ci_checks", "merge_state",
                 "known_failures_hash", "actor")


def encode_ci_checks(raw: str) -> dict:
    """Compact rollup string -> capped ci_checks record.

    Input: `name=conclusion` entries separated by semicolons, as the
    shepherd builds them from the step-1 statusCheckRollup. An entry
    without `=` counts as pending with an empty conclusion. Anything
    that is not a success conclusion is stored in full.
    """
    entries = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, conclusion = part.partition("=")
        entries.append((name.strip(), conclusion.strip().upper()))
    success = sum(1 for _, c in entries if c in SUCCESS_CONCLUSIONS)
    failure = sum(1 for _, c in entries if c in FAILURE_CONCLUSIONS)
    return {
        "total": len(entries),
        "success": success,
        "failure": failure,
        "pending": len(entries) - success - failure,
        "non_success": [{"name": name, "conclusion": conclusion}
                        for name, conclusion in entries
                        if conclusion not in SUCCESS_CONCLUSIONS],
    }


def packet_touches(files_raw: str) -> tuple[bool, bool]:
    """(judging_touch, contract_touch) for a comma-separated file list.

    The list may carry git name-status prefixes (the step-2e shape);
    parse_changed_files from bin/judging_files.py normalizes them.
    The computation covers exactly the paths it is handed: a list
    truncated upstream (the `gh pr view` 100-file cap) under-reports,
    which is a prompt-side concern, not something this function can
    detect.
    """
    judging = False
    contract = False
    for _status, path in parse_changed_files(files_raw.replace(",", "\n")):
        cls = judge_class(path)
        if cls is not None:
            judging = True
        if cls == "contract":
            contract = True
    return judging, contract


def known_failures_hash() -> str | None:
    """sha256 of the config's merge_policy.known_failures, or None.

    None (key absent) when the deployment config or the value is
    unresolvable; hashing must never make an append fail.
    """
    try:
        cfg = _load_deployment_config() or {}
        value = (cfg.get("merge_policy") or {}).get("known_failures")
    except Exception:  # noqa: BLE001 - capture must not block a merge
        return None
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def default_shepherd_ledger_path() -> str | None:
    worker = default_ledger_path()
    if not worker:
        return None
    return os.path.join(os.path.dirname(worker), "shepherd_ledger.jsonl")


def append_row(ledger_path: str, row: dict) -> dict:
    row = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           **row}
    os.makedirs(os.path.dirname(os.path.abspath(ledger_path)),
                exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return row


def summarize(ledger_path: str, since: str | None = None) -> dict:
    total = 0
    by_action = Counter()
    by_outcome = Counter()
    by_repo = Counter()
    merges = 0
    unstable_merges = 0
    needs_operator_prs = set()
    fix_attempt_prs = set()
    packet_counts = Counter()
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since and str(row.get("ts") or "")[:10] < since:
                continue
            total += 1
            action = str(row.get("action") or "other")
            by_action[action] += 1
            if row.get("outcome"):
                by_outcome[str(row["outcome"])] += 1
            if row.get("repo"):
                by_repo[str(row["repo"])] += 1
            if action in MERGE_ACTIONS:
                merges += 1
                if str(row.get("merge_state") or "").upper() == "UNSTABLE":
                    unstable_merges += 1
            if action == "needs_operator":
                needs_operator_prs.add((row.get("repo"), row.get("pr")))
            if action == "fix_attempt":
                fix_attempt_prs.add((row.get("repo"), row.get("pr")))
            for field in PACKET_FIELDS:
                if field == "merge_state":
                    if str(row.get("merge_state") or ""):
                        packet_counts[field] += 1
                elif row.get(field) is not None:
                    packet_counts[field] += 1
    packet_coverage = {
        field: (round(packet_counts[field] / total, 4) if total else 0.0)
        for field in PACKET_FIELDS}
    return {
        "ledger": ledger_path, "since": since, "rows": total,
        "by_action": dict(by_action.most_common()),
        "by_outcome": dict(by_outcome.most_common()),
        "by_repo": dict(by_repo.most_common()),
        "merges": merges, "unstable_merges": unstable_merges,
        "needs_operator_distinct_prs": len(needs_operator_prs),
        "fix_attempt_distinct_prs": len(fix_attempt_prs),
        "packet_coverage": packet_coverage,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_app = sub.add_parser("append", help="append one shepherd action row")
    p_app.add_argument("--repo", required=True)
    p_app.add_argument("--pr", required=True, type=int)
    p_app.add_argument("--action", required=True, choices=ACTIONS)
    p_app.add_argument("--url", default="")
    p_app.add_argument("--outcome", default="")
    p_app.add_argument("--ci", default="")
    p_app.add_argument("--merge-state", default="")
    p_app.add_argument("--detail", default="")
    p_app.add_argument("--head-sha", default=None,
                       help="the PR's head SHA at decision time")
    p_app.add_argument("--ci-checks", default=None,
                       help="compact rollup: name=conclusion entries "
                            "separated by semicolons (empty = no checks ran)")
    p_app.add_argument("--files", default=None,
                       help="comma-separated changed paths; the script "
                            "computes judging/contract touch booleans")
    p_app.add_argument("--merged-by", default=None,
                       help="merging login for an observed merge "
                            "(recorded as data; actor is derived)")
    p_app.add_argument("--ledger", default=None)
    p_sum = sub.add_parser("summary", help="aggregate the shepherd ledger")
    p_sum.add_argument("--since", default=None,
                       help="YYYY-MM-DD; rows with an earlier ts are skipped")
    p_sum.add_argument("--ledger", default=None)
    p_sum.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    path = args.ledger or default_shepherd_ledger_path()
    if not path:
        print("shepherd_ledger: no ledger path (no deployment config)",
              file=sys.stderr)
        return 2

    if args.cmd == "append":
        error = validate_ci(args.action, args.ci)
        if error:
            print(error, file=sys.stderr)
            return 2
        row = {
            "repo": args.repo, "pr": args.pr, "pr_url": args.url,
            "action": args.action, "outcome": args.outcome,
            "ci": args.ci, "merge_state": args.merge_state,
            "detail": args.detail}
        if args.head_sha:
            row["head_sha"] = args.head_sha
        if args.ci_checks is not None:
            row["ci_checks"] = encode_ci_checks(args.ci_checks)
        if args.files is not None:
            judging, contract = packet_touches(args.files)
            row["judging_touch"] = judging
            row["contract_touch"] = contract
        if args.merged_by:
            row["merged_by"] = args.merged_by
        actor = ACTOR_BY_ACTION.get(args.action)
        if actor:
            row["actor"] = actor
        kf_hash = known_failures_hash()
        if kf_hash:
            row["known_failures_hash"] = kf_hash
        row = append_row(path, row)
        print(f"LOGGED: {row['repo']}#{row['pr']} {row['action']} "
              f"-> {path}")
        return 0

    if not os.path.exists(path):
        print(f"shepherd_ledger: ledger not found: {path}", file=sys.stderr)
        return 2
    result = summarize(path, since=args.since)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"Shepherd ledger: {result['rows']} row(s)"
              + (f" since {args.since}" if args.since else "")
              + f" in {path}")
        print(f"  merges: {result['merges']} "
              f"(unstable: {result['unstable_merges']})")
        print(f"  by action: {result['by_action']}")
        print(f"  by outcome: {result['by_outcome']}")
        print(f"  by repo: {result['by_repo']}")
        print(f"  needs_operator distinct PRs: "
              f"{result['needs_operator_distinct_prs']}; "
              f"fix-attempt distinct PRs: "
              f"{result['fix_attempt_distinct_prs']}")
        print(f"  packet coverage: {result['packet_coverage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
