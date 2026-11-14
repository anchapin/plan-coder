#!/usr/bin/env python3
"""Phase-0 trajectory-coupling measurement for plan-coder issue #38.

Phase 0 is measurement only: no routing rule, no prompt change, no ledger
schema change, no harness edit.  This script reconstructs, from the
supervisor's own run history, whether two worker runs that were dispatched
close together in the same repo *touch the same files* and whether that
coupling predicts an outcome (PR opened, tokens spent, shepherd fix
attempts, merge conflicts).

Pipeline (all read-only; nothing is ever written to a ledger):

  1. load the worker ledger (one row per worker run) and the shepherd
     ledger (one row per shepherd action on a PR);
  2. reconstruct cohorts: same repo, timestamps within a --window-hours
     window (default 2h) of the cohort's first row;
  3. enumerate every same-repo, same-cohort pair of runs;
  4. classify each pair's coupling from the PR file lists (gh, when
     reachable) and, when gh is not reachable, from the file paths the
     ledgers themselves mention (lower-bound evidence tier);
  5. join outcomes per run (outcome class, tokens_out, grade) and per PR
     (shepherd fix attempts, conflict evidence, merged / closed-unmerged);
  6. emit the headline table: coupling class x n (runs, pairs) x PR-opened
     rate x median tokens_out x shepherd fix attempts per PR x conflict
     rate, plus the unattributable fractions and a feasibility finding on
     whether cohort/cycle reconstruction is sound.

Usage:
    python3 bin/trajectory_coupling_phase0.py
    python3 bin/trajectory_coupling_phase0.py --format json
    python3 bin/trajectory_coupling_phase0.py --no-gh   # ledger-only run

Options:
    --worker-ledger PATH    Worker ledger jsonl (default: manager-setup).
    --shepherd-ledger PATH  Shepherd ledger jsonl (default: manager-setup).
    --window-hours H        Cohort window in hours (default: 2).
    --cycle-gap-minutes M   Dispatch-time gap that separates two cycles
                            when cross-checking cohorts (default: 30).
    --ratchet-min-prs N     A file touched by >= N distinct PRs of one repo
                            counts as a shared-ratchet file (default: 3).
    --no-gh                 Do not call ``gh``; use ledger evidence only.
    --gh-cache PATH         JSON cache for gh results (default:
                            /tmp/trajectory_coupling_gh_cache.json).
    --format FORMAT         markdown | json (default: markdown).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from itertools import combinations

DEFAULT_LEDGER_DIR = os.path.expanduser(
    "~/workspace/goals/token-plan-coding-manager-setup/hidden_files")
DEFAULT_WORKER_LEDGER = os.path.join(DEFAULT_LEDGER_DIR, "worker_ledger.jsonl")
DEFAULT_SHEPHERD_LEDGER = os.path.join(DEFAULT_LEDGER_DIR, "shepherd_ledger.jsonl")
DEFAULT_GH_CACHE = "/tmp/trajectory_coupling_gh_cache.json"

PR_URL_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
EPOCH_RE = re.compile(r"(\d{9,10})\b")
PATH_RE = re.compile(
    r"(?<![\w/.-])((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z][A-Za-z0-9]{0,7})")

COUPLING_CLASSES = ("disjoint", "same-module", "file-overlap", "shared-ratchet")


# --------------------------------------------------------------------------
# loading / normalisation helpers
# --------------------------------------------------------------------------


def parse_json_object(line):
    """Parse one jsonl line into a dict, or None when it is not usable."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def load_jsonl(path):
    """Read a jsonl file into a list of dicts.

    Returns (rows, malformed_count).  Malformed lines are skipped and
    counted, never guessed at.
    """
    rows = []
    malformed = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            obj = parse_json_object(line)
            if obj is None:
                malformed += 1
            else:
                rows.append(obj)
    return rows, malformed


def parse_ts(value):
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None."""
    if not isinstance(value, str):
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


def normalize_repo(value):
    """Normalise a repo label: 'anchapin/fluxion' and 'fluxion' are one repo."""
    if not isinstance(value, str):
        return None
    name = value.strip().lower()
    if not name:
        return None
    if "/" in name:
        name = name.split("/", 1)[1]
    return name or None


def parse_pr_url(url):
    """Return (owner, repo, number) for a GitHub PR url, or None."""
    if not isinstance(url, str):
        return None
    match = PR_URL_RE.search(url)
    if not match:
        return None
    return match.group(1), match.group(2), int(match.group(3))


def outcome_class(outcome):
    """Map a raw worker outcome string onto a coarse outcome class."""
    if not isinstance(outcome, str):
        return "unknown"
    value = outcome.strip()
    if value.startswith("pr_opened"):
        return "pr_opened"
    if value.startswith("failed"):
        return "failed"
    if value in ("stale", "escalated", "aborted", "diagnosis",
                 "split_suggested", "done"):
        return value
    if value.startswith(("pr_blocked", "ready_push_blocked")):
        return "blocked"
    if value.startswith(("push_queued", "fix_prepared")):
        return "push_queued"
    if value.startswith("failed"):
        return "failed"
    return "other"


def tokens_out_of(row):
    """tokens_out of a worker row as an int, or None."""
    value = row.get("tokens_out")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def dispatch_epoch_of(row):
    """Dispatch epoch seconds recovered from the run_id, or None.

    Supervisor run ids look like ``fluxion-4224-1790642079``: the trailing
    digits are the unix timestamp at which the run was dispatched.  That is
    the only dispatch-time signal in the worker ledger.
    """
    run_id = row.get("run_id")
    if not isinstance(run_id, str):
        return None
    matches = EPOCH_RE.findall(run_id)
    if not matches:
        return None
    return int(matches[-1])


# --------------------------------------------------------------------------
# cohort reconstruction
# --------------------------------------------------------------------------


def assign_cohorts(rows, window_hours):
    """Group worker rows into cohorts.

    Rule (exact): rows are grouped per repo; within a repo the rows are
    sorted by ``ts``; a row joins the open cohort when
    ``row.ts - cohort.first_ts <= window`` (the window is anchored at the
    FIRST row of the cohort, it does not chain); otherwise it opens a new
    cohort.  Rows without a usable ``ts`` or ``repo`` are not cohort
    assignable and are reported separately.

    Returns (cohorts, unassigned) where cohorts is a list of dicts with
    keys repo, start, rows, and unassigned is a list of (row, reason).
    """
    window = timedelta(hours=window_hours)
    by_repo = defaultdict(list)
    unassigned = []
    for row in rows:
        stamp = parse_ts(row.get("ts"))
        repo = normalize_repo(row.get("repo"))
        if repo is None:
            unassigned.append((row, "no-repo"))
            continue
        if stamp is None:
            unassigned.append((row, "no-ts"))
            continue
        by_repo[repo].append((stamp, row))

    cohorts = []
    for repo in sorted(by_repo):
        items = sorted(by_repo[repo], key=lambda item: item[0])
        current = []
        start = None
        for stamp, row in items:
            if start is None or stamp - start > window:
                if current:
                    cohorts.append(make_cohort(repo, start, current))
                current = []
                start = stamp
            current.append(row)
        if current:
            cohorts.append(make_cohort(repo, start, current))
    return cohorts, unassigned


def make_cohort(repo, start, rows):
    """Build a cohort record."""
    return {
        "repo": repo,
        "start": start,
        "id": f"{repo}@{start.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "rows": rows,
    }


def cohort_pairs(cohort):
    """All unordered same-cohort run pairs of one cohort."""
    return list(combinations(range(len(cohort["rows"])), 2))


# --------------------------------------------------------------------------
# file evidence: gh diffs (tier A) and ledger mentions (tier B)
# --------------------------------------------------------------------------


def load_cache(path):
    """Load the gh cache file (may be missing / corrupt -> empty)."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(path, cache):
    """Persist the gh cache; failures are non-fatal."""
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=1, sort_keys=True)
    except OSError:
        pass


def gh_pr_files(pr, cache, use_gh):
    """Fetch the changed file list of one PR through ``gh``.

    Returns a dict with keys status ('ok' | 'error' | 'skipped'), files
    (list of repo-relative paths) and, on failure, error (short reason).
    """
    owner, repo, number = pr
    key = f"{owner}/{repo}#{number}"
    if key in cache:
        return cache[key]
    if not use_gh:
        result = {"status": "skipped", "files": [], "error": "gh disabled"}
        cache[key] = result
        return result
    proc = subprocess.run(
        ["gh", "pr", "view", str(number), "--repo", f"{owner}/{repo}",
         "--json", "files"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        reason = (proc.stderr or proc.stdout or "").strip().splitlines()
        result = {
            "status": "error",
            "files": [],
            "error": (reason[-1] if reason else f"gh exit {proc.returncode}")[:200],
        }
    else:
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result = {"status": "error", "files": [], "error": "gh: unparsable json"}
        else:
            files = []
            for entry in payload.get("files") or []:
                path = entry.get("path") if isinstance(entry, dict) else None
                if isinstance(path, str):
                    files.append(path)
            result = {"status": "ok", "files": sorted(set(files)), "error": ""}
    cache[key] = result
    return result


def extract_paths(text):
    """Extract file-path-looking tokens from free text (ledger evidence)."""
    found = set()
    if not isinstance(text, str):
        return found
    for match in PATH_RE.findall(text):
        path = match.strip().rstrip(".,;:)]}\"'")
        while path.startswith("./"):
            path = path[2:]
        if not path:
            continue
        found.add(path)
    return found


def canonicalize_paths(path_sets):
    """Rewrite bare file names to their unique full path.

    The shepherd ledger names the same file in two shapes ("ci.yml" and
    ".github/workflows/ci.yml").  A bare name is rewritten to the full
    path only when exactly one full path with that basename was seen
    anywhere in the evidence, so nothing is merged by guesswork.
    """
    basename_map = defaultdict(set)
    for paths in path_sets.values():
        for path in paths:
            if "/" in path:
                basename_map[path.rsplit("/", 1)[1]].add(path)
    canonical = {}
    for key, paths in path_sets.items():
        rewritten = set()
        for path in paths:
            if "/" not in path:
                full = basename_map.get(path)
                if full and len(full) == 1:
                    rewritten.add(next(iter(full)))
                    continue
            rewritten.add(path)
        canonical[key] = rewritten
    return canonical


def ledger_pr_files(shepherd_rows_by_pr, worker_rows_by_pr):
    """File paths the ledgers themselves mention for each PR (tier B).

    The shepherd ledger names the files it reacted to (contract touches,
    the file a CI fix patched); the worker ledger notes sometimes name the
    files a worker changed.  This is a LOWER BOUND on the touched set:
    a file that is not mentioned is not proof that it was not touched.
    """
    evidence = {}
    for key, rows in shepherd_rows_by_pr.items():
        paths = set()
        for row in rows:
            for field in ("outcome", "detail", "ci"):
                paths |= extract_paths(row.get(field))
        evidence[key] = paths
    for key, rows in worker_rows_by_pr.items():
        paths = set(evidence.get(key, set()))
        for row in rows:
            paths |= extract_paths(row.get("note"))
        evidence[key] = paths
    return evidence


# --------------------------------------------------------------------------
# coupling classification
# --------------------------------------------------------------------------


def dirname_of(path):
    """Directory part of a repo-relative path ('' for a root-level file)."""
    if "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]


def same_module(files_a, files_b):
    """Return a shared module directory for two file sets, or ''.

    Two files are in the same module when their directories are equal or
    one directory contains the other (nested package dirs).
    """
    dirs_a = {dirname_of(path) for path in files_a if dirname_of(path)}
    dirs_b = {dirname_of(path) for path in files_b if dirname_of(path)}
    for dir_a in sorted(dirs_a):
        for dir_b in sorted(dirs_b):
            if dir_a == dir_b:
                return dir_a
            if dir_a.startswith(dir_b + "/") or dir_b.startswith(dir_a + "/"):
                return dir_b if len(dir_b) < len(dir_a) else dir_a
    return ""


def classify_pair(files_a, files_b, ratchet_files):
    """Classify the coupling of one run pair.

    Returns (class_name, detail).  Precedence:
      shared-ratchet  the two runs share at least one file that the repo's
                      data marks as a shared ratchet (touched by many PRs);
      file-overlap    the two runs share at least one other file;
      same-module     no shared file, but the runs touch the same module
                      directory;
      disjoint        no shared file and no shared module;
      unclassifiable  one of the two runs has no file evidence at all.
    """
    if not files_a or not files_b:
        return "unclassifiable", ""
    shared = sorted(set(files_a) & set(files_b))
    if shared:
        ratchet = [path for path in shared if path in ratchet_files]
        if ratchet:
            return "shared-ratchet", ",".join(ratchet)
        return "file-overlap", ",".join(shared)
    module = same_module(files_a, files_b)
    if module:
        return "same-module", module
    return "disjoint", ""


def ratchet_files_by_repo(pr_files, min_prs):
    """Derive the shared-ratchet file list per repo FROM THE DATA.

    A file is a shared-ratchet file of its repo when at least ``min_prs``
    distinct PRs of that repo touched it (ratchet = many runs keep bumping
    the same file).  Nothing is hardcoded: the list is whatever the
    measured file sets say.
    """
    counts = defaultdict(lambda: defaultdict(set))
    for (repo, _number), files in pr_files.items():
        for path in files:
            counts[repo][path].add((repo, _number))
    ratchets = {}
    for repo, per_file in counts.items():
        ratchets[repo] = {
            path for path, prs in per_file.items() if len(prs) >= min_prs
        }
    return ratchets


# --------------------------------------------------------------------------
# shepherd join (per PR)
# --------------------------------------------------------------------------


def build_shepherd_index(rows):
    """Index shepherd rows per PR and summarise each PR's shepherd history."""
    by_pr = defaultdict(list)
    for row in rows:
        repo = normalize_repo(row.get("repo"))
        number = row.get("pr")
        if repo is None or not isinstance(number, int):
            continue
        by_pr[(repo, number)].append(row)

    summary = {}
    for key, pr_rows in by_pr.items():
        actions = Counter(row.get("action") for row in pr_rows)
        outcomes = " || ".join(str(row.get("outcome") or "") for row in pr_rows)
        merge_states = {str(row.get("merge_state") or "") for row in pr_rows}
        conflict = bool(actions.get("merge_conflicts")) or any(
            "CONFLICTING" in outcomes for _ in (0,)
        ) or "DIRTY" in merge_states
        merged = bool(actions.get("merged") or actions.get("merged_by_shepherd"))
        closed = bool(actions.get("closed_unmerged"))
        summary[key] = {
            "rows": len(pr_rows),
            "actions": actions,
            "fix_attempts": int(actions.get("fix_attempt", 0)),
            "reruns": int(actions.get("rerun", 0)),
            "conflict": conflict,
            "merged": merged,
            "closed_unmerged": closed,
        }
    return by_pr, summary


# --------------------------------------------------------------------------
# main measurement
# --------------------------------------------------------------------------


def median_or_none(values):
    """Median of a list, or None when the list is empty."""
    if not values:
        return None
    return statistics.median(values)


def fmt_rate(numerator, denominator):
    """Render a rate with its n, e.g. '40.0% (4/10)'."""
    if not denominator:
        return "n/a (0)"
    return f"{100.0 * numerator / denominator:.1f}% ({numerator}/{denominator})"


def fmt_num(value):
    """Render a number (or None) as a string."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def run_measurement(args):
    """Run the whole Phase-0 measurement and return a result dict."""
    worker_rows, worker_malformed = load_jsonl(args.worker_ledger)
    shepherd_rows, shepherd_malformed = load_jsonl(args.shepherd_ledger)

    cohorts, unassigned = assign_cohorts(worker_rows, args.window_hours)
    shepherd_by_pr, shepherd_summary = build_shepherd_index(shepherd_rows)

    # Worker rows per PR (for ledger tier-B file evidence).
    worker_rows_by_pr = defaultdict(list)
    for row in worker_rows:
        pr = parse_pr_url(row.get("pr_url"))
        if pr is None:
            continue
        key = (normalize_repo(row.get("repo")) or pr[1].lower(), pr[2])
        worker_rows_by_pr[key].append(row)

    evidence = canonicalize_paths(
        ledger_pr_files(shepherd_by_pr, worker_rows_by_pr))

    # Tier A: gh file lists for every PR referenced by a worker run.
    gh_cache = load_cache(args.gh_cache)
    pr_file_sets = {}
    gh_stats = Counter()
    pr_keys = {}
    for row in worker_rows:
        pr = parse_pr_url(row.get("pr_url"))
        if pr is None:
            gh_stats["no-pr-url"] += 1
            continue
        key = (normalize_repo(row.get("repo")) or pr[1].lower(), pr[2])
        pr_keys[key] = pr
    for key, pr in sorted(pr_keys.items()):
        fetched = gh_pr_files(pr, gh_cache, not args.no_gh)
        gh_stats[fetched["status"]] += 1
        if fetched["status"] == "ok":
            pr_file_sets[key] = set(fetched["files"])
        else:
            pr_file_sets[key] = set()
    save_cache(args.gh_cache, gh_cache)

    # Merge tier A and tier B evidence into the file set used per PR.
    effective_files = {}
    tier_of = {}
    for key in set(pr_keys) | set(evidence):
        tier_a = pr_file_sets.get(key, set())
        tier_b = evidence.get(key, set())
        if tier_a:
            effective_files[key] = tier_a
            tier_of[key] = "A-diff"
        elif tier_b:
            effective_files[key] = tier_b
            tier_of[key] = "B-ledger-mention"
        else:
            effective_files[key] = set()
            tier_of[key] = "none"

    ratchets = ratchet_files_by_repo(effective_files, args.ratchet_min_prs)

    # Pairs.
    pair_rows = []
    for cohort in cohorts:
        rows = cohort["rows"]
        for i, j in cohort_pairs(cohort):
            row_a, row_b = rows[i], rows[j]
            key_a = pr_key_of(row_a)
            key_b = pr_key_of(row_b)
            files_a = effective_files.get(key_a, set())
            files_b = effective_files.get(key_b, set())
            ratchet_files = ratchets.get(cohort["repo"], set())
            klass, detail = classify_pair(files_a, files_b, ratchet_files)
            if klass == "unclassifiable":
                if key_a is None or key_b is None:
                    detail = "no-pr"
                else:
                    detail = "no-file-evidence"
            pair_rows.append({
                "cohort": cohort["id"],
                "repo": cohort["repo"],
                "ts_a": row_a.get("ts"),
                "ts_b": row_b.get("ts"),
                "run_a": row_a.get("run_id"),
                "run_b": row_b.get("run_id"),
                "issue_a": row_a.get("issue"),
                "issue_b": row_b.get("issue"),
                "pr_a": key_a[1] if key_a else None,
                "pr_b": key_b[1] if key_b else None,
                "tier_a": tier_of.get(key_a, "none"),
                "tier_b": tier_of.get(key_b, "none"),
                "files_a": sorted(files_a),
                "files_b": sorted(files_b),
                "class": klass,
                "detail": detail,
                "row_a": row_a,
                "row_b": row_b,
            })

    # Headline table per class.
    classes = {}
    for klass in list(COUPLING_CLASSES) + ["unclassifiable"]:
        pairs = [pair for pair in pair_rows if pair["class"] == klass]
        runs = []
        for pair in pairs:
            runs.append(pair["row_a"])
            runs.append(pair["row_b"])
        distinct_runs = list({id(run): run for run in runs}.values())
        opened = sum(1 for run in distinct_runs
                     if outcome_class(run.get("outcome")) == "pr_opened")
        tokens = [t for t in (tokens_out_of(run) for run in distinct_runs)
                  if t is not None]
        pr_keys_used = set()
        for run in distinct_runs:
            key = pr_key_of(run)
            if key is not None:
                pr_keys_used.add(key)
        fix_attempts = 0
        conflict_prs = 0
        merged_prs = 0
        closed_prs = 0
        shepherd_prs = 0
        for key in pr_keys_used:
            info = shepherd_summary.get(key)
            if info is None:
                continue
            shepherd_prs += 1
            fix_attempts += info["fix_attempts"]
            conflict_prs += 1 if info["conflict"] else 0
            merged_prs += 1 if info["merged"] else 0
            closed_prs += 1 if info["closed_unmerged"] else 0
        classes[klass] = {
            "pairs": len(pairs),
            "runs": len(distinct_runs),
            "run_instances": len(runs),
            "pr_opened": opened,
            "pr_opened_rate": fmt_rate(opened, len(distinct_runs)),
            "grades": dict(Counter(
                str(run.get("grade")) for run in distinct_runs
                if run.get("grade") not in (None, ""))),
            "grade_median": median_or_none([
                float(run["grade"]) for run in distinct_runs
                if isinstance(run.get("grade"), (int, float))
                and not isinstance(run.get("grade"), bool)
            ]),
            "tokens_median": median_or_none(tokens),
            "tokens_n": len(tokens),
            "prs": len(pr_keys_used),
            "shepherd_prs": shepherd_prs,
            "fix_attempts": fix_attempts,
            "fix_per_pr": (fix_attempts / shepherd_prs) if shepherd_prs else None,
            "conflict_prs": conflict_prs,
            "conflict_rate": fmt_rate(conflict_prs, shepherd_prs),
            "merged_prs": merged_prs,
            "closed_unmerged_prs": closed_prs,
        }

    # Ratchet file lists per repo (enumerated from the data).
    ratchet_list = {
        repo: sorted(paths) for repo, paths in sorted(ratchets.items())
    }

    # Feasibility: are the 2h timestamp cohorts the same thing as the
    # supervisor's dispatch cycles?  Cross-check against the dispatch epoch
    # carried in run_id (the only dispatch-time evidence in the ledgers).
    feasibility = cycle_feasibility(cohorts, args.cycle_gap_minutes,
                                    args.window_hours)

    # Unattributable fractions.
    total_rows = len(worker_rows)
    classifiable_pairs = sum(
        1 for pair in pair_rows if pair["class"] != "unclassifiable"
    )
    diff_fetched_prs = gh_stats.get("ok", 0)
    pr_total = len(pr_keys)
    pairs_both_pr = sum(
        1 for pair in pair_rows
        if pair["pr_a"] is not None and pair["pr_b"] is not None)
    shepherd_covered_prs = len(set(pr_keys) & set(shepherd_summary))
    per_repo_class = defaultdict(Counter)
    for pair in pair_rows:
        per_repo_class[pair["repo"]][pair["class"]] += 1

    result = {
        "params": {
            "worker_ledger": args.worker_ledger,
            "shepherd_ledger": args.shepherd_ledger,
            "window_hours": args.window_hours,
            "cycle_gap_minutes": args.cycle_gap_minutes,
            "ratchet_min_prs": args.ratchet_min_prs,
            "gh_enabled": not args.no_gh,
        },
        "worker_rows": total_rows,
        "worker_malformed": worker_malformed,
        "shepherd_rows": len(shepherd_rows),
        "shepherd_malformed": shepherd_malformed,
        "cohorts": cohorts,
        "unassigned": unassigned,
        "pairs": pair_rows,
        "classes": classes,
        "ratchets": ratchet_list,
        "ratchet_pr_counts": ratchet_pr_counts(effective_files),
        "feasibility": feasibility,
        "gh_stats": gh_stats,
        "pr_total": pr_total,
        "diff_fetched_prs": diff_fetched_prs,
        "classifiable_pairs": classifiable_pairs,
        "pairs_both_pr": pairs_both_pr,
        "shepherd_covered_prs": shepherd_covered_prs,
        "per_repo_class": {repo: dict(counter)
                           for repo, counter in sorted(per_repo_class.items())},
        "tier_of": {f"{k[0]}#{k[1]}": v for k, v in tier_of.items()},
    }
    return result


def pr_key_of(row):
    """(repo, pr-number) key of a worker row, or None when it has no PR."""
    pr = parse_pr_url(row.get("pr_url"))
    if pr is None:
        return None
    repo = normalize_repo(row.get("repo")) or pr[1].lower()
    return (repo, pr[2])


def ratchet_pr_counts(effective_files):
    """Per repo: file -> number of distinct PRs that touched it."""
    counts = defaultdict(lambda: defaultdict(set))
    for (repo, number), files in effective_files.items():
        for path in files:
            counts[repo][path].add(number)
    return {
        repo: {path: len(prs) for path, prs in sorted(per_file.items())}
        for repo, per_file in sorted(counts.items())
    }


def cycle_feasibility(cohorts, gap_minutes, window_hours):
    """Cross-check the 2h timestamp cohorts against dispatch-time cycles.

    Dispatch batches are reconstructed from the dispatch epoch embedded in
    run_id: rows are sorted by epoch and a new batch opens when two
    consecutive dispatches are more than ``gap_minutes`` apart.  A cohort
    is 'misassigned' when its rows fall into more than one dispatch batch,
    i.e. the 2h timestamp window does not line up with a single dispatch
    batch of the supervisor.

    Two populations are reported:
      * all cohorts whose members all carry a dispatch epoch;
      * only cohorts whose members' ledger ts is within one window of
        their dispatch time (rows whose ts was backfilled late are then
        excluded -- those are the rows whose ts cannot place them in a
        cohort at all).
    """
    epoch_rows = []
    for cohort in cohorts:
        for row in cohort["rows"]:
            epoch = dispatch_epoch_of(row)
            if epoch is None:
                continue
            epoch_rows.append((epoch, cohort, row))
    epoch_rows.sort(key=lambda item: item[0])

    def batches_for(gap):
        """Assign a batch id to every row for one gap threshold."""
        gap_seconds = gap * 60
        batch_of = {}
        batch_id = None
        previous = None
        for epoch, cohort, row in epoch_rows:
            if previous is None or epoch - previous > gap_seconds:
                batch_id = (batch_id or 0) + 1
            batch_of[id(row)] = batch_id
            previous = epoch
        return batch_of

    def misassignment(gap, restrict_fresh):
        """Cohorts spanning more than one dispatch batch at this gap."""
        batch_of = batches_for(gap)
        checked = 0
        bad = []
        for cohort in cohorts:
            members = [row for row in cohort["rows"]
                       if dispatch_epoch_of(row) is not None]
            if len(members) < 2:
                continue
            if restrict_fresh:
                fresh = []
                for row in members:
                    stamp = parse_ts(row.get("ts"))
                    if stamp is None:
                        continue
                    drift = (stamp - datetime.fromtimestamp(
                        dispatch_epoch_of(row), tz=UTC)).total_seconds()
                    if drift <= window_hours * 3600:
                        fresh.append(row)
                members = fresh
                if len(members) < 2:
                    continue
            checked += 1
            if len({batch_of[id(row)] for row in members}) > 1:
                bad.append(cohort)
        return checked, bad

    checked, bad = misassignment(gap_minutes, False)
    checked_fresh, bad_fresh = misassignment(gap_minutes, True)

    sensitivity = []
    for gap in (5, 10, 15, 30, 60, 120):
        gap_checked, gap_bad = misassignment(gap, False)
        sensitivity.append({
            "gap_minutes": gap,
            "checked": gap_checked,
            "misassigned": len(gap_bad),
            "rate": (len(gap_bad) / gap_checked) if gap_checked else None,
        })

    spans = []
    for cohort in cohorts:
        epochs = [dispatch_epoch_of(row) for row in cohort["rows"]]
        epochs = [e for e in epochs if e is not None]
        if len(epochs) >= 2:
            spans.append((max(epochs) - min(epochs)) / 60.0)

    drifts = []
    for epoch, cohort, row in epoch_rows:
        stamp = parse_ts(row.get("ts"))
        if stamp is None:
            continue
        dispatch = datetime.fromtimestamp(epoch, tz=UTC)
        drifts.append((stamp - dispatch).total_seconds() / 3600.0)

    return {
        "gap_minutes": gap_minutes,
        "cycles": batches_for(gap_minutes) and max(
            batches_for(gap_minutes).values(), default=0),
        "rows_with_epoch": len(epoch_rows),
        "checked_cohorts": checked,
        "misassigned_cohorts": len(bad),
        "misassigned_rate": (len(bad) / checked) if checked else None,
        "checked_cohorts_fresh_ts": checked_fresh,
        "misassigned_cohorts_fresh_ts": len(bad_fresh),
        "misassigned_rate_fresh_ts": ((len(bad_fresh) / checked_fresh)
                                       if checked_fresh else None),
        "sensitivity": sensitivity,
        "cohort_epoch_span_minutes_median": median_or_none(spans),
        "cohort_epoch_span_minutes_max": max(spans) if spans else None,
        "misassigned_examples": [
            {
                "cohort": cohort["id"],
                "runs": [row.get("run_id") for row in cohort["rows"]],
                "ts": [row.get("ts") for row in cohort["rows"]],
                "epochs": [dispatch_epoch_of(row) for row in cohort["rows"]],
            }
            for cohort in bad[:15]
        ],
        "ts_minus_dispatch_hours_median": median_or_none(drifts),
        "ts_minus_dispatch_hours_max": max(drifts) if drifts else None,
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def render_markdown(result):
    """Render the whole report as markdown."""
    params = result["params"]
    lines = []
    lines.append("# Phase 0 trajectory-coupling measurement (issue #38)")
    lines.append("")
    lines.append(
        "Coupling class of same-repo, same-cohort worker run pairs, joined to "
        "run outcomes and shepherd PR history.  Measurement only.")
    lines.append("")
    lines.append("## Parameters")
    lines.append("")
    lines.append("| parameter | value |")
    lines.append("|---|---|")
    for key in ("worker_ledger", "shepherd_ledger", "window_hours",
                "cycle_gap_minutes", "ratchet_min_prs", "gh_enabled"):
        lines.append(f"| {key} | {params[key]} |")
    lines.append("")

    feas = result["feasibility"]
    lines.append("## Feasibility: is the 2h cohort window the supervisor cycle?")
    lines.append("")
    lines.append(
        f"- dispatch-time cycles reconstructed from the epoch embedded in "
        f"run_id: **{feas['cycles']}** cycles over {feas['rows_with_epoch']} "
        f"rows (gap threshold {feas['gap_minutes']} min).")
    lines.append(
        f"- cohorts checked against cycle evidence: **{feas['checked_cohorts']}**; "
        f"misassigned: **{feas['misassigned_cohorts']}** "
        f"({fmt_rate(feas['misassigned_cohorts'], feas['checked_cohorts'])}).")
    lines.append(
        f"- same check restricted to cohorts whose members' ledger ts is "
        f"within one window of their dispatch time (late-backfilled rows "
        f"excluded): {feas['misassigned_cohorts_fresh_ts']} of "
        f"{feas['checked_cohorts_fresh_ts']} misassigned "
        f"({fmt_rate(feas['misassigned_cohorts_fresh_ts'], feas['checked_cohorts_fresh_ts'])}).")
    lines.append(
        f"- dispatch-time span inside a cohort: median "
        f"{fmt_num(feas['cohort_epoch_span_minutes_median'])} min, max "
        f"{fmt_num(feas['cohort_epoch_span_minutes_max'])} min.")
    lines.append(
        f"- ledger ts minus dispatch time: median "
        f"{fmt_num(feas['ts_minus_dispatch_hours_median'])} h, max "
        f"{fmt_num(feas['ts_minus_dispatch_hours_max'])} h (the ledger ts is "
        f"the run's END time, not its dispatch time).")
    lines.append("")
    lines.append("Sensitivity of the misassignment rate to the dispatch-batch gap:")
    lines.append("")
    lines.append("| batch gap (min) | cohorts checked | misassigned | rate |")
    lines.append("|---|---|---|---|")
    for entry in feas["sensitivity"]:
        lines.append(
            f"| {entry['gap_minutes']} | {entry['checked']} | "
            f"{entry['misassigned']} | {fmt_rate(entry['misassigned'], entry['checked'])} |")
    lines.append("")
    if feas["misassigned_rate"] is not None and feas["misassigned_rate"] > 0.20:
        lines.append(
            "- **FEASIBILITY FINDING: cycle reconstruction is unsound** "
            f"({feas['misassigned_rate'] * 100:.1f}% of checked cohorts span "
            "more than one dispatch cycle, above the issue's 20% bar).  "
            "Phase 1 would have to become a schema proposal (cycle_id + "
            "dispatch-time scope paths) rather than a routing rule.")
    else:
        lines.append(
            "- cycle reconstruction stays inside the issue's 20% unsoundness "
            "bar; a routing rule (Phase 1) is not ruled out by this check.")
    if feas["misassigned_examples"]:
        lines.append("")
        lines.append("Misassigned cohort examples:")
        lines.append("")
        lines.append("| cohort | runs | ts |")
        lines.append("|---|---|---|")
        for example in feas["misassigned_examples"]:
            runs = ", ".join(str(run) for run in example["runs"])
            stamps = ", ".join(str(stamp) for stamp in example["ts"])
            lines.append(f"| {example['cohort']} | {runs} | {stamps} |")
    lines.append("")

    lines.append("## Headline table: coupling class x outcome")
    lines.append("")
    lines.append(
        "| coupling class | pairs (n) | runs (n) | PR-opened rate | median "
        "tokens_out | shepherd fix attempts / PR | conflict rate | thin? |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for klass in list(COUPLING_CLASSES) + ["unclassifiable"]:
        info = result["classes"][klass]
        thin = "thin (n<10)" if info["pairs"] < 10 else ""
        lines.append(
            f"| {klass} | {info['pairs']} | {info['runs']} | "
            f"{info['pr_opened_rate']} | {fmt_num(info['tokens_median'])} "
            f"(n={info['tokens_n']}) | "
            f"{fmt_num(info['fix_per_pr'])} (n={info['shepherd_prs']} PRs, "
            f"{info['fix_attempts']} attempts) | {info['conflict_rate']} | "
            f"{thin} |")
    lines.append("")
    tier_counts = Counter(result["tier_of"].values())
    lines.append(
        "Evidence tier of the PR file lists behind these classes: "
        + ", ".join(f"{key}={value}" for key, value in sorted(tier_counts.items()))
        + " (`A-diff` = file list fetched from the PR diff via gh; "
        "`B-ledger-mention` = file paths the shepherd/worker ledgers name "
        "for that PR; `none` = no file evidence).  A tier-B `disjoint` means "
        "\"no file that either ledger mentioned for either PR\", which "
        "over-counts disjoint pairs; tier-B `shared-ratchet` is sound in the "
        "other direction (a named file is certainly touched).")
    lines.append("")
    lines.append("")
    lines.append("## Grades present per coupling class")
    lines.append("")
    lines.append("| coupling class | runs with a grade | grade counts |")
    lines.append("|---|---|---|")
    for klass in list(COUPLING_CLASSES) + ["unclassifiable"]:
        grades = result["classes"][klass]["grades"]
        lines.append(
            f"| {klass} | {sum(grades.values())} | "
            f"{', '.join(f'{key}={value}' for key, value in sorted(grades.items())) or '-'} |")
    lines.append("")

    lines.append("## Pair counts per repo and class")
    lines.append("")
    classes_all = list(COUPLING_CLASSES) + ["unclassifiable"]
    lines.append("| repo | " + " | ".join(classes_all) + " |")
    lines.append("|---" * (len(classes_all) + 1) + "|")
    for repo, counter in result["per_repo_class"].items():
        cells = " | ".join(str(counter.get(klass, 0)) for klass in classes_all)
        lines.append(f"| {repo} | {cells} |")
    lines.append("")

    lines.append("## Attribution completeness")
    lines.append("")
    lines.append("| quantity | count | of | note |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| worker rows read | {result['worker_rows']} | - | malformed "
        f"lines skipped: {result['worker_malformed']} |")
    lines.append(
        f"| rows not cohort-assignable | {len(result['unassigned'])} | "
        f"{result['worker_rows']} | "
        f"{fmt_rate(len(result['unassigned']), result['worker_rows'])} |")
    lines.append(
        f"| PRs with a gh diff fetched | {result['diff_fetched_prs']} | "
        f"{result['pr_total']} | "
        f"{fmt_rate(result['diff_fetched_prs'], result['pr_total'])} |")
    lines.append(
        f"| pairs classifiable | {result['classifiable_pairs']} | "
        f"{len(result['pairs'])} | "
        f"{fmt_rate(result['classifiable_pairs'], len(result['pairs']))} |")
    lines.append(
        f"| pairs where BOTH runs opened a PR | {result['pairs_both_pr']} | "
        f"{len(result['pairs'])} | the ceiling on classifiable pairs once "
        f"diffs are fetchable |")
    lines.append(
        f"| PRs with shepherd history | {result['shepherd_covered_prs']} | "
        f"{result['pr_total']} | "
        f"{fmt_rate(result['shepherd_covered_prs'], result['pr_total'])} |")
    gh_stats = result["gh_stats"]
    gh_detail = ", ".join(f"{key}={value}" for key, value in sorted(gh_stats.items()))
    lines.append(f"| gh fetch outcomes | {gh_detail} | | |")
    lines.append("")
    reasons = Counter(reason for _row, reason in result["unassigned"])
    if reasons:
        lines.append("Unassignable-row reasons: "
                     + ", ".join(f"{key}={value}" for key, value in sorted(reasons.items()))
                     + ".")
        lines.append("")
    pair_reasons = Counter(pair["detail"] for pair in result["pairs"]
                           if pair["class"] == "unclassifiable")
    if pair_reasons:
        lines.append("Unclassifiable-pair reasons: "
                     + ", ".join(f"{key}={value}" for key, value in sorted(pair_reasons.items()))
                     + " (`no-pr` = one of the two runs never opened a PR; "
                     "`no-file-evidence` = both runs opened a PR but no file "
                     "list could be obtained for at least one of them).")
        lines.append("")

    lines.append("## Shared-ratchet files per repo (from the data)")
    lines.append("")
    if not result["ratchets"]:
        lines.append("No file reached the shared-ratchet threshold in any repo.")
    else:
        lines.append("| repo | ratchet files | PRs touching the file |")
        lines.append("|---|---|---|")
        counts = result["ratchet_pr_counts"]
        for repo, files in result["ratchets"].items():
            for path in files:
                lines.append(
                    f"| {repo} | {path} | {counts.get(repo, {}).get(path, 0)} |")
    lines.append("")

    lines.append("## Cohort size distribution")
    lines.append("")
    sizes = Counter(len(cohort["rows"]) for cohort in result["cohorts"])
    lines.append("| cohort size | cohorts |")
    lines.append("|---|---|")
    for size in sorted(sizes):
        lines.append(f"| {size} | {sizes[size]} |")
    lines.append("")

    lines.append("## Verdict vs the issue's bars")
    lines.append("")
    lines.append(verdict_text(result))
    lines.append("")
    lines.append("## Classified pairs (raw rows)")
    lines.append("")
    lines.append(
        "Every pair that could be classified, verbatim.  The unclassifiable "
        "pairs (one run never opened a PR, or no file list was obtainable "
        "for one of the two PRs) are counted in the attribution table and "
        "in the per-repo class table above; re-running the script "
        "regenerates the complete pair list.")
    lines.append("")
    lines.append("| repo | cohort | run A | run B | PR A | PR B | tier A | tier B | class | detail |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for pair in result["pairs"]:
        if pair["class"] == "unclassifiable":
            continue
        lines.append(
            f"| {pair['repo']} | {pair['cohort']} | {pair['run_a']} | "
            f"{pair['run_b']} | {pair['pr_a']} | {pair['pr_b']} | "
            f"{pair['tier_a']} | {pair['tier_b']} | {pair['class']} | "
            f"{pair['detail']} |")
    lines.append("")
    return "\n".join(lines)


def verdict_text(result):
    """Verdict paragraph against the issue's Proceed / Kill bars."""
    classes = result["classes"]
    disjoint = classes["disjoint"]
    classifiable = result["classifiable_pairs"]
    lines = []
    if classifiable < 15:
        lines.append(
            f"**KILL (measured).** The full ledger yields "
            f"{classifiable} classifiable same-repo pairs, below the issue's "
            f"bar of 15.  No coupling class can be shown to differ from "
            f"disjoint at this n, so the issue's Proceed condition is not "
            f"testable on this history.")
        if result["diff_fetched_prs"] == 0:
            lines.append(
                "  Cause: the diff-fetch fraction is 0 "
                f"({result['pr_total']} PRs referenced by worker runs, none "
                "fetched) because `gh` could not reach api.github.com from "
                "this environment; coupling classes therefore rest on ledger "
                "file mentions (tier B), which are a lower bound on the "
                "touched sets.")
        return "\n".join(lines)

    best = []
    for klass in COUPLING_CLASSES:
        if klass == "disjoint":
            continue
        info = classes[klass]
        if info["pairs"] < 15:
            continue
        if not disjoint["runs"]:
            continue
        opened_delta = (
            (disjoint["pr_opened"] / disjoint["runs"] if disjoint["runs"] else 0)
            - (info["pr_opened"] / info["runs"] if info["runs"] else 0)
        ) * 100
        fix_ratio = None
        if info["fix_per_pr"] is not None and disjoint["fix_per_pr"]:
            fix_ratio = info["fix_per_pr"] / disjoint["fix_per_pr"]
        conflict = (info["conflict_prs"] / info["shepherd_prs"]
                    if info["shepherd_prs"] else 0)
        disjoint_conflict = (disjoint["conflict_prs"] / disjoint["shepherd_prs"]
                             if disjoint["shepherd_prs"] else 0)
        best.append((klass, info, opened_delta, fix_ratio, conflict,
                     disjoint_conflict))
    winners = [
        entry for entry in best
        if entry[2] >= 15 or (entry[3] is not None and entry[3] >= 1.5)
        or (entry[4] - entry[5]) >= 0.25
    ]
    if winners:
        lines.append("**PROCEED.**")
        for klass, info, opened_delta, fix_ratio, conflict, dconflict in winners:
            lines.append(
                f"- {klass}: pairs={info['pairs']}, PR-opened "
                f"{info['pr_opened_rate']} vs disjoint "
                f"{disjoint['pr_opened_rate']} ({opened_delta:+.1f} pt), fix "
                f"attempts/PR {fmt_num(info['fix_per_pr'])} vs "
                f"{fmt_num(disjoint['fix_per_pr'])} ("
                f"{fmt_num(fix_ratio)}x), conflict rate "
                f"{info['conflict_rate']} vs {disjoint['conflict_rate']} "
                f"({(conflict - dconflict) * 100:+.1f} pt).")
    else:
        lines.append(
            "**KILL (measured).** No coupling class with pair-n >= 15 shows "
            "a >=15 pt lower PR-opened rate, a >=1.5x fix-attempt ratio, or "
            "a >=25 pt higher conflict rate against disjoint.")
    return "\n".join(lines)


def render_json(result):
    """Render the result as JSON (for downstream re-use)."""
    payload = {
        "params": result["params"],
        "worker_rows": result["worker_rows"],
        "shepherd_rows": result["shepherd_rows"],
        "cohorts": [
            {"id": cohort["id"], "repo": cohort["repo"], "size": len(cohort["rows"])}
            for cohort in result["cohorts"]
        ],
        "unassigned": len(result["unassigned"]),
        "classes": result["classes"],
        "ratchets": result["ratchets"],
        "feasibility": result["feasibility"],
        "gh_stats": dict(result["gh_stats"]),
        "diff_fetched_prs": result["diff_fetched_prs"],
        "pr_total": result["pr_total"],
        "classifiable_pairs": result["classifiable_pairs"],
        "pairs": [
            {key: pair[key] for key in (
                "repo", "cohort", "run_a", "run_b", "pr_a", "pr_b",
                "tier_a", "tier_b", "class", "detail")}
            for pair in result["pairs"]
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def parse_args(argv=None):
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        description="Phase-0 trajectory-coupling measurement (issue #38).")
    parser.add_argument("--worker-ledger", default=DEFAULT_WORKER_LEDGER)
    parser.add_argument("--shepherd-ledger", default=DEFAULT_SHEPHERD_LEDGER)
    parser.add_argument("--window-hours", type=float, default=2.0)
    parser.add_argument("--cycle-gap-minutes", type=int, default=30)
    parser.add_argument("--ratchet-min-prs", type=int, default=3)
    parser.add_argument("--no-gh", action="store_true",
                        help="do not call gh; use ledger file mentions only")
    parser.add_argument("--gh-cache", default=DEFAULT_GH_CACHE)
    parser.add_argument("--format", default="markdown",
                        choices=("markdown", "json"))
    return parser.parse_args(argv)


def main(argv=None):
    """Entry point."""
    args = parse_args(argv)
    for path in (args.worker_ledger, args.shepherd_ledger):
        if not os.path.exists(path):
            sys.stderr.write(f"missing input ledger: {path}\n")
            return 2
    result = run_measurement(args)
    if args.format == "json":
        print(render_json(result))
    else:
        print(render_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
