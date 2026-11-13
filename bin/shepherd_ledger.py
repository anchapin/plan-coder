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
        [--merge-state <status>] [--detail <short text>] [--ledger PATH]
    python3 bin/shepherd_ledger.py summary [--since YYYY-MM-DD]
        [--ledger PATH] [--json]

--ledger defaults to shepherd_ledger.jsonl in the deployment config's
state dir (same search order as trace_summary.py).
Exit 0 on success; exit 2 when the ledger cannot be read (summary).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import default_ledger_path  # noqa: E402

ACTIONS = ("rerun", "fix_attempt", "merged", "merged_by_shepherd",
           "needs_operator", "closed_unmerged", "merge_conflicts",
           "trigger_requested", "checked", "snapshot", "other")
MERGE_ACTIONS = ("merged", "merged_by_shepherd")


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
    return {
        "ledger": ledger_path, "since": since, "rows": total,
        "by_action": dict(by_action.most_common()),
        "by_outcome": dict(by_outcome.most_common()),
        "by_repo": dict(by_repo.most_common()),
        "merges": merges, "unstable_merges": unstable_merges,
        "needs_operator_distinct_prs": len(needs_operator_prs),
        "fix_attempt_distinct_prs": len(fix_attempt_prs),
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
        row = append_row(path, {
            "repo": args.repo, "pr": args.pr, "pr_url": args.url,
            "action": args.action, "outcome": args.outcome,
            "ci": args.ci, "merge_state": args.merge_state,
            "detail": args.detail})
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
