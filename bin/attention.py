#!/usr/bin/env python3
"""Attention per correctly finished task (issue #125, from the #123
Elwing spike, Part B, Phase 0 PROCEED 2026-11-18).

Phase 0 measured this metric by hand over the most recent 20 shepherd
cycles: 28 tasks, 32 ledger-visible attention units, and 26 of those 32
units (81.2%) sat on tasks that did not finish correctly, dominated by
retained NEEDS ALEX flags re-presented every cycle. This tool makes
that hand count a standing weekly readout for harness-tune: one
deterministic pass over the worker and shepherd ledgers, plus (unless
--no-gh) the same gh refresh harness-tune step 3 already performs, to
classify merged tasks as correctly finished, reworked, or still
censored by the 14-day definition.

The definition is frozen from Phase 0 (brief: issue #125). A task is a
repo + issue group of worker ledger runs. Units per task:

  flag_presentations  shepherd needs_alex / needs_operator rows for
                      the task's PR(s), counted per row (one per
                      cycle presentation)
  flag_episodes       distinct flag episodes for the same PRs (see
                      count_episodes for the exact reading)
  fix_attempts        shepherd fix_attempt rows
  reruns              shepherd rerun rows
  redispatches        worker runs for the task beyond the first
  escalations         worker rows with outcome escalated

Both totals are always reported, side by side, and never summed:
units_total_presentations carries flag_presentations,
units_total_distinct carries flag_episodes, and every other unit type
is identical in both. metrics_rollup.py's founding principle is
distinct entities, never raw rows; the presentation count deliberately
departs from it to price flag re-presentation, and the pair is the
reconciliation.

Outcome classes per task: correctly_finished (merged, at least 14
days observable at readout time, gh rework check clean),
merged_censored (merged, 14-day window not yet fully observable, no
rework found so far), finished_with_rework (reverted, source issue
reopened, or follow-up fix PR found within 14 days of merge),
open_unresolved, failed_abandoned (no PR, or PR closed unmerged).

The headline median / p90 of units per correctly finished task is
computed over correctly_finished tasks only when that n is at least
5; otherwise it is computed over merged_censored tasks and labelled
"proxy, censored", never silently. The not-correctly-finished share
counts finished_with_rework, open_unresolved and failed_abandoned
tasks in the numerator: merged_censored tasks count as finished for
this share, exactly as Phase 0's 26 of 32 was computed (its
correctly_finished set was empty; counting censored tasks as
unfinished would make the share a trivial 100% and destroy the
signal). That reading is frozen here, not re-derived.

READOUT ONLY: nothing in the harness reads this output to gate, block,
or influence a dispatch, merge, or tune edit. The ledgers are read,
never written. Zero LLM tokens.

Usage:
    python3 bin/attention.py summary --since <ISO> [--until <ISO>]
        [--state-dir DIR] [--no-gh] [--json]

Exit 2 when either ledger is missing.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import default_ledger_path  # noqa: E402

MISSING_UNITS_NOTE = (
    "Ledger-visible attention only: chat-side operator taps and "
    "decisions made between cycles, and escalations phrased only in "
    "cycle final messages, are not in any ledger and are excluded "
    "from every figure here."
)

FLAG_ACTIONS = {"needs_alex", "needs_operator"}
TERMINAL_ACTIONS = {"merged", "merged_by_shepherd", "closed_unmerged"}
MERGE_ACTIONS = {"merged", "merged_by_shepherd"}
CYCLE_GAP_SECONDS = 1800  # >30 minutes: the Phase 0 cycle rule
REWORK_WINDOW_DAYS = 14
PROXY_MIN_N = 5


# --------------------------------------------------------------------------
# small helpers (parse_ts / load_rows follow the metrics_rollup pattern)
# --------------------------------------------------------------------------
def parse_ts(value):
    """ISO-8601 timestamp -> datetime (UTC), or None when unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def load_rows(path):
    """Read a JSONL ledger; unparseable lines are skipped, not fatal."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def short_repo(repo):
    """'anchapin/plan-coder' -> 'plan-coder'; short names pass through."""
    return str(repo or "").strip().rstrip("/").rsplit("/", 1)[-1]


def pr_number_from_url(url):
    """'https://github.com/o/r/pull/77' -> 77, else None."""
    text = str(url or "").strip().rstrip("/")
    if "/pull/" not in text:
        return None
    tail = text.rsplit("/pull/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _p90(values):
    """Nearest-rank p90 (the Phase 0 hand count's percentile reading)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = -(-len(ordered) * 9 // 10)  # ceil(0.9 * n), 1-indexed
    return ordered[max(rank, 1) - 1]


# --------------------------------------------------------------------------
# shepherd cycles and flag episodes
# --------------------------------------------------------------------------
def assign_cycles(shepherd_rows):
    """Annotate each row with _cycle: cycles are maximal runs of rows
    separated by gaps of at most 30 minutes (the Phase 0 rule, applied
    to the whole ledger so window-edge cycles keep their indices)."""
    ordered = sorted(
        (r for r in shepherd_rows if parse_ts(r.get("ts")) is not None),
        key=lambda r: parse_ts(r.get("ts")),
    )
    cycle = -1
    prev = None
    for row in ordered:
        stamp = parse_ts(row.get("ts"))
        if prev is None or (stamp - prev).total_seconds() > CYCLE_GAP_SECONDS:
            cycle += 1
        row["_cycle"] = cycle
        prev = stamp
    return ordered


def count_episodes(events):
    """Flag episodes from one PR's chronological ledger events.

    events: dicts with keys cycle (int or None) and kind ("flag" or
    "terminal"). An episode is a maximal run of flag presentations in
    consecutive shepherd cycles with no intervening terminal
    disposition (merge, close, or operator disposition row).

    Reading, chosen because the brief's text forces one: "consecutive
    shepherd cycles" is taken literally over the >30-minute cycle
    grouping, so a cycle in which the shepherd did not present the
    flag breaks the episode, and duplicate presentations inside one
    cycle count as one episode step. The alternative reading (an
    episode ends only at a terminal disposition, however many cycles
    are skipped) is NOT what is implemented; the difference is named
    in the #125 build report.
    """
    episodes = 0
    last_flag_cycle = None
    broken = True  # a terminal disposition (or the start) breaks a run
    for event in events:
        if event["kind"] == "terminal":
            broken = True
            last_flag_cycle = None
            continue
        cyc = event.get("cycle")
        if broken or last_flag_cycle is None:
            episodes += 1
        elif cyc is not None and last_flag_cycle is not None and cyc > last_flag_cycle + 1:
            episodes += 1
        broken = False
        if cyc is not None:
            last_flag_cycle = cyc
    return episodes


# --------------------------------------------------------------------------
# gh rework check (same shape as harness-tune step 3's gh refresh)
# --------------------------------------------------------------------------
def _gh(args):
    """Run one gh command; return parsed JSON or None on any failure."""
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def reopened_after_merge(events, merged_at):
    """True when an issue timeline shows a reopen after the merge.

    Reading, per the #125 brief's wording: a source issue counts as
    rework only if it was REOPENED after the merge, meaning a closed
    event at or before the merge followed by a reopened event after
    the merge and inside the 14-day rework window. An issue that is
    merely standing open (a tracker issue that never closed) is NOT
    rework: with no closed event at or before the merge, this returns
    False however long the issue stays open. `events` is the gh
    timeline list (dicts with event / created_at); the caller treats a
    failed timeline fetch as 'unavailable', never as evidence either
    way.
    """
    if merged_at is None:
        return False
    closed_at_or_before_merge = False
    for event in sorted(
        (e for e in events if isinstance(e, dict)),
        key=lambda e: parse_ts(e.get("created_at")) or merged_at,
    ):
        stamp = parse_ts(event.get("created_at"))
        if stamp is None:
            continue
        if event.get("event") == "closed" and stamp <= merged_at:
            closed_at_or_before_merge = True
        elif (
            event.get("event") == "reopened"
            and closed_at_or_before_merge
            and merged_at < stamp <= merged_at + timedelta(days=REWORK_WINDOW_DAYS)
        ):
            return True
    return False


def gh_rework_check(slug, pr, issue, merged_at):
    """'clean' | 'rework' | 'unavailable' for one merged PR.

    Rework is any of: a later PR in the same repo whose title
    references this PR number and reads as a revert or a fix inside
    the 14-day window, or the source issue reopened after the merge
    (see reopened_after_merge for the exact reading; standing open is
    not rework). Any gh failure, including a timeline fetch that
    cannot establish reopened status, is 'unavailable', never
    'clean'.
    """
    view = _gh(
        ["pr", "view", str(pr), "--repo", slug, "--json", "state,mergedAt,title"]
    )
    if view is None or view.get("state") != "MERGED":
        return "unavailable"
    if issue is not None:
        timeline = _gh(
            ["api", f"repos/{slug}/issues/{issue}/timeline?per_page=100"]
        )
        if not isinstance(timeline, list):
            return "unavailable"
        if reopened_after_merge(timeline, merged_at):
            return "rework"
    later = _gh(
        [
            "pr", "list", "--repo", slug, "--state", "all",
            "--search", f"{pr} in:title",
            "--json", "number,title,createdAt", "--limit", "50",
        ]
    )
    if later is None:
        return "unavailable"
    for cand in later:
        if cand.get("number") == pr:
            continue
        created = parse_ts(cand.get("createdAt"))
        if merged_at is not None and created is not None and created < merged_at:
            continue
        title = str(cand.get("title") or "").lower()
        if str(pr) not in title or ("revert" not in title and "fix" not in title):
            continue
        # Only a follow-up inside the 14-day window is rework; a later
        # fix is ordinary subsequent work, not a failed finish.
        if merged_at is not None and created is not None:
            if created > merged_at + timedelta(days=REWORK_WINDOW_DAYS):
                continue
        return "rework"
    return "clean"


# --------------------------------------------------------------------------
# core aggregation
# --------------------------------------------------------------------------
def build_summary(worker_rows, shepherd_rows, since, until, use_gh=True, now=None):
    """Assemble the frozen readout from raw ledger rows.

    since / until bound the readout window (worker runs and shepherd
    unit rows inside it select the tasks and their units); cycle
    indices and terminal dispositions are computed over the full
    shepherd ledger up to `until`, as Phase 0 did.
    """
    readout_time = now or until
    cycled = assign_cycles(shepherd_rows)
    shepherd_by_pr = {}
    for row in cycled:
        if parse_ts(row.get("ts")) is None or parse_ts(row.get("ts")) > until:
            continue
        key = (short_repo(row.get("repo")), row.get("pr"))
        if key[1] is not None:
            shepherd_by_pr.setdefault(key, []).append(row)

    tasks = {}
    for row in worker_rows:
        stamp = parse_ts(row.get("ts"))
        if stamp is None or not (since <= stamp <= until):
            continue
        key = (short_repo(row.get("repo")), row.get("issue"))
        task = tasks.setdefault(
            key,
            {
                "repo": key[0],
                "issue": key[1],
                "runs": 0,
                "prs": set(),
                "escalations": 0,
                "worker_rows": 0,
            },
        )
        task["runs"] += 1
        task["worker_rows"] += 1
        pr = pr_number_from_url(row.get("pr_url"))
        if pr is not None:
            task["prs"].add(pr)
        if str(row.get("outcome") or "").strip() == "escalated":
            task["escalations"] += 1

    out_tasks = []
    for key in sorted(tasks, key=lambda k: (str(k[0]), k[1] if isinstance(k[1], int) else 0)):
        task = tasks[key]
        flag_presentations = 0
        fix_attempts = 0
        reruns = 0
        episodes = 0
        shepherd_unit_rows = 0
        merged_at = None
        closed_unmerged = False
        slug = None
        for pr in sorted(task["prs"]):
            events = []
            for row in shepherd_by_pr.get((task["repo"], pr), []):
                if slug is None and "/" in str(row.get("repo") or ""):
                    slug = str(row.get("repo"))
                action = str(row.get("action") or "")
                stamp = parse_ts(row.get("ts"))
                if action in TERMINAL_ACTIONS:
                    events.append({"cycle": row.get("_cycle"), "kind": "terminal"})
                    if action in MERGE_ACTIONS and (
                        merged_at is None or stamp < merged_at
                    ):
                        merged_at = stamp
                    if action == "closed_unmerged":
                        closed_unmerged = True
                    continue
                if not (since <= stamp <= until):
                    continue
                if action in FLAG_ACTIONS:
                    flag_presentations += 1
                    shepherd_unit_rows += 1
                    events.append({"cycle": row.get("_cycle"), "kind": "flag"})
                elif action == "fix_attempt":
                    fix_attempts += 1
                    shepherd_unit_rows += 1
                elif action == "rerun":
                    reruns += 1
                    shepherd_unit_rows += 1
            events.sort(key=lambda e: (e["cycle"] is None, e["cycle"] or 0))
            episodes += count_episodes(events)
        redispatches = max(task["runs"] - 1, 0)
        units = {
            "flag_presentations": flag_presentations,
            "flag_episodes": episodes,
            "fix_attempts": fix_attempts,
            "reruns": reruns,
            "redispatches": redispatches,
            "escalations": task["escalations"],
        }
        total_pres = (
            flag_presentations + fix_attempts + reruns + redispatches + task["escalations"]
        )
        total_dist = episodes + fix_attempts + reruns + redispatches + task["escalations"]

        # Outcome classification.
        rework_check = "skipped"
        if merged_at is not None:
            age = readout_time - merged_at
            if not use_gh:
                outcome = "merged_censored"
            else:
                verdicts = [
                    gh_rework_check(slug or task["repo"], pr, task["issue"], merged_at)
                    for pr in sorted(task["prs"])
                    if any(
                        str(r.get("action") or "") in MERGE_ACTIONS
                        for r in shepherd_by_pr.get((task["repo"], pr), [])
                    )
                ]
                if "rework" in verdicts:
                    rework_check = "rework"
                    outcome = "finished_with_rework"
                elif "clean" in verdicts and age >= timedelta(days=REWORK_WINDOW_DAYS):
                    rework_check = "clean"
                    outcome = "correctly_finished"
                elif "clean" in verdicts:
                    rework_check = "clean"
                    outcome = "merged_censored"
                else:
                    rework_check = "unavailable"
                    outcome = "merged_censored"
        elif closed_unmerged or not task["prs"]:
            outcome = "failed_abandoned"
        else:
            outcome = "open_unresolved"

        out_tasks.append(
            {
                "task": f"{task['repo']}#{task['issue']}",
                "repo": task["repo"],
                "issue": task["issue"],
                "prs": sorted(task["prs"]),
                "runs": task["runs"],
                "units": units,
                "units_total_presentations": total_pres,
                "units_total_distinct": total_dist,
                "outcome": outcome,
                "rework_check": rework_check,
                "evidence": {
                    "worker_rows": task["worker_rows"],
                    "shepherd_rows": shepherd_unit_rows,
                },
            }
        )

    by_outcome = {}
    for task in out_tasks:
        by_outcome[task["outcome"]] = by_outcome.get(task["outcome"], 0) + 1
    total_pres_all = sum(t["units_total_presentations"] for t in out_tasks)
    total_dist_all = sum(t["units_total_distinct"] for t in out_tasks)
    unfinished = {"finished_with_rework", "open_unresolved", "failed_abandoned"}
    bad_pres = sum(
        t["units_total_presentations"] for t in out_tasks if t["outcome"] in unfinished
    )
    bad_dist = sum(
        t["units_total_distinct"] for t in out_tasks if t["outcome"] in unfinished
    )

    finished = [t for t in out_tasks if t["outcome"] == "correctly_finished"]
    if len(finished) >= PROXY_MIN_N:
        basis_tasks, basis_label = finished, None
    else:
        basis_tasks = [t for t in out_tasks if t["outcome"] == "merged_censored"]
        basis_label = "proxy, censored"
    basis_values = [t["units_total_presentations"] for t in basis_tasks]

    top_flags = sorted(
        out_tasks,
        key=lambda t: (-t["units"]["flag_presentations"], t["repo"], str(t["issue"])),
    )[:3]

    return {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "missing_units_note": MISSING_UNITS_NOTE,
        "n_tasks": len(out_tasks),
        "tasks_by_outcome": dict(sorted(by_outcome.items())),
        "totals": {
            "units_total_presentations": total_pres_all,
            "units_total_distinct": total_dist_all,
        },
        "attention_per_correctly_finished": {
            "label": basis_label,
            "basis": "merged_censored" if basis_label else "correctly_finished",
            "n": len(basis_tasks),
            "median": statistics.median(basis_values) if basis_values else None,
            "p90": _p90(basis_values),
        },
        "share_not_correctly_finished": {
            "presentations_pct": (
                round(100.0 * bad_pres / total_pres_all, 1) if total_pres_all else None
            ),
            "presentations_n": f"{bad_pres}/{total_pres_all}",
            "distinct_pct": (
                round(100.0 * bad_dist / total_dist_all, 1) if total_dist_all else None
            ),
            "distinct_n": f"{bad_dist}/{total_dist_all}",
        },
        "top_flag_presentations": [
            {
                "task": t["task"],
                "repo": t["repo"],
                "prs": t["prs"],
                "flag_presentations": t["units"]["flag_presentations"],
            }
            for t in top_flags
            if t["units"]["flag_presentations"] > 0
        ],
        "tasks": out_tasks,
    }


def default_state_dir():
    """The state dir the deployment config points at (may be None)."""
    ledger = default_ledger_path()
    if not ledger:
        return None
    return os.path.dirname(os.path.abspath(os.path.expanduser(ledger)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p_sum = sub.add_parser("summary", help="the frozen attention readout")
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

    since = parse_ts(args.since)
    if since is None:
        print(f"attention: unparseable --since {args.since!r}", file=sys.stderr)
        return 2
    until = parse_ts(args.until) if args.until else datetime.now(UTC)
    if until is None:
        print(f"attention: unparseable --until {args.until!r}", file=sys.stderr)
        return 2
    state_dir = args.state_dir or default_state_dir()
    if not state_dir:
        print("attention: no state dir (no deployment config)", file=sys.stderr)
        return 2
    worker_path = os.path.join(state_dir, "worker_ledger.jsonl")
    shepherd_path = os.path.join(state_dir, "shepherd_ledger.jsonl")
    for path in (worker_path, shepherd_path):
        if not os.path.isfile(path):
            print(f"attention: ledger missing: {path}", file=sys.stderr)
            return 2
    summary = build_summary(
        load_rows(worker_path),
        load_rows(shepherd_path),
        since,
        until,
        use_gh=not args.no_gh,
        now=until,
    )
    if args.json:
        print(json.dumps(summary, indent=1, sort_keys=True))
    else:
        apcf = summary["attention_per_correctly_finished"]
        label = f" ({apcf['label']})" if apcf["label"] else ""
        share = summary["share_not_correctly_finished"]
        print(
            f"attention {summary['window']['since']}..{summary['window']['until']}: "
            f"{summary['n_tasks']} tasks, outcomes {summary['tasks_by_outcome']}"
        )
        print(
            f"units: {summary['totals']['units_total_presentations']} presentations / "
            f"{summary['totals']['units_total_distinct']} distinct"
        )
        print(
            f"attention per correctly finished task{label}: median {apcf['median']}, "
            f"p90 {apcf['p90']} (n={apcf['n']})"
        )
        print(
            "share not correctly finished: "
            f"{share['presentations_pct']}% of presentations ({share['presentations_n']}), "
            f"{share['distinct_pct']}% of distinct ({share['distinct_n']})"
        )
        for top in summary["top_flag_presentations"]:
            print(
                f"top flag presentations: {top['task']} PR(s) {top['prs']}: "
                f"{top['flag_presentations']}"
            )
        print(summary["missing_units_note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
