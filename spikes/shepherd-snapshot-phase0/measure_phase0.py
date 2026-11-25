#!/usr/bin/env python3
"""Phase 0 measurement for plan-coder issue #124 (shepherd shadow typed
extraction contract).

Retrospective, kill-gated measurement: reconstruct decision-time snapshots
for the 40 most recent TERMINAL shepherd dispositions in the live shepherd
ledger and measure

  1. per-field completeness of the reconstructed snapshot (as of ledger_ts),
  2. agreement between the reconstructed typed CI state and the ledger's
     free-text `--ci` prose,
  3. invisible-error yield (decision-time evidence empty / stale by more than
     one shepherd cycle / contradicting the prose),
  4. median `gh` calls per disposition used for the reconstruction,
  5. the changed-files approximation used for still-open PRs.

Phase 0 ONLY: nothing here writes to the ledger, nothing is deployed, no
prompt/config/schema is touched.  The only writes are `results.json` next to
this script.

Usage:
    python3 spikes/shepherd-snapshot-phase0/measure_phase0.py
        [--ledger PATH] [--sample N] [--out PATH] [--variant all]

The ledger corpus is read-only.  `gh` calls are read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import UTC, datetime

LEDGER_DEFAULT = (
    "/home/hatch/workspace/goals/token-plan-coding-manager-setup/"
    "hidden_files/shepherd_ledger.jsonl"
)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DEFAULT = os.path.join(HERE, "results.json")

# Issue #124 names the terminal dispositions it cares about.  `needs_alex`
# is the ledger's older name for the same class as `needs_operator` (the
# shepherd parks the PR for a human); it is counted as terminal too.  The
# 40-row window measured here is 2026-11-23..2026-11-25, entirely after the
# last `needs_alex` row (2026-11-18), so the sample is identical with or
# without it -- the script verifies that claim instead of asserting it.
TERMINAL_ACTIONS = {
    "merged",
    "merged_by_shepherd",
    "needs_operator",
    "closed_unmerged",
    "merge_conflicts",
    "needs_alex",
}
TERMINAL_ISSUE_LIST = TERMINAL_ACTIONS - {"needs_alex"}

# One shepherd cycle, from the deployment example config
# (workers.shepherd.interval_minutes = 30).  "Stale by more than one cycle"
# means the newest CI evidence that had completed at decision time was
# already older than this.
CYCLE_MINUTES = 30

PROXY_URL = "http://hatch-egress-proxy:3128"

TEST_PATH_RULES = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)test_[^/]*\.[a-z]+$"),
    re.compile(r"(^|/)[^/]*_test\.[a-z]+$"),
    re.compile(r"(^|/)[^/]*\.test\.[a-z]+$"),
    re.compile(r"(^|/)spec/"),
)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        return None


class Gh:
    """Minimal read-only `gh` runner that counts every invocation."""

    def __init__(self, proxy: str | None) -> None:
        self.env = dict(os.environ)
        if proxy:
            self.env["HTTPS_PROXY"] = proxy
            self.env["HTTP_PROXY"] = proxy
        else:
            self.env.pop("HTTPS_PROXY", None)
            self.env.pop("HTTP_PROXY", None)
        self.calls = 0

    def run(self, args: list[str]) -> tuple[int, str, str]:
        self.calls += 1
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=120,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def json(self, args: list[str]) -> tuple[bool, object, str]:
        code, out, err = self.run(args)
        if code != 0:
            return False, None, (err or out).strip()[:400]
        try:
            return True, json.loads(out), ""
        except json.JSONDecodeError as exc:
            return False, None, f"json decode error: {exc}"


def pick_proxy() -> tuple[str | None, str]:
    """Return (proxy, note).  Probes `gh api rate_limit` once (not counted
    against any disposition) to find out whether the cell needs the egress
    proxy."""
    for proxy in (None, PROXY_URL):
        gh = Gh(proxy)
        ok, _, err = gh.json(["api", "rate_limit"])
        if ok:
            note = "direct" if proxy is None else f"egress proxy {proxy}"
            return proxy, note
        last = err
    return PROXY_URL, f"probe failed, assuming proxy {PROXY_URL}: {last}"


def load_ledger(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def is_test_path(path: str) -> bool:
    return any(rule.search(path) for rule in TEST_PATH_RULES)


def is_workflow_path(path: str) -> bool:
    return path.startswith(".github/workflows/")


# --------------------------------------------------------------------------
# prose parsing (ledger `--ci` free text -> one of the five typed states,
# or "unverifiable")
# --------------------------------------------------------------------------
NEGATED_FAILURE = re.compile(
    r"(no|not|never|zero|0)\s+(?:\w+\s+){0,2}?(fail|failure|failed|failures|"
    r"red|broken)",
    re.IGNORECASE,
)
FAILURE_COUNT = re.compile(
    r"(\d+)\s*(?:x\s*)?(?:fail|failure|failed|failures|red)\b", re.IGNORECASE
)
FAILURE_TOKEN = re.compile(
    r"\b(fail|failure|failed|fails|failing|red|broken)\b", re.IGNORECASE
)
SUCCESS_TOKEN = re.compile(
    r"\b(pass|passed|passes|success|successful|green|clean|ok)\b", re.IGNORECASE
)
PENDING_TOKEN = re.compile(
    r"\b(in_progress|in progress|pending|queued|running|waiting)\b", re.IGNORECASE
)
NO_CI_TOKEN = re.compile(
    r"\b(no\s+(ci|checks?|workflow)|no\s+ci\s+configured|without\s+ci)\b",
    re.IGNORECASE,
)


def parse_prose(text: str) -> tuple[str, str]:
    """Map the ledger's free-text `--ci` to a typed state.

    Returns (state, reason).  `state` is one of success/failure/pending/
    no_ci_configured, or "unverifiable" when the prose carries no decidable
    CI claim (per issue #124: unparseable prose is unverifiable, never a
    disagreement).
    """
    raw = (text or "").strip()
    if not raw:
        return "unverifiable", "empty prose"
    if NO_CI_TOKEN.search(raw):
        return "no_ci_configured", "prose says no CI"
    cleaned = NEGATED_FAILURE.sub(" ", raw)
    counts = [int(m.group(1)) for m in FAILURE_COUNT.finditer(cleaned)]
    if any(count > 0 for count in counts):
        return "failure", f"failure count {max(counts)} in prose"
    if FAILURE_TOKEN.search(cleaned):
        return "failure", "failure token in prose"
    if PENDING_TOKEN.search(raw):
        return "pending", "pending token in prose"
    if SUCCESS_TOKEN.search(raw):
        return "success", "success token in prose"
    return "unverifiable", "no decidable CI token in prose"


# --------------------------------------------------------------------------
# reconstruction
# --------------------------------------------------------------------------
CI_FAILURE_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED",
                          "STARTUP_FAILURE", "STALE"}
CI_SUCCESS_CONCLUSIONS = {"SUCCESS", "SKIPPED", "NEUTRAL"}


def check_state_from_runs(runs: list[dict], ledger_ts: datetime) -> tuple[str, dict]:
    """Typed CI state from check runs, filtered as of ledger_ts.

    A run counts as concluded only when completed_at <= ledger_ts; a run
    that had started by ledger_ts but had not completed by then is
    pending; runs that started after ledger_ts are invisible to the
    decision and are dropped.
    """
    concluded = []
    pending = 0
    dropped = 0
    newest_completion = None
    for run in runs:
        started = parse_ts(run.get("started_at") or run.get("startedAt") or "")
        completed = parse_ts(
            run.get("completed_at") or run.get("completedAt") or ""
        )
        if started is not None and started > ledger_ts:
            dropped += 1
            continue
        if completed is not None and completed <= ledger_ts:
            concluded.append(run)
            if newest_completion is None or completed > newest_completion:
                newest_completion = completed
        else:
            pending += 1
    failures = [
        run
        for run in concluded
        if str(run.get("conclusion") or "").upper() in CI_FAILURE_CONCLUSIONS
    ]
    if failures:
        state = "failure"
    elif pending:
        state = "pending"
    elif concluded:
        state = "success"
    else:
        state = "no_runs"
    counts = {
        "total": len(concluded) + pending,
        "success": sum(
            1
            for run in concluded
            if str(run.get("conclusion") or "").upper() in CI_SUCCESS_CONCLUSIONS
        ),
        "failure": len(failures),
        "pending": pending,
        "dropped_after_ledger_ts": dropped,
        "concluded": len(concluded),
    }
    # Name-level rollup: the latest attempt per check NAME, which is what
    # `gh pr view --json statusCheckRollup` reports and what the ledger's
    # own typed packet (#130) counts.
    latest: dict = {}
    for run in runs:
        started = parse_ts(run.get("started_at") or run.get("startedAt") or "")
        if started is not None and started > ledger_ts:
            continue
        name = run.get("name") or ""
        key = (started or datetime.min.replace(tzinfo=UTC))
        if name not in latest or key >= latest[name][0]:
            latest[name] = (key, run)
    names = [run for _, run in latest.values()]
    name_failure = [
        run
        for run in names
        if str(run.get("conclusion") or "").upper() in CI_FAILURE_CONCLUSIONS
    ]
    name_pending = [
        run for run in names if not (run.get("conclusion") or run.get("completed_at"))
    ]
    counts["by_name"] = {
        "total": len(names),
        "success": sum(
            1
            for run in names
            if str(run.get("conclusion") or "").upper() in CI_SUCCESS_CONCLUSIONS
        ),
        "failure": len(name_failure),
        "pending": len(name_pending),
    }
    return state, {
        "counts": counts,
        "newest_completed_at": newest_completion.strftime("%Y-%m-%dT%H:%M:%SZ")
        if newest_completion
        else None,
        "failing_checks": [
            {
                "name": run.get("name"),
                "conclusion": run.get("conclusion"),
                "completed_at": run.get("completed_at")
                or run.get("completedAt"),
            }
            for run in failures
        ],
    }


def gh_pr_view(gh: Gh, repo: str, number: int) -> tuple[dict | None, str, int]:
    fields = ",".join(
        [
            "number",
            "title",
            "state",
            "mergedAt",
            "closedAt",
            "mergeStateStatus",
            "headRefOid",
            "headRefName",
            "baseRefName",
            "baseRefOid",
            "additions",
            "deletions",
            "changedFiles",
            "files",
            "statusCheckRollup",
            "commits",
        ]
    )
    before = gh.calls
    ok, data, err = gh.json(
        ["pr", "view", str(number), "--repo", repo, "--json", fields]
    )
    used = gh.calls - before
    if not ok:
        return None, err, used
    return data, "", used


def repo_has_workflows(gh: Gh, repo: str, cache: dict) -> tuple[bool | None, str, int]:
    """(has_workflows, note, gh_calls).  Cached per repo: the call is made
    once per repo and charged to the disposition that triggered it."""
    if repo in cache:
        return cache[repo][0], "", 0
    code, out, err = gh.run(
        ["api", f"repos/{repo}/contents/.github/workflows", "--jq", ".[].name"]
    )
    if code != 0:
        if "404" in err or "Not Found" in err:
            cache[repo] = (False, "no .github/workflows directory")
            return False, cache[repo][1], 1
        cache[repo] = (None, err.strip()[:200])
        return None, cache[repo][1], 1
    cache[repo] = (True, out.strip().replace("\n", ",")[:200])
    return True, cache[repo][1], 1


def commit_date(gh: Gh, repo: str, sha: str) -> str:
    code, out, err = gh.run(
        ["api", f"repos/{repo}/commits/{sha}", "--jq", ".commit.committer.date"]
    )
    if code != 0:
        return ""
    return out.strip()


def base_sha_as_of(gh: Gh, repo: str, base_ref: str, base_ref_oid: str,
                   ledger_ts: datetime, pr_oids: set[str]):
    """Reconstruct the base-branch tip as of ledger_ts.

    GitHub's stored `baseRefOid` for a PR is the base branch tip recorded
    when the PR reached its terminal state.  When that commit predates the
    decision it IS the as-of base (used directly).  When the PR was
    terminated later than the decision (a needs_operator flag that was
    merged later), `baseRefOid` post-dates the decision and the base tip is
    reconstructed from the base branch's commit history up to ledger_ts,
    excluding the PR's own commits (they must not be mistaken for base).
    """
    if base_ref_oid:
        dated = commit_date(gh, repo, base_ref_oid)
        dated_dt = parse_ts(dated)
        if dated_dt is not None and dated_dt <= ledger_ts:
            return base_ref_oid, "baseRefOid (committer date <= ledger_ts)", 2
    if not base_ref:
        return None, "no base ref", 0
    code, out, err = gh.run(
        [
            "api",
            (
                f"repos/{repo}/commits?sha={base_ref}&until="
                f"{ledger_ts.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            ),
            "--jq",
            '[.[] | {"sha": .sha, "d": .commit.committer.date}]',
        ]
    )
    if code != 0:
        return None, f"base commit list failed: {err.strip()[:160]}", 1
    try:
        entries = json.loads(out)
    except json.JSONDecodeError:
        return None, "unparseable base commit list", 1
    candidates = []
    for entry in entries:
        sha = entry.get("sha")
        when = parse_ts(entry.get("d") or "")
        if not sha or when is None or when > ledger_ts:
            continue
        if sha in pr_oids:
            continue
        candidates.append((when, sha))
    if not candidates:
        return None, "no base commit at or before ledger_ts", 1
    candidates.sort()
    return (
        candidates[-1][1],
        "base branch commit history up to ledger_ts (PR commits excluded)",
        1,
    )


def rest_check_runs(gh: Gh, repo: str, sha: str):
    """All check-run attempts recorded for a SHA (REST)."""
    code, out, err = gh.run(
        ["api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100"]
    )
    if code != 0:
        return [], "rest:check-runs(failed)", err.strip()[:200]
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return [], "rest:check-runs(unparseable)", "unparseable payload"
    runs = [
        {
            "name": run.get("name"),
            "conclusion": run.get("conclusion"),
            "status": run.get("status"),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
        }
        for run in payload.get("check_runs", [])
    ]
    return runs, "rest:check-runs(as-of head)", ""


def reconstruct_row(gh: Gh, row: dict, workflow_cache: dict) -> dict:
    repo = row.get("repo", "")
    number = int(row.get("pr") or 0)
    ledger_ts = parse_ts(row.get("ts", ""))
    record = {
        "ts": row.get("ts"),
        "repo": repo,
        "pr": number,
        "action": row.get("action"),
        "outcome": row.get("outcome"),
        "ledger_ci_prose": row.get("ci", ""),
        "ledger_merge_state": row.get("merge_state", ""),
        "ledger_head_sha": row.get("head_sha"),
        "ledger_ci_checks": row.get("ci_checks"),
        "gh_calls": 0,
    }
    if ledger_ts is None:
        record["error"] = "unparseable ledger ts"
        return record
    if not repo or repo in {"queue", "baseline"} or number <= 0:
        record["error"] = "not a PR-scoped disposition"
        record["ci_state"] = "not_found"
        return record

    data, err, used = gh_pr_view(gh, repo, number)
    record["gh_calls"] += used
    if data is None:
        record["error"] = f"gh pr view failed: {err}"
        record["ci_state"] = "not_found"
        record["pr_found"] = False
        return record
    record["pr_found"] = True
    record["pr_state"] = data.get("state")
    record["merged_at"] = data.get("mergedAt")
    record["closed_at"] = data.get("closedAt")
    record["title"] = data.get("title")
    record["merge_state_status"] = data.get("mergeStateStatus")
    # What ONE live `gh pr view --json ...` call delivers.  A live capture
    # (Phase 1) would read exactly these fields at decision time, so the
    # capture cost per disposition is one gh call; the extra calls below
    # are retrospective-only (as-of reconstruction).
    record["live_capture_fields"] = {
        "head_sha": bool(data.get("headRefOid")),
        "base_sha": bool(data.get("baseRefOid")),
        "merge_state": data.get("mergeStateStatus") not in (None, ""),
        "ci_rollup": data.get("statusCheckRollup") is not None,
        "files": data.get("files") is not None,
    }
    record["live_capture_calls"] = (
        1 if all(record["live_capture_fields"].values()) else 0
    )

    # --- head SHA as of ledger_ts -------------------------------------
    commits = data.get("commits") or []
    as_of_commits = [
        c
        for c in commits
        if parse_ts(c.get("committedDate") or "") is not None
        and parse_ts(c.get("committedDate") or "") <= ledger_ts
    ]
    as_of_commits.sort(key=lambda c: c.get("committedDate") or "")
    as_of_head = as_of_commits[-1]["oid"] if as_of_commits else None
    final_head = data.get("headRefOid")
    record["head_sha_as_of"] = as_of_head
    record["head_sha_final"] = final_head
    record["head_moved_after_decision"] = bool(
        as_of_head and final_head and as_of_head != final_head
    )
    record["head_sha_matches_ledger"] = (
        None
        if not (as_of_head and row.get("head_sha"))
        else as_of_head == row.get("head_sha")
    )

    # --- base SHA as of ledger_ts --------------------------------------
    base_ref = data.get("baseRefName") or ""
    base_ref_oid = data.get("baseRefOid") or ""
    pr_oids = {c.get("oid") for c in commits if c.get("oid")}
    base_sha, base_method, base_calls = base_sha_as_of(
        gh, repo, base_ref, base_ref_oid, ledger_ts, pr_oids
    )
    record["gh_calls"] += base_calls
    record["base_sha_as_of"] = base_sha
    record["base_sha_method"] = base_method
    record["base_ref_oid_at_terminal"] = base_ref_oid
    record["base_sha_matches_terminal_oid"] = (
        None
        if not (base_sha and base_ref_oid)
        else base_sha == base_ref_oid
    )

    # --- CI check runs as of ledger_ts ----------------------------------
    rollup = data.get("statusCheckRollup") or []
    if isinstance(rollup, dict):
        nodes = rollup.get("checkRuns") or []
    else:
        nodes = rollup
    check_runs = []
    for node in nodes:
        check_runs.append(
            {
                "name": node.get("name"),
                "conclusion": node.get("conclusion"),
                "status": node.get("status"),
                "started_at": node.get("startedAt"),
                "completed_at": node.get("completedAt"),
            }
        )
    rollup_sha_ok = (not record["head_moved_after_decision"]) and bool(as_of_head)
    if not rollup_sha_ok and as_of_head:
        # The GraphQL rollup belongs to the FINAL head; the as-of head is a
        # different commit, so ask the REST endpoint about that SHA.
        check_runs, source, err = rest_check_runs(gh, repo, as_of_head)
        record["gh_calls"] += 1
        record["check_runs_source"] = source
        if err:
            record["check_runs_error"] = err
    else:
        record["check_runs_source"] = "graphql statusCheckRollup"

    state, detail = check_state_from_runs(check_runs, ledger_ts)
    if state == "no_runs" and as_of_head:
        # The GraphQL rollup shows only the LATEST attempt per check name.
        # If nothing is visible as of ledger_ts, ask REST for every attempt
        # on that SHA before concluding that there was no CI evidence.
        rest_runs, source, err = rest_check_runs(gh, repo, as_of_head)
        record["gh_calls"] += 1
        if rest_runs:
            rest_state, rest_detail = check_state_from_runs(rest_runs, ledger_ts)
            if rest_state != "no_runs":
                state, detail = rest_state, rest_detail
                record["check_runs_source"] = source
            elif err:
                record["check_runs_error"] = err
    record["ci_counts_as_of"] = detail["counts"]
    record["ci_newest_completed_at"] = detail["newest_completed_at"]
    record["ci_failing_checks"] = detail["failing_checks"]

    has_workflows, wf_note, wf_calls = repo_has_workflows(gh, repo, workflow_cache)
    record["gh_calls"] += wf_calls
    record["repo_has_ci_workflows"] = has_workflows
    record["repo_ci_note"] = wf_note
    counts = detail["counts"]
    if counts["failure"]:
        record["ci_state"] = "failure"
    elif has_workflows is False:
        # No CI is configured in the repository at all: a fact, not an
        # error (issue #124's `no_ci_configured`).
        record["ci_state"] = "no_ci_configured"
    elif state == "pending" or counts["pending"]:
        record["ci_state"] = "pending"
    elif counts["concluded"]:
        record["ci_state"] = "success"
    elif has_workflows is True:
        # CI is configured but nothing had run by decision time.
        record["ci_state"] = "pending"
        record["ci_state_note"] = "CI configured, no run visible as-of"
    else:
        record["ci_state"] = "not_found"
        record["ci_state_note"] = "workflow listing failed and no run visible"

    # --- changed files span ---------------------------------------------
    files = data.get("files") or []
    paths = [f.get("path") for f in files if f.get("path")]
    record["changed_files_count"] = data.get("changedFiles")
    record["changed_files_paths_returned"] = len(paths)
    record["files_truncated"] = bool(
        data.get("changedFiles") is not None and len(paths) < data["changedFiles"]
    )
    record["additions"] = data.get("additions")
    record["deletions"] = data.get("deletions")
    record["additions_from_file_list"] = sum(f.get("additions") or 0 for f in files)
    record["deletions_from_file_list"] = sum(f.get("deletions") or 0 for f in files)
    record["touches_workflow"] = any(is_workflow_path(p) for p in paths)
    record["touches_tests"] = any(is_test_path(p) for p in paths)
    record["first_20_paths"] = paths[:20]
    record["pr_open_at_decision"] = record["pr_state"] == "OPEN"
    record["changed_files_approximation"] = (
        "current head vs base diff of a still-open PR"
        if record["pr_open_at_decision"]
        else "terminal (merged/closed) diff"
    )
    return record


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
CONTRACT_FIELDS = [
    "ci_state",
    "ci_counts",
    "head_sha",
    "base_sha",
    "merge_state",
    "changed_files_count",
    "additions",
    "deletions",
    "touches_workflow",
    "touches_tests",
    "first_20_paths",
]


def field_completeness(records: list[dict]) -> dict:
    out = {}
    for field in CONTRACT_FIELDS:
        present = 0
        approximate = 0
        reasons: dict[str, int] = {}
        for rec in records:
            ok = False
            approx = False
            reason = ""
            if field == "ci_state":
                ok = rec.get("ci_state") not in (None, "not_found")
                reason = "PR/SHA not retrievable" if not ok else ""
            elif field == "ci_counts":
                counts = rec.get("ci_counts_as_of") or {}
                ok = bool(counts.get("total"))
                reason = "no check run visible as-of" if not ok else ""
            elif field == "head_sha":
                ok = bool(rec.get("head_sha_as_of"))
                reason = "no commit at or before ledger_ts" if not ok else ""
            elif field == "base_sha":
                ok = bool(rec.get("base_sha_as_of"))
                reason = "base SHA not reconstructible as-of" if not ok else ""
            elif field == "merge_state":
                status = rec.get("merge_state_status")
                if status in (None, "", "UNKNOWN"):
                    ok = False
                    reason = "mergeStateStatus UNKNOWN (destroyed by the merge)"
                elif rec.get("pr_open_at_decision"):
                    ok = False
                    approx = True
                    reason = "still-open PR: current status, not decision-time"
                else:
                    ok = False
                    reason = "terminal PR: merge state destroyed by the merge"
            elif field in ("changed_files_count", "additions", "deletions",
                           "touches_workflow", "touches_tests", "first_20_paths"):
                ok = rec.get("pr_found") and rec.get("changed_files_count") is not None
                if not ok:
                    reason = "PR not retrievable"
                elif rec.get("pr_open_at_decision"):
                    approx = True
                    reason = "still-open PR: current diff used as approximation"
                elif rec.get("head_moved_after_decision"):
                    approx = True
                    reason = (
                        "head advanced after the decision: the final diff is used "
                        "as an approximation, not the decision-time diff"
                    )
            if ok:
                present += 1
            elif approx:
                approximate += 1
            if reason and not ok:
                reasons[reason] = reasons.get(reason, 0) + 1
        total = len(records)
        out[field] = {
            "complete": present,
            "approximate_only": approximate,
            "missing": total - present - approximate,
            "complete_pct": round(100.0 * present / total, 1) if total else 0.0,
            "complete_plus_approx_pct": round(
                100.0 * (present + approximate) / total, 1
            )
            if total
            else 0.0,
            "reasons": reasons,
        }
    return out


def agreement(records: list[dict]) -> dict:
    agree = 0
    disagree = 0
    unverifiable = 0
    details = []
    for rec in records:
        prose = rec.get("ledger_ci_prose") or ""
        prose_state, prose_reason = parse_prose(prose)
        rec["prose_state"] = prose_state
        rec["prose_reason"] = prose_reason
        recon = rec.get("ci_state")
        if prose_state == "unverifiable":
            unverifiable += 1
            rec["agreement"] = "unverifiable"
        elif prose_state == recon:
            agree += 1
            rec["agreement"] = "agree"
        else:
            disagree += 1
            rec["agreement"] = "disagree"
        details.append(
            {
                "ts": rec.get("ts"),
                "repo": rec.get("repo"),
                "pr": rec.get("pr"),
                "action": rec.get("action"),
                "prose": prose,
                "prose_state": prose_state,
                "prose_reason": prose_reason,
                "reconstructed": recon,
                "agreement": rec.get("agreement"),
            }
        )
    verifiable = agree + disagree
    return {
        "agree": agree,
        "disagree": disagree,
        "unverifiable": unverifiable,
        "verifiable": verifiable,
        "agreement_rate_pct_of_verifiable": (
            round(100.0 * agree / verifiable, 1) if verifiable else None
        ),
        "agreement_rate_pct_of_sample": (
            round(100.0 * agree / len(records), 1) if records else 0.0
        ),
        "details": details,
    }


def invisible_error_yield(records: list[dict], cycle_minutes: int) -> dict:
    flags = []
    empty = []
    stale = []
    contradicting = []
    head_moved = []
    for rec in records:
        row_flags = []
        counts = rec.get("ci_counts_as_of") or {}
        has_evidence = bool(counts.get("total"))
        if not has_evidence:
            row_flags.append("empty")
            empty.append(rec)
        newest = rec.get("ci_newest_completed_at")
        if newest:
            newest_dt = parse_ts(newest)
            ledger_dt = parse_ts(rec.get("ts") or "")
            if newest_dt and ledger_dt:
                age = (ledger_dt - newest_dt).total_seconds() / 60.0
                rec["ci_evidence_age_minutes"] = round(age, 1)
                if age > cycle_minutes:
                    row_flags.append("stale_by_cycle")
                    stale.append(rec)
        else:
            rec["ci_evidence_age_minutes"] = None
        if rec.get("agreement") == "disagree":
            row_flags.append("contradicts_prose")
            contradicting.append(rec)
        if rec.get("head_moved_after_decision"):
            row_flags.append("head_moved_after_decision")
            head_moved.append(rec)
        rec["invisible_error_flags"] = row_flags
        flags.append(row_flags)
    affected = sum(1 for f in flags if f)
    core = sum(
        1
        for f in flags
        if {"empty", "stale_by_cycle", "contradicts_prose"} & set(f)
    )
    return {
        "cycle_minutes": cycle_minutes,
        "rows_with_any_flag": affected,
        "rows_with_core_flag": core,
        "core_yield_pct": round(100.0 * core / len(records), 1) if records else 0.0,
        "any_flag_yield_pct": (
            round(100.0 * affected / len(records), 1) if records else 0.0
        ),
        "empty_rows": len(empty),
        "stale_rows": len(stale),
        "contradicting_rows": len(contradicting),
        "head_moved_rows": len(head_moved),
        "empty_ledger_prose_rows": sum(
            1 for rec in records if not (rec.get("ledger_ci_prose") or "").strip()
        ),
        "no_ci_configured_rows": sum(
            1 for rec in records if rec.get("ci_state") == "no_ci_configured"
        ),
    }


def ledger_packet_agreement(records: list[dict]) -> dict:
    """Cross-check against the ledger's own typed CI packet (issue #130),
    which the shepherd wrote at decision time.  Not part of the issue's
    agreement metric (that one is prose vs snapshot); reported because it
    is the only other typed record of the same fact."""
    match = 0
    mismatch = 0
    absent = 0
    details = []
    for rec in records:
        packet = rec.get("ledger_ci_checks")
        counts = rec.get("ci_counts_as_of") or {}
        names = counts.get("by_name") or {}
        if not isinstance(packet, dict) or not counts:
            absent += 1
            continue
        same = all(
            int(packet.get(key, -1)) == int(names.get(key, -2))
            for key in ("total", "success", "failure", "pending")
        )
        if same:
            match += 1
        else:
            mismatch += 1
            details.append(
                {
                    "ts": rec.get("ts"),
                    "repo": rec.get("repo"),
                    "pr": rec.get("pr"),
                    "ledger_packet": {
                        k: packet.get(k) for k in ("total", "success", "failure", "pending")
                    },
                    "reconstructed_by_name": {
                        k: names.get(k) for k in ("total", "success", "failure", "pending")
                    },
                    "reconstructed_runs": {
                        k: counts.get(k) for k in ("total", "success", "failure", "pending")
                    },
                }
            )
    return {
        "rows_with_ledger_packet": match + mismatch,
        "packet_matches_reconstruction": match,
        "packet_mismatches_reconstruction": mismatch,
        "rows_without_packet": absent,
        "mismatches": details,
    }


def cost_stats(records: list[dict]) -> dict:
    calls = [rec.get("gh_calls", 0) for rec in records]
    capture = [
        rec.get("live_capture_calls")
        for rec in records
        if rec.get("live_capture_calls") is not None
    ]
    if not calls:
        return {}
    out = {
        "calls_per_disposition": calls,
        "total_calls": sum(calls),
        "median_calls": statistics.median(calls),
        "mean_calls": round(statistics.fmean(calls), 2),
        "min_calls": min(calls),
        "max_calls": max(calls),
    }
    if capture:
        out["capture_calls_per_disposition"] = capture
        out["median_capture_calls"] = statistics.median(capture)
        out["total_capture_calls"] = sum(capture)
    return out


def verdict(sample: dict) -> dict:
    completeness = sample["field_completeness"]
    # The contract fields of issue #124, merge state INCLUDED: the merge
    # state of a merged PR is destroyed by the merge itself, so it is
    # reported as not reconstructible rather than quietly dropped.
    core_fields = [
        "ci_state",
        "ci_counts",
        "head_sha",
        "base_sha",
        "merge_state",
        "changed_files_count",
        "additions",
        "deletions",
        "touches_workflow",
        "touches_tests",
        "first_20_paths",
    ]
    size = max(sample["sample_size"], 1)
    complete_pct = round(
        sum(completeness[f]["complete"] for f in core_fields)
        / (len(core_fields) * size)
        * 100,
        1,
    )
    without_merge_state = round(
        sum(
            completeness[f]["complete"]
            for f in core_fields
            if f != "merge_state"
        )
        / ((len(core_fields) - 1) * size)
        * 100,
        1,
    )
    yield_pct = sample["invisible_error_yield"]["core_yield_pct"]
    median_calls = sample["cost"]["median_calls"]
    median_capture = sample["cost"].get("median_capture_calls")
    bars = {
        "reconstruction_at_least_90pct_complete": {
            "bar": ">= 90%",
            "measured": (
                f"{complete_pct}% of contract-field cells reconstructed "
                f"(merge state included; {without_merge_state}% without it)"
            ),
            "met": complete_pct >= 90.0,
        },
        "invisible_error_yield_at_least_5pct": {
            "bar": ">= 5%",
            "measured": f"{yield_pct}%",
            "met": yield_pct >= 5.0,
        },
        "median_capture_cost_at_most_2_gh_calls": {
            "bar": "<= 2 gh calls per disposition",
            "measured": (
                f"{median_capture} gh calls per disposition for a live capture "
                f"(median {median_calls} for the retrospective reconstruction)"
            ),
            "met": median_capture is not None and median_capture <= 2,
        },
    }
    passed = all(bar["met"] for bar in bars.values())
    return {
        "core_field_completeness_pct": complete_pct,
        "core_field_completeness_pct_excluding_merge_state": without_merge_state,
        "core_fields": core_fields,
        "bars": bars,
        "verdict": "PROCEED" if passed else "KILL",
    }


def measure_variant(gh: Gh, rows: list[dict], name: str, sample_size: int,
                    terminal_set: set[str]) -> dict:
    terminal_rows = [r for r in rows if r.get("action") in terminal_set]
    terminal_rows.sort(key=lambda r: (r.get("ts") or "", r.get("repo") or "",
                                      r.get("pr") or 0))
    sample = terminal_rows[-sample_size:]
    workflow_cache: dict = {}
    records = []
    for row in sample:
        records.append(reconstruct_row(gh, row, workflow_cache))
    result = {
        "variant": name,
        "terminal_actions": sorted(terminal_set),
        "sample_size": len(records),
        "sample_window": [records[0]["ts"], records[-1]["ts"]] if records else [],
        "records": records,
    }
    result["field_completeness"] = field_completeness(records)
    result["agreement"] = agreement(records)
    result["invisible_error_yield"] = invisible_error_yield(records, CYCLE_MINUTES)
    result["ledger_packet_cross_check"] = ledger_packet_agreement(records)
    result["cost"] = cost_stats(records)
    result["verdict"] = verdict(result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=os.environ.get(
        "SHEPHERD_LEDGER", LEDGER_DEFAULT))
    parser.add_argument("--sample", type=int, default=40)
    parser.add_argument("--out", default=OUT_DEFAULT)
    parser.add_argument("--variant", default="all",
                        choices=["all", "issue", "dedup"])
    args = parser.parse_args(argv)

    proxy, proxy_note = pick_proxy()
    gh = Gh(proxy)
    rows = load_ledger(args.ledger)

    action_counts: dict[str, int] = {}
    for row in rows:
        action_counts[row.get("action", "")] = action_counts.get(
            row.get("action", ""), 0) + 1

    terminal_rows_all = [r for r in rows if r.get("action") in TERMINAL_ACTIONS]
    terminal_rows_issue = [
        r for r in rows if r.get("action") in TERMINAL_ISSUE_LIST
    ]

    variants = {}
    if args.variant in ("all", "issue"):
        variants["issue_terminal_set"] = measure_variant(
            gh, rows, "issue_terminal_set", args.sample, TERMINAL_ISSUE_LIST
        )
    if args.variant in ("all", "dedup"):
        # Sensitivity variant: one row per distinct PR (the most recent
        # terminal disposition for that PR), so the repeated re-flagging of
        # one PR cannot dominate the sample.
        seen = {}
        for row in terminal_rows_all:
            key = (row.get("repo"), row.get("pr"))
            if row.get("action") not in TERMINAL_ACTIONS:
                continue
            seen[key] = row
        dedup_rows = list(seen.values())
        variants["distinct_pr_terminal_set"] = {
            "variant": "distinct_pr_terminal_set",
            "terminal_actions": sorted(TERMINAL_ACTIONS),
            "sample_size": min(args.sample, len(dedup_rows)),
            "sample_window": None,
            "records": [],
        }
        workflow_cache: dict = {}
        dedup_sorted = sorted(
            dedup_rows, key=lambda r: (r.get("ts") or "", r.get("repo") or "")
        )
        dedup_sample = dedup_sorted[-args.sample:]
        records = [reconstruct_row(gh, row, workflow_cache) for row in dedup_sample]
        variant = {
            "variant": "distinct_pr_terminal_set",
            "terminal_actions": sorted(TERMINAL_ACTIONS),
            "sample_size": len(records),
            "sample_window": [records[0]["ts"], records[-1]["ts"]]
            if records
            else [],
            "records": records,
        }
        variant["field_completeness"] = field_completeness(records)
        variant["agreement"] = agreement(records)
        variant["invisible_error_yield"] = invisible_error_yield(
            records, CYCLE_MINUTES
        )
        variant["ledger_packet_cross_check"] = ledger_packet_agreement(records)
        variant["cost"] = cost_stats(records)
        variant["verdict"] = verdict(variant)
        variants["distinct_pr_terminal_set"] = variant

    # Does the choice of terminal set change the 40-row window?
    window_issue = {
        (r.get("repo"), r.get("pr"), r.get("ts"))
        for r in terminal_rows_issue[-args.sample:]
    }
    window_all = {
        (r.get("repo"), r.get("pr"), r.get("ts"))
        for r in terminal_rows_all[-args.sample:]
    }

    payload = {
        "generated_at": now_iso(),
        "issue": 124,
        "ledger_path": args.ledger,
        "ledger_rows": len(rows),
        "ledger_action_counts": action_counts,
        "terminal_actions_counted": sorted(TERMINAL_ACTIONS),
        "terminal_actions_from_issue_124": sorted(TERMINAL_ISSUE_LIST),
        "needs_alex_legacy_equivalent_of": "needs_operator",
        "terminal_window_identical_with_or_without_needs_alex": (
            window_issue == window_all
        ),
        "sample_size_requested": args.sample,
        "cycle_minutes": CYCLE_MINUTES,
        "gh_proxy": proxy_note,
        "gh_total_calls_including_probes": gh.calls,
        "variants": variants,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {args.out}")
    for name, variant in variants.items():
        print(
            f"{name}: n={variant['sample_size']} "
            f"completeness={variant['verdict']['core_field_completeness_pct']}% "
            f"yield={variant['invisible_error_yield']['core_yield_pct']}% "
            f"median_calls={variant['cost']['median_calls']} "
            f"verdict={variant['verdict']['verdict']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
