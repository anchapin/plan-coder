#!/usr/bin/env python3
"""Summarize the plan-coder metrics ledger (trace-grader spike, 2026-11-07).

Reads worker_ledger.jsonl — one JSON object per collected worker — and
prints totals per day / repo / tier / issue: runs, PR-opened rate,
tokens in/out, steps, hit-cap rate, gate triggers, top-burn issues, and
repeat failures. Read-only: never writes to the ledger.

The ledger has no cycle id, so per-day is the grouping available;
a supervisor cycle is ~2h, so a day holds many cycles. Gate-trigger
strings are free-form supervisor annotations — only the three machine
gates (empty_diff, midrun_no_edit, worker_hit_cap) are counted as
gates; anything else is reported as an annotation.

Usage:
    python3 bin/trace_summary.py [--ledger PATH] [--since YYYY-MM-DD]
                                 [--json] [--top N]
    --ledger defaults to the deployment config's state dir
    (same search order as agent.py: $PLAN_CODER_CONFIG, then
    config/deployment.local.json next to the repo root, then
    ~/.plan-coder/deployment.json).
Exit 0 on success; exit 2 when the ledger cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

MACHINE_GATES = ("empty_diff", "midrun_no_edit", "worker_hit_cap")


def _load_deployment_config() -> dict:
    candidates = []
    env_path = os.environ.get("PLAN_CODER_CONFIG")
    if env_path:
        candidates.append(env_path)
    repo_root = os.path.dirname(os.path.dirname(
        os.path.realpath(os.path.abspath(__file__))))
    candidates.append(os.path.join(
        repo_root, "config", "deployment.local.json"))
    candidates.append(os.path.expanduser("~/.plan-coder/deployment.json"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001 - config is a convenience
            continue
    return {}


def default_ledger_path() -> str | None:
    cfg = _load_deployment_config()
    state_dir = ((cfg.get("state") or {}).get("dir")
                 or "~/.plan-coder/state")
    return os.path.join(os.path.expanduser(state_dir),
                         "worker_ledger.jsonl")


def _pct(sorted_xs: list, q: float):
    if not sorted_xs:
        return None
    return sorted_xs[min(len(sorted_xs) - 1, int(q * len(sorted_xs)))]


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _parse_steps(value):
    """'43/60' -> (43, 60); 'unknown' or missing -> (None, None)."""
    if not isinstance(value, str) or "/" not in value:
        return None, None
    used, _, maximum = value.partition("/")
    try:
        return int(used), int(maximum)
    except ValueError:
        return None, None


def _parse_gates(value) -> dict:
    """'empty_diff=1, midrun_no_edit=0' -> {'empty_diff': 1, ...}."""
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


def _bucket_stats(rows: list) -> dict:
    """Aggregate one bucket of ledger rows. Rows with zero/missing
    tokens (split_suggested / escalated fallbacks) are counted but
    excluded from the token/step distributions."""
    n = len(rows)
    pr = sum(1 for r in rows if r.get("outcome") == "pr_opened")
    failed = sum(1 for r in rows
                 if str(r.get("outcome") or "").startswith("failed"))
    tok_in = sorted(_num(r.get("tokens_in")) for r in rows
                    if _num(r.get("tokens_in")))
    tok_out = sorted(_num(r.get("tokens_out")) for r in rows
                     if _num(r.get("tokens_out")))
    steps = []
    hit_cap = 0
    capped_runs = 0
    for r in rows:
        used, maximum = _parse_steps(r.get("steps"))
        if used is not None:
            steps.append(used)
        if maximum:
            capped_runs += 1
            if used is not None and used >= maximum:
                hit_cap += 1
    gates: Counter = Counter()
    annotations: Counter = Counter()
    for r in rows:
        for key, val in _parse_gates(r.get("gate_triggers")).items():
            if isinstance(val, int) and val:
                if key in MACHINE_GATES:
                    gates[key] += 1
                else:
                    annotations[key] += 1
    return {
        "runs": n,
        "pr_opened": pr,
        "pr_rate": round(pr / n, 3) if n else None,
        "failed": failed,
        "tokens_in_total": sum(tok_in),
        "tokens_in_median": statistics.median(tok_in) if tok_in else None,
        "tokens_in_p90": _pct(tok_in, 0.90),
        "tokens_out_total": sum(tok_out),
        "tokens_out_median": statistics.median(tok_out) if tok_out else None,
        "tokens_out_p90": _pct(tok_out, 0.90),
        "tokens_out_p99": _pct(tok_out, 0.99),
        "tokens_out_max": max(tok_out) if tok_out else None,
        "steps_median": statistics.median(steps) if steps else None,
        "hit_cap": hit_cap,
        "hit_cap_runs": capped_runs,
        "hit_cap_rate": round(hit_cap / capped_runs, 3) if capped_runs else None,
        "gates_nonzero": dict(gates),
        "annotations_nonzero": dict(annotations),
    }


def summarize(rows: list, top_n: int = 10) -> dict:
    overall = _bucket_stats(rows)
    by_day, by_repo, by_tier = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in rows:
        by_day[(r.get("ts") or "")[:10] or "(no date)"].append(r)
        by_repo[r.get("repo") or "(no repo)"].append(r)
        by_tier[r.get("tier") or "(no tier)"].append(r)
    by_issue: dict = defaultdict(list)
    for r in rows:
        by_issue[(r.get("repo"), r.get("issue"))].append(r)
    repeat_failures = []
    for (repo, issue), rs in sorted(
            by_issue.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        n_failed = sum(1 for r in rs
                       if str(r.get("outcome") or "").startswith("failed"))
        n_pr = sum(1 for r in rs if r.get("outcome") == "pr_opened")
        if n_failed >= 2 and n_pr == 0:
            repeat_failures.append({
                "repo": repo, "issue": issue, "attempts": len(rs),
                "failed": n_failed,
                "tokens_out_total": sum(_num(r.get("tokens_out")) or 0
                                        for r in rs),
            })
    top_burn = sorted(
        rows,
        key=lambda r: (_num(r.get("tokens_in")) or 0)
        + (_num(r.get("tokens_out")) or 0),
        reverse=True)[:top_n]
    if overall["runs"]:
        median_out = overall["tokens_out_median"]
        median_txt = f"{median_out:,}" if median_out is not None else "—"
        headline = (
            f"{overall['runs']} runs, {overall['pr_opened']} PRs opened "
            f"({overall['pr_rate'] * 100:.0f}% PR rate), "
            f"{overall['tokens_out_total']:,} tokens out total "
            f"(median {median_txt} per run), "
            f"hit-cap {overall['hit_cap']}/{overall['hit_cap_runs']}, "
            f"{len(repeat_failures)} issue(s) with 2+ failures and no PR")
    else:
        headline = "no runs in the ledger"
    return {
        "rows": len(rows),
        "overall": overall,
        "by_day": {d: _bucket_stats(rs) for d, rs in sorted(by_day.items())},
        "by_repo": {k: _bucket_stats(rs) for k, rs in sorted(by_repo.items())},
        "by_tier": {k: _bucket_stats(rs) for k, rs in sorted(by_tier.items())},
        "top_burn": [{
            "repo": r.get("repo"), "issue": r.get("issue"),
            "outcome": r.get("outcome"), "tier": r.get("tier"),
            "tokens_in": r.get("tokens_in"),
            "tokens_out": r.get("tokens_out"),
            "steps": r.get("steps"),
        } for r in top_burn],
        "repeat_failures": repeat_failures,
        "headline": headline,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default=None,
                    help="path to worker_ledger.jsonl "
                         "(default: deployment config state dir)")
    ap.add_argument("--since", default=None,
                    help="only rows with ts >= YYYY-MM-DD")
    ap.add_argument("--json", action="store_true",
                    help="print the full summary as JSON")
    ap.add_argument("--top", type=int, default=10,
                    help="how many top-burn runs to list")
    args = ap.parse_args(argv)
    path = args.ledger or default_ledger_path()
    if not path or not os.path.exists(path):
        print(f"ledger not found: {path}", file=sys.stderr)
        return 2
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if args.since and (row.get("ts") or "")[:10] < args.since:
                continue
            rows.append(row)
    result = summarize(rows, top_n=args.top)
    result["ledger"] = path
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        def _fmt(value):
            return f"{value:,}" if value is not None else "—"

        print(result["headline"])
        print(f"ledger: {path} ({result['rows']} rows)")
        for repo, stats in result["by_repo"].items():
            print(f"  {repo}: {stats['runs']} runs, "
                  f"{stats['pr_opened']} PRs "
                  f"({stats['pr_rate'] * 100:.0f}%), "
                  f"tokens out median {_fmt(stats['tokens_out_median'])}, "
                  f"hit-cap {stats['hit_cap']}/{stats['hit_cap_runs']}")
        for item in result["top_burn"][:5]:
            print(f"  top burn: {item['repo']}#{item['issue']} "
                  f"{item['outcome']} "
                  f"{_fmt(item['tokens_in'])} in / "
                  f"{_fmt(item['tokens_out'])} out "
                  f"({item['steps']})")
        for item in result["repeat_failures"]:
            print(f"  repeat failure: {item['repo']}#{item['issue']} "
                  f"{item['failed']} failed / {item['attempts']} attempts, "
                  f"no PR opened")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
