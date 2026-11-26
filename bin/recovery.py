#!/usr/bin/env python3
"""Trouble-recovery readout (issue #194, Phase 1, from the Claude Code
expertise spike, Phase 0 PROCEED 2026-11-26).

Phase 0 measured this by hand over the worker and shepherd ledgers:
112 troubled tasks, 53 recovered (47.3%), 24 abandoned (21.4%), tier
change on redispatch showing no recovery benefit (44% vs 50%), and two
predictors, narrowed brief and failover reflection carried, that could
not be measured at all because no ledger field recorded them. This
tool makes that hand count a standing readout and reads the two new
fields when they are present.

The definitions are frozen from Phase 0. A task is a repo + issue
group of worker ledger runs, as in bin/attention.py. A task is
troubled when it has at least one of:

  W  a worker row whose outcome starts with "failed", or whose
     outcome is "escalated";
  S  a shepherd row for one of the task's PRs (PR number from a
     worker row's pr_url) whose ci_checks.failure is at least 1.

First trouble is the earliest timestamp among the qualifying rows.
Extra runs and extra tokens are the worker rows after first trouble.

Outcome classes are NOT redefined here: they come from
bin/attention.py's build_summary, including its 14-day rework window.
Recovered means the attention outcome is correctly_finished or
merged_censored (merged, with no rework verdict from attention);
finished_with_rework tasks are reported separately and never count
as recovered. Abandoned means failed_abandoned, and open means
open_unresolved. With --no-gh, attention cannot run its rework
check, so merged tasks stay merged_censored and the recovered figure
is the censored proxy, exactly as attention labels its own proxy.

Breakdowns, each with n per cell and cells under MIN_READABLE_N
labelled "too small to read": tier change on redispatch (a
post-trouble worker row on a different tier than the trouble run),
tier of the trouble run, repo, split_suggested (any worker row for
the task with that outcome), and the two redispatch fields below.

Redispatch fields (recorded on worker ledger rows for redispatch
runs by the supervisor, step 8): narrowed_brief and
failover_reflection_carried, booleans. They are read only on worker
rows after first trouble, the recovery attempts. A task's category
for a field is "true" when at least one such row records true,
"false" when at least one records a value and none records true,
and "unknown" when no such row records the field at all. Absent
means unknown, never false: rows written before the fields existed
parse and count exactly as they did before, under "unknown", and
the coverage block reports how many post-trouble rows and tasks
actually record each field.

READOUT ONLY: nothing in the harness reads this output to gate,
block, or influence a dispatch, merge, or tune edit. The ledgers
are read, never written. Zero LLM tokens.

Usage:
    python3 bin/recovery.py summary --since <ISO> [--until <ISO>]
        [--state-dir DIR] [--no-gh] [--json]

Exit 2 when either ledger is missing.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attention

MIN_READABLE_N = 5
RECOVERED_OUTCOMES = {"correctly_finished", "merged_censored"}
REDISPATCH_FIELDS = ("narrowed_brief", "failover_reflection_carried")
UNKNOWN_NOTE = (
    "Absent redispatch fields mean unknown, not false: ledger rows "
    "written before the fields existed are counted under unknown, "
    "with coverage reported per field."
)


def _num(value):
    """Numeric ledger value -> float, else None (bools are not numbers)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _tokens(row):
    """One worker row's token total: tokens_total, else in + out."""
    total = _num(row.get("tokens_total"))
    if total is not None:
        return total
    parts = [_num(row.get("tokens_in")), _num(row.get("tokens_out"))]
    present = [p for p in parts if p is not None]
    if not present:
        return 0.0
    return sum(present)


def _cell(tasks):
    """One breakdown cell over a list of troubled-task dicts."""
    n = len(tasks)
    recovered = sum(1 for t in tasks if t["outcome"] in RECOVERED_OUTCOMES)
    return {
        "n": n,
        "recovered": recovered,
        "recovered_pct": round(100.0 * recovered / n, 1) if n else None,
        "abandoned": sum(1 for t in tasks if t["outcome"] == "failed_abandoned"),
        "open_unresolved": sum(1 for t in tasks if t["outcome"] == "open_unresolved"),
        "finished_with_rework": sum(
            1 for t in tasks if t["outcome"] == "finished_with_rework"
        ),
        "readable": n >= MIN_READABLE_N,
        "note": None if n >= MIN_READABLE_N else "too small to read",
    }


def _breakdown(tasks, key_fn):
    """Group troubled tasks by key_fn and build a cell per group."""
    groups = {}
    for task in tasks:
        groups.setdefault(key_fn(task), []).append(task)
    return {key: _cell(groups[key]) for key in sorted(groups, key=str)}


def build_summary(worker_rows, shepherd_rows, since, until, use_gh=True, now=None):
    """Assemble the frozen Phase 0 recovery readout from raw ledger rows.

    Outcome classification is delegated to attention.build_summary
    over the same window; this function adds only the trouble
    detection, the post-trouble accounting, and the breakdowns.
    """
    base = attention.build_summary(
        worker_rows, shepherd_rows, since, until, use_gh=use_gh, now=now
    )
    outcomes = {
        (task["repo"], task["issue"]): task["outcome"] for task in base["tasks"]
    }

    workers_by_task = {}
    for row in worker_rows:
        stamp = attention.parse_ts(row.get("ts"))
        if stamp is None or not (since <= stamp <= until):
            continue
        key = (attention.short_repo(row.get("repo")), row.get("issue"))
        if key in outcomes:
            workers_by_task.setdefault(key, []).append((stamp, row))
    for rows in workers_by_task.values():
        rows.sort(key=lambda pair: pair[0])

    shepherd_by_pr = {}
    for row in shepherd_rows:
        stamp = attention.parse_ts(row.get("ts"))
        if stamp is None or not (since <= stamp <= until):
            continue
        pr = row.get("pr")
        if pr is not None:
            key = (attention.short_repo(row.get("repo")), pr)
            shepherd_by_pr.setdefault(key, []).append((stamp, row))

    troubled = []
    post_rows_total = 0
    recorded_rows = {field: 0 for field in REDISPATCH_FIELDS}
    for key in sorted(outcomes, key=lambda k: (str(k[0]), str(k[1]))):
        rows = workers_by_task.get(key, [])
        worker_trouble = [
            (stamp, row)
            for stamp, row in rows
            if str(row.get("outcome") or "").startswith("failed")
            or str(row.get("outcome") or "").strip() == "escalated"
        ]
        prs = set()
        for _stamp, row in rows:
            pr = attention.pr_number_from_url(row.get("pr_url"))
            if pr is not None:
                prs.add(pr)
        shepherd_trouble = []
        for pr in prs:
            for stamp, row in shepherd_by_pr.get((key[0], pr), []):
                checks = row.get("ci_checks")
                if isinstance(checks, dict) and (_num(checks.get("failure")) or 0) >= 1:
                    shepherd_trouble.append(stamp)
        trouble_stamps = [s for s, _r in worker_trouble] + shepherd_trouble
        if not trouble_stamps:
            continue
        first_trouble = min(trouble_stamps)
        if worker_trouble:
            trouble_tier = min(worker_trouble, key=lambda pair: pair[0])[1].get("tier")
            trouble_source = "worker+shepherd" if shepherd_trouble else "worker"
        else:
            trouble_tier = rows[0][1].get("tier") if rows else None
            trouble_source = "shepherd"
        post = [(stamp, row) for stamp, row in rows if stamp > first_trouble]
        tier_change = None
        if trouble_tier is not None:
            tier_change = any(
                row.get("tier") is not None and row.get("tier") != trouble_tier
                for _stamp, row in post
            )
        fields = {}
        post_rows_total += len(post)
        for field in REDISPATCH_FIELDS:
            recorded = [
                row[field] for _stamp, row in post if isinstance(row.get(field), bool)
            ]
            recorded_rows[field] += len(recorded)
            if any(recorded):
                fields[field] = "true"
            elif recorded:
                fields[field] = "false"
            else:
                fields[field] = "unknown"
        troubled.append(
            {
                "task": f"{key[0]}#{key[1]}",
                "repo": key[0],
                "issue": key[1],
                "outcome": outcomes[key],
                "recovered": outcomes[key] in RECOVERED_OUTCOMES,
                "trouble_source": trouble_source,
                "first_trouble": first_trouble.isoformat(),
                "trouble_tier": trouble_tier,
                "tier_change": tier_change,
                "split_suggested": any(
                    str(row.get("outcome") or "").strip() == "split_suggested"
                    for _stamp, row in rows
                ),
                "extra_runs": len(post),
                "extra_tokens": int(sum(_tokens(row) for _stamp, row in post)),
                "post_trouble_rows": len(post),
                "redispatch_fields": fields,
            }
        )

    n = len(troubled)
    by_outcome = {}
    for task in troubled:
        by_outcome[task["outcome"]] = by_outcome.get(task["outcome"], 0) + 1
    recovered_tasks = [t for t in troubled if t["recovered"]]
    extra_runs = [t["extra_runs"] for t in troubled]
    extra_tokens = [t["extra_tokens"] for t in troubled]

    field_report = {}
    for field in REDISPATCH_FIELDS:
        field_report[field] = {
            "coverage": {
                "post_trouble_rows": post_rows_total,
                "recorded_rows": recorded_rows[field],
                "unrecorded_rows": post_rows_total - recorded_rows[field],
                "tasks_recorded": sum(
                    1 for t in troubled if t["redispatch_fields"][field] != "unknown"
                ),
                "tasks_unknown": sum(
                    1 for t in troubled if t["redispatch_fields"][field] == "unknown"
                ),
            },
            "breakdown": _breakdown(
                troubled, lambda t, f=field: t["redispatch_fields"][f]
            ),
        }

    return {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "unknown_note": UNKNOWN_NOTE,
        "n_tasks": base["n_tasks"],
        "n_troubled": n,
        "troubled_by_outcome": dict(sorted(by_outcome.items())),
        "recovered": {
            "n": len(recovered_tasks),
            "pct": round(100.0 * len(recovered_tasks) / n, 1) if n else None,
        },
        "abandoned": {
            "n": by_outcome.get("failed_abandoned", 0),
            "pct": (
                round(100.0 * by_outcome.get("failed_abandoned", 0) / n, 1)
                if n
                else None
            ),
        },
        "open_unresolved": {
            "n": by_outcome.get("open_unresolved", 0),
            "pct": (
                round(100.0 * by_outcome.get("open_unresolved", 0) / n, 1)
                if n
                else None
            ),
        },
        "extra_after_trouble": {
            "median_runs": statistics.median(extra_runs) if extra_runs else None,
            "median_tokens": statistics.median(extra_tokens) if extra_tokens else None,
            "total_tokens": sum(extra_tokens),
        },
        "breakdowns": {
            "tier_change": _breakdown(
                troubled,
                lambda t: (
                    "unknown"
                    if t["tier_change"] is None
                    else ("tier_changed" if t["tier_change"] else "no_tier_change")
                ),
            ),
            "trouble_tier": _breakdown(
                troubled, lambda t: str(t["trouble_tier"] or "(no tier)")
            ),
            "repo": _breakdown(troubled, lambda t: t["repo"]),
            "split_suggested": _breakdown(
                troubled,
                lambda t: "split_suggested" if t["split_suggested"] else "no_split",
            ),
        },
        "redispatch_fields": field_report,
        "tasks": troubled,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p_sum = sub.add_parser("summary", help="the frozen recovery readout")
    p_sum.add_argument("--since", required=True, help="window start, ISO-8601")
    p_sum.add_argument(
        "--until", default=None, help="window end, ISO-8601 (default: now)"
    )
    p_sum.add_argument(
        "--state-dir",
        default=None,
        help="state dir holding worker_ledger.jsonl and "
        "shepherd_ledger.jsonl (default: deployment config state dir)",
    )
    p_sum.add_argument(
        "--no-gh",
        action="store_true",
        help="skip the gh rework check (merged tasks stay merged_censored)",
    )
    p_sum.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args(argv)

    since = attention.parse_ts(args.since)
    if since is None:
        print(f"recovery: unparseable --since {args.since!r}", file=sys.stderr)
        return 2
    until = attention.parse_ts(args.until) if args.until else datetime.now(UTC)
    if until is None:
        print(f"recovery: unparseable --until {args.until!r}", file=sys.stderr)
        return 2
    state_dir = args.state_dir or attention.default_state_dir()
    if not state_dir:
        print("recovery: no state dir (no deployment config)", file=sys.stderr)
        return 2
    worker_path = os.path.join(state_dir, "worker_ledger.jsonl")
    shepherd_path = os.path.join(state_dir, "shepherd_ledger.jsonl")
    for path in (worker_path, shepherd_path):
        if not os.path.isfile(path):
            print(f"recovery: ledger missing: {path}", file=sys.stderr)
            return 2
    summary = build_summary(
        attention.load_rows(worker_path),
        attention.load_rows(shepherd_path),
        since,
        until,
        use_gh=not args.no_gh,
        now=until,
    )
    if args.json:
        print(json.dumps(summary, indent=1, sort_keys=True))
    else:
        print(
            f"recovery {summary['window']['since']}..{summary['window']['until']}: "
            f"{summary['n_troubled']} troubled of {summary['n_tasks']} tasks, "
            f"outcomes {summary['troubled_by_outcome']}"
        )
        print(
            f"recovered {summary['recovered']['n']} ({summary['recovered']['pct']}%), "
            f"abandoned {summary['abandoned']['n']} ({summary['abandoned']['pct']}%), "
            f"open {summary['open_unresolved']['n']} "
            f"({summary['open_unresolved']['pct']}%)"
        )
        extra = summary["extra_after_trouble"]
        print(
            f"extra after trouble: median {extra['median_runs']} runs, "
            f"median {extra['median_tokens']} tokens, "
            f"total {extra['total_tokens']} tokens"
        )
        for name, cells in summary["breakdowns"].items():
            for key, cell in cells.items():
                note = f" ({cell['note']})" if cell["note"] else ""
                print(
                    f"{name} {key}: n={cell['n']}, "
                    f"recovered {cell['recovered']} ({cell['recovered_pct']}%), "
                    f"abandoned {cell['abandoned']}{note}"
                )
        for field, report in summary["redispatch_fields"].items():
            cov = report["coverage"]
            print(
                f"{field}: recorded on {cov['recorded_rows']} of "
                f"{cov['post_trouble_rows']} post-trouble rows, "
                f"{cov['tasks_recorded']} tasks recorded, "
                f"{cov['tasks_unknown']} unknown"
            )
        print(summary["unknown_note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
