#!/usr/bin/env python3
"""Catching JiTTests for the backlog supervisor (JiTTest spike,
approved by the operator 2026-11-10; pattern from Meta's Catching
JiTTests, arXiv:2601.22832 / Engineering at Meta 2026-10-29).

Phase 0 calibration (2026-11-09/10) killed the original one-shot
design: cheap-tier tests generated straight from a diff pinned the
intent layer the buggy code already satisfied, inherited the
spec's own errors, or failed indiscriminately — 0 of 10 tests
passed the three-state gate. Phase 0b's hypothesis-first variant
passed in substance on the same case set (2 of 4 known-bug
catches — the seam and ordering defects, both
ranked #1 by the hypothesis step and landed by shape-matched
tests — plus a third real catch against a case mislabelled
clean). This script is the mechanical half of the 0B pipeline:

  check    Trigger decision at PR collection, from the facts the
           supervisor already has (dispatch tier, diff size/paths,
           failover status, trajectory grade band). Policy-as-data
           in the deployment config (workers.jittest).
  packet   Assemble the hypothesis packet: issue verbatim, PR
           description, the FULL diff (truncation is detected from
           hunk line counts and refused — Phase 0b case C3 lost a
           catch to a diff excerpt that hid an edit), EXTERNAL
           CONTRACT material for data-format diffs (Phase 0b case
           K2: the ranker trusted a docstring that misstated the
           format because the packet carried no counter-evidence),
           the changed-function list (coverage seed), and the
           narrowed run-shape blocks for both JiT runs.
  parse-hypotheses
           Parse HYPOTHESES.md (the 0b format): keep only
           hypotheses whose quoted EVIDENCE verifies verbatim
           (whitespace-normalised) in the named worktree file —
           the check_hyp0b.py approach from calibration — and
           account per-changed-function COVERAGE (Phase 0b case
           K3: the facet the rewrite touched went unvisited
           because nothing required a hypothesis per changed
           function). Unaccounted functions flag the packet
           coverage-incomplete.
  launch   Run one JiT agent run (bin/agent.py) with a HARD
           tokens-out cap: agent.py's own --max-tokens-out is a
           soft after-step guard (Phase 0b: a test run reported
           43,599 tokens-out against a 30k cap), so this wrapper
           also polls the run's transcript, sums tokens_out, and
           terminates the subprocess the moment the cap is
           crossed. Used for both the hypothesis run (balanced)
           and the test run (cheap).
  harvest  Mechanically collect a JiT worktree's artifacts —
           generated test files (untracked/modified, matched by
           test-file patterns) and any report-shaped file — so
           the pipeline never depends on the agent having written
           one specifically-named report (Phase 0b: 3 of 7 test
           runs wrote no JITTEST_REPORT.md).
  three-state
           The mechanical gate: the generated test must FAIL on
           the PR/diff state, PASS on the verify state (base or
           fixed state), and FAIL THE SAME WAY on a re-run of the
           diff state. Direct subprocess exit codes, never piped.
           Executions are budgeted (workers.jittest.
           max_three_state_runs) — the test step's verify-loop
           budget.
  assess   Stage A deterministic kills (RubFake-style: env /
           import / fixture failures, assertions on implementation
           details including exact-float equality on simulation
           outputs per the operator's output-value policy, tests
           that write to or mock the code under test, non-
           determinism by construction, three-state irreproducible)
           followed by the single-judge verdict parser
           (catch|intended|unclear, quoted-evidence-or-discard).
  log      Append one row per completed check to
           jittest_ledger.jsonl (kept separate from
           worker_ledger.jsonl so failover/grade counting over
           worker rows is never polluted; the weekly harness-tune
           pass reads it). Rows carry a label_adjudication object
           (design requirement 1): when a "clean" ground-truth
           label is disputed — Phase 0b's C1 survivor was a real
           defect in a clean-labelled PR — the adjudication is
           recorded on the row so future scoring fixes the label
           before computing rates.

The LLM halves are ordinary bin/agent.py runs driven by the
supervisor prompt (step 7b): a hypothesis run at the balanced
tier producing HYPOTHESES.md under the narrowed run shape, then
ONE test run for the top-ranked surviving hypothesis only, with
the shape rule enforced there (a seam/ordering hypothesis
requires a two-component / end-to-end test; a unit test for a
seam claim is a shape mismatch, not a test of the claim).
No voting, no parallel generators (arXiv:2611.07140, 2611.00314).

Policy (workers.jittest in the deployment config) ships DISABLED:
enabled=false means the pipeline subcommands exit 2 and the
supervisor collects exactly as before. Enabling is a separate
operator decision, per the burn-guard caps precedent.

Exit codes: 0 = ok, 2 = jittest disabled / input missing,
3 = packet rejected (truncated diff) / budget refused,
4 = launch hard-cap kill (run treated as failed).

Usage:
    python3 bin/jittest.py check --repo R --issue N --pr P
        [--tier T] [--diff-files K] [--diff-lines K]
        [--diff-paths a,b,c] [--failover-action A]
        [--grade-band bottom|normal] [--has-regression-test no]
        [--cycle-checks-used K] [--json]
    python3 bin/jittest.py packet --repo R --issue N --pr P
        --issue-file F --pr-file F --diff-file F --base-dir D
        [--contract F [--contract F...]] [--out F] [--json]
    python3 bin/jittest.py parse-hypotheses --report F
        --workdir D [--diff-file F] [--changed-functions a,b]
        [--json]
    python3 bin/jittest.py launch --brief F --workdir D
        --tier T --cap K --run-id ID --transcript-dir DIR
        [--provider minimax,zai] [--max-steps K] [--json]
    python3 bin/jittest.py harvest --workdir D [--all] [--json]
    python3 bin/jittest.py three-state --test-path P
        --diff-workdir D --verify-workdir D
        --cmd <argv...> [--timeout-secs K] [--out F] [--json]
    python3 bin/jittest.py assess --test-file F
        [--three-state-file F] [--buggy-output F]
        [--verify-output F] [--judge-file F] [--diff-file F]
        [--issue-file F] [--json]
    python3 bin/jittest.py log --repo R --issue N --pr P
        --trigger T --outcome O [count flags...]
        [--label-ground-truth G] [--label-adjudicated-by W]
        [--label-note T] [--ledger PATH] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config  # noqa: E402

DEFAULT_POLICY = {
    "enabled": False,
    "max_per_cycle": 2,
    "hypothesis_tier": "balanced",
    "test_tier": "cheap",
    "max_tokens_out_hypothesis": 30000,
    "max_tokens_out_test": 30000,
    "max_steps": 25,
    "max_files": 5,
    "max_diff_lines": 300,
    "max_three_state_runs": 3,
}
TRIGGERS = ("hard-stop", "size", "flagship", "failover",
            "lucky-green")
OUTCOMES = ("catch_flagged", "no_catch", "unavailable",
            "shape_mismatch", "coverage_incomplete")
HYP_CLASSES = ("seam", "ordering", "spec-vs-code", "facet", "edge")
SHAPE_REQUIRED = {"seam": "seam", "ordering": "ordering"}
CONFIDENCES = ("high", "medium", "low")
HARD_STOP_MARKERS = (".github/workflows/", "migrat", "/physics",
                     "physics/", "simulation", "/auth", "auth/",
                     "privacy", "deletion", "public api", "/api/")
DOC_EXTS = (".md", ".rst", ".txt")
TEST_PATH_MARKERS = ("test", "spec")
# Data-format boundaries: a diff touching one of these needs the
# format's own contract in the packet (requirement 2, case K2).
FORMAT_EXTS = (".parquet", ".csv", ".tsv", ".jsonl", ".ndjson",
               ".xlsx", ".xls", ".ifc", ".xml", ".avro", ".feather",
               ".arrow", ".proto", ".gbxml", ".idf", ".epw", ".bin")
FORMAT_PATH_MARKERS = ("format", "schema", "cobie", "floorplancad",
                       "dataset", "parquet")
CONTRACT_NAME_MARKERS = ("spec", "schema", "format", "contract",
                         "readme")
MAX_CONTRACT_FILES = 4
MAX_CONTRACT_CHARS = 6000
MAX_NAMED_FILES = 3  # requirement 4: the narrowed shape's file cap


def policy_from_config() -> dict:
    cfg = _load_deployment_config() or {}
    workers = cfg.get("workers") or {}
    policy = dict(DEFAULT_POLICY)
    policy.update(workers.get("jittest") or {})
    return policy


def default_jittest_ledger_path() -> str | None:
    cfg = _load_deployment_config() or {}
    state_dir = ((cfg.get("state") or {}).get("dir")
                 or "~/.plan-coder/state")
    return os.path.join(os.path.expanduser(state_dir),
                        "jittest_ledger.jsonl")


def _disabled(policy: dict) -> bool:
    return policy.get("enabled") is not True


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ",
                  (text or "").replace('\\"', '"')).strip()


# ---------------------------------------------------------------- check

def _is_test_path(path: str) -> bool:
    low = path.lower()
    base = low.rsplit("/", 1)[-1]
    return ("/tests/" in low or "/test/" in low
            or base.startswith("test_") or base.startswith("test.")
            or ".test." in base or ".spec." in base
            or base.endswith("_test.py") or base.endswith("_test.go"))


def _is_doc_path(path: str) -> bool:
    low = path.lower()
    return low.endswith(DOC_EXTS) or "/docs/" in low \
        or low.startswith("docs/")


def decide_trigger(policy: dict, tier: str = "", diff_files: int = 0,
                   diff_lines: int = 0, diff_paths: list | None = None,
                   failover_action: str = "", grade_band: str = "",
                   has_regression_test: bool = False,
                   cycle_checks_used: int = 0) -> dict:
    diff_paths = diff_paths or []
    base = {"triggered": False, "trigger": None, "exempt": None,
            "matched": [], "tier": tier, "diff_files": diff_files,
            "diff_lines": diff_lines}
    if diff_paths and all(_is_doc_path(p) for p in diff_paths):
        return {**base, "exempt": "docs-only"}
    if diff_paths and all(_is_test_path(p) for p in diff_paths):
        return {**base, "exempt": "test-only"}
    if has_regression_test:
        return {**base, "exempt": "has-regression-test"}
    matched = []
    joined = " ".join(diff_paths).lower()
    if any(marker in joined for marker in HARD_STOP_MARKERS):
        matched.append("hard-stop")
    if tier == "flagship":
        matched.append("flagship")
    if failover_action in ("failover", "dispatch_with_reflection"):
        matched.append("failover")
    if diff_files > int(policy.get("max_files", 5)) \
            or diff_lines > int(policy.get("max_diff_lines", 300)):
        matched.append("size")
    if grade_band == "bottom":
        matched.append("lucky-green")
    base["matched"] = matched
    if not matched:
        return base
    if cycle_checks_used >= int(policy.get("max_per_cycle", 2)):
        return {**base, "exempt": "cycle-cap"}
    return {**base, "triggered": True, "trigger": matched[0]}


# ----------------------------------------------------------------- diff

FUNC_PATTERNS = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
               r"function\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_]\w*)\s*="
               r"\s*(?:async\s*)?\("),
    re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)"),
    re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),
)
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
                     r"\s*(.*)$")


def _func_of_line(text: str) -> str | None:
    for pat in FUNC_PATTERNS:
        m = pat.match(text)
        if m:
            return m.group(1)
    return None


def parse_diff(diff_text: str) -> dict:
    """Parse a unified diff: per-file added/deleted counts, the
    functions touched (definitions on +/- lines plus hunk-header
    context — a mechanical approximation: a function whose body
    changed but whose def line and hunk context name another
    symbol can be missed), and truncation detection from hunk
    declared-vs-actual line counts."""
    files: dict[str, dict] = {}
    cur = None
    hunk = None  # [want_old, want_new, got_old, got_new]

    def flush_hunk():
        nonlocal hunk
        if hunk is not None and cur is not None:
            want_old, want_new, got_old, got_new = hunk
            if want_old != got_old or want_new != got_new:
                files[cur]["hunk_mismatch"] = True
        hunk = None

    for raw in (diff_text or "").splitlines():
        if raw.startswith("diff --git "):
            flush_hunk()
            cur = None
            continue
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            if path == "/dev/null":
                cur = None
                continue
            cur = path[2:] if path.startswith("b/") else path
            files.setdefault(cur, {"path": cur, "added": 0,
                                   "deleted": 0, "functions": [],
                                   "hunk_mismatch": False})
            continue
        m = HUNK_RE.match(raw)
        if m and cur is not None:
            flush_hunk()
            want_old = int(m.group(2)) if m.group(2) else 1
            want_new = int(m.group(4)) if m.group(4) else 1
            hunk = [want_old, want_new, 0, 0]
            ctx_func = _func_of_line(m.group(5) or "")
            if ctx_func and ctx_func not in files[cur]["functions"]:
                files[cur]["functions"].append(ctx_func)
            continue
        if cur is None or hunk is None:
            continue
        if raw.startswith("\\"):
            continue
        sign = raw[:1]
        body = raw[1:]
        if sign == "+":
            files[cur]["added"] += 1
            hunk[3] += 1
        elif sign == "-":
            files[cur]["deleted"] += 1
            hunk[2] += 1
        elif sign == " " or raw == "":
            hunk[2] += 1
            hunk[3] += 1
        else:
            continue
        if sign in "+- ":
            fn = _func_of_line(body)
            if fn and fn not in files[cur]["functions"]:
                files[cur]["functions"].append(fn)
    flush_hunk()
    out_files = sorted(files.values(), key=lambda f: f["path"])
    truncated = any(f["hunk_mismatch"] for f in out_files)
    changed_functions = []
    for f in out_files:
        for fn in f["functions"]:
            changed_functions.append({"function": fn,
                                      "file": f["path"]})
    return {
        "files": out_files,
        "n_files": len(out_files),
        "added": sum(f["added"] for f in out_files),
        "deleted": sum(f["deleted"] for f in out_files),
        "changed_functions": changed_functions,
        "truncated": truncated,
    }


def _is_format_file(path: str) -> bool:
    low = path.lower()
    return low.endswith(FORMAT_EXTS) \
        or any(marker in low for marker in FORMAT_PATH_MARKERS)


def find_contract_material(base_dir: str | None,
                           format_files: list) -> tuple:
    """For each data-format file the diff touches, look for the
    format's canonical contract inside base_dir: caller-supplied
    files are handled by the caller; here we search for spec /
    schema / format / readme files sharing the format file's stem
    or directory. Returns (included, missing): included entries
    are {for, path, excerpt}; missing lists format files with no
    contract found."""
    included, missing = [], []
    if not base_dir:
        return included, list(format_files)
    candidates = []
    for root, dirs, names in os.walk(base_dir):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "node_modules", ".venv",
                                "__pycache__")]
        if root[len(base_dir):].count(os.sep) > 4:
            dirs[:] = []
            continue
        for name in names:
            low = name.lower()
            if any(m in low for m in CONTRACT_NAME_MARKERS):
                candidates.append(os.path.join(root, name))
    for fmt in format_files:
        stem = os.path.splitext(os.path.basename(fmt))[0].lower()
        fmt_dir = os.path.dirname(fmt)
        hit = None
        for cand in candidates:
            rel = os.path.relpath(cand, base_dir)
            low = rel.lower()
            if stem and stem in low:
                hit = rel
                break
            if fmt_dir and os.path.dirname(rel) == fmt_dir:
                hit = rel
                break
        if hit is None:
            missing.append(fmt)
            continue
        try:
            text = open(os.path.join(base_dir, hit),
                        encoding="utf-8", errors="replace").read()
        except OSError:
            missing.append(fmt)
            continue
        included.append({"for": fmt, "path": hit,
                         "excerpt": text[:MAX_CONTRACT_CHARS]})
        if len(included) >= MAX_CONTRACT_FILES:
            break
    return included, missing


# --------------------------------------------------------------- packet

HYPOTHESIS_FORMAT_BLOCK = """\
## HYPOTHESES.md format (exactly)

# Hypotheses — <repo> PR #<pr>

## H1
CLAIM: <one sentence: the specific wrong behaviour, and where>
CLASS: seam | ordering | spec-vs-code | facet | edge
EVIDENCE: "<verbatim quoted line>" — <file>:<line>
EVIDENCE: "<verbatim quoted line>" — <file>:<line>   (second line optional)
FALSIFIED_BY: <what a test would observe on this code if the claim is true, vs on correct code>
CONFIDENCE: high | medium | low
COVERAGE: <changed function name>: covered | no-hypothesis — <one-line reason>

## H2
... (3 to 5 hypotheses total, ranked most-likely-real first)

## Classes considered
<one line per class: the hypothesis id that covers it, or why it does not apply>

RANKED: H1, H2, ...
"""


def build_packet(policy: dict, repo: str, issue, pr,
                 issue_text: str, pr_text: str, diff_text: str,
                 base_dir: str | None,
                 contracts: list | None = None) -> tuple:
    """Return (packet_text, manifest). contracts is a list of
    {for, path, excerpt} dicts (caller-supplied files merged with
    the ones find_contract_material found)."""
    info = parse_diff(diff_text)
    digest = hashlib.sha256(
        (issue_text + "\n---PR---\n" + pr_text + "\n---DIFF---\n"
         + diff_text).encode("utf-8")).hexdigest()
    contracts = contracts or []
    format_files = [f["path"] for f in info["files"]
                    if _is_format_file(f["path"])]
    named = []
    for cf in info["changed_functions"]:
        if cf["file"] not in named:
            named.append(cf["file"])
        if len(named) >= MAX_NAMED_FILES:
            break
    if not named:
        named = [f["path"] for f in info["files"][:MAX_NAMED_FILES]]
    manifest = {
        "repo": repo, "issue": issue, "pr": pr,
        "diff_files": info["n_files"],
        "diff_lines": info["added"] + info["deleted"],
        "diff_truncated": info["truncated"],
        "format_files": format_files,
        "contract_included": [c["path"] for c in contracts],
        "changed_functions": info["changed_functions"],
        "named_files": named,
        "content_sha256": digest,
    }
    max_steps = int(policy.get("max_steps", 25))
    lines = [
        "<!-- jittest-manifest: " + json.dumps(manifest) + " -->",
        "",
        f"# JiTTest hypothesis packet — {repo} PR #{pr} "
        f"(issue #{issue})",
        "",
        "## Issue (verbatim)",
        "",
        issue_text.strip(),
        "",
        "## Pull request description (verbatim)",
        "",
        pr_text.strip(),
        "",
        "## The change (full diff — never excerpted)",
        "",
        "```diff",
        diff_text.rstrip(),
        "```",
        "",
    ]
    if format_files:
        lines += ["## External contract material (read this BEFORE "
                  "trusting any docstring in the diff)", ""]
        if contracts:
            for c in contracts:
                lines += [f"### Contract for `{c['for']}` — "
                          f"`{c['path']}`", "", "```",
                          c["excerpt"].rstrip(), "```", ""]
        else:
            lines += ["(No contract file was found in the repo for "
                      "the format(s) this diff touches: "
                      + ", ".join(format_files) + ". Treat every "
                      "in-diff claim about the format's encoding "
                      "as UNVERIFIED — a spec-vs-code hypothesis "
                      "naming that risk is in scope.)", ""]
    lines += [
        "## Changed functions (coverage seed)",
        "",
        "Every function below must be accounted for in your "
        "hypothesis list: either a hypothesis covers it, or the "
        "hypothesis that touches its file carries a COVERAGE line "
        "marking it `no-hypothesis` with a one-line reason. "
        "Unaccounted functions are flagged mechanically.",
        "",
    ]
    lines += [f"- `{cf['function']}` ({cf['file']})"
              for cf in info["changed_functions"]] or ["(none "
                                                       "detected)"]
    lines += [
        "",
        "## Run shape — hypothesis run (mandatory)",
        "",
        "Your job: produce a RANKED list of defect hypotheses for "
        "this change. NO test code. NO code changes. The only file "
        "you write is HYPOTHESES.md in the worktree root.",
        "",
        "- FIRST-TOOL-CALL RULE: after reading this packet and the "
        "named source file(s) — and nothing else — your very next "
        "action MUST be writing a first draft of HYPOTHESES.md "
        "(skeleton with at least 2 hypotheses). Then refine. If "
        "you are past step 8 and HYPOTHESES.md does not exist, "
        "you are failing this task.",
        f"- READS ARE RESTRICTED to: this packet, and these named "
        f"files only (at most {MAX_NAMED_FILES}): "
        + ", ".join(f"`{p}`" for p in named) + ". Do not explore "
        "the rest of the repo.",
        f"- STEP BUDGET: finish within {max_steps} steps; a "
        "shorter list of 3 well-evidenced hypotheses is a "
        "complete result.",
        "- The change is a REAL change, not a seeded-defect "
        "puzzle: do not verify the packet against git history or "
        "upstream. Do not run any tests. Do not write probe "
        "scripts.",
        "- This is a verification-style run. The no-edit / "
        "empty-diff worker gates do NOT apply: writing "
        "HYPOTHESES.md IS your entire edit. No other files. No "
        "commits, no pushes.",
        "",
        "## Mandatory method — consider these classes explicitly",
        "",
        "1. SEAM: two components exercised together — a value "
        "produced by one component is consumed by another under "
        "a different name, shape, unit, encoding, or assumption.",
        "2. ORDERING: steps run in the wrong order — a resource "
        "is claimed/charged/committed before input is parsed or "
        "validated; a check runs after the action it guards.",
        "3. SPEC-VS-CODE: the stated intent, docstring, comment "
        "or issue text itself may be WRONG and the code faithfully "
        "implements the wrong spec — or contradicts the spec it "
        "claims to follow. Where contract material is included "
        "above, check in-diff format claims AGAINST it.",
        "4. FACET: the defect lives in a different facet of the "
        "same module than the headline intent (a performance "
        "rewrite whose defect is in the numeric predicate it "
        "refactored).",
        "5. EDGE: boundary, empty, duplicate, or extreme inputs "
        "mishandled by the new code path.",
        "",
        "Address each class with at least one candidate "
        "hypothesis OR state in one line why the class does not "
        "apply. EVERY hypothesis must carry at least one "
        "EVIDENCE line quoting the actual diff or code verbatim "
        "with file and line; a hypothesis whose evidence cannot "
        "be quoted will be discarded mechanically.",
        "",
        HYPOTHESIS_FORMAT_BLOCK,
        "## Run shape — test run (for the follow-on run; read so "
        "your FALSIFIED_BY lines are testable)",
        "",
        "A separate run will write ONE test for the top-ranked "
        "surviving hypothesis only, under these rules: if the "
        "hypothesis's CLASS is seam or ordering, the test must "
        "exercise the components together (real producer into "
        "real consumer; real entry point end-to-end, mocks only "
        "at true external boundaries) — a single-component unit "
        "test will be recorded as a shape mismatch, not a test "
        "of the claim. If the CLASS is spec-vs-code, the test "
        "must encode the CORRECT behaviour from an authority "
        "independent of the possibly-wrong spec text. The test "
        "must assert behaviour, be deterministic (no wall clock, "
        "no unseeded randomness, no network), and will be run "
        "mechanically: it must fail on this change, pass on the "
        "base, and fail the same way on re-run.",
        "",
    ]
    return "\n".join(lines), manifest


# ------------------------------------------------------- parse-hypotheses

HYP_BLOCK = re.compile(r"\n## (H\d+)\s*\n")
EVIDENCE_RE = re.compile(r"EVIDENCE:\s*\"(.+?)\"\s*[—-]\s*(\S+?):(\d+)",
                         re.DOTALL)
COVERAGE_RE = re.compile(r"COVERAGE:\s*`?([A-Za-z_][\w.]*)`?\s*:\s*"
                         r"(covered|no-hypothesis)",
                         re.IGNORECASE)


def _read_worktree_file(workdir: str, rel: str, cache: dict):
    if rel not in cache:
        path = os.path.join(workdir, rel)
        try:
            cache[rel] = open(path, encoding="utf-8",
                              errors="replace").read()
        except OSError:
            cache[rel] = None
    return cache[rel]


def verify_evidence(workdir: str, quote: str, fname: str, line: int,
                    cache: dict) -> dict:
    """The check_hyp0b.py approach: the quote must appear
    (whitespace-normalised) in the named file; line proximity
    (the cited line ±1) is recorded separately — a diff-line
    quote with a leading '+' fails the verbatim match and is
    recorded, not hidden (Phase 0b case C3)."""
    content = _read_worktree_file(workdir, fname, cache)
    result = {"file": fname, "line": line,
              "quote": quote[:110], "found_in_file": False,
              "line_matches": None}
    if content is None:
        return result
    result["found_in_file"] = _norm(quote) in _norm(content)
    lines = content.splitlines()
    if 1 <= line <= len(lines):
        window = lines[max(0, line - 2):line + 1]
        result["line_matches"] = any(
            _norm(quote) in _norm(w) for w in window)
    return result


def parse_hypotheses(report_text: str, workdir: str,
                     changed_functions: list | None = None) -> dict:
    changed_functions = changed_functions or []
    text = report_text or ""
    blocks = HYP_BLOCK.split("\n" + text if not text.startswith("\n")
                              else text)
    # blocks: [pre, id, body, id, body...]
    hypotheses, discarded = [], []
    cache: dict = {}
    declared_no_hyp: dict[str, str] = {}
    for m in COVERAGE_RE.finditer(text):
        if m.group(2).lower() == "no-hypothesis":
            declared_no_hyp[m.group(1).split(".")[-1]] = "declared"
    for i in range(1, len(blocks), 2):
        hid, body = blocks[i], blocks[i + 1]
        claim_m = re.search(r"CLAIM:\s*(.+)", body)
        class_m = re.search(r"CLASS:\s*(\S+)", body)
        conf_m = re.search(r"CONFIDENCE:\s*(\w+)", body)
        fals_m = re.search(r"FALSIFIED_BY:\s*(.+)", body)
        claim = claim_m.group(1).strip() if claim_m else ""
        hclass = class_m.group(1).strip().lower() if class_m else ""
        evidence = []
        for quote, fname, line in EVIDENCE_RE.findall(body):
            evidence.append(verify_evidence(workdir, quote, fname,
                                            int(line), cache))
        entry = {"id": hid, "claim": claim, "class": hclass,
                 "confidence": (conf_m.group(1).strip().lower()
                                if conf_m else ""),
                 "falsified_by": (fals_m.group(1).strip()
                                  if fals_m else ""),
                 "evidence": evidence}
        if not claim:
            discarded.append({**entry, "reason": "no_claim"})
        elif hclass not in HYP_CLASSES:
            discarded.append({**entry, "reason": "bad_class"})
        elif not evidence:
            discarded.append({**entry, "reason": "no_evidence"})
        elif not all(e["found_in_file"] for e in evidence):
            discarded.append({**entry,
                              "reason": "evidence_unverified"})
        else:
            hypotheses.append(entry)
    ranked_m = re.search(r"RANKED:\s*([H\d,\s]+)", text)
    if ranked_m:
        order = [t.strip() for t in ranked_m.group(1).split(",")
                 if t.strip()]
        by_id = {h["id"]: h for h in hypotheses}
        hypotheses = [by_id[h] for h in order if h in by_id] \
            + [h for h in hypotheses if h["id"] not in order]
    coverage = []
    for cf in changed_functions:
        fn = cf["function"] if isinstance(cf, dict) else str(cf)
        short = fn.split(".")[-1]
        covered_by = []
        for h in hypotheses:
            hay = " ".join([h["claim"], h["falsified_by"]] +
                           [e["quote"] for e in h["evidence"]])
            if short in hay:
                covered_by.append(h["id"])
        status = ("covered" if covered_by
                  else "no-hypothesis" if short in declared_no_hyp
                  else "unaccounted")
        coverage.append({"function": fn,
                         "file": (cf.get("file") if isinstance(cf, dict)
                                  else None),
                         "covered_by": covered_by, "status": status})
    unaccounted = [c["function"] for c in coverage
                   if c["status"] == "unaccounted"]
    return {"hypotheses": hypotheses, "discarded": discarded,
            "n_hypotheses": len(hypotheses),
            "n_discarded": len(discarded),
            "discarded_no_evidence":
                sum(1 for d in discarded
                    if d["reason"] == "no_evidence"),
            "discarded_unverifiable":
                sum(1 for d in discarded
                    if d["reason"] == "evidence_unverified"),
            "coverage": coverage,
            "coverage_complete": not unaccounted,
            "unaccounted_functions": unaccounted,
            "top": hypotheses[0] if hypotheses else None}


# --------------------------------------------------------------- harvest

REPORT_NAME_RE = re.compile(
    r"(report|hypotheses)", re.IGNORECASE)
TEST_FILE_RES = (
    re.compile(r"^test_.*\.py$"), re.compile(r".*_test\.py$"),
    re.compile(r".*\.test\.(ts|tsx|js|jsx)$"),
    re.compile(r".*\.spec\.(ts|tsx|js|jsx)$"),
    re.compile(r"^test_.*\.(ts|js)$"),
)


def _is_test_file(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    if any(p.match(base) for p in TEST_FILE_RES):
        return True
    parts = rel.split("/")
    return ("tests" in parts or "test" in parts) \
        and base.rsplit(".", 1)[-1] in ("py", "ts", "tsx", "js")


def harvest(workdir: str, all_files: bool = False) -> dict:
    """Collect generated tests + report-shaped artifacts from a
    JiT worktree. Candidate set: untracked/modified files per
    `git status --porcelain` (fallback: all files when not a git
    worktree or --all). The supervisor never has to guess which
    report filename an agent chose."""
    candidates = None
    if not all_files:
        try:
            r = subprocess.run(
                ["git", "-C", workdir, "status", "--porcelain",
                 "--untracked-files=all"],
                capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                candidates = []
                for line in r.stdout.splitlines():
                    path = line[3:].strip()
                    if " -> " in path:
                        path = path.split(" -> ")[-1]
                    candidates.append(path.strip('"'))
        except (OSError, subprocess.SubprocessError):
            candidates = None
    if candidates is None:
        candidates = []
        for root, dirs, names in os.walk(workdir):
            dirs[:] = [d for d in dirs if d not in
                       (".git", "node_modules", ".venv",
                        "__pycache__")]
            for name in names:
                candidates.append(os.path.relpath(
                    os.path.join(root, name), workdir))
    tests, reports = [], []
    hypotheses_file = None
    for rel in sorted(set(candidates)):
        if rel.startswith((".git/", "node_modules/")):
            continue
        if _is_test_file(rel):
            tests.append(rel)
        base = rel.rsplit("/", 1)[-1]
        if base == "HYPOTHESES.md":
            hypotheses_file = rel
        if REPORT_NAME_RE.search(base) and base.endswith(
                (".md", ".json", ".txt")):
            reports.append(rel)
    return {"tests": tests, "reports": reports,
            "hypotheses_file": hypotheses_file}


# ----------------------------------------------------------- three-state

def _run_direct(cmd: list, cwd: str, timeout: int) -> dict:
    """Direct subprocess run — the exit code IS the test runner's
    (Phase 0's first pass read a shell pipe's code and had to be
    discarded in full)."""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
        return {"exit": r.returncode,
                "output": (r.stdout + r.stderr)[-4000:]}
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"") + (exc.stderr or b"")
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return {"exit": -1, "output": (out or "")[-4000:],
                "timeout": True}
    except OSError as exc:
        return {"exit": -2, "output": f"runner error: {exc}"}


def failure_signature(output: str) -> str:
    lines = [ln.strip() for ln in (output or "").splitlines()
             if ln.strip()]
    interesting = [ln for ln in lines
                   if re.search(r"(assert|error|fail|expected)",
                                ln, re.IGNORECASE)]
    tail = (interesting[-3:] if interesting else lines[-3:])
    sig = " | ".join(tail)
    sig = re.sub(r"\d+(\.\d+)?\s*(ms|s)\b", "<t>", sig)
    sig = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "<ts>",
                 sig)
    return sig[:400]


def classify_three_state(buggy1: dict, buggy2: dict,
                         verify: dict) -> str:
    b1 = buggy1.get("exit", -9)
    b2 = buggy2.get("exit", -9)
    v = verify.get("exit", -9)
    if b1 == 0 and v != 0:
        return "feature_test"
    if b1 == 0 and v == 0:
        return "pass_on_both"
    # b1 failed from here
    if v != 0:
        return "fail_on_both"
    if b2 == 0:
        return "flake"
    if failure_signature(buggy1.get("output", "")) \
            != failure_signature(buggy2.get("output", "")):
        return "flake"
    return "catch"


def run_three_state(policy: dict, test_path: str, cmd: list,
                    diff_workdir: str, verify_workdir: str,
                    timeout: int = 600) -> dict:
    """Budgeted three-state execution: exactly 3 runner executions
    (diff, verify, diff re-run). If the policy's verify-loop budget
    is below 3 the check cannot run and is refused rather than
    silently exceeding the budget."""
    budget = int(policy.get("max_three_state_runs", 3))
    result = {"test": test_path, "cmd": cmd, "budget": budget,
              "executions": 0}
    if budget < 3:
        result["refused"] = (
            f"max_three_state_runs={budget} < 3: a three-state "
            "check needs exactly 3 executions")
        return result
    src = os.path.join(diff_workdir, test_path)
    result["generated_exists"] = os.path.exists(src)
    if not result["generated_exists"]:
        result["classification"] = "no_test"
        return result
    dst = os.path.join(verify_workdir, test_path)
    result["copied_to_verify"] = False
    if os.path.abspath(dst) != os.path.abspath(src) \
            and not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst) or verify_workdir,
                    exist_ok=True)
        with open(src, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(content)
        result["copied_to_verify"] = True
    buggy1 = _run_direct(cmd, diff_workdir, timeout)
    result["executions"] += 1
    verify = _run_direct(cmd, verify_workdir, timeout)
    result["executions"] += 1
    buggy2 = _run_direct(cmd, diff_workdir, timeout)
    result["executions"] += 1
    result.update({
        "buggy_run1": buggy1, "buggy_run2": buggy2,
        "verify_run": verify,
        "pattern": f"buggy={buggy1['exit']}/{buggy2['exit']} "
                   f"verify={verify['exit']}",
    })
    result["classification"] = classify_three_state(buggy1, buggy2,
                                                    verify)
    result["three_state_pass"] = result["classification"] == "catch"
    return result


# ---------------------------------------------------------------- assess

ENV_MARKERS = (
    "modulenotfounderror", "importerror", "cannot find module",
    "command not found", "no such file or directory",
    "fixture '", "eperm", "econnrefused", "getaddrinfo",
    "fetch failed", "networkerror", "collection error",
    "error collecting",
)
FLOAT_EQ_RE = re.compile(
    r"(?:assert\w*\s*\(|assert\s+|expect\s*\()[^\n]*?"
    r"(?:==|\.toBe\(|\.toEqual\()\s*-?\d+\.\d{6,}")
PRIVATE_ASSERT_RE = re.compile(r"\._[A-Za-z]\w*")
DESTRUCTIVE_RE = re.compile(
    r"(os\.remove|os\.unlink|shutil\.rmtree|fs\.unlink|"
    r"writeFileSync|open\([^)]*,\s*[\"'][wa][\"'])")
NONDET_MARKERS = ("time.time(", "datetime.now(", "date.now()",
                  "math.random(", "time.sleep(", "sleep(")
MOCK_RES = (
    re.compile(r"(?:jest|vi)\.mock\(\s*[\"']([^\"']+)[\"']"),
    re.compile(r"mock\.patch\(\s*[\"']([^\"']+)[\"']"),
    re.compile(r"monkeypatch\.setattr\(\s*[\"']([^\"']+)[\"']"),
)
PHYSICS_MARKERS = ("physics", "simulation", "/sim", "thermal",
                   "energyplus", "ashrae")


def stage_a_kills(test_text: str, buggy_output: str,
                  verify_output: str, three_state: dict | None,
                  diff_paths: list | None = None) -> list:
    kills = []
    diff_paths = diff_paths or []
    both_out = ((buggy_output or "") + "\n"
                + (verify_output or "")).lower()
    if any(marker in both_out for marker in ENV_MARKERS):
        kills.append({"rule": "env_failure",
                      "detail": "failure output carries an import / "
                                "fixture / environment marker"})
    text = test_text or ""
    if DESTRUCTIVE_RE.search(text):
        kills.append({"rule": "modifies_code_or_tests",
                      "detail": "test writes to or deletes files "
                                "instead of only asserting"})
    for pat in MOCK_RES:
        for target in pat.findall(text):
            norm_target = target.replace("\\", "/")
            for path in diff_paths:
                stem = path.rsplit(".", 1)[0]
                if stem and (stem in norm_target
                             or norm_target.endswith(
                                 stem.rsplit("/", 1)[-1])):
                    kills.append({
                        "rule": "mocks_code_under_test",
                        "detail": f"test mocks the changed module "
                                  f"itself: {target}"})
                    break
    physics_diff = any(any(m in p.lower()
                           for m in PHYSICS_MARKERS)
                       for p in diff_paths)
    if physics_diff and FLOAT_EQ_RE.search(text):
        kills.append({"rule": "exact_float_simulation_output",
                      "detail": "exact float equality asserted on "
                                "a simulation-output diff (operator "
                                "output-value policy: bounded "
                                "properties, not pinned values)"})
    assertion_lines = [ln for ln in text.splitlines()
                       if "assert" in ln or "expect(" in ln]
    if any(PRIVATE_ASSERT_RE.search(ln) for ln in assertion_lines):
        kills.append({"rule": "implementation_detail_assertion",
                      "detail": "assertion reaches a private "
                                "(_-prefixed) member"})
    nondet = [m for m in NONDET_MARKERS if m in text]
    if nondet and "seed" not in text.lower():
        kills.append({"rule": "nondeterministic_by_construction",
                      "detail": "uses " + ", ".join(nondet)
                                + " with no seed in the file"})
    if three_state is not None:
        cls = three_state.get("classification")
        if cls in ("flake",):
            kills.append({"rule": "three_state_irreproducible",
                          "detail": "re-run of the diff state did "
                                    "not fail the same way"})
    return kills


JUDGE_BLOCK = re.compile(r"```jittest-judge\s*\n(.*?)```",
                         re.DOTALL)
JUDGE_VERDICTS = ("catch", "intended", "unclear")


def parse_judge(text: str, context_text: str = "") -> dict:
    match = JUDGE_BLOCK.search(text or "")
    if not match:
        return {"verdict": None, "evidence": "",
                "discarded_no_evidence": 0, "block_found": False}
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {"verdict": None, "evidence": "",
                "discarded_no_evidence": 0, "block_found": True,
                "invalid": 1}
    verdict = str(raw.get("verdict") or "").strip().lower()
    evidence = str(raw.get("evidence") or "").strip()
    if verdict not in JUDGE_VERDICTS:
        return {"verdict": None, "evidence": evidence,
                "discarded_no_evidence": 0, "block_found": True,
                "invalid": 1}
    quote = evidence.strip().strip('"').strip()
    if not quote:
        return {"verdict": None, "evidence": "",
                "discarded_no_evidence": 1, "block_found": True}
    if context_text:
        hay = _norm(context_text)
        chunks = [quote[i:i + 40] for i in range(0, len(quote), 40)]
        if not any(_norm(c) in hay for c in chunks if len(c) >= 12):
            return {"verdict": None, "evidence": evidence,
                    "discarded_no_evidence": 0,
                    "discarded_unverifiable": 1, "block_found": True}
    return {"verdict": verdict, "evidence": evidence,
            "discarded_no_evidence": 0, "block_found": True}


def assess(test_text: str, three_state: dict | None,
           buggy_output: str = "", verify_output: str = "",
           judge_text: str = "", diff_paths: list | None = None,
           context_text: str = "") -> dict:
    kills = stage_a_kills(test_text, buggy_output, verify_output,
                          three_state, diff_paths)
    judge = parse_judge(judge_text, context_text) if judge_text \
        else {"verdict": None, "block_found": False,
              "discarded_no_evidence": 0}
    cls = (three_state or {}).get("classification")
    if kills:
        outcome = "killed_stage_a"
    elif cls != "catch":
        outcome = "not_a_catch"
    elif judge.get("verdict") == "catch":
        outcome = "surviving_catch"
    elif judge.get("verdict") in ("intended", "unclear"):
        outcome = f"judge_{judge['verdict']}"
    else:
        outcome = "pending_judge"
    return {"outcome": outcome, "stage_a_kills": kills,
            "judge": judge, "three_state_class": cls}


# ---------------------------------------------------------------- launch

def transcript_tokens_out(transcript_path: str) -> int:
    total = 0
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                value = row.get("tokens_out")
                if isinstance(value, (int, float)):
                    total += int(value)
    except OSError:
        return 0
    return total


def run_capped(argv: list, cap: int, transcript_path: str,
               poll_secs: float = 0.5,
               timeout_secs: int = 3600) -> dict:
    """Run argv as a subprocess with a HARD tokens-out cap
    (requirement 5): poll the transcript, sum tokens_out, and
    terminate the process the moment the sum crosses the cap.
    agent.py's internal --max-tokens-out stays as the first,
    soft belt (it can only check between steps); this wrapper
    is the hard stop calibration showed was missing."""
    started = time.monotonic()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    hard_killed = False
    timed_out = False
    while proc.poll() is None:
        if transcript_tokens_out(transcript_path) > cap:
            hard_killed = True
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            break
        if time.monotonic() - started > timeout_secs:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            break
        time.sleep(poll_secs)
    output = ""
    try:
        output = (proc.stdout.read() if proc.stdout else "") or ""
    except (OSError, ValueError):
        output = ""
    return {"rc": proc.returncode, "hard_cap_killed": hard_killed,
            "timed_out": timed_out,
            "tokens_out": transcript_tokens_out(transcript_path),
            "cap": cap,
            "output_tail": output[-2000:]}


def build_launch_argv(policy: dict, agent_path: str, brief: str,
                      workdir: str, tier: str, cap: int,
                      run_id: str, transcript_dir: str,
                      provider: str = "",
                      max_steps: int | None = None) -> list:
    argv = [sys.executable, agent_path, "--brief", brief,
            "--workdir", workdir, "--run-id", run_id,
            "--transcript-dir", transcript_dir,
            "--model-tier", tier,
            "--max-steps", str(max_steps if max_steps is not None
                               else policy.get("max_steps", 25)),
            "--max-tokens-out", str(cap)]
    if provider:
        argv += ["--provider", provider]
    return argv


# ------------------------------------------------------------------ log

def ledger_row(policy: dict, args) -> dict:
    killed = {}
    for pair in (args.killed_stage_a or "").split(","):
        if "=" in pair:
            rule, _, count = pair.partition("=")
            try:
                killed[rule.strip()] = int(count)
            except ValueError:
                killed[rule.strip()] = count.strip()
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": args.repo, "issue": args.issue, "pr": args.pr,
        "trigger": args.trigger,
        "hypotheses": {
            "produced": args.hypotheses_produced,
            "kept": args.hypotheses_kept,
            "discarded_no_evidence": args.discarded_no_evidence,
            "discarded_unverifiable": args.discarded_unverifiable,
            "coverage_complete": args.coverage_complete == "yes",
        },
        "tests_generated": args.tests_generated,
        "three_state_passed": args.three_state_passed,
        "three_state_class": args.three_state_class,
        "killed_stage_a": killed,
        "judge": {"catch": args.judge_catch,
                  "intended": args.judge_intended,
                  "unclear": args.judge_unclear,
                  "discarded_no_evidence":
                      args.judge_discarded_no_evidence},
        "survived": args.survived,
        "tokens_out": args.tokens_out,
        "operator_disposition": args.operator_disposition,
        "label_adjudication": {
            "ground_truth": args.label_ground_truth,
            "adjudicated_by": args.label_adjudicated_by,
            "note": args.label_note,
        },
        "outcome": args.outcome,
    }


# ------------------------------------------------------------------ cli

def _yes_no(value: str) -> bool:
    return str(value).strip().lower() in ("yes", "true", "1")


def _emit(result, json_flag: bool, human: str) -> None:
    if json_flag:
        print(json.dumps(result, indent=1, default=str))
    else:
        print(human)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", required=True)
    p.add_argument("--pr", default="")
    p.add_argument("--tier", default="")
    p.add_argument("--diff-files", type=int, default=0)
    p.add_argument("--diff-lines", type=int, default=0)
    p.add_argument("--diff-paths", default="")
    p.add_argument("--failover-action", default="")
    p.add_argument("--grade-band", default="")
    p.add_argument("--has-regression-test", default="no")
    p.add_argument("--cycle-checks-used", type=int, default=0)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("packet")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", required=True)
    p.add_argument("--pr", required=True)
    p.add_argument("--issue-file", required=True)
    p.add_argument("--pr-file", required=True)
    p.add_argument("--diff-file", required=True)
    p.add_argument("--base-dir", default=None)
    p.add_argument("--contract", action="append", default=[])
    p.add_argument("--allow-truncated", action="store_true")
    p.add_argument("--out", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("parse-hypotheses")
    p.add_argument("--report", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--diff-file", default=None)
    p.add_argument("--changed-functions", default="")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("launch")
    p.add_argument("--brief", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--tier", required=True)
    p.add_argument("--cap", type=int, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--transcript-dir", required=True)
    p.add_argument("--provider", default="")
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--timeout-secs", type=int, default=3600)
    p.add_argument("--agent", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("harvest")
    p.add_argument("--workdir", required=True)
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("three-state")
    p.add_argument("--test-path", required=True)
    p.add_argument("--diff-workdir", required=True)
    p.add_argument("--verify-workdir", required=True)
    p.add_argument("--timeout-secs", type=int, default=600)
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--cmd", dest="run_cmd",
                   nargs=argparse.REMAINDER, required=True,
                   help="test runner argv, given last")

    p = sub.add_parser("assess")
    p.add_argument("--test-file", required=True)
    p.add_argument("--three-state-file", default=None)
    p.add_argument("--buggy-output", default=None)
    p.add_argument("--verify-output", default=None)
    p.add_argument("--judge-file", default=None)
    p.add_argument("--diff-file", default=None)
    p.add_argument("--issue-file", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("log")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--trigger", required=True, choices=TRIGGERS)
    p.add_argument("--outcome", required=True, choices=OUTCOMES)
    p.add_argument("--hypotheses-produced", type=int, default=0)
    p.add_argument("--hypotheses-kept", type=int, default=0)
    p.add_argument("--discarded-no-evidence", type=int, default=0)
    p.add_argument("--discarded-unverifiable", type=int, default=0)
    p.add_argument("--coverage-complete", default="yes")
    p.add_argument("--tests-generated", type=int, default=0)
    p.add_argument("--three-state-passed", type=int, default=0)
    p.add_argument("--three-state-class", default="")
    p.add_argument("--killed-stage-a", default="")
    p.add_argument("--judge-catch", type=int, default=0)
    p.add_argument("--judge-intended", type=int, default=0)
    p.add_argument("--judge-unclear", type=int, default=0)
    p.add_argument("--judge-discarded-no-evidence", type=int,
                   default=0)
    p.add_argument("--survived", type=int, default=0)
    p.add_argument("--tokens-out", type=int, default=0)
    p.add_argument("--operator-disposition", default="pending")
    p.add_argument("--label-ground-truth", default="unlabelled")
    p.add_argument("--label-adjudicated-by", default="")
    p.add_argument("--label-note", default="")
    p.add_argument("--ledger", default=None)
    p.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    policy = policy_from_config()
    gated = ("check", "packet", "parse-hypotheses", "launch",
             "three-state", "assess")
    if args.cmd in gated and _disabled(policy):
        print("jittest disabled in deployment config "
              "(workers.jittest.enabled is not true) — collect "
              "without a JiT check", file=sys.stderr)
        return 2

    if args.cmd == "check":
        result = decide_trigger(
            policy, tier=args.tier, diff_files=args.diff_files,
            diff_lines=args.diff_lines,
            diff_paths=[s for s in args.diff_paths.split(",") if s],
            failover_action=args.failover_action,
            grade_band=args.grade_band,
            has_regression_test=_yes_no(args.has_regression_test),
            cycle_checks_used=args.cycle_checks_used)
        result.update({"repo": args.repo, "issue": args.issue,
                       "pr": args.pr})
        human = (f"TRIGGERED: {args.repo} PR#{args.pr} — "
                 f"trigger={result['trigger']}"
                 if result["triggered"] else
                 f"NOT TRIGGERED: {args.repo} PR#{args.pr} — "
                 f"exempt={result['exempt']} "
                 f"matched={result['matched']}")
        _emit(result, args.json, human)
        return 0

    if args.cmd == "packet":
        for path in (args.issue_file, args.pr_file, args.diff_file):
            if not os.path.exists(path):
                print(f"input not found: {path}", file=sys.stderr)
                return 2
        issue_text = open(args.issue_file, encoding="utf-8").read()
        pr_text = open(args.pr_file, encoding="utf-8").read()
        diff_text = open(args.diff_file, encoding="utf-8").read()
        info = parse_diff(diff_text)
        if info["truncated"] and not args.allow_truncated:
            print("PACKET REJECTED: the diff's hunk line counts do "
                  "not reconcile — the diff is truncated or "
                  "corrupt. A JiT packet always carries the FULL "
                  "diff (requirement 2); regenerate it and retry. "
                  "Nothing was written.", file=sys.stderr)
            return 3
        format_files = [f["path"] for f in info["files"]
                        if _is_format_file(f["path"])]
        found, _missing = find_contract_material(args.base_dir,
                                                 format_files)
        contracts = list(found)
        for cpath in args.contract:
            if not os.path.exists(cpath):
                print(f"contract file not found: {cpath}",
                      file=sys.stderr)
                return 2
            contracts.append({
                "for": format_files[0] if format_files else "manual",
                "path": cpath,
                "excerpt": open(cpath, encoding="utf-8",
                                errors="replace").read()
                [:MAX_CONTRACT_CHARS]})
        text, manifest = build_packet(
            policy, args.repo, args.issue, args.pr, issue_text,
            pr_text, diff_text, args.base_dir, contracts)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        _emit(manifest, args.json,
              f"PACKET: {args.repo} PR#{args.pr} -> {args.out} "
              f"({manifest['diff_files']} file(s), "
              f"{len(manifest['contract_included'])} contract(s), "
              f"{len(manifest['changed_functions'])} changed "
              f"function(s))")
        return 0

    if args.cmd == "parse-hypotheses":
        if not os.path.exists(args.report):
            print(f"report not found: {args.report}",
                  file=sys.stderr)
            return 2
        changed = []
        if args.changed_functions:
            changed = [s for s in
                       args.changed_functions.split(",") if s]
        if args.diff_file:
            if not os.path.exists(args.diff_file):
                print(f"diff not found: {args.diff_file}",
                      file=sys.stderr)
                return 2
            changed = parse_diff(
                open(args.diff_file, encoding="utf-8").read()
            )["changed_functions"]
        result = parse_hypotheses(
            open(args.report, encoding="utf-8").read(),
            args.workdir, changed)
        human = (f"HYPOTHESES: {result['n_hypotheses']} kept, "
                 f"{result['discarded_no_evidence']} discarded "
                 f"(no evidence), "
                 f"{result['discarded_unverifiable']} discarded "
                 f"(evidence unverified); coverage_complete="
                 f"{result['coverage_complete']}")
        _emit(result, args.json, human)
        return 0

    if args.cmd == "launch":
        agent_path = args.agent or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "agent.py")
        argv_cmd = build_launch_argv(
            policy, agent_path, args.brief, args.workdir, args.tier,
            args.cap, args.run_id, args.transcript_dir,
            provider=args.provider, max_steps=args.max_steps)
        os.makedirs(os.path.expanduser(args.transcript_dir),
                    exist_ok=True)
        transcript = os.path.join(
            os.path.expanduser(args.transcript_dir),
            f"{args.run_id}.jsonl")
        result = run_capped(argv_cmd, args.cap, transcript,
                            timeout_secs=args.timeout_secs)
        result.update({"run_id": args.run_id, "tier": args.tier,
                       "argv": argv_cmd})
        _emit(result, args.json,
              f"LAUNCH {args.run_id}: rc={result['rc']} "
              f"tokens_out={result['tokens_out']} "
              f"hard_cap_killed={result['hard_cap_killed']}")
        return 4 if result["hard_cap_killed"] else 0

    if args.cmd == "harvest":
        result = harvest(args.workdir, all_files=args.all)
        _emit(result, args.json,
              f"HARVEST {args.workdir}: {len(result['tests'])} "
              f"test file(s), {len(result['reports'])} report(s), "
              f"hypotheses={result['hypotheses_file']}")
        return 0

    if args.cmd == "three-state":
        cmd = list(args.run_cmd)
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            print("three-state needs a runner command after "
                  "--cmd", file=sys.stderr)
            return 2
        result = run_three_state(policy, args.test_path, cmd,
                                 args.diff_workdir,
                                 args.verify_workdir,
                                 timeout=args.timeout_secs)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=1)
        if result.get("refused"):
            _emit(result, args.json,
                  f"THREE-STATE REFUSED: {result['refused']}")
            return 3
        _emit(result, args.json,
              f"THREE-STATE {args.test_path}: "
              f"{result.get('classification')} "
              f"({result.get('pattern')})")
        return 0

    if args.cmd == "assess":
        if not os.path.exists(args.test_file):
            print(f"test file not found: {args.test_file}",
                  file=sys.stderr)
            return 2
        test_text = open(args.test_file, encoding="utf-8",
                         errors="replace").read()
        three_state = None
        buggy_output = verify_output = ""
        if args.three_state_file:
            three_state = json.loads(open(
                args.three_state_file, encoding="utf-8").read())
            buggy_output = (three_state.get("buggy_run1") or {}
                            ).get("output", "")
            verify_output = (three_state.get("verify_run") or {}
                             ).get("output", "")
        if args.buggy_output:
            buggy_output = open(args.buggy_output, encoding="utf-8",
                                errors="replace").read()
        if args.verify_output:
            verify_output = open(args.verify_output,
                                 encoding="utf-8",
                                 errors="replace").read()
        judge_text = ""
        if args.judge_file:
            judge_text = open(args.judge_file, encoding="utf-8",
                              errors="replace").read()
        diff_paths, context = [], ""
        if args.diff_file:
            diff_text = open(args.diff_file, encoding="utf-8",
                             errors="replace").read()
            diff_paths = [f["path"] for f in
                          parse_diff(diff_text)["files"]]
            context += diff_text
        if args.issue_file:
            context += "\n" + open(args.issue_file,
                                   encoding="utf-8",
                                   errors="replace").read()
        result = assess(test_text, three_state, buggy_output,
                        verify_output, judge_text, diff_paths,
                        context)
        _emit(result, args.json,
              f"ASSESS: {result['outcome']} "
              f"(stage A kills: "
              f"{[k['rule'] for k in result['stage_a_kills']]})")
        return 0

    if args.cmd == "log":
        row = ledger_row(policy, args)
        path = args.ledger or default_jittest_ledger_path()
        if not path:
            print("no jittest ledger path resolvable",
                  file=sys.stderr)
            return 2
        os.makedirs(os.path.dirname(os.path.abspath(
            os.path.expanduser(path))), exist_ok=True)
        with open(os.path.expanduser(path), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        _emit(row, args.json,
              f"LOGGED: {args.repo}#{args.issue} PR#{args.pr} "
              f"-> {path}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
