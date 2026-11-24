#!/usr/bin/env python3
"""Flag-resolution verifier for the plan-coder harness
(flag-resolution verifier spike, Phase 1 built 2026-11-11 after
Phase 0 PASS; parent PR-review-checklist spike, Phase 0
2026-11-11 — see ~/workspace/flag-resolution-verifier-phase0/
RESULTS.md and ~/workspace/pr-review-checklist-phase0/RESULTS.md).

Why this exists: the shepherd's needs-operator flags accumulate
rows (one PR produced 49 identical needs_alex rows in the parent
spike's sample) because nothing re-checks whether a flag's
condition still holds. The operator's review layer IS the flag
layer (validated 24/24 in the parent Phase 0): the flag's reason
in one line, remaining reds all known/exempt, the underlying
question resolved, a trustworthy CI signal, no new security
criticals, workflow-touch kind, reds classified vs. clean
develop, staleness. This tool verifies each of those conditions
deterministically, with zero LLM tokens, and reports per-class
state — it never clears a flag, never merges, and never feeds
the shepherd's merge policy.

Classes (closed taxonomy, eight): ci-none, ci-reds,
ci-unclassified, workflow-kind, security-scan, conflict-stale,
physics-gate, held-green. Each applicable class reports
resolved_verified / unresolved / unverifiable with evidence
and the timestamp its evidence rests on (a held PR does not
re-run CI, so "green" is always *as of* the last run — a
Phase 0 finding). physics-gate is unverifiable by design
until the operator's word is recorded in flag_resolutions.json
(state dir; written only by the main agent from the operator's
word; this tool and the shepherd never write it). held-green
resolves the same way: the operator's recorded disposition.

  check   Policy-gated. Print the per-class flag status for one
          PR (gh rollup + diff, or an injected --facts-file for
          tests). Exit 0 when enabled; exit 2 when disabled —
          prompt steps proceed exactly as before.
  log     check + append to flagcheck_ledger.jsonl ONLY on
          first sight or when the state signature changes
          (the anti-repetition rule). Policy-gated like check.
  summary Harness-tune's read over the ledger: latest state
          per PR, counts by class/state, transitions, and the
          unclassified failing-check names seen. Read-only,
          flag-independent (post-No-go audits stay possible).

Policy (workers.flagcheck in the deployment config) ships
DISABLED in the example config, carrying the exempt mapping
the operator approved as data 2026-11-11 (fluxion patterns:
the ASHRAE Strict gate, the Fabric gate, the Scripts Test
Suite and its gate wrapper; other repos empty). The exempt
list changes only by an operator-approved edit of that config
value; this tool never edits it and never treats an unmatched
name as exempt — the Phase 0 absolute bar (no false-exempt)
is the tool's reason to exist.

Usage:
    python3 bin/flagcheck.py check --repo anchapin/fluxion --pr 4647
        [--state-dir D] [--config F] [--facts-file F] [--json]
    python3 bin/flagcheck.py log --repo anchapin/fluxion --pr 4647
        [--state-dir D] [--config F] [--facts-file F] [--json]
    python3 bin/flagcheck.py summary [--state-dir D] [--since DATE] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config, default_ledger_path

CLASSES = ("ci-none", "ci-reds", "ci-unclassified", "workflow-kind",
           "security-scan", "conflict-stale", "physics-gate",
           "held-green")
SECURITY_RE = re.compile(r"codeql|security|audit", re.I)
COUNT_LINE_RE = re.compile(r"EXPECTED_TEST_COUNT|expected[_ ]test[_ ]count", re.I)
PHYSICS_PATHS = ("src/physics/", "src/sim/", "physics/", "ashrae",
                 "src/thermal/", "src/hvac/")


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config(explicit):
    if explicit:
        with open(explicit) as fh:
            return json.load(fh)
    return _load_deployment_config() or {}


def policy_from_config(cfg):
    pol = dict(((cfg.get("workers") or {}).get("flagcheck") or {}))
    pol.setdefault("enabled", False)
    pol.setdefault("exempt_checks", {})
    return pol


def state_dir(explicit, cfg):
    if explicit:
        return os.path.expanduser(explicit)
    ledger = default_ledger_path()
    if ledger:
        return os.path.dirname(ledger)
    d = (cfg.get("state") or {}).get("dir")
    return os.path.expanduser(d) if d else None


def load_jsonl(path):
    if not path or not os.path.exists(path):
        return None
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


# ---------------------------------------------------------------
# Pure classification (the Phase 0 tests, as code)
# ---------------------------------------------------------------

def _real_checks(checks):
    """A real CI run = at least one check that actually ran
    (not SKIPPED). Phase 0: #1352/#1316 carry exactly one
    SKIPPED Sourcery check and nothing else."""
    return [c for c in checks
            if (c.get("conclusion") or "") not in ("", "SKIPPED")]


UNNAMED_FAILING = "(unnamed failing check)"


def failing_names(checks):
    """Names of FAILURE-conclusion checks, deduped. A failing
    check with an empty name is kept as UNNAMED_FAILING: dropping
    it would let ci-reds / held-green claim "no failing checks" /
    "all checks green" over a rollup that contains a FAILURE
    (#163 A07)."""
    seen, out = set(), []
    for c in checks:
        if c.get("conclusion") == "FAILURE":
            name = str(c.get("name") or "") or UNNAMED_FAILING
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def exempt_match(name, patterns):
    """An exempt pattern matches a check name only as the whole
    name or as a prefix that ends at a separator: the approved
    shapes are the gate name itself and the gate name plus a
    suffix such as " (Issue #1333)". A pattern must never match
    mid-token — "ASHRAE 140 Strict Energy Gate" does not exempt
    "ASHRAE 140 Strict Energy Gatekeeper (experimental)"
    (#163 A01, the false-exempt absolute bar)."""
    n = name.lower()
    for p in patterns:
        pat = p.lower()
        if n == pat:
            return True
        if n.startswith(pat):
            rest = n[len(pat):]
            if rest and not rest[0].isalnum() and rest[0] != "_":
                return True
    return False


def workflow_kind(workflow_diff):
    """Classify workflow-file changed lines. count_only when
    every content line is an expected-test-count line (value or
    its comment); substantive otherwise; none when the diff has
    no workflow content lines. Phase 0: #1356/#1349/#1316
    count-only, #2899 substantive."""
    content = []
    for line in (workflow_diff or "").split("\n"):
        if line.startswith(("+++", "---", "diff ", "index ", "@@")):
            continue
        if line.startswith(("+", "-")) and line[1:].strip():
            content.append(line[1:])
    if not content:
        return "none"
    if all(COUNT_LINE_RE.search(line) for line in content):
        return "count_only"
    return "substantive"


def load_resolutions(sd):
    data = {"resolutions": []}
    if sd:
        p = os.path.join(sd, "flag_resolutions.json")
        if os.path.exists(p):
            try:
                with open(p) as fh:
                    data = json.load(fh)
            except ValueError:
                pass
    return data.get("resolutions") or []


def resolution_for(resolutions, repo, pr, cls):
    for r in resolutions:
        if (str(r.get("repo")) == repo and str(r.get("pr")) == str(pr)
                and r.get("class") in (cls, "*")):
            return r
    return None


def evaluate(facts, policy, resolutions):
    """facts: {repo, pr, checks[], mergeable, workflow_files[],
    workflow_diff, files[], linked_issue_closed}
    Returns {overall, classes{cls: {state, evidence}}, as_of}."""
    repo, pr = facts.get("repo"), facts.get("pr")
    checks = facts.get("checks") or []
    patterns = (policy.get("exempt_checks") or {}).get(repo) or []
    classes = {}
    real = _real_checks(checks)
    started = sorted(str(c.get("startedAt")) for c in checks
                     if c.get("startedAt"))
    as_of = started[-1] if started else None
    fails = failing_names(checks)

    # ci-none / ci-reds / ci-unclassified
    if not real:
        classes["ci-none"] = {
            "state": "unresolved",
            "evidence": f"no real CI run (rollup holds {len(checks)} "
                        f"check(s), none completed)"}
    else:
        classes["ci-none"] = {
            "state": "resolved_verified",
            "evidence": f"{len(real)} completed check(s), latest run "
                        f"started {as_of}"}
        if not fails:
            classes["ci-reds"] = {
                "state": "resolved_verified",
                "evidence": f"no failing checks as of {as_of}"}
        else:
            uncl = [n for n in fails if not exempt_match(n, patterns)]
            if uncl:
                classes["ci-unclassified"] = {
                    "state": "unresolved",
                    "evidence": "failing checks matching no exempt "
                                "pattern: " + "; ".join(uncl)}
                if len(uncl) < len(fails):
                    classes["ci-reds"] = {
                        "state": "resolved_verified",
                        "evidence": "exempt failing checks: " + "; ".join(
                            n for n in fails if n not in uncl)}
            else:
                classes["ci-reds"] = {
                    "state": "resolved_verified",
                    "evidence": "all failing checks match the approved "
                                "exempt mapping: " + "; ".join(fails)}

    # security-scan
    sec = [n for n in fails if SECURITY_RE.search(n)]
    classes["security-scan"] = {
        "state": "unresolved" if sec else "resolved_verified",
        "evidence": ("failing security checks: " + "; ".join(sec)) if sec
                    else f"no failing security checks as of {as_of}"}

    # workflow-kind
    wfiles = facts.get("workflow_files") or []
    if wfiles:
        kind = workflow_kind(facts.get("workflow_diff"))
        if kind == "count_only":
            classes["workflow-kind"] = {
                "state": "resolved_verified",
                "evidence": "workflow files touched (" +
                            ", ".join(wfiles) + "), changed lines are "
                            "expected-test-count only (routine class)"}
        elif kind == "none":
            # The diff carries no changed lines, so the kind
            # cannot be determined. Report that honestly instead
            # of folding "none" into the substantive branch and
            # asserting changed lines that do not exist (#163 A06).
            classes["workflow-kind"] = {
                "state": "unverifiable",
                "evidence": "workflow files touched (" +
                            ", ".join(wfiles) + "), but the workflow "
                            "diff carries no changed lines — kind "
                            "cannot be determined"}
        else:
            classes["workflow-kind"] = {
                "state": "unresolved",
                "evidence": "workflow files touched (" +
                            ", ".join(wfiles) + "), changed lines are "
                            "substantive — operator review class"}

    # conflict-stale
    if facts.get("mergeable") == "CONFLICTING":
        classes["conflict-stale"] = {
            "state": "unresolved",
            "evidence": "mergeable=CONFLICTING"}
    elif facts.get("linked_issue_closed"):
        classes["conflict-stale"] = {
            "state": "unresolved",
            "evidence": "linked issue is closed (staleness/"
                        "supersession candidate)"}
    else:
        classes["conflict-stale"] = {
            "state": "resolved_verified",
            "evidence": f"mergeable={facts.get('mergeable')}, linked "
                        "issue open"}

    # physics-gate
    phys = [f for f in (facts.get("files") or [])
            if any(p in f for p in PHYSICS_PATHS)]
    if phys:
        res = resolution_for(resolutions, repo, pr, "physics-gate")
        if res:
            classes["physics-gate"] = {
                "state": "resolved_verified",
                "evidence": f"operator word recorded {res.get('ts')}: "
                            f"{res.get('disposition')}"}
        else:
            classes["physics-gate"] = {
                "state": "unverifiable",
                "evidence": "physics paths touched (" +
                            ", ".join(phys[:3]) + ") — gate call is "
                            "the operator's, never inferred"}

    # held-green: green and mergeable means the only remaining
    # input is the operator's word.
    if real and not fails and facts.get("mergeable") in (
            "MERGEABLE", "CLEAN"):
        res = resolution_for(resolutions, repo, pr, "held-green")
        if res:
            classes["held-green"] = {
                "state": "resolved_verified",
                "evidence": f"operator word recorded {res.get('ts')}: "
                            f"{res.get('disposition')}"}
        else:
            classes["held-green"] = {
                "state": "unresolved",
                "evidence": f"all checks green as of {as_of}; awaiting "
                            "the operator's word"}

    states = [c["state"] for c in classes.values()]
    if all(s == "resolved_verified" for s in states):
        overall = "resolved_verified"
    elif all(s in ("resolved_verified", "unverifiable") for s in states):
        overall = "unverifiable"
    else:
        overall = "unresolved"
    return {"overall": overall, "classes": classes, "as_of": as_of}


# ---------------------------------------------------------------
# Fact gathering (gh) — check/log only; tests inject facts
# ---------------------------------------------------------------

def gh_facts(repo, pr):
    p = subprocess.run(
        ["gh", "pr", "view", str(pr), "--repo", repo, "--json",
         "statusCheckRollup,mergeable,files,closingIssuesReferences"],
        capture_output=True, text=True, timeout=60, check=False)
    d = json.loads(p.stdout)
    checks = [{"name": c.get("name"), "conclusion": c.get("conclusion"),
               "status": c.get("status"),
               "startedAt": c.get("startedAt")}
              for c in d.get("statusCheckRollup") or []]
    files = [f.get("path") for f in d.get("files") or []]
    wfiles = [f for f in files if f.startswith(".github/workflows/")]
    wdiff = ""
    if wfiles:
        p2 = subprocess.run(["gh", "pr", "diff", str(pr), "--repo", repo],
                            capture_output=True, text=True, timeout=60,
                            check=False)
        keep, cur = [], False
        for line in p2.stdout.split("\n"):
            if line.startswith("diff --git"):
                cur = ".github/workflows/" in line
            if cur:
                keep.append(line)
        wdiff = "\n".join(keep)
    linked_closed = False
    for ref in d.get("closingIssuesReferences") or []:
        if str(ref.get("state") or "").upper() == "CLOSED":
            linked_closed = True
    return {"repo": repo, "pr": pr, "checks": checks,
            "mergeable": d.get("mergeable"), "files": files,
            "workflow_files": wfiles, "workflow_diff": wdiff,
            "linked_issue_closed": linked_closed}


def signature(result):
    return json.dumps({k: v["state"] for k, v in
                       sorted(result["classes"].items())}
                      | {"overall": result["overall"]},
                      sort_keys=True)


def cmd_check(args, do_log):
    cfg = load_config(args.config)
    pol = policy_from_config(cfg)
    if not pol.get("enabled"):
        print("flagcheck disabled (workers.flagcheck.enabled=false)",
              file=sys.stderr)
        return 2
    sd = state_dir(args.state_dir, cfg)
    if args.facts_file:
        with open(args.facts_file) as fh:
            facts = json.load(fh)
    else:
        facts = gh_facts(args.repo, args.pr)
    result = evaluate(facts, pol, load_resolutions(sd))
    out = {"repo": facts.get("repo"), "pr": facts.get("pr"),
           "ts": now_iso(), **result}
    appended = None
    if do_log:
        if not sd:
            print("no state dir resolvable", file=sys.stderr)
            return 1
        path = os.path.join(sd, "flagcheck_ledger.jsonl")
        rows = load_jsonl(path) or []
        sig = signature(result)
        prior = [r for r in rows if str(r.get("repo")) == str(out["repo"])
                 and str(r.get("pr")) == str(out["pr"])]
        appended = (not prior
                    or signature(prior[-1]) != sig)
        if appended:
            row = {**out, "signature": sig}
            with open(path, "a") as fh:
                fh.write(json.dumps(row) + "\n")
        out["appended"] = appended
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        print(f"{out['repo']}#{out['pr']} — {out['overall']} "
              f"(evidence as of {out['as_of']})")
        for cls, c in out["classes"].items():
            print(f"  {cls}: {c['state']} — {c['evidence']}")
        if appended is not None:
            print(f"  ledger: {'appended (state change)' if appended else 'no change, not appended'}")
    return 0


def cmd_summary(args):
    cfg = load_config(args.config)
    sd = state_dir(args.state_dir, cfg)
    rows = load_jsonl(os.path.join(sd, "flagcheck_ledger.jsonl")) if sd else None
    rows = rows or []
    if args.since:
        rows = [r for r in rows if str(r.get("ts", "")) >= args.since]
    latest = {}
    transitions = 0
    for r in rows:
        key = (str(r.get("repo")), str(r.get("pr")))
        if key in latest:
            transitions += 1
        latest[key] = r
    by_state, uncl_names = {}, {}
    for r in latest.values():
        by_state[r.get("overall")] = by_state.get(r.get("overall"), 0) + 1
        u = (r.get("classes") or {}).get("ci-unclassified") or {}
        if u.get("state") == "unresolved":
            for n in str(u.get("evidence", "")).split(": ", 1)[-1].split("; "):
                if n:
                    uncl_names[n] = uncl_names.get(n, 0) + 1
    out = {"rows": len(rows), "prs": len(latest),
           "transitions_beyond_first": transitions,
           "latest_by_overall": by_state,
           "unclassified_failing_names": uncl_names,
           "open_flags": [
               {"repo": r.get("repo"), "pr": r.get("pr"),
                "overall": r.get("overall"), "as_of": r.get("as_of"),
                "unresolved": [k for k, v in (r.get("classes") or {}).items()
                               if v.get("state") != "resolved_verified"]}
               for r in latest.values()
               if r.get("overall") != "resolved_verified"]}
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        print(f"flagcheck ledger: {out['rows']} row(s) over {out['prs']} PR(s); "
              f"latest states {by_state}; {transitions} transition row(s)")
        for f in out["open_flags"]:
            print(f"  {f['repo']}#{f['pr']}: {f['overall']} "
                  f"(open: {', '.join(f['unresolved'])}) as of {f['as_of']}")
        if uncl_names:
            print("  unclassified failing names: " +
                  ", ".join(f"{k} x{v}" for k, v in uncl_names.items()))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="flagcheck.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "log"):
        s = sub.add_parser(name)
        s.add_argument("--repo", required=True)
        s.add_argument("--pr", required=True, type=int)
        s.add_argument("--state-dir")
        s.add_argument("--config")
        s.add_argument("--facts-file")
        s.add_argument("--json", action="store_true")
    s = sub.add_parser("summary")
    s.add_argument("--state-dir")
    s.add_argument("--config")
    s.add_argument("--since")
    s.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "summary":
        return cmd_summary(args)
    return cmd_check(args, do_log=(args.cmd == "log"))


if __name__ == "__main__":
    sys.exit(main())
