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
import statistics
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shepherd_ledger import MERGE_ACTIONS
from shepherd_ledger import summarize as shepherd_ledger_summary
from trace_summary import summarize as trace_summary

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
# Phase 0's derived rollup (bin/metrics_rollup.py) -- the source of the
# PR-lifecycle and daily rows Panel 3 (trends) and Panel 4 (funnel and
# dwell) read.  It is optional: a state dir without it renders the
# "Phase 0 rollup not found" note instead of the panels.
ROLLUP_FILE = "metrics_rollup.jsonl"
# The experiment registry (issue #60, Panel 5).  Optional as well: an
# absent registry renders the "no experiments yet" note.
EXPERIMENTS_FILE = "experiments.jsonl"
# Panel 3's small multiples: the three windows the issue asks for.
TREND_WINDOWS = (7, 30, 90)
# The supervisor's plan window.  The crons run every 2h by default; the
# deployment config's `supervisor.interval_hours` wins when it is set.
DEFAULT_PLAN_WINDOW_HOURS = 2.0
# Shepherd actions that put a PR into the operator's queue.
NEEDS_ACTIONS = ("needs_alex", "needs_operator")


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
# shared helpers for the v2 panels (0, 3, 4, 5)
# ---------------------------------------------------------------------
def num(value):
    """A number or None (booleans are not numbers here)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def percentile(values, q):
    """Nearest-rank percentile of a list of numbers (None when empty)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def median(values):
    """Median of a list of numbers (None when empty)."""
    if not values:
        return None
    return statistics.median(values)


def iso_utc(stamp):
    """Epoch seconds -> an ISO-8601 UTC stamp (None stays None)."""
    if stamp is None:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))
    except (OverflowError, OSError, ValueError):
        return None


def age_hours(stamp, now):
    """Hours between a timestamp and now (None when the stamp is unset)."""
    if stamp is None:
        return None
    return round(max(0.0, (now - stamp) / 3600.0), 2)


def day_of_ts(value):
    """The UTC calendar day ('YYYY-MM-DD') of a ledger timestamp."""
    stamp = parse_ts(value)
    if stamp is None:
        text = str(value or "")[:10]
        return text or None
    return datetime.datetime.fromtimestamp(
        stamp, datetime.UTC).date().isoformat()


def day_in_range(day, start_day: str, end_day: str) -> bool:
    """Is a calendar day inside the [start_day, end_day] window?"""
    text = str(day or "")
    return bool(text) and start_day <= text <= end_day


def pr_key(row):
    """The join key of a ledger row: the PR url, else repo#pr.

    Same key `bin/metrics_rollup.py` uses, so a rollup PR row and a
    ledger row about the same PR meet under one key.
    """
    url = str(row.get("pr_url") or "").strip().rstrip("/")
    if url:
        return "url:" + url
    repo = str(row.get("repo") or "").strip()
    number = row.get("pr")
    if repo and number not in (None, ""):
        return f"{repo}#{number}"
    return ""


def supervisor_max_parallel(config: dict) -> int:
    """`supervisor.max_parallel` -- how many worker slots exist."""
    value = (config.get("supervisor") or {}).get("max_parallel")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def plan_window_hours(config: dict) -> float:
    """The supervisor's plan window, in hours."""
    value = (config.get("supervisor") or {}).get("interval_hours")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_PLAN_WINDOW_HOURS
    if value <= 0:
        return DEFAULT_PLAN_WINDOW_HOURS
    return float(value)


def _max_ts(rows: list[dict]) -> float | None:
    """The newest timestamp in a list of ledger rows (None when empty)."""
    stamps = [parse_ts(row.get("ts")) for row in rows]
    stamps = [stamp for stamp in stamps if stamp is not None]
    return max(stamps) if stamps else None


def needs_queue(shepherd_rows: list[dict], now: float) -> list[dict]:
    """The PRs waiting for a human, newest wait first.

    One entry per distinct PR -- a shepherd row is a polling event, not
    a PR.  `age_hours` is how long the PR has been waiting since the
    first flag; a PR that was merged after the flag is not queued.
    """
    by_pr: dict[str, dict] = {}
    for row in shepherd_rows:
        action = str(row.get("action") or "")
        key = pr_key(row)
        if not key:
            continue
        stamp = parse_ts(row.get("ts"))
        entry = by_pr.setdefault(key, {
            "key": key,
            "repo": row.get("repo"),
            "pr": row.get("pr"),
            "pr_url": row.get("pr_url"),
            "flag_action": None,
            "flagged_at": None,
            "last_action": None,
            "last_action_ts": None,
            "merged_at": None,
        })
        if stamp is not None and (entry["last_action_ts"] is None
                                  or stamp > entry["last_action_ts"]):
            entry["last_action_ts"] = stamp
            entry["last_action"] = action
        if action in NEEDS_ACTIONS:
            if stamp is not None and (entry["flagged_at"] is None
                                      or stamp < entry["flagged_at"]):
                entry["flagged_at"] = stamp
                entry["flag_action"] = action
        if action in MERGE_ACTIONS:
            if stamp is not None and (entry["merged_at"] is None
                                      or stamp > entry["merged_at"]):
                entry["merged_at"] = stamp

    queue: list[dict] = []
    for entry in by_pr.values():
        if entry["flagged_at"] is None:
            continue
        if entry["merged_at"] is not None \
                and entry["merged_at"] > entry["flagged_at"]:
            continue  # merged after the flag: nothing is waiting
        entry["age_hours"] = age_hours(entry["flagged_at"], now)
        entry["flagged_at_iso"] = iso_utc(entry["flagged_at"])
        entry["last_action_iso"] = iso_utc(entry["last_action_ts"])
        queue.append(entry)
    queue.sort(key=lambda item: (-(item["age_hours"] or 0.0),
                                 str(item["key"])))
    return queue


# ---------------------------------------------------------------------
# panel 0 -- pulse header
# ---------------------------------------------------------------------
def build_pulse(state_dir: str, config: dict, now: float) -> dict:
    """Panel 0 -- the pulse header.

    Four things the operator wants before anything else: when the
    supervisor and the shepherd last ran, which workers are in flight
    and for how long, what the current plan window has burned, and how
    many PRs are waiting for a human.

    Everything is read-only and derived from files that already exist;
    a missing file only empties its own field.
    """
    workers_path = os.path.join(state_dir, WORKERS_FILE)
    shepherd_path = os.path.join(state_dir, SHEPHERD_FILE)
    worker_ledger_path = os.path.join(state_dir, WORKER_LEDGER_FILE)
    shepherd_ledger_path = os.path.join(state_dir, SHEPHERD_LEDGER_FILE)

    workers_raw = read_json(workers_path)
    shepherd_raw = read_json(shepherd_path)
    worker_rows = read_jsonl(worker_ledger_path)
    shepherd_rows = read_jsonl(shepherd_ledger_path)

    worker_last = _max_ts(worker_rows)
    shepherd_last = _max_ts(shepherd_rows)

    trigger = (shepherd_raw or {}).get("last_trigger") or {}
    pending = (shepherd_raw or {}).get("pending_trigger") or {}
    trigger_at = parse_ts(trigger.get("requested_at")
                          if isinstance(trigger, dict) else trigger)
    pending_at = parse_ts(pending.get("requested_at")
                          if isinstance(pending, dict) else pending)
    # The supervisor's last cycle is the newest worker run or the
    # trigger it asked for, whichever is more recent.
    candidates = [stamp for stamp in (worker_last, trigger_at)
                  if stamp is not None]
    supervisor_last = max(candidates) if candidates else None

    in_flight: list[dict] = []
    for entry in worker_entries(workers_raw):
        status = str(entry.get("status") or "")
        if status not in IN_FLIGHT_STATUSES:
            continue
        started = parse_ts(entry.get("started_at"))
        in_flight.append({
            "run_id": entry.get("run_id"),
            "repo": entry.get("repo"),
            "issue": entry.get("issue"),
            "status": status,
            "started_at": entry.get("started_at"),
            "elapsed_hours": age_hours(started, now),
            "tokens": num(entry.get("tokens")),
        })
    in_flight.sort(key=lambda row: -(row["elapsed_hours"] or 0.0))

    # The current plan window: the cycle bucket the supervisor is in
    # right now (same 2h windows Panel 2 buckets the burn into).
    window_hours = plan_window_hours(config)
    window_seconds = int(window_hours * 3600)
    window_start = int(now // window_seconds) * window_seconds
    tokens_out = 0
    tokens_in = 0
    runs = 0
    for row in worker_rows:
        stamp = parse_ts(row.get("ts"))
        if stamp is None or stamp < window_start or stamp > now + 60:
            continue
        runs += 1
        value = num(row.get("tokens_out"))
        if value is not None:
            tokens_out += int(value)
        value = num(row.get("tokens_in"))
        if value is not None:
            tokens_in += int(value)
    budget = cycle_budget(config)

    queue = needs_queue(shepherd_rows, now)
    oldest = max((entry["age_hours"] for entry in queue), default=None)

    return {
        "state_dir": state_dir,
        "supervisor": {
            "last_cycle": iso_utc(supervisor_last),
            "last_cycle_age_hours": age_hours(supervisor_last, now),
            "last_worker_run": iso_utc(worker_last),
            "last_worker_run_age_hours": age_hours(worker_last, now),
            "last_trigger": iso_utc(trigger_at),
            "pending_trigger": iso_utc(pending_at),
            "ledger_present": os.path.exists(worker_ledger_path),
        },
        "shepherd": {
            "last_cycle": iso_utc(shepherd_last),
            "last_cycle_age_hours": age_hours(shepherd_last, now),
            "ledger_present": os.path.exists(shepherd_ledger_path),
        },
        "workers": {
            "in_flight": len(in_flight),
            "rows": in_flight,
            "present": workers_raw is not None,
        },
        "burn": {
            "window_hours": window_hours,
            "window_start": iso_utc(window_start),
            "runs": runs,
            "tokens_out": tokens_out,
            "tokens_in": tokens_in,
            "cycle_budget": budget,
            "budget_enabled": budget > 0,
            "budget_pct": (round(tokens_out / budget, 3) if budget else None),
        },
        "needs": {
            "count": len(queue),
            "oldest_age_hours": oldest,
            "queue": queue,
            "ledger_present": os.path.exists(shepherd_ledger_path),
        },
        "files": [file_state(workers_path), file_state(shepherd_path),
                  file_state(worker_ledger_path),
                  file_state(shepherd_ledger_path)],
    }


# ---------------------------------------------------------------------
# panel 3 -- trends
# ---------------------------------------------------------------------
def build_trends(state_dir: str, config: dict, now: float,
                 as_of: float | None = None) -> dict:
    """Panel 3 -- 7d / 30d / 90d small multiples.

    The PR-level numbers come from the Phase 0 rollup
    (`metrics_rollup.jsonl`); the run-level ones the rollup does not
    carry (failure classes, grades) come from the worker ledger, the
    file the rollup itself is derived from.  No number is invented here:
    every one is a count or a median over the rows on disk.

    The windows end on the newest day the state dir knows about (or on
    "now", whichever is later), so a state dir whose last activity is
    recent is not silently empty and an old one honestly shows empty
    windows.  `--as-of` pins the end of the windows explicitly.
    """
    rollup_path = os.path.join(state_dir, ROLLUP_FILE)
    worker_ledger_path = os.path.join(state_dir, WORKER_LEDGER_FILE)
    present = os.path.exists(rollup_path)
    rows = read_jsonl(rollup_path)
    pr_rows = [row for row in rows if str(row.get("type") or "") == "pr"]
    daily_rows = [row for row in rows if str(row.get("type") or "") == "daily"]
    worker_rows = read_jsonl(worker_ledger_path)
    experiment_rows = read_jsonl(os.path.join(state_dir, EXPERIMENTS_FILE))

    latest = _max_ts(worker_rows)
    for row in pr_rows:
        for field in ("opened_at", "merged_at"):
            stamp = parse_ts(row.get(field))
            if stamp is not None and (latest is None or stamp > latest):
                latest = stamp
    anchor = as_of
    if anchor is None:
        anchor = max([stamp for stamp in (now, latest) if stamp is not None],
                     default=now)
    end_date = datetime.datetime.fromtimestamp(
        anchor, datetime.UTC).date()
    max_slots = supervisor_max_parallel(config)

    windows: list[dict] = []
    for size in TREND_WINDOWS:
        start_date = end_date - datetime.timedelta(days=size - 1)
        windows.append({
            "days": size,
            "start_day": start_date.isoformat(),
            "end_day": end_date.isoformat(),
        })

    for window in windows:
        start_day = window["start_day"]
        end_day = window["end_day"]

        opened = [row for row in pr_rows
                  if day_in_range(row.get("opened_day"), start_day, end_day)]
        merged_in_window = [row for row in pr_rows
                            if day_in_range(row.get("merged_day"),
                                            start_day, end_day)]
        merged_cohort = [row for row in opened if row.get("merged")]
        day_rows = [row for row in daily_rows
                    if str(row.get("scope") or "") == "day_total"
                    and day_in_range(row.get("day"), start_day, end_day)]
        days_with_data = sorted({str(row.get("day")) for row in day_rows})

        merged_tokens = [num(row.get("tokens_out")) for row in merged_in_window]
        merged_tokens = [value for value in merged_tokens if value is not None]

        opened_tokens = sum(num(row.get("tokens_out")) or 0 for row in opened)
        wasted_tokens = sum(num(row.get("tokens_out")) or 0
                            for row in opened if not row.get("merged"))

        occupancy: list[float] = []
        if max_slots:
            for row in day_rows:
                runs = num(row.get("runs")) or 0
                occupancy.append(min(1.0, runs / max_slots))

        window_worker_rows = [row for row in worker_rows
                              if day_in_range(day_of_ts(row.get("ts")),
                                              start_day, end_day)]
        failure_mix: Counter = Counter()
        for row in window_worker_rows:
            cls = outcome_class(row.get("outcome"))
            if cls.startswith("failed:"):
                failure_mix[cls.split(":", 1)[1]] += 1
        grades = [num(row.get("grade")) for row in window_worker_rows]
        grades = [value for value in grades if value is not None]

        window_experiments = [
            {"ts": row.get("ts"), "name": experiment_name(row),
             "change": experiment_change(row)}
            for row in experiment_rows
            if day_in_range(day_of_ts(row.get("ts")), start_day, end_day)]

        window.update({
            "opened_prs": len(opened),
            "merged_prs": len(merged_in_window),
            "merged_cohort_prs": len(merged_cohort),
            "days_with_data": len(days_with_data),
            "prs_per_day": (round(len(opened) / len(days_with_data), 2)
                            if days_with_data else None),
            "merge_rate": (round(len(merged_cohort) / len(opened), 3)
                           if opened else None),
            "tokens_per_merged_pr_median": median(merged_tokens),
            "tokens_per_merged_pr_p90": percentile(merged_tokens, 0.90),
            "tokens_out_total": opened_tokens,
            "wasted_token_share": (round(wasted_tokens / opened_tokens, 3)
                                   if opened_tokens else None),
            "slot_occupancy": (round(sum(occupancy) / len(occupancy), 3)
                               if occupancy else None),
            "slot_occupancy_days": len(occupancy),
            "max_slots": max_slots,
            "failure_mix": dict(failure_mix.most_common()),
            "failure_total": sum(failure_mix.values()),
            "runs": len(window_worker_rows),
            "grade_quartiles": {
                "count": len(grades),
                "p25": percentile(grades, 0.25),
                "p50": median(grades),
                "p75": percentile(grades, 0.75),
            },
            "experiments": window_experiments,
        })

    return {
        "present": present,
        "rollup": rollup_path,
        "pr_rows": len(pr_rows),
        "daily_rows": len(daily_rows),
        "worker_rows": len(worker_rows),
        "window_end": end_date.isoformat(),
        "windows": windows,
        "files": [file_state(rollup_path),
                  file_state(worker_ledger_path)],
    }


def experiment_name(row: dict) -> str:
    """The experiment's name, whichever field the registry row uses."""
    for field in ("name", "experiment", "id", "key"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def experiment_change(row: dict) -> str:
    """The experiment's change note, whichever field the row uses."""
    for field in ("change", "change_summary", "summary", "note", "detail"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


# ---------------------------------------------------------------------
# panel 4 -- funnel and dwell
# ---------------------------------------------------------------------
def _dwell_to_action(pr: dict, index: dict[str, list[dict]],
                     actions: tuple[str, ...]) -> float | None:
    """Hours from a PR's opening to its first action of these kinds."""
    opened_at = parse_ts(pr.get("opened_at"))
    if opened_at is None:
        return None
    stamps = []
    for row in index.get(str(pr.get("pr_key") or ""), []):
        if str(row.get("action") or "") in actions:
            stamp = parse_ts(row.get("ts"))
            if stamp is not None:
                stamps.append(stamp)
    if not stamps:
        return None
    return round((min(stamps) - opened_at) / 3600.0, 2)


def build_funnel(state_dir: str, now: float) -> dict:
    """Panel 4 -- the stage funnel, the dwell per stage and the queue.

    The funnel counts distinct PRs (never ledger rows) over the PR
    rows of the Phase 0 rollup; the dwell per stage is the median time
    a PR spends between two stages.
    """
    rollup_path = os.path.join(state_dir, ROLLUP_FILE)
    shepherd_ledger_path = os.path.join(state_dir, SHEPHERD_LEDGER_FILE)
    present = os.path.exists(rollup_path)
    rows = read_jsonl(rollup_path)
    pr_rows = [row for row in rows if str(row.get("type") or "") == "pr"]
    shepherd_rows = read_jsonl(shepherd_ledger_path)

    index: dict[str, list[dict]] = {}
    for row in shepherd_rows:
        key = pr_key(row)
        if key:
            index.setdefault(key, []).append(row)

    opened = [row for row in pr_rows if row.get("opened")]
    base = len(opened)

    def has_flag(row, flag) -> bool:
        flags = row.get("flags") or []
        return flag in flags if isinstance(flags, list) else False

    def action_count(row, action) -> int:
        actions = row.get("shepherd_by_action") or {}
        if not isinstance(actions, dict):
            return 0
        return int(actions.get(action) or 0)

    shepherd_seen = [row for row in opened
                     if int(row.get("shepherd_actions") or 0) > 0]
    flagged = [row for row in opened
               if has_flag(row, "needs_alex") or has_flag(row, "needs_operator")
               or has_flag(row, "merge_conflicts")]
    fixes = [row for row in opened if action_count(row, "fix_attempt")]
    merged = [row for row in opened if row.get("merged")]

    dwell_first_action = [num(row.get("dwell_open_to_first_action_hours"))
                           for row in opened]
    dwell_first_action = [v for v in dwell_first_action if v is not None]
    dwell_merge = [num(row.get("dwell_open_to_merge_hours")) for row in opened]
    dwell_merge = [v for v in dwell_merge if v is not None]
    dwell_flag = [value for value in (
        _dwell_to_action(row, index, ("needs_alex", "needs_operator",
                                      "merge_conflicts"))
        for row in opened) if value is not None]
    dwell_fix = [value for value in (
        _dwell_to_action(row, index, ("fix_attempt",)) for row in opened)
        if value is not None]

    stages = [
        {"name": "PR opened", "count": base, "dwell_hours": None},
        {"name": "shepherd seen", "count": len(shepherd_seen),
         "dwell_hours": median(dwell_first_action)},
        {"name": "flagged for the operator", "count": len(flagged),
         "dwell_hours": median(dwell_flag)},
        {"name": "fix attempt", "count": len(fixes),
         "dwell_hours": median(dwell_fix)},
        {"name": "merged", "count": len(merged),
         "dwell_hours": median(dwell_merge)},
    ]
    for stage in stages:
        stage["conversion"] = (round(stage["count"] / base, 3) if base else None)

    queue = needs_queue(shepherd_rows, now)
    return {
        "present": present,
        "rollup": rollup_path,
        "pr_rows": len(pr_rows),
        "opened": base,
        "stages": stages,
        "queue": queue,
        "needs_count": len(queue),
        "needs_oldest_age_hours": max(
            (entry["age_hours"] for entry in queue), default=None),
        "files": [file_state(rollup_path),
                  file_state(shepherd_ledger_path)],
    }


# ---------------------------------------------------------------------
# panel 5 -- experiment registry (stub)
# ---------------------------------------------------------------------
def build_experiments(state_dir: str) -> dict:
    """Panel 5 -- the experiment registry.

    A stub for now: the registry file is optional, and an absent
    registry renders the "no experiments yet" note rather than an
    empty table.
    """
    path = os.path.join(state_dir, EXPERIMENTS_FILE)
    present = os.path.exists(path)
    entries = []
    for row in read_jsonl(path):
        entries.append({
            "ts": row.get("ts") or row.get("date") or row.get("at") or "",
            "name": experiment_name(row),
            "change": experiment_change(row),
        })
    entries.sort(key=lambda entry: str(entry["ts"]))
    return {
        "present": present,
        "path": path,
        "count": len(entries),
        "entries": entries,
        "files": [file_state(path)],
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


def fmt_hours(value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.1f}h"
    return "—"


def fmt_text(value) -> str:
    """A rendered cell: the value, or an em dash when it is unset."""
    text = str(value if value is not None else "").strip()
    return esc(text) if text else "—"


# ---------------------------------------------------------------------
# panel rendering (0, 3, 4, 5)
# ---------------------------------------------------------------------
def render_pulse(pulse: dict) -> str:
    """Panel 0 -- the pulse header."""
    parts: list[str] = []
    parts.append("<section class=\"panel\" id=\"pulse\">\n")
    parts.append("<h2>Panel 0 &mdash; Pulse</h2>\n")
    supervisor = pulse["supervisor"]
    shepherd = pulse["shepherd"]
    parts.append(
        "<table><tr><th>signal</th><th>value</th></tr>\n"
        f"<tr><td>supervisor last cycle</td>"
        f"<td>{fmt_text(supervisor['last_cycle'])} "
        f"<span class=\"meta\">({fmt_hours(supervisor['last_cycle_age_hours'])}"
        " ago)</span></td></tr>\n"
        f"<tr><td>shepherd last cycle</td>"
        f"<td>{fmt_text(shepherd['last_cycle'])} "
        f"<span class=\"meta\">({fmt_hours(shepherd['last_cycle_age_hours'])}"
        " ago)</span></td></tr>\n"
        f"<tr><td>last trigger</td>"
        f"<td>{fmt_text(supervisor['last_trigger'])}</td></tr>\n"
        f"<tr><td>pending trigger</td>"
        f"<td>{fmt_text(supervisor['pending_trigger'])}</td></tr>\n"
        "</table>\n")

    parts.append("<h3>Workers in flight</h3>\n")
    workers = pulse["workers"]
    if not workers["rows"]:
        parts.append(
            "<p class=\"note\">no rows yet &mdash; no worker is in flight."
            "</p>\n")
    else:
        parts.append(
            f"<p>{workers['in_flight']} worker(s) in flight.</p>\n")
        parts.append(
            "<table><tr><th>run</th><th>repo</th><th>issue</th>"
            "<th>status</th><th>started</th><th>elapsed</th>"
            "<th>tokens</th></tr>\n")
        for row in workers["rows"]:
            parts.append(
                "<tr>"
                f"<td>{fmt_text(row['run_id'])}</td>"
                f"<td>{fmt_text(row['repo'])}</td>"
                f"<td>{fmt_text(row['issue'])}</td>"
                f"<td>{fmt_text(row['status'])}</td>"
                f"<td>{fmt_text(row['started_at'])}</td>"
                f"<td>{fmt_hours(row['elapsed_hours'])}</td>"
                f"<td>{fmt_num(row['tokens'])}</td>"
                "</tr>\n")
        parts.append("</table>\n")

    burn = pulse["burn"]
    parts.append("<h3>Plan-window burn</h3>\n")
    if burn["budget_enabled"]:
        parts.append(
            f"<p>Current {fmt_num(burn['window_hours'])}h plan window "
            f"(from {fmt_text(burn['window_start'])}): "
            f"{burn['runs']} run(s), {fmt_num(burn['tokens_out'])} tokens out, "
            f"{fmt_num(burn['tokens_in'])} tokens in "
            f"({fmt_pct(burn['budget_pct'])} of the "
            f"{fmt_num(burn['cycle_budget'])} cycle budget).</p>\n")
    else:
        parts.append(
            f"<p>Current {fmt_num(burn['window_hours'])}h plan window "
            f"(from {fmt_text(burn['window_start'])}): "
            f"{burn['runs']} run(s), {fmt_num(burn['tokens_out'])} tokens out, "
            f"{fmt_num(burn['tokens_in'])} tokens in "
            "(cycle budget off).</p>\n")

    needs = pulse["needs"]
    parts.append("<h3>Needs-Alex queue</h3>\n")
    if not needs["count"]:
        if needs["ledger_present"]:
            parts.append(
                "<p class=\"note\">no rows yet &mdash; no PR is waiting for "
                "the operator.</p>\n")
        else:
            parts.append(
                f"<p class=\"note\">no rows yet &mdash; "
                f"{esc(SHEPHERD_LEDGER_FILE)} is not in the state dir yet."
                "</p>\n")
    else:
        parts.append(
            f"<p>{needs['count']} PR(s) waiting for the operator; the oldest "
            f"has waited {fmt_hours(needs['oldest_age_hours'])}.</p>\n")
    parts.append("</section>\n")
    return "".join(parts)


def render_trends(trends: dict) -> str:
    """Panel 3 -- the 7d / 30d / 90d small multiples."""
    parts: list[str] = []
    parts.append("<section class=\"panel\" id=\"trends\">\n")
    parts.append("<h2>Panel 3 &mdash; Trends</h2>\n")
    if not trends["present"]:
        parts.append(
            f"<p class=\"note\">Phase 0 rollup not found &mdash; "
            f"{esc(ROLLUP_FILE)} is not in the state dir yet; run "
            "bin/metrics_rollup.py to populate this panel.</p>\n")
        parts.append("</section>\n")
        return "".join(parts)
    parts.append(
        f"<p class=\"meta\">source: {esc(trends['rollup'])} "
        f"({trends['pr_rows']} PR row(s), {trends['daily_rows']} daily "
        f"row(s)) &middot; windows end on {esc(trends['window_end'])}"
        "</p>\n")
    for window in trends["windows"]:
        parts.append(
            f"<h3>Last {window['days']} days "
            f"({esc(window['start_day'])} &rarr; {esc(window['end_day'])})"
            "</h3>\n")
        quarters = window["grade_quartiles"]
        parts.append(
            "<table><tr><th>metric</th><th>value</th></tr>\n"
            f"<tr><td>PRs/day</td>"
            f"<td>{fmt_num(window['prs_per_day'])}</td></tr>\n"
            f"<tr><td>PRs opened</td>"
            f"<td>{fmt_num(window['opened_prs'])}</td></tr>\n"
            f"<tr><td>tokens per merged PR (median)</td>"
            f"<td>{fmt_num(window['tokens_per_merged_pr_median'])}</td></tr>\n"
            f"<tr><td>tokens per merged PR (p90)</td>"
            f"<td>{fmt_num(window['tokens_per_merged_pr_p90'])}</td></tr>\n"
            f"<tr><td>merge rate</td>"
            f"<td>{fmt_pct(window['merge_rate'])}</td></tr>\n"
            f"<tr><td>wasted-token share</td>"
            f"<td>{fmt_pct(window['wasted_token_share'])}</td></tr>\n"
            f"<tr><td>slot occupancy</td>"
            f"<td>{fmt_pct(window['slot_occupancy'])}</td></tr>\n"
            f"<tr><td>failure classes</td>"
            f"<td>{fmt_text(', '.join(f'{name}={count}' for name, count in window['failure_mix'].items()) or None)}"
            "</td></tr>\n"
            f"<tr><td>grade quartiles (p25 / p50 / p75)</td>"
            f"<td>{fmt_num(quarters['p25'])} / {fmt_num(quarters['p50'])}"
            f" / {fmt_num(quarters['p75'])}"
            f" <span class=\"meta\">({quarters['count']} graded run(s))"
            "</span></td></tr>\n"
            "</table>\n")
        if window["experiments"]:
            parts.append(
                "<p class=\"meta\">experiment-registry changes in this "
                "window:</p>\n")
            parts.append(
                "<table><tr><th>ts</th><th>experiment</th><th>change</th>"
                "</tr>\n")
            for entry in window["experiments"]:
                parts.append(
                    "<tr>"
                    f"<td>{fmt_text(entry['ts'])}</td>"
                    f"<td>{fmt_text(entry['name'])}</td>"
                    f"<td>{fmt_text(entry['change'])}</td>"
                    "</tr>\n")
            parts.append("</table>\n")
    parts.append("</section>\n")
    return "".join(parts)


def render_funnel(funnel: dict) -> str:
    """Panel 4 -- the stage funnel, the dwell per stage and the queue."""
    parts: list[str] = []
    parts.append("<section class=\"panel\" id=\"funnel\">\n")
    parts.append("<h2>Panel 4 &mdash; Funnel &amp; dwell</h2>\n")
    if not funnel["present"]:
        parts.append(
            f"<p class=\"note\">Phase 0 rollup not found &mdash; "
            f"{esc(ROLLUP_FILE)} is not in the state dir yet; run "
            "bin/metrics_rollup.py to populate this panel.</p>\n")
        parts.append("</section>\n")
        return "".join(parts)
    parts.append(
        f"<p class=\"meta\">source: {esc(funnel['rollup'])} "
        f"({funnel['pr_rows']} PR row(s)) &middot; dwell is the median "
        "hours between the stages</p>\n")
    parts.append(
        "<table><tr><th>stage</th><th>PRs</th><th>conversion</th>"
        "<th>median dwell</th></tr>\n")
    for stage in funnel["stages"]:
        parts.append(
            "<tr>"
            f"<td>{fmt_text(stage['name'])}</td>"
            f"<td>{fmt_num(stage['count'])}</td>"
            f"<td>{fmt_pct(stage['conversion'])}</td>"
            f"<td>{fmt_hours(stage['dwell_hours'])}</td>"
            "</tr>\n")
    parts.append("</table>\n")

    parts.append("<h3>Needs-Alex queue</h3>\n")
    if not funnel["queue"]:
        parts.append(
            "<p class=\"note\">no rows yet &mdash; no PR is waiting for the "
            "operator.</p>\n")
    else:
        parts.append(
            f"<p>{funnel['needs_count']} PR(s) waiting; the oldest has "
            f"waited {fmt_hours(funnel['needs_oldest_age_hours'])}.</p>\n")
        parts.append(
            "<table><tr><th>PR</th><th>repo</th><th>flagged</th>"
            "<th>last action</th><th>age</th></tr>\n")
        for entry in funnel["queue"]:
            parts.append(
                "<tr>"
                f"<td>{fmt_text(entry['pr'])}</td>"
                f"<td>{fmt_text(entry['repo'])}</td>"
                f"<td>{fmt_text(entry['flagged_at_iso'])}</td>"
                f"<td>{fmt_text(entry['last_action_iso'])}</td>"
                f"<td>{fmt_hours(entry['age_hours'])}</td>"
                "</tr>\n")
        parts.append("</table>\n")
    parts.append("</section>\n")
    return "".join(parts)


def render_experiments(experiments: dict) -> str:
    """Panel 5 -- the experiment registry (stub)."""
    parts: list[str] = []
    parts.append("<section class=\"panel\" id=\"experiments\">\n")
    parts.append("<h2>Panel 5 &mdash; Experiment registry</h2>\n")
    if not experiments["present"]:
        parts.append(
            "<p class=\"note\">no experiments yet &mdash; "
            f"{esc(EXPERIMENTS_FILE)} is not in the state dir yet.</p>\n")
        parts.append("</section>\n")
        return "".join(parts)
    if not experiments["entries"]:
        parts.append(
            f"<p class=\"note\">no experiments yet &mdash; "
            f"{esc(EXPERIMENTS_FILE)} holds no rows.</p>\n")
        parts.append("</section>\n")
        return "".join(parts)
    parts.append(
        "<table><tr><th>ts</th><th>experiment</th><th>change</th></tr>\n")
    for entry in experiments["entries"]:
        parts.append(
            "<tr>"
            f"<td>{fmt_text(entry['ts'])}</td>"
            f"<td>{fmt_text(entry['name'])}</td>"
            f"<td>{fmt_text(entry['change'])}</td>"
            "</tr>\n")
    parts.append("</table>\n")
    parts.append("</section>\n")
    return "".join(parts)


def render_html(live: dict, metrics: dict, generated_at: str,
                pulse: dict | None = None, trends: dict | None = None,
                funnel: dict | None = None,
                experiments: dict | None = None) -> str:
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

    # --- panel 0 -----------------------------------------------------
    if pulse:
        parts.append(render_pulse(pulse))

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

    if trends:
        parts.append(render_trends(trends))
    if funnel:
        parts.append(render_funnel(funnel))
    if experiments:
        parts.append(render_experiments(experiments))
    parts.append(
        "<p class=\"meta\">read-only: this page was rendered from the "
        "state dir without writing to it.</p>\n")
    parts.append("</body>\n</html>\n")
    return "".join(parts)


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def build_snapshot(state_dir: str, config: dict, tail: int,
                   since: str | None, as_of: float | None = None) -> dict:
    now = time.time()
    live = build_live_view(state_dir, now, tail)
    if since:
        live["activity"] = [
            row for row in live["activity"]
            if str(row.get("ts") or "")[:10] >= since
        ]
    metrics = build_pr_metrics(state_dir, config, since)
    pulse = build_pulse(state_dir, config, now)
    trends = build_trends(state_dir, config, now, as_of)
    funnel = build_funnel(state_dir, now)
    experiments = build_experiments(state_dir)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "live_view": live,
        "pr_metrics": metrics,
        "pulse": pulse,
        "trends": trends,
        "funnel": funnel,
        "experiments": experiments,
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
    parser.add_argument("--as-of", default=None,
                        help="YYYY-MM-DD; the day Panel 3's trend windows "
                             "end on (default: the newest day the state "
                             "dir knows about)")
    args = parser.parse_args(argv)

    as_of = None
    if args.as_of:
        stamp = parse_ts(str(args.as_of))
        if stamp is None:
            print(f"cannot parse --as-of: {args.as_of} "
                  "(expected YYYY-MM-DD)", file=sys.stderr)
            return 2
        as_of = stamp

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

    snapshot = build_snapshot(state_dir, config, args.tail, args.since,
                              as_of)
    if args.json:
        print(json.dumps(snapshot, indent=1, default=str))
        return 0

    page = render_html(snapshot["live_view"], snapshot["pr_metrics"],
                       snapshot["generated_at"], snapshot["pulse"],
                       snapshot["trends"], snapshot["funnel"],
                       snapshot["experiments"])
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
