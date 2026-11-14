#!/usr/bin/env python3
"""Read-only dashboard over the plan-coder state dir (issue #5, MVP).

The dashboard is a *presentation layer* over the state directory the
harness already writes.  It reads `workers.json`, `shepherd.json`,
`worker_ledger.jsonl` and `shepherd_ledger.jsonl` and renders one
self-contained HTML page (no CDN, no network calls, no external
assets) with two panels:

  * **Live view** -- workers in flight per repo (from `workers.json`),
    the PRs the shepherd tracks / has merged / has flagged
    (from `shepherd.json`), and the tail of `worker_ledger.jsonl` as a
    live activity feed.
  * **PR metrics** -- PR-opened rate (the same `pr_opened / runs`
    ratio `bin/trace_summary.py` computes), merges (including the
    count of merges whose `merge_state` was `UNSTABLE`), reruns and
    fix attempts per distinct PR (the same counts
    `bin/shepherd_ledger.py summary` computes), failure classes, and
    token burn against the configured cycle budget.

Design constraints (ROADMAP.md, "Dashboard design"):

  1. Read-only.  The dashboard opens the state dir and reads it.  It
     never writes to `workers.json`, `shepherd.json` or any ledger,
     and never repairs them.
  2. No new storage.  Every number is derived on the fly from files
     that already exist.
  3. No hosted service, no credentials.  The dashboard runs locally
     against a local state dir, talks to nothing else, never calls
     `gh` and never embeds operator identifiers.
  4. Same aggregation semantics as the CLIs.  Where a number already
     exists in `bin/trace_summary.py` or `bin/shepherd_ledger.py
     summary`, the dashboard shows that number, not a second one.
  5. Graceful on missing files.  A missing ledger renders an empty
     panel with a "no rows yet" note -- never a crash.

Usage:
    python3 bin/dashboard.py [--state-dir DIR] [--config PATH]
                             [--since YYYY-MM-DD] [--tail N]
                             [--out FILE] [--json]

`--state-dir` defaults to the deployment config's `state.dir`
(same search order as `bin/agent.py` and `bin/trace_summary.py`:
`$PLAN_CODER_CONFIG`, then `config/deployment.local.json` beside the
repo root, then `~/.plan-coder/deployment.json`).

With no `--out` the HTML page is written to stdout.  `--out` writes
the same page to a file; the file must not live inside the state dir
(the dashboard never writes to the state dir).

Exit 0 on success; exit 2 when the state dir cannot be used or the
requested output path is not writable.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shepherd_ledger import summarize as shepherd_ledger_summary  # noqa: E402
from trace_summary import summarize as trace_summary  # noqa: E402

# Worker statuses that mean "in flight" (the run is still burning).
IN_FLIGHT_STATUSES = ("working", "collecting")
# Timestamps we accept on a shepherd.json tracked-PR entry when
# computing the age of an open PR.
AGE_KEYS = ("added_at", "tracked_at", "first_seen", "opened_at",
            "started_at", "ts")
# The supervisor's cycle is ~2h (crons.backlog-supervisor), so cycle
# burn is bucketed into 2h windows.  The ledger carries no cycle id,
# so this is an approximation and is labelled as such in the page.
CYCLE_SECONDS = 2 * 60 * 60

WORKERS_FILE = "workers.json"
SHEPHERD_FILE = "shepherd.json"
WORKER_LEDGER_FILE = "worker_ledger.jsonl"
SHEPHERD_LEDGER_FILE = "shepherd_ledger.jsonl"


# ---------------------------------------------------------------------
# config / path resolution
# ---------------------------------------------------------------------
def repo_root() -> str:
    return os.path.dirname(
        os.path.dirname(os.path.realpath(os.path.abspath(__file__))))


def load_deployment_config(config_path: str | None = None) -> dict:
    """Load the deployment config (read-only).

    Search order is the one the harness uses everywhere:
    `$PLAN_CODER_CONFIG`, then `config/deployment.local.json` beside
    the repo root, then `~/.plan-coder/deployment.json`.  An explicit
    `--config` path wins over all of them.  Only the cycle token
    budget is ever read out of it -- no credentials, no operator
    identifiers.
    """
    candidates: list[str] = []
    if config_path:
        candidates.append(config_path)
    else:
        env_path = os.environ.get("PLAN_CODER_CONFIG")
        if env_path:
            candidates.append(env_path)
        candidates.append(os.path.join(repo_root(), "config",
                                       "deployment.local.json"))
        candidates.append(os.path.expanduser("~/.plan-coder/deployment.json"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def resolve_state_dir(config: dict, state_dir: str | None) -> str:
    if state_dir:
        return os.path.abspath(os.path.expanduser(state_dir))
    configured = (config.get("state") or {}).get("dir") \
        or "~/.plan-coder/state"
    return os.path.abspath(os.path.expanduser(configured))


def cycle_budget(config: dict) -> int:
    """`workers.cycle_tokens_out_budget` (0 = the guard is off)."""
    value = (config.get("workers") or {}).get("cycle_tokens_out_budget")
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


# ---------------------------------------------------------------------
# state readers (read-only, tolerant of missing/broken files)
# ---------------------------------------------------------------------
def read_json(path: str) -> dict | None:
    """Read a JSON object; None when the file is missing or unusable."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_jsonl(path: str) -> list[dict]:
    """Read an append-only JSONL ledger; unparsable lines are skipped."""
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def file_state(path: str) -> dict:
    """Describe one state file for the page's provenance table."""
    exists = os.path.exists(path)
    return {"name": os.path.basename(path), "path": path, "present": exists}


# ---------------------------------------------------------------------
# panel 1 -- live view
# ---------------------------------------------------------------------
def worker_entries(workers: dict | None) -> list[dict]:
    """Normalise `workers.json` into a list of worker entries."""
    if not workers:
        return []
    raw = workers.get("workers")
    if not isinstance(raw, (dict, list)):
        raw = workers
    entries: list[dict] = []
    if isinstance(raw, dict):
        for run_id, entry in raw.items():
            item = dict(entry) if isinstance(entry, dict) else {}
            item.setdefault("run_id", run_id)
            entries.append(item)
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                entries.append(dict(entry))
    return entries


def tracked_prs(shepherd: dict | None) -> list[dict]:
    """Normalise `shepherd.json`'s `tracked_prs` into a list of PRs.

    The supervisor appends PR URLs to an array; the shepherd's own
    bookkeeping keeps a per-PR object.  Both shapes are accepted.
    """
    if not shepherd:
        return []
    tracked = shepherd.get("tracked_prs")
    out: list[dict] = []
    if isinstance(tracked, dict):
        for key, value in tracked.items():
            item = dict(value) if isinstance(value, dict) else {}
            item.setdefault("pr_url", key)
            out.append(item)
    elif isinstance(tracked, list):
        for value in tracked:
            if isinstance(value, str):
                out.append({"pr_url": value})
            elif isinstance(value, dict):
                item = dict(value)
                item.setdefault("pr_url", str(item.get("url") or ""))
                out.append(item)
    return out


def pr_number(value: str) -> str:
    """Trailing PR number of a PR URL (or the value itself)."""
    text = str(value or "").strip()
    if not text:
        return ""
    tail = text.rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else text


def parse_ts(value) -> float | None:
    """Parse an ISO-8601 UTC timestamp into epoch seconds."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    try:
        return parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def open_pr_age_hours(entry: dict, now: float) -> float | None:
    """Age of a tracked PR in hours, when the entry carries a timestamp."""
    for key in AGE_KEYS:
        stamp = parse_ts(entry.get(key))
        if stamp is not None:
            return max(0.0, (now - stamp) / 3600.0)
    return None


def build_live_view(state_dir: str, now: float, tail: int) -> dict:
    workers_path = os.path.join(state_dir, WORKERS_FILE)
    shepherd_path = os.path.join(state_dir, SHEPHERD_FILE)
    ledger_path = os.path.join(state_dir, WORKER_LEDGER_FILE)

    workers_raw = read_json(workers_path)
    shepherd_raw = read_json(shepherd_path)
    ledger_rows = read_jsonl(ledger_path)

    entries = worker_entries(workers_raw)
    by_status: Counter = Counter()
    by_repo: Counter = Counter()
    in_flight_rows: list[dict] = []
    tokens_in_flight = 0
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        by_status[status] += 1
        if status in IN_FLIGHT_STATUSES:
            repo = str(entry.get("repo") or "(no repo)")
            by_repo[repo] += 1
            tokens = entry.get("tokens")
            if isinstance(tokens, (int, float)) and not isinstance(tokens, bool):
                tokens_in_flight += int(tokens)
            in_flight_rows.append({
                "run_id": entry.get("run_id"),
                "repo": repo,
                "issue": entry.get("issue"),
                "status": status,
                "branch": entry.get("branch"),
                "workdir": entry.get("workdir"),
                "started_at": entry.get("started_at"),
                "pr": entry.get("pr"),
                "tokens": tokens,
                "provider_used": entry.get("provider_used"),
                "note": entry.get("note"),
            })

    tracked = tracked_prs(shepherd_raw)
    tracked_rows = []
    for item in tracked:
        url = str(item.get("pr_url") or item.get("url") or "")
        tracked_rows.append({
            "pr": pr_number(url),
            "pr_url": url,
            "repo": item.get("repo"),
            "rerun_count": item.get("rerun_count"),
            "ci_fix_attempts": item.get("ci_fix_attempts"),
            "jittest_catch": bool(item.get("jittest:catch")
                                  or item.get("jittest_catch")),
            "age_hours": open_pr_age_hours(item, now),
        })

    pending = (shepherd_raw or {}).get("pending_trigger") or {}
    last_trigger = (shepherd_raw or {}).get("last_trigger") or {}
    seen_merged = (shepherd_raw or {}).get("seen_merged") or []
    catch_prs = (shepherd_raw or {}).get("jittest_catch_prs") or []

    activity = []
    for row in ledger_rows[-tail:] if tail > 0 else []:
        activity.append({
            "ts": row.get("ts"),
            "repo": row.get("repo"),
            "issue": row.get("issue"),
            "run_id": row.get("run_id"),
            "tier": row.get("tier"),
            "outcome": row.get("outcome"),
            "tokens_in": row.get("tokens_in"),
            "tokens_out": row.get("tokens_out"),
            "pr_url": row.get("pr_url"),
            "grade": row.get("grade"),
        })

    return {
        "state_dir": state_dir,
        "workers": {
            "present": workers_raw is not None,
            "total": len(entries),
            "by_status": dict(sorted(by_status.items())),
            "in_flight": len(in_flight_rows),
            "in_flight_by_repo": dict(sorted(by_repo.items())),
            "tokens_in_flight": tokens_in_flight,
            "rows": in_flight_rows,
        },
        "shepherd": {
            "present": shepherd_raw is not None,
            "tracked_prs": len(tracked_rows),
            "tracked": tracked_rows,
            "pending_trigger": pending,
            "last_trigger": last_trigger,
            "seen_merged": len(seen_merged),
            "jittest_catch_prs": len(catch_prs),
        },
        "activity": activity,
        "activity_rows": len(ledger_rows),
        "activity_present": os.path.exists(ledger_path),
        "files": [file_state(workers_path), file_state(shepherd_path),
                  file_state(ledger_path)],
    }


# ---------------------------------------------------------------------
# panel 2 -- PR metrics
# ---------------------------------------------------------------------
def outcome_class(outcome) -> str:
    """Outcome vocabulary: `pr_opened`, `failed:<class>`, `stale`, ..."""
    text = str(outcome or "").strip()
    if not text:
        return "unknown"
    if text.startswith("failed"):
        _, _, cls = text.partition(":")
        return "failed:" + (cls.strip() or "unknown")
    return text


def cycle_buckets(rows: list[dict]) -> list[dict]:
    """Group ledger rows into ~2h cycle windows (the ledger has no cycle id)."""
    buckets: dict[int, dict] = {}
    for row in rows:
        stamp = parse_ts(row.get("ts"))
        if stamp is None:
            continue
        key = int(stamp // CYCLE_SECONDS) * CYCLE_SECONDS
        bucket = buckets.setdefault(key, {"start": key, "tokens_out": 0,
                                          "tokens_in": 0, "runs": 0})
        bucket["runs"] += 1
        for field in ("tokens_in", "tokens_out"):
            value = row.get(field)
            if isinstance(value, (int, float)) \
                    and not isinstance(value, bool):
                bucket[field] += int(value)
    return [buckets[key] for key in sorted(buckets)]


def build_pr_metrics(state_dir: str, config: dict,
                     since: str | None = None) -> dict:
    ledger_path = os.path.join(state_dir, WORKER_LEDGER_FILE)
    shepherd_ledger_path = os.path.join(state_dir, SHEPHERD_LEDGER_FILE)

    rows = [row for row in read_jsonl(ledger_path)
            if not since or str(row.get("ts") or "")[:10] >= since]
    # Same aggregation as `bin/trace_summary.py` (pr_rate, token
    # totals, gate triggers, hit-cap rate).
    worker_summary = trace_summary(rows)
    overall = worker_summary["overall"]

    outcomes: Counter = Counter()
    failure_classes: Counter = Counter()
    for row in rows:
        cls = outcome_class(row.get("outcome"))
        outcomes[cls] += 1
        if cls.startswith("failed:"):
            failure_classes[cls.split(":", 1)[1]] += 1

    # Same aggregation as `bin/shepherd_ledger.py summary`.
    if os.path.exists(shepherd_ledger_path):
        shepherd_summary = shepherd_ledger_summary(shepherd_ledger_path,
                                                    since)
    else:
        shepherd_summary = {
            "rows": 0, "by_action": {}, "by_outcome": {}, "by_repo": {},
            "merges": 0, "unstable_merges": 0,
            "needs_operator_distinct_prs": 0,
            "fix_attempt_distinct_prs": 0,
        }
    shepherd_rows = read_jsonl(shepherd_ledger_path)
    if since:
        shepherd_rows = [
            row for row in shepherd_rows
            if str(row.get("ts") or "")[:10] >= since
        ]

    flagged: set[tuple] = set()
    rerun_prs: set[tuple] = set()
    for row in shepherd_rows:
        action = str(row.get("action") or "other")
        key = (row.get("repo"), row.get("pr"))
        if action == "needs_operator":
            flagged.add(key)
        elif action == "rerun":
            rerun_prs.add(key)

    budget = cycle_budget(config)
    buckets = cycle_buckets(rows)
    max_bucket = max((b["tokens_out"] for b in buckets), default=0)

    return {
        "worker": {
            "ledger": ledger_path,
            "ledger_present": os.path.exists(ledger_path),
            "rows": len(rows),
            "runs": overall["runs"],
            "pr_opened": overall["pr_opened"],
            "pr_rate": overall["pr_rate"],
            "failed": overall["failed"],
            "tokens_in_total": overall["tokens_in_total"],
            "tokens_out_total": overall["tokens_out_total"],
            "tokens_out_median": overall["tokens_out_median"],
            "tokens_out_max": overall["tokens_out_max"],
            "hit_cap": overall["hit_cap"],
            "hit_cap_runs": overall["hit_cap_runs"],
            "hit_cap_rate": overall["hit_cap_rate"],
            "gates_nonzero": overall["gates_nonzero"],
            "by_repo": worker_summary["by_repo"],
        },
        "outcomes": dict(outcomes.most_common()),
        "failure_classes": dict(failure_classes.most_common()),
        "shepherd": {
            "ledger": shepherd_ledger_path,
            "ledger_present": os.path.exists(shepherd_ledger_path),
            "rows": shepherd_summary["rows"],
            "by_action": shepherd_summary["by_action"],
            "by_outcome": shepherd_summary["by_outcome"],
            "merges": shepherd_summary["merges"],
            "unstable_merges": shepherd_summary["unstable_merges"],
            "fix_attempt_distinct_prs":
                shepherd_summary["fix_attempt_distinct_prs"],
            "needs_operator_distinct_prs":
                shepherd_summary["needs_operator_distinct_prs"],
            "rerun_distinct_prs": len(rerun_prs),
            "flagged_prs": sorted(
                f"{repo}#{pr}" for repo, pr in flagged),
        },
        "burn": {
            "cycle_budget": budget,
            "budget_enabled": budget > 0,
            "cycles": buckets,
            "max_cycle_tokens_out": max_bucket,
            "max_cycle_pct": (round(max_bucket / budget, 3)
                              if budget else None),
            "cycle_seconds": CYCLE_SECONDS,
        },
        "files": [file_state(ledger_path), file_state(shepherd_ledger_path)],
    }


# ---------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------
def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fmt_num(value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,}"
    return "—"


def fmt_pct(value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value * 100:.0f}%"
    return "—"


def render_html(live: dict, metrics: dict, generated_at: str) -> str:
    """Render the whole page (one self-contained HTML file)."""
    parts: list[str] = []
    parts.append(
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<title>plan-coder dashboard</title>\n"
        "<style>\n"
        "body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
        "margin:1.5rem;background:#fbfbfd;color:#1c1c1e}\n"
        "h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:1.6rem}\n"
        "h3{font-size:.95rem;margin:1rem 0 .3rem}\n"
        "table{border-collapse:collapse;margin:.3rem 0;font-size:.85rem}\n"
        "th,td{border:1px solid #d7d7dc;padding:.25rem .5rem;text-align:left}\n"
        "th{background:#f0f0f4}\n"
        ".meta{color:#6b6b70;font-size:.8rem}\n"
        ".note{color:#6b6b70;font-style:italic}\n"
        ".panel{border:1px solid #d7d7dc;border-radius:6px;"
        "background:#fff;padding:.6rem 1rem;margin-bottom:1rem}\n"
        "</style>\n</head>\n<body>\n"
    )
    parts.append("<h1>plan-coder dashboard</h1>\n")
    parts.append(
        f"<p class=\"meta\">state dir: <code>{esc(live['state_dir'])}</code>"
        f" &middot; generated: {esc(generated_at)} (UTC)</p>\n")

    # --- panel 1 -----------------------------------------------------
    parts.append("<section class=\"panel\" id=\"live-view\">\n")
    parts.append("<h2>Panel 1 &mdash; Live view</h2>\n")
    parts.append(
        f"<p class=\"meta\">refresh cadence: 60s &middot; "
        f"generated: {esc(generated_at)} (UTC)</p>\n")
    workers = live["workers"]
    parts.append("<h3>Workers in flight</h3>\n")
    if not workers["present"]:
        parts.append(
            f"<p class=\"note\">no rows yet &mdash; {esc(WORKERS_FILE)} "
            "is not in the state dir yet.</p>\n")
    elif workers["total"] == 0:
        parts.append(
            f"<p class=\"note\">no rows yet &mdash; {esc(WORKERS_FILE)} "
            "holds no worker slots.</p>\n")
    else:
        parts.append(
            f"<p>{workers['in_flight']} in flight of "
            f"{workers['total']} worker slot(s); "
            f"{fmt_num(workers['tokens_in_flight'])} tokens spent by "
            "in-flight workers.</p>\n")
        parts.append("<table><tr><th>status</th><th>workers</th></tr>\n")
        for status, count in workers["by_status"].items():
            parts.append(
                f"<tr><td>{esc(status)}</td><td>{count}</td></tr>\n")
        parts.append("</table>\n")
        if workers["in_flight_by_repo"]:
            parts.append("<h3>In flight per repo</h3>\n")
            parts.append(
                "<table><tr><th>repo</th><th>workers in flight</th></tr>\n")
            for repo, count in workers["in_flight_by_repo"].items():
                parts.append(
                    f"<tr><td>{esc(repo)}</td><td>{count}</td></tr>\n")
            parts.append("</table>\n")
        if workers["rows"]:
            parts.append("<h3>Active workers</h3>\n")
            parts.append(
                "<table><tr><th>run</th><th>repo</th><th>issue</th>"
                "<th>status</th><th>branch</th><th>tokens</th>"
                "<th>started</th><th>provider</th><th>note</th></tr>\n")
            for row in workers["rows"]:
                parts.append(
                    "<tr>"
                    f"<td>{esc(row['run_id'])}</td>"
                    f"<td>{esc(row['repo'])}</td>"
                    f"<td>{esc(row['issue'])}</td>"
                    f"<td>{esc(row['status'])}</td>"
                    f"<td>{esc(row['branch'])}</td>"
                    f"<td>{fmt_num(row['tokens'])}</td>"
                    f"<td>{esc(row['started_at'])}</td>"
                    f"<td>{esc(row['provider_used'])}</td>"
                    f"<td>{esc(row['note'])}</td>"
                    "</tr>\n")
            parts.append("</table>\n")

    shepherd = live["shepherd"]
    parts.append("<h3>Shepherd PRs</h3>\n")
    if not shepherd["present"]:
        parts.append(
            f"<p class=\"note\">no rows yet &mdash; {esc(SHEPHERD_FILE)} "
            "is not in the state dir yet.</p>\n")
    else:
        parts.append(
            f"<p>{shepherd['tracked_prs']} tracked PR(s), "
            f"{shepherd['seen_merged']} merged PR(s) seen, "
            f"{shepherd['jittest_catch_prs']} JiT-flagged PR(s).</p>\n")
        if shepherd["tracked"]:
            parts.append(
                "<table><tr><th>PR</th><th>reruns</th><th>fix attempts</th>"
                "<th>JiT catch</th><th>age (h)</th></tr>\n")
            for row in shepherd["tracked"]:
                age = row["age_hours"]
                age_txt = f"{age:.1f}" if age is not None else "—"
                parts.append(
                    "<tr>"
                    f"<td>{esc(row['pr'])}</td>"
                    f"<td>{esc(row['rerun_count'])}</td>"
                    f"<td>{esc(row['ci_fix_attempts'])}</td>"
                    f"<td>{'yes' if row['jittest_catch'] else 'no'}</td>"
                    f"<td>{age_txt}</td>"
                    "</tr>\n")
            parts.append("</table>\n")
        pending = shepherd["pending_trigger"]
        last = shepherd["last_trigger"]
        pending_reason = (pending.get("reason")
                          if isinstance(pending, dict) else pending)
        last_at = (last.get("requested_at")
                   if isinstance(last, dict) else last)
        parts.append(
            "<p class=\"meta\">pending trigger: "
            f"{esc(pending_reason) or 'none'}"
            " &middot; last trigger: "
            f"{esc(last_at) or 'none'}"
            "</p>\n")

    parts.append("<h3>Activity feed (worker ledger tail)</h3>\n")
    if not live["activity"]:
        if live["activity_present"]:
            parts.append(
                f"<p class=\"note\">no rows yet &mdash; "
                f"{esc(WORKER_LEDGER_FILE)} has no rows.</p>\n")
        else:
            parts.append(
                f"<p class=\"note\">no rows yet &mdash; "
                f"{esc(WORKER_LEDGER_FILE)} is not in the state dir yet."
                "</p>\n")
    else:
        parts.append(
            "<table><tr><th>ts</th><th>repo</th><th>issue</th>"
            "<th>tier</th><th>outcome</th><th>tokens in</th>"
            "<th>tokens out</th><th>grade</th></tr>\n")
        for row in live["activity"]:
            parts.append(
                "<tr>"
                f"<td>{esc(row['ts'])}</td>"
                f"<td>{esc(row['repo'])}</td>"
                f"<td>{esc(row['issue'])}</td>"
                f"<td>{esc(row['tier'])}</td>"
                f"<td>{esc(row['outcome'])}</td>"
                f"<td>{fmt_num(row['tokens_in'])}</td>"
                f"<td>{fmt_num(row['tokens_out'])}</td>"
                f"<td>{esc(row['grade'])}</td>"
                "</tr>\n")
        parts.append("</table>\n")
    parts.append("</section>\n")

    # --- panel 2 -----------------------------------------------------
    parts.append("<section class=\"panel\" id=\"pr-metrics\">\n")
    parts.append("<h2>Panel 2 &mdash; PR metrics</h2>\n")
    parts.append(
        f"<p class=\"meta\">refresh cadence: 5m &middot; "
        f"generated: {esc(generated_at)} (UTC) &middot; aggregation owner: "
        "bin/trace_summary.py, bin/shepherd_ledger.py summary</p>\n")
    worker = metrics["worker"]
    if not worker["rows"]:
        if worker["ledger_present"]:
            parts.append(
                f"<p class=\"note\">no rows yet &mdash; "
                f"{esc(WORKER_LEDGER_FILE)} has no rows.</p>\n")
        else:
            parts.append(
                f"<p class=\"note\">no rows yet &mdash; "
                f"{esc(WORKER_LEDGER_FILE)} is not in the state dir yet."
                "</p>\n")
    else:
        parts.append(
            f"<p>{worker['runs']} run(s), {worker['pr_opened']} PR(s) opened "
            f"({fmt_pct(worker['pr_rate'])} PR rate), "
            f"{fmt_num(worker['tokens_out_total'])} tokens out total "
            f"(median {fmt_num(worker['tokens_out_median'])} per run).</p>\n")
        parts.append(
            "<table><tr><th>repo</th><th>runs</th><th>PRs opened</th>"
            "<th>PR rate</th><th>tokens out median</th></tr>\n")
        for repo, stats in worker["by_repo"].items():
            parts.append(
                f"<tr><td>{esc(repo)}</td><td>{stats['runs']}</td>"
                f"<td>{stats['pr_opened']}</td>"
                f"<td>{fmt_pct(stats['pr_rate'])}</td>"
                f"<td>{fmt_num(stats['tokens_out_median'])}</td></tr>\n")
        parts.append("</table>\n")

    parts.append("<h3>Outcomes</h3>\n")
    if not metrics["outcomes"]:
        parts.append(
            "<p class=\"note\">no rows yet &mdash; no worker outcome "
            "classes recorded.</p>\n")
    else:
        parts.append(
            "<table><tr><th>class</th><th>runs</th></tr>\n")
        for name, count in metrics["outcomes"].items():
            parts.append(f"<tr><td>{esc(name)}</td><td>{count}</td></tr>\n")
        parts.append("</table>\n")

    parts.append("<h3>Failure classes</h3>\n")
    if not metrics["failure_classes"]:
        parts.append("<p class=\"note\">no rows yet &mdash; no failures "
                     "recorded.</p>\n")
    else:
        parts.append(
            "<table><tr><th>failure class</th><th>runs</th></tr>\n")
        for name, count in metrics["failure_classes"].items():
            parts.append(f"<tr><td>{esc(name)}</td><td>{count}</td></tr>\n")
        parts.append("</table>\n")

    sh = metrics["shepherd"]
    parts.append("<h3>Shepherd actions</h3>\n")
    if not sh["rows"]:
        if sh["ledger_present"]:
            parts.append(
                f"<p class=\"note\">no rows yet &mdash; "
                f"{esc(SHEPHERD_LEDGER_FILE)} has no rows.</p>\n")
        else:
            parts.append(
                f"<p class=\"note\">no rows yet &mdash; "
                f"{esc(SHEPHERD_LEDGER_FILE)} is not in the state dir yet."
                "</p>\n")
    else:
        parts.append(
            f"<p>{sh['merges']} merge(s) "
            f"({sh['unstable_merges']} on UNSTABLE), "
            f"{sh['fix_attempt_distinct_prs']} distinct PR(s) with fix "
            f"attempts, {sh['needs_operator_distinct_prs']} distinct PR(s) "
            f"flagged, {sh['rerun_distinct_prs']} distinct PR(s) rerun.</p>\n")
        parts.append("<table><tr><th>action</th><th>rows</th></tr>\n")
        for action, count in sh["by_action"].items():
            parts.append(
                f"<tr><td>{esc(action)}</td><td>{count}</td></tr>\n")
        parts.append("</table>\n")
        if sh["flagged_prs"]:
            parts.append(
                "<p class=\"meta\">flagged PRs: "
                + esc(", ".join(sh["flagged_prs"])) + "</p>\n")

    burn = metrics["burn"]
    parts.append("<h3>Token burn vs cycle budget</h3>\n")
    if not burn["cycles"]:
        parts.append(
            "<p class=\"note\">no rows yet &mdash; no ledger rows to "
            "bucket into cycles.</p>\n")
    elif not burn["budget_enabled"]:
        parts.append(
            "<p>Cycle budget: off (0). Peak ~2h cycle burn: "
            f"{fmt_num(burn['max_cycle_tokens_out'])} tokens out.</p>\n")
    else:
        parts.append(
            f"<p>Cycle budget: {fmt_num(burn['cycle_budget'])} tokens out. "
            f"Peak ~2h cycle burn: {fmt_num(burn['max_cycle_tokens_out'])} "
            f"({fmt_pct(burn['max_cycle_pct'])} of budget).</p>\n")
    if burn["cycles"]:
        parts.append(
            "<table><tr><th>cycle start (UTC)</th><th>runs</th>"
            "<th>tokens in</th><th>tokens out</th></tr>\n")
        for bucket in burn["cycles"]:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                  time.gmtime(bucket["start"]))
            parts.append(
                f"<tr><td>{esc(stamp)}</td><td>{bucket['runs']}</td>"
                f"<td>{fmt_num(bucket['tokens_in'])}</td>"
                f"<td>{fmt_num(bucket['tokens_out'])}</td></tr>\n")
        parts.append("</table>\n")
        parts.append(
            "<p class=\"note\">the ledger carries no cycle id, so cycles "
            "are approximated by 2h windows.</p>\n")

    parts.append("<h3>Provenance</h3>\n")
    parts.append(
        "<table><tr><th>file</th><th>present</th><th>path</th></tr>\n")
    seen = set()
    for entry in live["files"] + metrics["files"]:
        if entry["path"] in seen:
            continue
        seen.add(entry["path"])
        parts.append(
            f"<tr><td>{esc(entry['name'])}</td>"
            f"<td>{'yes' if entry['present'] else 'no'}</td>"
            f"<td>{esc(entry['path'])}</td></tr>\n")
    parts.append("</table>\n")
    parts.append("</section>\n")

    parts.append(
        "<section class=\"panel\">\n<h2>Panel 3 &mdash; Quality trends</h2>\n"
        "<p class=\"note\">not built in this MVP (out of scope): the "
        "quality-trend panel is the weekly tune pass's job "
        "(bin/trace_summary.py).</p>\n</section>\n")
    parts.append(
        "<p class=\"meta\">read-only: this page was rendered from the "
        "state dir without writing to it.</p>\n")
    parts.append("</body>\n</html>\n")
    return "".join(parts)


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def build_snapshot(state_dir: str, config: dict, tail: int,
                   since: str | None) -> dict:
    now = time.time()
    live = build_live_view(state_dir, now, tail)
    if since:
        live["activity"] = [
            row for row in live["activity"]
            if str(row.get("ts") or "")[:10] >= since
        ]
    metrics = build_pr_metrics(state_dir, config, since)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "live_view": live,
        "pr_metrics": metrics,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=None,
                        help="state dir to read (default: the deployment "
                             "config's state.dir)")
    parser.add_argument("--config", default=None,
                        help="deployment config to read the cycle token "
                             "budget from (default: the usual search order)")
    parser.add_argument("--since", default=None,
                        help="YYYY-MM-DD; ledger rows with an earlier ts "
                             "are ignored")
    parser.add_argument("--tail", type=int, default=10,
                        help="how many ledger rows the activity feed shows")
    parser.add_argument("--out", default=None,
                        help="write the HTML page to this file instead of "
                             "stdout (must not be inside the state dir)")
    parser.add_argument("--json", action="store_true",
                        help="print the aggregated snapshot as JSON")
    args = parser.parse_args(argv)

    config = load_deployment_config(args.config)
    state_dir = resolve_state_dir(config, args.state_dir)
    if not os.path.isdir(state_dir):
        print(f"warning: state dir not found: {state_dir} "
              "(rendering empty panels)", file=sys.stderr)

    out_path = os.path.abspath(os.path.expanduser(args.out)) if args.out \
        else None
    if out_path and (out_path == state_dir
                     or out_path.startswith(state_dir + os.sep)):
        print("refusing to write inside the state dir: the dashboard is "
              "read-only", file=sys.stderr)
        return 2

    snapshot = build_snapshot(state_dir, config, args.tail, args.since)
    if args.json:
        print(json.dumps(snapshot, indent=1, default=str))
        return 0

    page = render_html(snapshot["live_view"], snapshot["pr_metrics"],
                       snapshot["generated_at"])
    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(page)
        except OSError as exc:
            print(f"cannot write {out_path}: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {out_path} ({len(page)} bytes)")
        return 0
    sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
