#!/usr/bin/env python3
"""Phase-0 Ponytail-ladder brief-rule measurement for plan-coder issue #21.

Phase 0 is measurement only.  It does NOT change any prompt, does NOT run
any worker, and does NOT touch the ledger.  It produces the three
deliverables issue #21 asks for:

  raw table       One row per ``pr_opened`` ledger row (the most recent
                  ``--rows`` of them): run id, repo, tier, token counts,
                  steps, grade and the PR's diff size (files changed,
                  lines added, lines deleted, net lines).

  distributions   Median and p90 of ``net_lines`` and ``tokens_out`` per
                  repo and per tier.  Distributions, not averages: the
                  median and p90 are reported, the mean is not.

  case set        A frozen, deterministic set of ``--cases`` recently
                  completed issues with known-good PRs, spanning several
                  repos and both the cheap and the balanced tier, each
                  with a one-line re-runnability justification.

Diff sizes are fetched with the ``gh`` CLI when the GitHub API is
reachable (``gh api repos/<owner>/<repo>/pulls/<n>/files``) and otherwise
from the local supervisor-loop clones with ``git diff --numstat`` at the
recorded worker branch.  The fetch method used for every row is printed
in the ``diff_source`` column; rows whose diff cannot be fetched are kept
in the table with the failure stated, never dropped and never invented.

Usage:
    python3 bin/ponytail_phase0.py [--rows 60] [--cases 12]
    python3 bin/ponytail_phase0.py --format csv --out table.csv

Options:
    --ledger PATH      Worker ledger jsonl (default: the manager-setup
                       ledger path).
    --rows N           Most recent ``pr_opened`` rows to analyse
                       (default: 60).
    --cases N          Number of frozen cases to pick (default: 12).
    --clone-root DIR   Root holding the supervisor-loop clones
                       (default: ~/workspace).
    --repo-dir NAME=PATH
                       Override the clone directory for one repo.
    --format FORMAT    markdown | csv | tsv (default: markdown).
    --out PATH         Write the rendered output to PATH as well.
    --no-gh            Do not attempt the GitHub API; use local clones.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import statistics
import subprocess
import sys
from collections import defaultdict


GH_OWNER = "anchapin"
GH_TIMEOUT = 30

DEFAULT_LEDGER = os.path.join(
    os.path.expanduser("~"),
    "workspace/goals/token-plan-coding-manager-setup/hidden_files/worker_ledger.jsonl",
)

# plan-coder's clone under the workspace root is "plan-coder-repo".
REPO_DIR_CANDIDATES = {
    "plan-coder": ["plan-coder", "plan-coder-repo"],
}

RAW_COLUMNS = [
    "run_id",
    "repo",
    "tier",
    "tokens_in",
    "tokens_out",
    "steps",
    "grade",
    "files_changed",
    "lines_added",
    "lines_deleted",
    "net_lines",
    "diff_source",
]

CASE_COLUMNS = [
    "case",
    "repo",
    "issue",
    "tier",
    "pr",
    "pr_url",
    "grade",
    "files_changed",
    "net_lines",
    "justification",
]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def git(repo_dir, *args):
    """Run a git command in repo_dir; return (rc, stdout, stderr)."""
    proc = subprocess.run(
        ["git", "-C", repo_dir, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def percentile(values, pct):
    """Nearest-rank percentile of a non-empty list of numbers."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct * len(ordered)))
    return ordered[rank - 1]


def median(values):
    """Median of a list of numbers, or None when the list is empty."""
    if not values:
        return None
    return statistics.median(values)


def fmt_number(value):
    """Render a number for a markdown cell (blank when missing)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------


def read_ledger(path):
    """Read the worker ledger jsonl; return (rows, malformed_line_count)."""
    rows = []
    malformed = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
            else:
                rows.append(record)
    return rows, malformed


def select_pr_rows(rows, count):
    """The most recent ``count`` ``pr_opened`` rows (ledger order)."""
    opened = [row for row in rows if row.get("outcome") == "pr_opened"]
    if count <= 0 or count >= len(opened):
        return opened
    return opened[-count:]


def pr_number(row):
    """PR number recorded for a row, or None."""
    url = row.get("pr_url")
    if not url:
        return None
    tail = str(url).rstrip("/").rsplit("/", 1)[-1]
    if tail.isdigit():
        return int(tail)
    return None


def step_text(value):
    """Steps recorded for a row ("used/max", a number, or missing)."""
    if value is None:
        return ""
    return str(value)


# --------------------------------------------------------------------------
# diff fetching
# --------------------------------------------------------------------------


def gh_probe():
    """Return (available, detail) for the GitHub API via the gh CLI."""
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{GH_OWNER}/plan-coder"],
            capture_output=True,
            text=True,
            check=False,
            timeout=GH_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"gh not usable: {exc}"
    if proc.returncode == 0:
        return True, "gh api reachable"
    detail = (proc.stderr.strip() or proc.stdout.strip() or "gh api failed").splitlines()
    return False, detail[-1].strip()


def fetch_diff_gh(repo, pr):
    """Fetch a PR's diff size from the GitHub API.

    Returns a stats dict on success, or (None, reason) when the API call
    fails.  Never invents numbers.
    """
    if pr is None:
        return None, "no PR number recorded"
    endpoint = f"repos/{GH_OWNER}/{repo}/pulls/{pr}/files"
    try:
        proc = subprocess.run(
            ["gh", "api", endpoint, "--paginate"],
            capture_output=True,
            text=True,
            check=False,
            timeout=GH_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"gh api error: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr.strip() or proc.stdout.strip() or "gh api failed").splitlines()
        return None, detail[-1].strip()
    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None, "gh api returned unparsable payload"
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return None, "gh api returned an unexpected payload shape"
    files = 0
    added = 0
    deleted = 0
    binary = 0
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        files += 1
        try:
            added += int(entry.get("additions") or 0)
            deleted += int(entry.get("deletions") or 0)
        except (TypeError, ValueError):
            binary += 1
    return {
        "files": files,
        "added": added,
        "deleted": deleted,
        "binary": binary,
        "source": "gh api pulls/files",
        "detail": f"gh api {endpoint}",
    }, None


def resolve_clone(repo, clone_root, overrides):
    """Return the clone directory for repo, or None when unavailable."""
    if repo in overrides:
        return overrides[repo]
    candidates = [repo] + REPO_DIR_CANDIDATES.get(repo, [])
    for candidate in candidates:
        path = os.path.join(clone_root, candidate)
        if os.path.isdir(os.path.join(path, ".git")) or os.path.isfile(
            os.path.join(path, ".git")
        ):
            return path
    return None


def default_base_ref(repo_dir):
    """Default branch of the clone (origin/HEAD, else origin/main etc.)."""
    rc, out, _ = git(repo_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out.strip():
        return out.strip()
    for candidate in ("origin/main", "origin/develop", "origin/master"):
        rc, _, _ = git(repo_dir, "rev-parse", "--verify", "--quiet", candidate)
        if rc == 0:
            return candidate
    return None


def resolve_head(repo_dir, branch):
    """Resolve the worker branch to a commit sha; returns (sha, reason)."""
    if not branch:
        return None, "no branch recorded in ledger"
    for ref in (branch, f"origin/{branch}", f"refs/heads/{branch}"):
        rc, out, _ = git(repo_dir, "rev-parse", "--verify", "--quiet", ref)
        if rc == 0 and out.strip():
            return out.strip(), f"branch {branch}"
    return None, f"branch {branch} not present in clone"


def resolve_base(repo_dir, branch, head_sha, base_ref, pr):
    """Resolve the PR base commit; returns (sha, source).

    Preference order:
      1. the reflog "branch: Created from <ref>" entry -- the commit the
         worker branched off, i.e. the PR base;
      2. the merge-base with the default branch (the PR base for an
         unmerged branch);
      3. the first parent of the merge commit that brought the PR in.
    """
    rc, out, _ = git(repo_dir, "reflog", "show", "--format=%H %gs", branch)
    if rc == 0:
        for line in out.splitlines():
            if "branch: Created from" in line:
                sha = line.split(" ", 1)[0].strip()
                if sha:
                    return sha, "reflog branch-creation point"
    if base_ref:
        rc, out, _ = git(repo_dir, "merge-base", base_ref, head_sha)
        if rc == 0 and out.strip() and out.strip() != head_sha:
            return out.strip(), f"merge-base with {base_ref}"
    if pr is not None:
        rc, out, _ = git(
            repo_dir, "log", "--merges", "--format=%H %P", "-n", "20", "--grep", f"#{pr}"
        )
        if rc == 0:
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1], "first parent of the PR merge commit"
    return None, "PR base commit could not be resolved"


def fetch_diff_git(repo_dir, branch, pr):
    """Fetch a PR's diff size from the local clone with git numstat.

    Returns (stats, reason).  ``stats`` is None when the diff could not
    be fetched; ``reason`` then states the failure.
    """
    head_sha, head_reason = resolve_head(repo_dir, branch)
    if head_sha is None:
        return None, head_reason
    base_ref = default_base_ref(repo_dir)
    base_sha, base_source = resolve_base(repo_dir, branch, head_sha, base_ref, pr)
    if base_sha is None:
        return None, base_source
    rc, out, err = git(repo_dir, "diff", "--numstat", base_sha, head_sha)
    if rc != 0:
        detail = (err.strip() or "git diff --numstat failed").splitlines()
        return None, detail[-1].strip()
    files = 0
    added = 0
    deleted = 0
    binary = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        if parts[0] == "-" or parts[1] == "-":
            binary += 1
            continue
        try:
            added += int(parts[0])
            deleted += int(parts[1])
        except ValueError:
            binary += 1
    if files == 0:
        return None, "empty diff between PR base and branch tip"
    return {
        "files": files,
        "added": added,
        "deleted": deleted,
        "binary": binary,
        "source": f"git numstat (base: {base_source})",
        "detail": f"git -C {os.path.basename(repo_dir)} diff --numstat {base_sha[:12]}..{head_sha[:12]}",
        "base": base_sha,
        "head": head_sha,
    }, None


def is_landed(repo_dir, head_sha, base_ref):
    """True when the worker branch tip is already on the default branch."""
    if not base_ref or not head_sha:
        return False
    rc, _, _ = git(repo_dir, "merge-base", "--is-ancestor", head_sha, base_ref)
    return rc == 0


def measure_rows(rows, clone_root, overrides, use_gh):
    """Build the raw table rows; every ledger row yields exactly one row."""
    table = []
    for row in rows:
        repo = row.get("repo") or "n/a"
        pr = pr_number(row)
        branch = row.get("branch")
        tokens_in = row.get("tokens_in")
        tokens_out = row.get("tokens_out")
        grade = row.get("grade")
        record = {
            "run_id": row.get("run_id") or f"{repo}-{row.get('issue')}",
            "repo": repo,
            "tier": row.get("tier") or "n/a",
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "steps": step_text(row.get("steps")),
            "grade": grade,
            "files_changed": None,
            "lines_added": None,
            "lines_deleted": None,
            "net_lines": None,
            "diff_source": "",
            "status": "unmeasured",
            "issue": row.get("issue"),
            "pr": pr,
            "pr_url": row.get("pr_url") or "",
            "branch": branch,
            "landed": False,
            "ts": row.get("ts") or "",
        }
        stats = None
        reason = None
        if use_gh:
            stats, reason = fetch_diff_gh(repo, pr)
        if stats is None:
            repo_dir = resolve_clone(repo, clone_root, overrides)
            if repo_dir is None:
                source = f"FAILED: no local clone for {repo} (gh: {reason})" if use_gh else (
                    f"FAILED: no local clone for {repo}"
                )
                record["diff_source"] = source
                table.append(record)
                continue
            git_stats, git_reason = fetch_diff_git(repo_dir, branch, pr)
            if git_stats is None:
                source = f"FAILED: {git_reason}"
                if use_gh and reason:
                    source = f"FAILED: {git_reason} (gh: {reason})"
                record["diff_source"] = source
                table.append(record)
                continue
            stats = git_stats
            if use_gh:
                stats["source"] = f"{stats['source']}; gh fallback used"
        record["files_changed"] = stats["files"]
        record["lines_added"] = stats["added"]
        record["lines_deleted"] = stats["deleted"]
        record["net_lines"] = stats["added"] - stats["deleted"]
        record["diff_source"] = stats["source"]
        record["status"] = "measured"
        repo_dir = resolve_clone(repo, clone_root, overrides)
        if repo_dir is not None:
            base_ref = default_base_ref(repo_dir)
            head_sha = stats.get("head", "")
            if not head_sha and branch:
                # The gh-API stats carry no head sha; resolve the worker
                # branch in the clone so `landed` works for API rows too.
                head_sha, _head_reason = resolve_head(repo_dir, branch)
            record["landed"] = is_landed(repo_dir, head_sha or "", base_ref)
        table.append(record)
    return table


# --------------------------------------------------------------------------
# distributions
# --------------------------------------------------------------------------


def distributions(table):
    """Median and p90 of net_lines and tokens_out per repo and per tier."""
    buckets = defaultdict(list)
    for row in table:
        buckets[("repo", row["repo"])].append(row)
        buckets[("tier", row["tier"])].append(row)
        buckets[("all", "all rows")].append(row)
    out = []
    for (scope, key), rows in sorted(buckets.items()):
        measured = [row for row in rows if row["status"] == "measured"]
        net_values = [row["net_lines"] for row in measured if row["net_lines"] is not None]
        token_values = [
            row["tokens_out"] for row in rows if isinstance(row["tokens_out"], (int, float))
        ]
        out.append(
            {
                "scope": scope,
                "key": key,
                "rows": len(rows),
                "measured": len(measured),
                "unmeasured": len(rows) - len(measured),
                "net_n": len(net_values),
                "net_median": median(net_values),
                "net_p90": percentile(net_values, 0.90),
                "tokens_n": len(token_values),
                "tokens_median": median(token_values),
                "tokens_p90": percentile(token_values, 0.90),
            }
        )
    return out


# --------------------------------------------------------------------------
# frozen case set
# --------------------------------------------------------------------------


def recency_key(row):
    """Sort key: most recent first, ties broken by issue then run id."""
    return (row.get("ts") or "", -(row.get("issue") or 0), row.get("run_id") or "")


def select_cases(table, count, min_repos=3, required_tiers=("cheap", "balanced")):
    """Pick the frozen case set.

    Candidates are measured rows that carry a grade and whose worker
    branch is already landed on the repo's default branch (a known-good
    PR).  The rule is deterministic: tier anchors first, then repo
    anchors, then a diversity-first fill in recency order.
    """
    pool = [
        row
        for row in table
        if row["status"] == "measured"
        and row.get("grade") is not None
        and row.get("issue") is not None
        and row.get("pr") is not None
    ]
    known_good = [row for row in pool if row.get("landed")]
    if len(known_good) >= count:
        pool = known_good
    pool = sorted(pool, key=recency_key, reverse=True)
    picked = []
    used_repos = set()
    used_tiers = set()

    def take(candidate):
        picked.append(candidate)
        used_repos.add(candidate["repo"])
        used_tiers.add(candidate["tier"])

    for tier in required_tiers:
        for candidate in pool:
            if candidate in picked:
                continue
            if candidate["tier"] == tier:
                take(candidate)
                break
    while len(used_repos) < min_repos:
        candidate = None
        for option in pool:
            if option in picked:
                continue
            if option["repo"] not in used_repos:
                candidate = option
                break
        if candidate is None:
            break
        take(candidate)
    for candidate in pool:
        if len(picked) >= count:
            break
        if candidate in picked:
            continue
        if candidate["repo"] not in used_repos or candidate["tier"] not in used_tiers:
            take(candidate)
    # Final pass: spread the remaining slots over the least-used repos so
    # the frozen set does not pile up on a single repo.
    while len(picked) < count:
        counts = defaultdict(int)
        for chosen in picked:
            counts[chosen["repo"]] += 1
        remaining = [option for option in pool if option not in picked]
        if not remaining:
            break
        remaining.sort(
            key=lambda option: (counts[option["repo"]], option.get("ts") or ""),
            reverse=False,
        )
        least = min(counts[option["repo"]] for option in remaining)
        best = [option for option in remaining if counts[option["repo"]] == least]
        best.sort(key=recency_key, reverse=True)
        take(best[0])
    return picked[:count]


def case_rows(table, cases):
    """Turn the selected cases into report rows with justifications."""
    rows = []
    for index, case in enumerate(cases, start=1):
        landed = "PR landed on the default branch" if case.get("landed") else "PR opened"
        justification = (
            f"{case['repo']} / {case['tier']} tier: issue #{case['issue']} was completed by "
            f"worker PR #{case['pr']} (grade {case['grade']}, "
            f"{case['files_changed']} file(s) changed, net {case['net_lines']:+d} lines, "
            f"{landed}) -- re-runnable because the issue and its known-good PR are both "
            f"recorded in the ledger, so the same issue can be replayed from scratch on the "
            f"same repo and tier"
        )
        rows.append(
            {
                "case": f"C{index:02d}",
                "repo": case["repo"],
                "issue": case["issue"],
                "tier": case["tier"],
                "pr": case["pr"],
                "pr_url": case.get("pr_url") or "",
                "grade": case["grade"],
                "files_changed": case["files_changed"],
                "net_lines": case["net_lines"],
                "justification": justification,
            }
        )
    return rows


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def render_table(rows, columns, fmt):
    """Render rows as a markdown / tsv / csv table."""
    if fmt == "tsv":
        lines = ["\t".join(columns)]
        lines.extend("\t".join(str(row.get(col, "")) for col in columns) for row in rows)
        return "\n".join(lines)
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return buffer.getvalue().rstrip("\n")
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join(lines)


def render_report(ledger_path, table, dists, cases, gh_state, gh_note, args):
    """Render the whole measurement output as text."""
    measured = [row for row in table if row["status"] == "measured"]
    failed = [row for row in table if row["status"] != "measured"]
    lines = []
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- ledger: `{ledger_path}`")
    lines.append(f"- rows selected: {len(table)} most recent `pr_opened` rows")
    lines.append(f"- rows measured: {len(measured)}; rows with an unfetchable diff: {len(failed)}")
    lines.append(f"- GitHub API via `gh`: {gh_state} ({gh_note})")
    if not gh_state.startswith("available"):
        lines.append(
            "- diff sizes therefore come from the local supervisor-loop clones "
            "(`git diff --numstat` at the recorded worker branch); the fetch method "
            "used for each row is in the `diff_source` column"
        )
    lines.append(f"- p90 is nearest-rank; the median is the ordinary median; no means are reported")
    lines.append("")
    lines.append("## Raw table (verbatim script output)")
    lines.append("")
    lines.append(render_table(table, RAW_COLUMNS, args.format))
    lines.append("")
    lines.append("## Distributions -- per repo")
    lines.append("")
    dist_columns = [
        "scope",
        "key",
        "rows",
        "measured",
        "unmeasured",
        "net_n",
        "net_median",
        "net_p90",
        "tokens_n",
        "tokens_median",
        "tokens_p90",
    ]
    per_repo = [row for row in dists if row["scope"] == "repo"]
    lines.append(render_table(per_repo, dist_columns, args.format))
    lines.append("")
    lines.append("## Distributions -- per tier")
    lines.append("")
    per_tier = [row for row in dists if row["scope"] == "tier"]
    lines.append(render_table(per_tier, dist_columns, args.format))
    lines.append("")
    lines.append("## Distributions -- whole sample")
    lines.append("")
    overall = [row for row in dists if row["scope"] == "all"]
    lines.append(render_table(overall, dist_columns, args.format))
    lines.append("")
    lines.append(f"## Frozen case set ({len(cases)} cases)")
    lines.append("")
    lines.append(render_table(cases, CASE_COLUMNS, args.format))
    lines.append("")
    lines.append("## Rows whose diff could not be fetched")
    lines.append("")
    if failed:
        lines.append(render_table(failed, RAW_COLUMNS, args.format))
    else:
        lines.append("None: every selected row's diff was fetched.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def parse_overrides(pairs):
    """Parse repeated --repo-dir NAME=PATH options."""
    overrides = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--repo-dir expects NAME=PATH, got {pair!r}")
        name, path = pair.split("=", 1)
        overrides[name.strip()] = os.path.abspath(os.path.expanduser(path.strip()))
    return overrides


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Phase-0 Ponytail-ladder brief-rule measurement (issue #21).",
    )
    parser.add_argument("--ledger", default=DEFAULT_LEDGER, help="worker ledger jsonl")
    parser.add_argument(
        "--rows", type=int, default=60, help="most recent pr_opened rows to analyse"
    )
    parser.add_argument(
        "--cases", type=int, default=12, help="number of frozen cases to pick"
    )
    parser.add_argument(
        "--clone-root",
        default=os.path.expanduser("~/workspace"),
        help="root holding the supervisor-loop clones (default: ~/workspace)",
    )
    parser.add_argument(
        "--repo-dir",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="override the clone directory for one repo",
    )
    parser.add_argument(
        "--format", choices=["markdown", "csv", "tsv"], default="markdown", help="table format"
    )
    parser.add_argument("--out", default=None, help="also write the output to this path")
    parser.add_argument(
        "--no-gh", action="store_true", help="do not attempt the GitHub API"
    )
    args = parser.parse_args(argv)

    overrides = parse_overrides(args.repo_dir)
    rows, malformed = read_ledger(args.ledger)
    selected = select_pr_rows(rows, args.rows)

    if args.no_gh:
        gh_available_flag = False
        gh_note = "skipped via --no-gh"
    else:
        gh_available_flag, gh_note = gh_probe()
    gh_state = "available" if gh_available_flag else "unavailable"

    table = measure_rows(selected, args.clone_root, overrides, gh_available_flag)
    dists = distributions(table)
    cases = case_rows(table, select_cases(table, args.cases))
    report = render_report(
        args.ledger, table, dists, cases, gh_state, gh_note, args
    )
    if malformed:
        report += f"\n(ledger lines that were not valid JSON and were skipped: {malformed})\n"

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(report)
    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
