#!/usr/bin/env python3
"""Per-issue retry guard for the backlog supervisor (trace-grader spike,
2026-11-07).

The supervisor's prompt used to say "scan the metrics ledger" before
re-briefing an issue — eyeballing that let one issue burn 5
consecutive failed runs (~12M tokens). This script makes the check
mechanical: exit 1 (blocked) when the ledger shows >= --min-failures
failed rows for the issue and no pr_opened row; exit 0 otherwise.

A block is an override-with-reason, not a ban: the supervisor may still
brief the issue after materially narrowing/splitting the brief or
changing tier, and must state the reason in its report.

Usage:
    python3 bin/ledger_check.py <repo> <issue> [--ledger PATH]
                               [--min-failures N] [--json]
    --ledger defaults to the deployment config's state dir (same search
    order as agent.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import default_ledger_path  # noqa: E402


def check(ledger_path: str, repo: str, issue, min_failures: int = 2) -> dict:
    failed = 0
    attempts = 0
    pr_opened = False
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("repo") != repo:
                continue
            if str(row.get("issue")) != str(issue):
                continue
            attempts += 1
            outcome = str(row.get("outcome") or "")
            if outcome == "pr_opened":
                pr_opened = True
            elif outcome.startswith("failed"):
                failed += 1
    blocked = failed >= min_failures and not pr_opened
    return {
        "repo": repo, "issue": issue, "attempts": attempts,
        "failed": failed, "pr_opened": pr_opened,
        "min_failures": min_failures, "blocked": blocked,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo")
    ap.add_argument("issue")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--min-failures", type=int, default=2)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    path = args.ledger or default_ledger_path()
    if not path or not os.path.exists(path):
        print(f"ledger not found: {path}", file=sys.stderr)
        return 2
    result = check(path, args.repo, args.issue, args.min_failures)
    if args.json:
        print(json.dumps(result, indent=1))
    elif result["blocked"]:
        print(f"BLOCKED: {args.repo}#{args.issue} has "
              f"{result['failed']} failed attempt(s) and no PR opened — "
              f"do not re-brief unchanged (narrow/split the brief or "
              f"change tier, with a stated reason)")
    else:
        print(f"OK: {args.repo}#{args.issue} — "
              f"{result['attempts']} attempt(s), "
              f"{result['failed']} failed, "
              f"pr_opened={result['pr_opened']}")
    return 1 if result["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
