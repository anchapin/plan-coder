#!/usr/bin/env python3
"""Read-only manager CLI for plan-coder (issue #73, Phase 0).

A *manager* (the human-side agent) needs one place to ask "what is the
fleet doing right now, what is ready for me, and what is stuck?"  Until
now that answer lived only in the three cron prompts and in the state
files, and every answer had to be re-derived by hand from
`workers.json`, `shepherd.json` and `worker_ledger.jsonl`.

This script is the mechanical, read-only half of that answer.  It
prints JSON (or a short human summary) for four questions:

  status         slots vs. workers in flight, the PRs the shepherd
                 tracks, the pending trigger, repos whose backlog is
                 empty, and today's / this week's token burn.
  collect        which workers are in a *collectible* status and what
                 the manifest-vs-actual-diff check says about each one
                 (report only -- it never collects).
  shepherd-check per tracked PR: is it mergeable, is the CI signal
                 PR-specific or a known repo-wide failure, and does it
                 trip a hard stop (workflows / branch protection /
                 release config)?
  next-actions   a structured list of the actions the manager owes
                 next: merge / flag / split / escalation / backlog_empty.

Phase 0 is strictly **read-only**:

  * it never writes to `workers.json`, `shepherd.json` or any ledger;
  * it never merges, never dispatches, never talks to an MCP server;
  * the live PR facts come from `gh` (read-only subcommands only) and
    can be replaced by a fixture file (`--pr-data`), so the whole
    shepherd path is testable without a network;
  * the write commands (Phase 1) are deliberately absent.

Usage:
    python3 bin/plan_coder.py status [--state-dir DIR] [--config PATH] [--json]
    python3 bin/plan_coder.py collect --dry-run [--state-dir DIR] [--json]
    python3 bin/plan_coder.py shepherd-check [--pr-data FILE] [--json]
    python3 bin/plan_coder.py next-actions [--pr-data FILE] [--json]

The state dir is resolved the way the other tools resolve it
(`bin/dashboard.py`, `bin/trace_summary.py`): `--state-dir`, then
`$PLAN_CODER_STATE_DIR`, then the deployment config's `state.dir`
(`$PLAN_CODER_CONFIG`, then `config/deployment.local.json` beside the
repo root, then `~/.plan-coder/deployment.json`), then
`~/.plan-coder/state`.

Exit 0 on success; exit 2 when the state dir cannot be used.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Repo convention: reuse the existing readers and the diff-check owner.
from dashboard import (
    load_deployment_config,
    pr_number,
    read_json,
    read_jsonl,
    resolve_state_dir,
    tracked_prs,
    worker_entries,
)
from grade_run import claim_vs_diff

WORKERS_FILE = "workers.json"
SHEPHERD_FILE = "shepherd.json"
WORKER_LEDGER_FILE = "worker_ledger.jsonl"

# A worker burns tokens while it is `working` or `collecting`
# (bin/dashboard.py's IN_FLIGHT_STATUSES -- same vocabulary).
IN_FLIGHT_STATUSES = ("working", "collecting")

# The supervisor prompt (prompts/backlog-supervisor.md, step 6) collects
# a worker only when its status is `working` or `escalated`; every other
# status is skipped by the collector.
COLLECTIBLE_STATUSES = ("working", "escalated")

# Hard stops the shepherd must never merge past (prompts/pr-shepherd.md,
# step 2): PRs touching workflows, branch protection or release config
# are the human's call.
HARD_STOP_PATTERNS = (
    ("workflows", (".github/workflows/",)),
    ("branch_protection", ("CODEOWNERS", ".github/branch-protection",
                           "branch-protection", ".github/rulesets",
                           ".github/settings.yml")),
    ("release_config", ("release.yml", "release.yaml", ".releaserc",
                        "release-config", ".github/release")),
)

CI_GREEN = "green"
CI_PR_SPECIFIC = "pr_specific"
CI_REPO_WIDE = "repo_wide"
CI_UNKNOWN = "unknown"

ACTION_KINDS = ("merge", "flag", "split", "escalation", "backlog_empty")
# The order the manager reads them in.
KIND_ORDER = {"flag": 0, "merge": 1, "split": 2, "escalation": 3,
              "backlog_empty": 4}


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(UTC)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def repo_slug_map(config: dict) -> dict:
    """`{repo name: slug}` from the deployment config's `repos` list."""
    out = {}
    for repo in config.get("repos") or []:
        if not isinstance(repo, dict):
            continue
        name = repo.get("name") or repo.get("slug")
        slug = repo.get("slug") or repo.get("name")
        if name and slug:
            out[str(name)] = str(slug)
    return out


def known_repo_wide_failures(config: dict, repo_name: str) -> list:
    """Check names known to fail repo-wide for one repo (config data).

    The deployment config may declare, per repo, the checks that are
    known to fail on every PR (a broken base branch, a flaky job).  The
    shepherd uses that list to tell a repo-wide failure apart from a
    PR-specific one.  Absent config data -> nothing is known.
    """
    for repo in config.get("repos") or []:
        if not isinstance(repo, dict):
            continue
        if str(repo.get("name") or "") != str(repo_name or ""):
            continue
        names = repo.get("known_ci_failures") or repo.get("known_repo_wide_ci")
        if isinstance(names, list):
            return [str(n) for n in names]
    return []


def tier_for(entry: dict, ledger_rows: list[dict]) -> str | None:
    """The tier of a worker run: from its ledger row, else from the
    provider label (`minimax/MiniMax-M2.7 (balanced)`)."""
    run_id = str(entry.get("run_id") or "")
    repo = str(entry.get("repo") or "")
    issue = str(entry.get("issue") or "")
    for row in ledger_rows:
        if run_id and str(row.get("run_id") or "") == run_id:
            if row.get("tier"):
                return str(row["tier"])
    for row in ledger_rows:
        if (repo and str(row.get("repo") or "") == repo
                and issue and str(row.get("issue") or "") == issue):
            if row.get("tier"):
                return str(row["tier"])
    provider = str(entry.get("provider_used") or "")
    if "(" in provider and provider.rstrip().endswith(")"):
        return provider.rstrip()[:-1].rsplit("(", 1)[-1].strip() or None
    return None


# ---------------------------------------------------------------------
# PR probes (live `gh` or a fixture file) -- read-only either way
# ---------------------------------------------------------------------
class GhProbe:
    """Read-only GitHub facts for one PR (`gh pr view` / `gh pr checks`)."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.available = True

    def _run(self, args: list[str]) -> dict | None:
        try:
            proc = subprocess.run(args, capture_output=True, text=True,
                                  timeout=self.timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            self.available = False
            return None
        if proc.returncode != 0:
            return None
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def view(self, slug: str | None, pr: str) -> dict | None:
        if not slug or not pr:
            return None
        return self._run(["gh", "pr", "view", str(pr), "--repo", str(slug),
                          "--json", ("number,state,mergeable,mergeStateStatus,"
                                     "headRefName,url,files")])

    def checks(self, slug: str | None, pr: str) -> list[dict] | None:
        if not slug or not pr:
            return None
        data = self._run(["gh", "pr", "checks", str(pr), "--repo", str(slug),
                          "--json", "name,state,bucket,link"])
        if data is None:
            return None
        checks = data.get("checks") if isinstance(data, dict) else None
        if not isinstance(checks, list):
            return []
        return [c for c in checks if isinstance(c, dict)]


class FileProbe:
    """PR facts from a fixture file -- the injectable path for tests.

    The file is a JSON object keyed by `repo#pr` (or by the PR URL):

        {"repo-a#456": {"mergeable": "MERGEABLE",
                        "files": [{"path": ".github/workflows/ci.yml"}],
                        "checks": [{"name": "build", "state": "FAILURE"}]}}
    """

    def __init__(self, path: str):
        self.path = path
        self.available = True
        data = read_json(path) or {}
        self.data = data if isinstance(data, dict) else {}

    def _lookup(self, slug: str | None, pr: str) -> dict:
        keys = []
        if slug and pr:
            keys.append(f"{slug}#{pr}")
        if pr:
            keys.append(f"#{pr}")
            keys.append(str(pr))
        for key in keys:
            if key in self.data:
                value = self.data[key]
                return value if isinstance(value, dict) else {}
        # Fall back to any key that ends in the same PR number, so a
        # fixture keyed by "<slug>#<pr>" is found even when the slug
        # is unknown to the deployment config.
        for key, value in self.data.items():
            if str(key).rsplit("#", 1)[-1] == str(pr):
                return value if isinstance(value, dict) else {}
        return {}

    def view(self, slug: str | None, pr: str) -> dict | None:
        return self._lookup(slug, pr) or None

    def checks(self, slug: str | None, pr: str) -> list[dict] | None:
        entry = self._lookup(slug, pr)
        checks = entry.get("checks")
        if isinstance(checks, list):
            return [c for c in checks if isinstance(c, dict)]
        return []


# ---------------------------------------------------------------------
# status
# ---------------------------------------------------------------------
def collect_state(state_dir: str) -> dict:
    """Read the three state files (read-only, tolerant of absence)."""
    workers_path = os.path.join(state_dir, WORKERS_FILE)
    shepherd_path = os.path.join(state_dir, SHEPHERD_FILE)
    ledger_path = os.path.join(state_dir, WORKER_LEDGER_FILE)
    workers_raw = read_json(workers_path)
    shepherd_raw = read_json(shepherd_path)
    ledger_rows = read_jsonl(ledger_path)
    return {
        "workers_path": workers_path,
        "shepherd_path": shepherd_path,
        "ledger_path": ledger_path,
        "workers_raw": workers_raw,
        "shepherd_raw": shepherd_raw,
        "ledger_rows": ledger_rows,
    }


def worker_view(entries: list[dict], ledger_rows: list[dict]) -> list[dict]:
    """Normalise workers.json entries into the CLI's worker record."""
    out = []
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        out.append({
            "run_id": entry.get("run_id"),
            "repo": str(entry.get("repo") or ""),
            "issue": as_int(entry.get("issue")),
            "branch": entry.get("branch"),
            "status": status,
            "tier": tier_for(entry, ledger_rows),
            "tokens": as_int(entry.get("tokens")),
            "provider_used": entry.get("provider_used"),
            "workdir": entry.get("workdir"),
            "started_at": entry.get("started_at"),
            "pr": entry.get("pr"),
        })
    return out


def shepherd_view(items: list[dict]) -> list[dict]:
    """Normalise shepherd.json tracked PRs into the CLI's PR record."""
    out = []
    for item in items:
        url = str(item.get("pr_url") or item.get("url") or "")
        out.append({
            "repo": item.get("repo"),
            "pr": as_int(item.get("pr")) or (as_int(pr_number(url))),
            "pr_url": url,
            "added_at": item.get("added_at") or item.get("tracked_at")
            or item.get("first_seen"),
            "rerun_count": as_int(item.get("rerun_count")),
            "ci_fix_attempts": as_int(item.get("ci_fix_attempts")),
            "jittest_catch": bool(item.get("jittest:catch")
                                  or item.get("jittest_catch")),
        })
    return out


def token_totals(ledger_rows: list[dict], moment: datetime) -> dict:
    """Token totals for today (UTC) and for the last seven days."""
    today = moment.strftime("%Y-%m-%d")
    week_start = (moment - timedelta(days=6)).strftime("%Y-%m-%d")

    def totals(rows: list[dict]) -> dict:
        out = {"rows": len(rows), "tokens_in": 0, "tokens_out": 0,
               "tokens_total": 0}
        for row in rows:
            out["tokens_in"] += as_int(row.get("tokens_in")) or 0
            out["tokens_out"] += as_int(row.get("tokens_out")) or 0
            total = as_int(row.get("tokens_total"))
            if total is None:
                total = (as_int(row.get("tokens_in")) or 0) \
                    + (as_int(row.get("tokens_out")) or 0)
            out["tokens_total"] += total
        return out

    today_rows = [r for r in ledger_rows if str(r.get("ts") or "")[:10] == today]
    week_rows = [r for r in ledger_rows
                 if week_start <= str(r.get("ts") or "")[:10] <= today]
    return {"today": totals(today_rows), "week": totals(week_rows),
            "today_date": today, "week_start": week_start}


def backlog_empty_repos(state: dict) -> list[str]:
    """Repos whose backlog is empty, as far as the state files say.

    Derivable from three places, all optional: a `BACKLOG EMPTY` note
    on a worker entry, a `backlog_empty` ledger outcome, or an explicit
    `backlog_empty_repos` list in shepherd.json.
    """
    found = set()
    for entry in worker_entries(state["workers_raw"]):
        note = str(entry.get("note") or "").upper()
        if "BACKLOG EMPTY" in note:
            repo = str(entry.get("repo") or "")
            if repo:
                found.add(repo)
    for row in state["ledger_rows"]:
        outcome = str(row.get("outcome") or "").lower()
        if outcome in ("backlog_empty", "backlog-empty", "backlog:empty"):
            repo = str(row.get("repo") or "")
            if repo:
                found.add(repo)
    for repo in (state["shepherd_raw"] or {}).get("backlog_empty_repos") or []:
        if isinstance(repo, str) and repo:
            found.add(repo)
    return sorted(found)


def build_status(state_dir: str, config: dict, state: dict,
                 moment: datetime) -> dict:
    entries = worker_entries(state["workers_raw"])
    workers = worker_view(entries, state["ledger_rows"])
    in_flight = [w for w in workers if w["status"] in IN_FLIGHT_STATUSES]
    max_parallel = as_int((config.get("supervisor") or {}).get("max_parallel"))
    active = len(in_flight)
    by_status = {}
    for worker in workers:
        by_status[worker["status"]] = by_status.get(worker["status"], 0) + 1

    shepherd_raw = state["shepherd_raw"] or {}
    return {
        "schema": 1,
        "generated_at": stamp(moment),
        "state_dir": state_dir,
        "slots": {
            "max_parallel": max_parallel,
            "active": active,
            "free": (max_parallel - active) if max_parallel is not None
            else None,
            "source": "config:supervisor.max_parallel" if max_parallel
            else "unknown",
        },
        "workers": {
            "total": len(workers),
            "by_status": dict(sorted(by_status.items())),
            "in_flight": in_flight,
        },
        "shepherd": {
            "present": state["shepherd_raw"] is not None,
            "tracked_prs": shepherd_view(tracked_prs(state["shepherd_raw"])),
            "pending_trigger": shepherd_raw.get("pending_trigger") or {},
            "last_trigger": shepherd_raw.get("last_trigger") or {},
            "seen_merged": shepherd_raw.get("seen_merged") or [],
        },
        "backlog_empty_repos": backlog_empty_repos(state),
        "tokens": token_totals(state["ledger_rows"], moment),
        "ledger": {"path": state["ledger_path"],
                   "rows": len(state["ledger_rows"])},
        "files": [
            {"name": WORKERS_FILE, "path": state["workers_path"],
             "present": os.path.exists(state["workers_path"])},
            {"name": SHEPHERD_FILE, "path": state["shepherd_path"],
             "present": os.path.exists(state["shepherd_path"])},
            {"name": WORKER_LEDGER_FILE, "path": state["ledger_path"],
             "present": os.path.exists(state["ledger_path"])},
        ],
    }


# ---------------------------------------------------------------------
# collect --dry-run
# ---------------------------------------------------------------------
def build_collect(state_dir: str, state: dict, moment: datetime) -> dict:
    """Which workers a collector would take, and what the diff check says.

    Report only: nothing is collected, no state file is touched.
    """
    entries = worker_entries(state["workers_raw"])
    workers = worker_view(entries, state["ledger_rows"])
    collectible = []
    skipped = []
    for worker in workers:
        status = worker["status"]
        if status not in COLLECTIBLE_STATUSES:
            skipped.append({"run_id": worker["run_id"], "repo": worker["repo"],
                            "issue": worker["issue"], "status": status,
                            "reason": f"status '{status}' is not collectible"})
            continue
        entry = {
            "run_id": worker["run_id"], "repo": worker["repo"],
            "issue": worker["issue"], "branch": worker["branch"],
            "status": status, "workdir": worker["workdir"],
            "pr": worker["pr"], "tokens": worker["tokens"],
        }
        workdir = worker.get("workdir")
        if workdir and os.path.isdir(str(workdir)):
            report = os.path.join(str(workdir), "WORKER_REPORT.md")
            check = claim_vs_diff(report, str(workdir))
            entry["diff_check"] = {
                "status": check["status"], "detail": check["detail"],
                "report": report if os.path.exists(report) else None,
            }
        else:
            entry["diff_check"] = {
                "status": "unknown",
                "detail": "no worktree on disk (nothing to diff against)",
                "report": None,
            }
        collectible.append(entry)
    return {
        "schema": 1,
        "generated_at": stamp(moment),
        "state_dir": state_dir,
        "dry_run": True,
        "collectible": collectible,
        "skipped": skipped,
        "counts": {"collectible": len(collectible), "skipped": len(skipped)},
    }


# ---------------------------------------------------------------------
# shepherd-check
# ---------------------------------------------------------------------
def hard_stops_for(files: list) -> list[dict]:
    """Hard-stop hits in a PR's changed files."""
    hits = []
    for item in files:
        path = item.get("path") if isinstance(item, dict) else str(item)
        path = str(path or "")
        if not path:
            continue
        for kind, patterns in HARD_STOP_PATTERNS:
            for pattern in patterns:
                if pattern in path:
                    hits.append({"kind": kind, "path": path})
                    break
    return hits


def workflow_touch(hits: list[dict]) -> str:
    """The workflow-touch kind of a hard-stop set (one kind wins)."""
    kinds = [h["kind"] for h in hits]
    for kind in ("workflows", "branch_protection", "release_config"):
        if kind in kinds:
            return kind
    return "none"


def classify_ci(checks: list[dict] | None, known: list[str]) -> dict:
    """Classify the CI signal as PR-specific, repo-wide or unknown."""
    if checks is None:
        return {"classification": CI_UNKNOWN,
                "reason": "no CI data available",
                "failing": [], "known_repo_wide": []}
    failing = []
    for check in checks:
        state = str(check.get("state") or check.get("bucket") or "").upper()
        if state in ("FAILURE", "FAILED", "ERROR", "CANCELLED", "TIMED_OUT"):
            failing.append(str(check.get("name") or check.get("link") or "?"))
    if not failing:
        return {"classification": CI_GREEN, "reason": "no failing checks",
                "failing": [], "known_repo_wide": []}
    known_hits = [name for name in failing if name in known]
    if known_hits and len(known_hits) == len(failing):
        return {"classification": CI_REPO_WIDE,
                "reason": "every failing check is a known repo-wide failure",
                "failing": failing, "known_repo_wide": known_hits}
    return {"classification": CI_PR_SPECIFIC,
            "reason": "at least one failing check is not known repo-wide",
            "failing": failing, "known_repo_wide": known_hits}


def build_shepherd_check(state_dir: str, config: dict, state: dict,
                         probe, moment: datetime) -> dict:
    """One record per tracked PR: mergeability, CI signal, hard stops."""
    slugs = repo_slug_map(config)
    results = []
    for entry in shepherd_view(tracked_prs(state["shepherd_raw"])):
        repo = str(entry.get("repo") or "")
        slug = slugs.get(repo)
        pr = entry.get("pr")
        view = probe.view(slug, str(pr) if pr else "") or {}
        checks = probe.checks(slug, str(pr) if pr else "")
        files = view.get("files") if isinstance(view, dict) else None
        files = files if isinstance(files, list) else []
        hits = hard_stops_for(files)
        known = known_repo_wide_failures(config, repo)
        ci = classify_ci(checks, known)
        mergeable = view.get("mergeable") if isinstance(view, dict) else None
        merge_state = (view.get("mergeStateStatus")
                       if isinstance(view, dict) else None)
        results.append({
            "repo": repo,
            "pr": pr,
            "pr_url": entry.get("pr_url") or (view or {}).get("url") or "",
            "slug": slug,
            "state": (view or {}).get("state"),
            "mergeable": mergeable,
            "merge_state": merge_state,
            "ci": ci,
            "hard_stops": hits,
            "workflow_touch": workflow_touch(hits),
            "flags": (["jittest:catch"] if entry.get("jittest_catch")
                      else []),
            "rerun_count": entry.get("rerun_count"),
            "ci_fix_attempts": entry.get("ci_fix_attempts"),
            "evidence": [
                f"shepherd tracked PR {repo}#{pr}",
                f"mergeable={mergeable}",
                f"ci={ci['classification']}",
            ],
        })
    return {
        "schema": 1,
        "generated_at": stamp(moment),
        "state_dir": state_dir,
        "probe": getattr(probe, "kind", type(probe).__name__),
        "prs": results,
        "counts": {"tracked": len(results),
                   "mergeable": sum(1 for r in results
                                    if str(r["mergeable"] or "").upper()
                                    == "MERGEABLE"),
                   "hard_stops": sum(1 for r in results if r["hard_stops"]),
                   "flagged": sum(1 for r in results if r["flags"])},
    }


# ---------------------------------------------------------------------
# next-actions
# ---------------------------------------------------------------------
def reds_for(check: dict) -> list[dict]:
    """The reds of a PR, each classified."""
    ci = check.get("ci") or {}
    known = set(ci.get("known_repo_wide") or [])
    return [{"red": name,
             "class": CI_REPO_WIDE if name in known else CI_PR_SPECIFIC}
            for name in (ci.get("failing") or [])]


def build_next_actions(state_dir: str, config: dict, state: dict,
                       checks: dict, moment: datetime) -> dict:
    """The structured list of what the manager owes next."""
    actions = []

    for check in checks.get("prs", []):
        repo = str(check.get("repo") or "")
        pr = check.get("pr")
        reds = reds_for(check)
        hard = check.get("hard_stops") or []
        flags = check.get("flags") or []
        mergeable = str(check.get("mergeable") or "").upper()
        ci_class = (check.get("ci") or {}).get("classification", CI_UNKNOWN)

        if flags or hard or mergeable == "CONFLICTING":
            if flags:
                reason = (f"PR {repo}#{pr} carries a flag ({', '.join(flags)})"
                          " -- needs the operator's call")
                question = ("Does the flagged finding reproduce on the base "
                            "branch, and is it in scope for this PR?")
            elif hard:
                reason = (f"PR {repo}#{pr} touches "
                          f"{check.get('workflow_touch')} -- hard stop")
                question = ("Is this workflow / protection / release change "
                            "intended, and who signs it off?")
            else:
                reason = f"PR {repo}#{pr} is conflicting -- needs a rebase"
                question = "Should the branch be rebased onto the base?"
            actions.append({
                "kind": "flag",
                "repo": repo,
                "issue": None,
                "pr": pr,
                "reason_1line": reason,
                "reds_classified": reds,
                "underlying_question": question,
                "ci_signal_present": bool(reds),
                "workflow_touch": check.get("workflow_touch", "none"),
                "evidence": list(check.get("evidence") or []),
            })
        elif mergeable == "MERGEABLE" and ci_class in (CI_GREEN,
                                                       CI_REPO_WIDE):
            reason = (f"PR {repo}#{pr} is mergeable and CI is "
                      f"{ci_class} -- ready for the shepherd")
            actions.append({
                "kind": "merge",
                "repo": repo,
                "issue": None,
                "pr": pr,
                "reason_1line": reason,
                "evidence": list(check.get("evidence") or []),
            })

    for worker in worker_view(worker_entries(state["workers_raw"]),
                               state["ledger_rows"]):
        status = worker["status"]
        if status == "split_suggested":
            actions.append({
                "kind": "split",
                "repo": worker["repo"],
                "issue": worker["issue"],
                "pr": None,
                "reason_1line": (f"{worker['repo']}#{worker['issue']} was "
                                 "flagged for a split -- the manager's to "
                                 "decide"),
                "evidence": [f"workers.json status={status}",
                             f"branch={worker['branch']}"],
            })
        elif status == "escalated":
            actions.append({
                "kind": "escalation",
                "repo": worker["repo"],
                "issue": worker["issue"],
                "pr": None,
                "reason_1line": (f"{worker['repo']}#{worker['issue']} was "
                                 "escalated -- needs the operator"),
                "evidence": [f"workers.json status={status}",
                             f"branch={worker['branch']}"],
            })

    for repo in backlog_empty_repos(state):
        actions.append({
            "kind": "backlog_empty",
            "repo": repo,
            "issue": None,
            "pr": None,
            "reason_1line": f"{repo} has no open issues left -- pick work "
                             "elsewhere or file new work",
            "evidence": ["state files record a backlog-empty signal"],
        })

    actions.sort(key=lambda a: (KIND_ORDER.get(a["kind"], 9),
                                 str(a.get("repo") or ""),
                                 str(a.get("pr") or a.get("issue") or "")))
    return {
        "schema": 1,
        "generated_at": stamp(moment),
        "state_dir": state_dir,
        "actions": actions,
        "counts": {kind: sum(1 for a in actions if a["kind"] == kind)
                   for kind in ACTION_KINDS},
    }


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def build_probe(args, config: dict):
    """The PR-fact source: a fixture file when given, else live `gh`."""
    if getattr(args, "pr_data", None):
        probe = FileProbe(args.pr_data)
        probe.kind = "file"
        return probe
    probe = GhProbe()
    probe.kind = "gh"
    return probe


def print_status(result: dict) -> None:
    slots = result["slots"]
    print(f"state dir: {result['state_dir']}")
    print(f"slots: {slots['active']} active"
          + (f" / {slots['max_parallel']}" if slots["max_parallel"] else "")
          + (f" (free {slots['free']})" if slots["free"] is not None else ""))
    print(f"workers in flight: {len(result['workers']['in_flight'])}")
    for worker in result["workers"]["in_flight"]:
        print(f"  {worker['repo']}#{worker['issue']} {worker['status']} "
              f"{worker['branch']} tier={worker['tier'] or '?'} "
              f"tokens={worker['tokens'] if worker['tokens'] is not None else '?'}")
    print(f"tracked PRs: {len(result['shepherd']['tracked_prs'])}")
    print(f"pending trigger: {result['shepherd']['pending_trigger'] or 'none'}")
    print(f"backlog-empty repos: {result['backlog_empty_repos'] or 'none'}")
    tokens = result["tokens"]
    print(f"tokens today: {tokens['today']['tokens_total']:,} "
          f"({tokens['today']['rows']} rows)")
    print(f"tokens this week: {tokens['week']['tokens_total']:,} "
          f"({tokens['week']['rows']} rows)")


def print_collect(result: dict) -> None:
    print(f"state dir: {result['state_dir']} (dry run -- nothing collected)")
    for entry in result["collectible"]:
        check = entry["diff_check"]
        print(f"  collectible {entry['repo']}#{entry['issue']} "
              f"{entry['status']} diff-check={check['status']}: {check['detail']}")
    for entry in result["skipped"]:
        print(f"  skip {entry['repo']}#{entry['issue']} {entry['status']}: "
              f"{entry['reason']}")
    print(f"collectible: {result['counts']['collectible']}, "
          f"skipped: {result['counts']['skipped']}")


def print_shepherd_check(result: dict) -> None:
    for pr in result["prs"]:
        flags = ", ".join(pr["flags"]) or "none"
        print(f"  {pr['repo']}#{pr['pr']} mergeable={pr['mergeable']} "
              f"ci={pr['ci']['classification']} "
              f"workflow_touch={pr['workflow_touch']} "
              f"flags={flags}")
    print(f"tracked: {result['counts']['tracked']}, "
          f"mergeable: {result['counts']['mergeable']}, "
          f"hard stops: {result['counts']['hard_stops']}")


def print_next_actions(result: dict) -> None:
    for action in result["actions"]:
        target = action.get("issue")
        if target is None:
            target = action.get("pr")
        label = f"{action['repo']}#{target}" if target is not None \
            else action["repo"]
        print(f"  [{action['kind']}] {label} -- {action['reason_1line']}")
    print("counts: " + ", ".join(f"{kind}={count}" for kind, count
                                  in sorted(result["counts"].items())))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(parser):
        parser.add_argument("--state-dir", default=None,
                            help="state dir (default: the deployment "
                                 "config's state.dir)")
        parser.add_argument("--config", default=None,
                            help="deployment config path (default: the "
                                 "usual search order)")
        parser.add_argument("--json", action="store_true",
                            help="print JSON instead of a human summary")

    p_status = sub.add_parser("status", help="fleet status (read-only)")
    common(p_status)
    p_collect = sub.add_parser("collect",
                                help="collectible workers and their diff check "
                                     "(read-only)")
    p_collect.add_argument("--dry-run", action="store_true",
                            help="only ever a dry run in Phase 0")
    common(p_collect)
    p_shep = sub.add_parser("shepherd-check",
                            help="per tracked PR: mergeable, CI, hard stops")
    p_shep.add_argument("--pr-data", default=None,
                        help="JSON file with PR facts (fixture; replaces gh)")
    common(p_shep)
    p_next = sub.add_parser("next-actions",
                            help="structured list of the manager's next moves")
    p_next.add_argument("--pr-data", default=None,
                        help="JSON file with PR facts (fixture; replaces gh)")
    common(p_next)

    args = ap.parse_args(argv)

    config = load_deployment_config(args.config)
    state_dir = resolve_state_dir(config, args.state_dir)
    if not os.path.isdir(state_dir):
        print(f"plan_coder: state dir not found: {state_dir}", file=sys.stderr)
        return 2

    moment = now_utc()
    state = collect_state(state_dir)

    if args.cmd == "status":
        result = build_status(state_dir, config, state, moment)
        if args.json:
            print(json.dumps(result, indent=1, sort_keys=True))
        else:
            print_status(result)
        return 0

    if args.cmd == "collect":
        result = build_collect(state_dir, state, moment)
        if args.json:
            print(json.dumps(result, indent=1, sort_keys=True))
        else:
            print_collect(result)
        return 0

    probe = build_probe(args, config)

    if args.cmd == "shepherd-check":
        result = build_shepherd_check(state_dir, config, state, probe, moment)
        if args.json:
            print(json.dumps(result, indent=1, sort_keys=True))
        else:
            print_shepherd_check(result)
        return 0

    if args.cmd == "next-actions":
        checks = build_shepherd_check(state_dir, config, state, probe, moment)
        result = build_next_actions(state_dir, config, state, checks, moment)
        if args.json:
            print(json.dumps(result, indent=1, sort_keys=True))
        else:
            print_next_actions(result)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
