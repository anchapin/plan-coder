#!/usr/bin/env python3
"""Join the worker and shepherd ledgers into a PR-lifecycle + daily rollup.

Phase 0 of docs/METRICS.md (issue #59): one derived file,
`metrics_rollup.jsonl`, in the state dir, holding

  * one **PR-lifecycle row** per distinct PR -- opened, merged, tokens,
    shepherd action-days, stage dwell, outcome, and the flags that say
    what could not be measured; and
  * one **daily row** per calendar day per stratum (repo x tier x
    provider), plus one per-day total row.

The ledgers stay the truth: this file is a re-runnable cache, rebuilt
from scratch on every run.

Principles that the code implements (spec sections 1, 3 and 8):

  * **Distinct entities, never raw rows** (section 1.3).  Every
    PR-level number counts distinct PRs; a shepherd ledger row is a
    polling event, not a PR (spec example: 833 `needs_alex` rows are
    64 distinct PRs; the live ledger now carries 848 rows for the
    same 64 PRs).
  * **Nothing is silently dropped** (section 8).  A merged PR with no
    worker row (the 20-of-193 baseline case) and a PR opened before the
    shepherd ledger starts (the pre-2026-11-09 gap) are flagged on
    their own row.
  * **Same semantics as the CLIs that already own a number** (section
    1.5).  Run-level aggregates reuse `bin/trace_summary.py`'s bucket
    statistics verbatim; merge detection reuses
    `bin/shepherd_ledger.py`'s merge-action list.

Read-only over the state dir: the only file written is
`metrics_rollup.jsonl` (or stdout with `--out -`).  The worker and
shepherd ledgers are never modified.

Usage:
    python3 bin/metrics_rollup.py [--state-dir DIR]
                                  [--worker-ledger PATH]
                                  [--shepherd-ledger PATH]
                                  [--out PATH] [--since YYYY-MM-DD]
                                  [--until YYYY-MM-DD] [--json]

--state-dir defaults to the deployment config's state dir (same search
order as bin/trace_summary.py); --worker-ledger and --shepherd-ledger
default to worker_ledger.jsonl and shepherd_ledger.jsonl inside it.
--until turns the run into an as-of snapshot (the spec's baseline is
measured as of a day, so a grown ledger can still reproduce it).
Exit 0 on success; exit 2 when the worker ledger cannot be read (a
missing shepherd ledger is not an error -- the shepherd may simply
not have logged yet, and the rollup says so on every row).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shepherd_ledger import MERGE_ACTIONS
from trace_summary import _bucket_stats, default_ledger_path

# The shepherd ledger only starts on 2026-11-09 (spec section 2).  It is
# the fallback gap boundary when the shepherd ledger is missing or
# empty; otherwise the boundary is the first day the ledger actually
# has a row, so a ledger that starts later widens the gap honestly.
SHEPHERD_LEDGER_START = "2026-11-09"

UNKNOWN = "(unknown)"

# Shepherd actions that are worth a flag on the PR's lifecycle row.
FLAGGED_ACTIONS = {
    "needs_alex": "needs_alex",
    "needs_operator": "needs_operator",
    "merge_conflicts": "merge_conflicts",
    "closed_unmerged": "closed_unmerged",
    "fix_attempt": "fix_attempt",
    "rerun": "rerun",
    "trigger_requested": "trigger_requested",
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _num(value):
    """A number or None (booleans are not numbers here)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


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


def day_of(value):
    """The UTC calendar day of a ledger timestamp ('YYYY-MM-DD')."""
    stamp = parse_ts(value)
    if stamp is None:
        text = str(value or "")[:10]
        return text or None
    return stamp.date().isoformat()


def pr_key(row):
    """The join key for a ledger row: the PR url, else repo#pr.

    Both ledgers carry `pr_url`; a row without one still joins when it
    carries a repo and a PR number.  The url is the primary key because
    the url is what the two ledgers actually share.
    """
    url = str(row.get("pr_url") or "").strip().rstrip("/")
    if url:
        return "url:" + url
    repo = str(row.get("repo") or "").strip()
    number = row.get("pr")
    if repo and number not in (None, ""):
        return f"{repo}#{number}"
    return ""


def provider_of(row):
    """The stratum's provider for a worker row.

    `providers_used` may list several providers (a failover run); the
    stratum uses the primary one, and the full list stays on the PR
    row so a failover is visible.
    """
    for field in ("provider", "model"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    used = str(row.get("providers_used") or "").strip()
    if used:
        first = used.split(",")[0].strip()
        return first.split("/")[0].strip() or UNKNOWN
    return UNKNOWN


def _parse_row(line):
    """One JSONL line -> a dict, or None when the line is not usable."""
    text = line.strip()
    if not text:
        return None
    try:
        row = json.loads(text)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def load_rows(path):
    """Read a JSONL ledger; unparseable lines are skipped, not fatal."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            row = _parse_row(line)
            if row is not None:
                rows.append(row)
    return rows


def _median(values):
    return statistics.median(values) if values else None


def _mean(values):
    return round(statistics.fmean(values), 1) if values else None


def _pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


# --------------------------------------------------------------------------
# the join
# --------------------------------------------------------------------------
def build_pr_rows(worker_rows, shepherd_rows, shepherd_first_day=None):
    """One lifecycle row per distinct PR, from both ledgers.

    Every distinct PR in either ledger gets a row.  A PR that only the
    shepherd knows (no worker row) and a PR opened before the shepherd
    ledger starts both carry flags instead of being dropped.
    """
    gap_start = shepherd_first_day or SHEPHERD_LEDGER_START
    workers: dict[str, list] = defaultdict(list)
    shepherd: dict[str, list] = defaultdict(list)
    for row in worker_rows:
        key = pr_key(row)
        if key:
            workers[key].append(row)
    for row in shepherd_rows:
        key = pr_key(row)
        if key:
            shepherd[key].append(row)

    rows = []
    for key in sorted(set(workers) | set(shepherd)):
        wrows = sorted(workers.get(key, []),
                       key=lambda r: str(r.get("ts") or ""))
        srows = sorted(shepherd.get(key, []),
                       key=lambda r: str(r.get("ts") or ""))
        flags = []
        opened_at = None
        opened_day = None
        tokens_in = 0
        tokens_out = 0
        has_tokens = False
        tier = UNKNOWN
        provider = UNKNOWN
        providers_used = ""
        for wrow in wrows:
            if opened_at is None:
                opened_at = wrow.get("ts")
                opened_day = day_of(wrow.get("ts"))
            value = _num(wrow.get("tokens_in"))
            if value is not None:
                tokens_in += value
            value = _num(wrow.get("tokens_out"))
            if value is not None:
                tokens_out += value
                has_tokens = True
            if tier == UNKNOWN and wrow.get("tier"):
                tier = str(wrow["tier"])
            if provider == UNKNOWN:
                provider = provider_of(wrow)
            if wrow.get("providers_used"):
                providers_used = str(wrow["providers_used"])
        if len(wrows) > 1:
            flags.append("multiple_worker_runs")
        if not wrows:
            flags.append("no_worker_row")

        actions = Counter(str(s.get("action") or "other") for s in srows)
        action_days = {day_of(s.get("ts")) for s in srows}
        action_days.discard(None)
        merged_at = None
        for srow in srows:
            if str(srow.get("action") or "") in MERGE_ACTIONS:
                merged_at = srow.get("ts")
                break
        first_action = srows[0].get("ts") if srows else None
        for action, flag in sorted(FLAGGED_ACTIONS.items()):
            if actions.get(action):
                flags.append(flag)
        if any(str(s.get("merge_state") or "").upper() == "UNSTABLE"
               for s in srows):
            flags.append("unstable_merge")

        if opened_day and opened_day < gap_start:
            flags.append("shepherd_gap")
        if wrows and not srows:
            flags.append("no_shepherd_activity")

        if merged_at:
            outcome = "merged"
        elif actions.get("closed_unmerged"):
            outcome = "closed_unmerged"
        elif actions.get("needs_operator"):
            outcome = "needs_operator"
        elif actions.get("needs_alex"):
            outcome = "needs_alex"
        elif wrows:
            outcome = "open"
        else:
            outcome = "unknown"

        dwell_to_merge = _hours_between(opened_at, merged_at)
        dwell_to_action = _hours_between(opened_at, first_action)
        url = str(wrows[0].get("pr_url") or "") if wrows else ""
        if not url:
            for srow in srows:
                if srow.get("pr_url"):
                    url = str(srow["pr_url"])
                    break
        repo = ""
        number = None
        if wrows:
            repo = str(wrows[0].get("repo") or "")
            number = wrows[0].get("pr")
        else:
            repo = str(srows[0].get("repo") or "")
            number = srows[0].get("pr")
        rows.append({
            "type": "pr",
            "pr_key": key,
            "pr_url": url,
            "repo": repo,
            "pr": number,
            "tier": tier,
            "provider": provider,
            "providers_used": providers_used,
            "outcome": outcome,
            "opened": bool(wrows),
            "opened_at": opened_at,
            "opened_day": opened_day,
            "merged": bool(merged_at),
            "merged_at": merged_at,
            "merged_day": day_of(merged_at) if merged_at else None,
            "worker_runs": len(wrows),
            "tokens_in": tokens_in if has_tokens or wrows else None,
            "tokens_out": tokens_out if has_tokens else None,
            "shepherd_actions": len(srows),
            "shepherd_action_days": len(action_days),
            "shepherd_by_action": dict(sorted(actions.items())),
            "dwell_open_to_merge_hours": dwell_to_merge,
            "dwell_open_to_first_action_hours": dwell_to_action,
            "flags": sorted(flags),
        })
    return rows


def _hours_between(start, end):
    """Hours between two ledger timestamps, or None when not computable."""
    first = parse_ts(start)
    second = parse_ts(end)
    if first is None or second is None:
        return None
    return round((second - first).total_seconds() / 3600.0, 2)


def build_daily_rows(worker_rows, pr_rows):
    """One row per day per stratum (repo x tier x provider), plus a
    per-day total row.  Run-level numbers reuse trace_summary's own
    bucket statistics, so the semantics are identical."""
    strata: dict[tuple, list] = defaultdict(list)
    days: dict[str, list] = defaultdict(list)
    for row in worker_rows:
        day = day_of(row.get("ts")) or "(no date)"
        strata[(day, str(row.get("repo") or UNKNOWN),
                str(row.get("tier") or UNKNOWN),
                provider_of(row))].append(row)
        days[day].append(row)
    # A day on which only the shepherd acted (a merge, say) still gets a
    # per-day total row, so merged PRs are never invisible in the
    # daily rollup.
    for pr in pr_rows:
        if pr["merged"] and pr["merged_day"]:
            days.setdefault(pr["merged_day"], [])

    # Distinct PRs opened per stratum, and merged PRs per stratum/day
    # (a merged PR has no tier of its own -- it takes the tier of the
    # worker run that opened it, or "(unknown)" when it never joined).
    opened_prs: dict[tuple, set] = defaultdict(set)
    merged_prs: dict[tuple, set] = defaultdict(set)
    merged_tokens: dict[tuple, int] = defaultdict(int)
    for pr in pr_rows:
        key = (pr["opened_day"] or "(no date)", pr["repo"],
               pr["tier"], pr["provider"])
        if pr["opened"]:
            opened_prs[key].add(pr["pr_key"])
        if pr["merged"]:
            merged_key = (pr["merged_day"] or "(no date)", pr["repo"],
                          pr["tier"], pr["provider"])
            merged_prs[merged_key].add(pr["pr_key"])
            if pr["tokens_out"] is not None:
                merged_tokens[merged_key] += pr["tokens_out"]

    rows = []
    for (day, repo, tier, provider), bucket in sorted(strata.items()):
        key = (day, repo, tier, provider)
        stats = _bucket_stats(bucket)
        rows.append({
            "type": "daily",
            "scope": "stratum",
            "day": day,
            "repo": repo,
            "tier": tier,
            "provider": provider,
            **stats,
            "distinct_prs": len(opened_prs.get(key, ())),
            "merged_prs": len(merged_prs.get(key, ())),
            "tokens_out_merged": merged_tokens.get(key, 0),
            "runs_multi_provider": sum(
                1 for r in bucket
                if len(str(r.get("providers_used") or "").split(",")) > 1),
        })
    for day, bucket in sorted(days.items()):
        stats = _bucket_stats(bucket)
        merged_here = {pr["pr_key"] for pr in pr_rows
                       if pr["merged"] and pr["merged_day"] == day}
        rows.append({
            "type": "daily",
            "scope": "day_total",
            "day": day,
            "repo": "(all)",
            "tier": "(all)",
            "provider": "(all)",
            **stats,
            "distinct_prs": len({pr["pr_key"] for pr in pr_rows
                                 if pr["opened"] and pr["opened_day"] == day}),
            "merged_prs": len(merged_here),
            "tokens_out_merged": sum(
                pr["tokens_out"] or 0 for pr in pr_rows
                if pr["merged"] and pr["merged_day"] == day),
            "runs_multi_provider": sum(
                1 for r in bucket
                if len(str(r.get("providers_used") or "").split(",")) > 1),
        })
    return rows


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------
def summarize(worker_rows, shepherd_rows, pr_rows, daily_rows,
              worker_ledger, shepherd_ledger, state_dir):
    """The numbers: the spec's section 2 baseline, regenerated."""
    overall = _bucket_stats(worker_rows)
    merged = [pr for pr in pr_rows if pr["merged"]]
    joined = [pr for pr in merged if pr["opened"]]
    unjoinable = [pr for pr in merged if not pr["opened"]]
    merged_tokens = [pr["tokens_out"] for pr in joined
                     if pr["tokens_out"] is not None]
    action_days = [pr["shepherd_action_days"] for pr in merged]
    dwell = [pr["dwell_open_to_merge_hours"] for pr in joined
             if pr["dwell_open_to_merge_hours"] is not None]
    grades = [r for r in worker_rows if _num(r.get("grade")) is not None]
    shepherd_days = sorted({day_of(r.get("ts")) for r in shepherd_rows}
                            - {None})
    by_action = Counter(str(r.get("action") or "other")
                         for r in shepherd_rows)
    flag_counts = Counter(flag for pr in pr_rows for flag in pr["flags"])

    baseline = {
        "worker_runs": overall["runs"],
        "pr_opened": overall["pr_opened"],
        "pr_opened_rate": overall["pr_rate"],
        "tokens_out_per_run_median": overall["tokens_out_median"],
        "tokens_out_per_run_p90": overall["tokens_out_p90"],
        "tokens_out_per_run_n": len([
            r for r in worker_rows if _num(r.get("tokens_out"))]),
        "grade_coverage": len(grades),
        "grade_coverage_rate": (round(len(grades) / len(worker_rows), 3)
                                 if worker_rows else None),
        "distinct_prs_opened": len({pr["pr_key"] for pr in pr_rows
                                    if pr["opened"]}),
        "distinct_merged_prs": len(merged),
        "merged_prs_joinable": len(joined),
        "merged_prs_unjoinable": len(unjoinable),
        "tokens_out_per_merged_pr_median": _median(merged_tokens),
        "tokens_out_per_merged_pr_mean": _mean(merged_tokens),
        "tokens_out_per_merged_pr_p90": _pct(merged_tokens, 0.90),
        "tokens_out_per_merged_pr_n": len(merged_tokens),
        "shepherd_rows": len(shepherd_rows),
        "shepherd_ledger_start": shepherd_days[0] if shepherd_days else None,
        "shepherd_action_days_per_merged_pr_median": _median(action_days),
        "dwell_open_to_merge_hours_median": _median(dwell),
    }

    if overall["runs"]:
        merged_median = baseline["tokens_out_per_merged_pr_median"]
        merged_txt = (f"{merged_median:,.0f}" if merged_median is not None
                      else "—")
        headline = (
            f"{overall['runs']} runs, {overall['pr_opened']} PRs opened "
            f"({overall['pr_rate'] * 100:.1f}% PR rate), "
            f"{baseline['distinct_merged_prs']} distinct merged PRs "
            f"({baseline['merged_prs_joinable']} joined, "
            f"{baseline['merged_prs_unjoinable']} unjoinable), "
            f"median {merged_txt} tokens_out per merged PR, "
            f"{len(pr_rows)} PR lifecycle rows, "
            f"{len(daily_rows)} daily rows")
    else:
        headline = "no worker runs in the ledger"

    return {
        "as_of": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state_dir": state_dir,
        "worker_ledger": worker_ledger,
        "shepherd_ledger": shepherd_ledger,
        "worker": overall,
        "shepherd": {
            "rows": len(shepherd_rows),
            "by_action": dict(by_action.most_common()),
            "first_day": shepherd_days[0] if shepherd_days else None,
            "last_day": shepherd_days[-1] if shepherd_days else None,
            "ledger_start": baseline["shepherd_ledger_start"],
        },
        "prs": {
            "lifecycle_rows": len(pr_rows),
            "distinct_prs_opened": baseline["distinct_prs_opened"],
            "distinct_merged_prs": baseline["distinct_merged_prs"],
            "merged_prs_joinable": baseline["merged_prs_joinable"],
            "merged_prs_unjoinable": baseline["merged_prs_unjoinable"],
            "tokens_out_per_merged_pr_median":
                baseline["tokens_out_per_merged_pr_median"],
            "tokens_out_per_merged_pr_mean":
                baseline["tokens_out_per_merged_pr_mean"],
            "action_days_per_merged_pr_median":
                baseline["shepherd_action_days_per_merged_pr_median"],
            "flag_counts": dict(sorted(flag_counts.items())),
        },
        "baseline": baseline,
        "rows": {"pr": len(pr_rows), "daily": len(daily_rows)},
        "headline": headline,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def default_state_dir():
    """The state dir the deployment config points at (may be None)."""
    ledger = default_ledger_path()
    if not ledger:
        return None
    return os.path.dirname(os.path.abspath(os.path.expanduser(ledger)))


def write_rollup(path, rows):
    """Write the rollup cache: one JSON object per line, atomically."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(row, sort_keys=True, default=str) + "\n"
                      for row in rows)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", default=None,
                    help="state dir holding the ledgers and the rollup "
                         "(default: deployment config state dir)")
    ap.add_argument("--worker-ledger", default=None,
                    help="path to worker_ledger.jsonl "
                         "(default: <state dir>/worker_ledger.jsonl)")
    ap.add_argument("--shepherd-ledger", default=None,
                    help="path to shepherd_ledger.jsonl "
                         "(default: <state dir>/shepherd_ledger.jsonl)")
    ap.add_argument("--out", default=None,
                    help="rollup output path, or '-' for stdout "
                         "(default: <state dir>/metrics_rollup.jsonl)")
    ap.add_argument("--since", default=None,
                    help="only rows with ts >= YYYY-MM-DD")
    ap.add_argument("--until", default=None,
                    help="only rows with ts <= YYYY-MM-DD (reproduce the "
                         "spec's as-of snapshot)")
    ap.add_argument("--json", action="store_true",
                    help="print the full summary as JSON")
    args = ap.parse_args(argv)

    state_dir = args.state_dir or default_state_dir()
    worker_ledger = args.worker_ledger or (
        os.path.join(state_dir, "worker_ledger.jsonl") if state_dir else None)
    shepherd_ledger = args.shepherd_ledger or (
        os.path.join(state_dir, "shepherd_ledger.jsonl") if state_dir else None)
    if not worker_ledger:
        print("metrics_rollup: no ledger path (no deployment config)",
              file=sys.stderr)
        return 2
    if not os.path.exists(worker_ledger):
        print(f"worker ledger not found: {worker_ledger}", file=sys.stderr)
        return 2

    worker_rows = load_rows(worker_ledger)
    shepherd_rows = []
    shepherd_missing = False
    if shepherd_ledger and os.path.exists(shepherd_ledger):
        shepherd_rows = load_rows(shepherd_ledger)
    else:
        shepherd_missing = True

    def _since(row):
        return not args.since or str(row.get("ts") or "")[:10] >= args.since

    def _until(row):
        return not args.until or str(row.get("ts") or "")[:10] <= args.until

    worker_rows = [r for r in worker_rows if _since(r) and _until(r)]
    shepherd_rows = [r for r in shepherd_rows if _since(r) and _until(r)]

    shepherd_days = sorted({day_of(r.get("ts")) for r in shepherd_rows}
                            - {None})
    shepherd_first_day = shepherd_days[0] if shepherd_days else None
    pr_rows = build_pr_rows(worker_rows, shepherd_rows, shepherd_first_day)
    daily_rows = build_daily_rows(worker_rows, pr_rows)
    summary = summarize(worker_rows, shepherd_rows, pr_rows, daily_rows,
                        worker_ledger, shepherd_ledger, state_dir)
    summary["window"] = {"since": args.since, "until": args.until}
    summary["as_of_date"] = (args.until
                             or datetime.now(UTC).strftime("%Y-%m-%d"))
    if shepherd_missing:
        summary["shepherd"]["missing"] = True
        summary["baseline"]["shepherd_ledger_start"] = None
    rows = pr_rows + daily_rows
    summary["rollup_rows"] = len(rows)

    out_path = args.out
    if out_path is None:
        if not state_dir:
            print("metrics_rollup: no state dir for the rollup file",
                  file=sys.stderr)
            return 2
        out_path = os.path.join(state_dir, "metrics_rollup.jsonl")
    if out_path == "-":
        for row in rows:
            print(json.dumps(row, sort_keys=True, default=str))
        summary["rollup"] = "(stdout)"
    else:
        write_rollup(out_path, rows)
        summary["rollup"] = out_path

    if args.json:
        # With `--out -` stdout carries the rollup rows only, so the
        # summary goes to stderr.
        json_stream = sys.stderr if out_path == "-" else sys.stdout
        print(json.dumps(summary, indent=1, default=str), file=json_stream)
    else:
        # With `--out -` stdout carries the rollup rows only, so the
        # human summary goes to stderr.
        stream = sys.stderr if out_path == "-" else sys.stdout
        base = summary["baseline"]
        rate = base["pr_opened_rate"]
        rate_txt = f"{rate * 100:.1f}%" if rate is not None else "—"
        median_run = base["tokens_out_per_run_median"]
        median_run_txt = f"{median_run:,}" if median_run is not None else "—"
        median_merged = base["tokens_out_per_merged_pr_median"]
        median_merged_txt = (f"{median_merged:,.0f}"
                             if median_merged is not None else "—")
        print(summary["headline"], file=stream)
        print(f"  as of: {summary['as_of_date']}"
              + (f" (window since {args.since})" if args.since else ""),
              file=stream)
        print(f"  worker ledger: {worker_ledger} "
              f"({base['worker_runs']} runs)", file=stream)
        if shepherd_missing:
            print(f"  shepherd ledger: missing ({shepherd_ledger}) -- "
                  "every PR is flagged; no shepherd coverage", file=stream)
        else:
            print(f"  shepherd ledger: {shepherd_ledger} "
                  f"({summary['shepherd']['rows']} rows, from "
                  f"{summary['shepherd']['first_day']})", file=stream)
        print(f"  PR-opened rate: {rate_txt} "
              f"({base['pr_opened']}/{base['worker_runs']})", file=stream)
        print(f"  tokens_out per run: median {median_run_txt} "
              f"(n={base['tokens_out_per_run_n']}), p90 "
              f"{base['tokens_out_per_run_p90']}", file=stream)
        print(f"  distinct PRs merged: {base['distinct_merged_prs']} "
              f"({base['merged_prs_joinable']} joinable, "
              f"{base['merged_prs_unjoinable']} unjoinable)", file=stream)
        print(f"  tokens_out per merged PR: median {median_merged_txt} "
              f"(n={base['tokens_out_per_merged_pr_n']}), mean "
              f"{base['tokens_out_per_merged_pr_mean']}", file=stream)
        print(f"  flags: {summary['prs']['flag_counts']}", file=stream)
        print(f"  rollup: {summary['rollup']} ({summary['rollup_rows']} rows)", file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
