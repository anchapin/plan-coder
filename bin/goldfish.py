#!/usr/bin/env python3
"""Goldfish brief-review for the backlog supervisor (EGM spike,
approved by the operator 2026-11-07; pattern from Google's
Elephant-Goldfish Model, github.com/google/elephant-goldfish-model).

The supervisor (Elephant) writes worker briefs in the same context
that picked the issue, so its unstated assumptions pass straight
into worker runs and surface later as failed:brief-ambiguous,
empty-diff, and bad-diff burns. This spike gives a triggered brief
one fresh-eyes read *before* dispatch: a zero-context cheap-tier
reviewer (Goldfish) that receives only the issue text, the draft
brief, and the files the brief names — never the supervisor's
reasoning, the ledger, or prior-attempt reflections.

This script is the mechanical half (failover.py is the model):
  packet   Assemble the review packet from the verbatim issue text
           + the draft brief + a reading list filtered to files that
           exist on the fresh base, and validate it against a
           forbidden-content list (supervisor-context markers). A
           packet that carries supervisor context is refused
           (exit 3): a review in the author's context is not a
           Goldfish review, and there is no same-context fallback.
  check    Decide mechanically whether an issue triggers a review,
           from the dispatch facts the supervisor already knows
           (tier, failover action, scope size/paths, whether the
           issue states checkable criteria). Trigger thresholds are
           policy-as-data in the deployment config (workers.goldfish).
  parse    Extract the reviewer's findings block from its report,
           validate each finding, and DISCARD any finding with no
           evidence field — the mechanical floor under reviewer
           hallucination. Severities/lenses are closed vocabularies.
  log      Append one row per completed review to
           goldfish_ledger.jsonl in the state dir (kept separate
           from worker_ledger.jsonl so failover/grade counting over
           worker rows is never polluted). The weekly harness-tune
           pass reads it as the reviewer-quality loss signal.

The LLM half is an ordinary bin/agent.py verification-only run
driven by the supervisor prompt: cheap tier, capped steps/tokens,
one round per issue per cycle, findings resolved by the supervisor
under the EGM taxonomy (PROPOSE FIX / CONTEST with checkable
evidence / ESCALATE / DECLINE — see prompts/backlog-supervisor.md).
No voting, no parallel reviewers (arXiv:2611.07140, 2611.00314).

Policy (workers.goldfish in the deployment config) ships DISABLED:
enabled=false means every subcommand exits 2 and the supervisor
dispatches exactly as before. Enabling is a separate operator
decision, per the burn-guard caps precedent.

Exit codes: 0 = ok, 2 = goldfish disabled / input missing,
3 = packet rejected (forbidden content in the brief/issue text).

Usage:
    python3 bin/goldfish.py check --repo R --issue N --tier T
        [--failover-action A] [--scope-files K]
        [--scope-paths a,b,c] [--has-criteria yes|no] [--json]
    python3 bin/goldfish.py packet --repo R --issue N
        --issue-file F --brief-file F [--base-dir D]
        [--reading a,b,c] --out F [--json]
    python3 bin/goldfish.py parse --report F [--json]
    python3 bin/goldfish.py log --repo R --issue N --trigger T
        --tokens-out K --steps used/max --findings K --blockers K
        --propose-fix K --contest K --escalate K --decline K
        --discarded K --brief-changed yes|no --outcome O
        [--tier cheap] [--ledger PATH] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config  # noqa: E402

DEFAULT_POLICY = {
    "enabled": False,
    "max_per_cycle": 2,
    "max_tokens_out": 30000,
    "max_steps": 15,
    "tier": "cheap",
    "max_scope_files": 5,
}
SEVERITIES = ("Blocker", "Major", "Nit")
# Reviewers reliably improvise "Minor" for the lowest severity
# (Phase 0b calibration: 4 findings lost to it) — accept it as Nit.
SEVERITY_ALIASES = {"Minor": "Nit"}
LENSES = ("comprehension", "premise", "completeness", "readiness")
TRIGGERS = ("flagship", "failover", "hard-stop", "scope", "no-criteria")
OUTCOMES = ("dispatched_revised", "dispatched_unchanged",
            "skipped_escalated", "unavailable")
# Markers of supervisor/operator context that must never reach the
# zero-context reviewer. Matched case-insensitively against the issue
# text and the draft brief during packet assembly.
FORBIDDEN_MARKERS = (
    "prior attempts",
    "worker_ledger.jsonl",
    "goldfish_ledger.jsonl",
    "ledger_check.py",
    "bin/failover.py",
    "supervisor reasoning",
    "supervisor's reasoning",
    "standing directive",
)
HARD_STOP_MARKERS = (".github/workflows/", "migrat", "/physics",
                     "physics/", "simulation output")


def policy_from_config() -> dict:
    cfg = _load_deployment_config() or {}
    workers = cfg.get("workers") or {}
    policy = dict(DEFAULT_POLICY)
    policy.update(workers.get("goldfish") or {})
    return policy


def default_goldfish_ledger_path() -> str | None:
    cfg = _load_deployment_config() or {}
    state_dir = ((cfg.get("state") or {}).get("dir")
                 or "~/.plan-coder/state")
    return os.path.join(os.path.expanduser(state_dir),
                        "goldfish_ledger.jsonl")


def _disabled(policy: dict) -> bool:
    return policy.get("enabled") is not True


# ---------------------------------------------------------------- check

def decide_trigger(policy: dict, tier: str = "",
                   failover_action: str = "", scope_files: int = 0,
                   scope_paths: list | None = None,
                   has_criteria: bool = True) -> dict:
    scope_paths = scope_paths or []
    base = {"triggered": False, "trigger": None, "tier": tier,
            "failover_action": failover_action,
            "scope_files": scope_files}
    if tier == "flagship":
        return {**base, "triggered": True, "trigger": "flagship"}
    if failover_action in ("failover", "dispatch_with_reflection"):
        return {**base, "triggered": True, "trigger": "failover"}
    joined = " ".join(scope_paths).lower()
    if any(marker in joined for marker in HARD_STOP_MARKERS):
        return {**base, "triggered": True, "trigger": "hard-stop"}
    if scope_files > int(policy.get("max_scope_files", 5)):
        return {**base, "triggered": True, "trigger": "scope"}
    if not has_criteria:
        return {**base, "triggered": True, "trigger": "no-criteria"}
    return base


# --------------------------------------------------------------- packet

def find_forbidden(*texts: str) -> list:
    hits = []
    for text in texts:
        low = (text or "").lower()
        for marker in FORBIDDEN_MARKERS:
            if marker in low and marker not in hits:
                hits.append(marker)
    return hits


def build_packet(repo: str, issue, issue_text: str, brief_text: str,
                 reading: list, base_dir: str | None) -> tuple:
    """Return (packet_text, manifest). Reading entries are kept only
    when the file exists under base_dir (or as given, when no
    base_dir); the rest are recorded as excluded."""
    included, excluded = [], []
    for path in reading:
        probe = (os.path.join(base_dir, path) if base_dir else path)
        (included if os.path.isfile(probe) else excluded).append(path)
    digest = hashlib.sha256(
        (issue_text + "\n---BRIEF---\n" + brief_text).encode("utf-8")
    ).hexdigest()
    manifest = {"repo": repo, "issue": issue,
                "reading_list": included,
                "excluded_reading": excluded,
                "content_sha256": digest}
    lines = [
        "<!-- goldfish-manifest: " + json.dumps(manifest) + " -->",
        "",
        f"# Goldfish review packet — {repo}#{issue}",
        "",
        "You are a zero-context reviewer. You have not seen any prior",
        "conversation about this work. Review only what is below and",
        "the reading-list files in your worktree.",
        "",
        "## Issue (verbatim)",
        "",
        issue_text.strip(),
        "",
        "## Draft brief under review",
        "",
        brief_text.strip(),
        "",
        "## Reading list (paths on the fresh base)",
        "",
    ]
    lines += [f"- {p}" for p in included] or ["(none)"]
    lines += ["", "## Findings format",
              "",
              "End your report with exactly one fenced block:",
              "",
              "```goldfish-findings",
              '[{"severity": "Blocker|Major|Nit",',
              '  "lens": "comprehension|premise|completeness|readiness",',
              '  "evidence": "file:line, symbol, or quoted brief line",',
              '  "finding": "what is wrong or unstated",',
              '  "suggested_edit": "precise brief edit, or none"}]',
              "```",
              "A finding with an empty evidence field is discarded.",
              "If the brief is ready as written, emit an empty list.",
              ""]
    return "\n".join(lines), manifest


# ---------------------------------------------------------------- parse

FINDINGS_BLOCK = re.compile(
    r"```goldfish-findings\s*\n(.*?)```", re.DOTALL)


def parse_findings(report_text: str) -> dict:
    match = FINDINGS_BLOCK.search(report_text or "")
    if not match:
        return {"findings": [], "discarded_no_evidence": 0,
                "invalid": 0, "block_found": False}
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {"findings": [], "discarded_no_evidence": 0,
                "invalid": 1, "block_found": True}
    if not isinstance(raw, list):
        return {"findings": [], "discarded_no_evidence": 0,
                "invalid": 1, "block_found": True}
    findings, discarded, invalid = [], 0, 0
    for item in raw:
        if not isinstance(item, dict):
            invalid += 1
            continue
        evidence = str(item.get("evidence") or "").strip()
        if not evidence:
            discarded += 1
            continue
        severity = str(item.get("severity") or "")
        severity = SEVERITY_ALIASES.get(severity, severity)
        lens = str(item.get("lens") or "")
        finding = str(item.get("finding") or "").strip()
        if severity not in SEVERITIES or lens not in LENSES \
                or not finding:
            invalid += 1
            continue
        findings.append({
            "severity": severity, "lens": lens, "evidence": evidence,
            "finding": finding,
            "suggested_edit": str(item.get("suggested_edit") or ""),
        })
    return {"findings": findings,
            "discarded_no_evidence": discarded, "invalid": invalid,
            "block_found": True}


# ------------------------------------------------------------------ log

def ledger_row(policy: dict, args) -> dict:
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": args.repo, "issue": args.issue,
        "trigger": args.trigger,
        "tier": args.tier or policy.get("tier", "cheap"),
        "tokens_out": args.tokens_out, "steps": args.steps,
        "findings": args.findings, "blockers": args.blockers,
        "dispositions": {
            "propose_fix": args.propose_fix, "contest": args.contest,
            "escalate": args.escalate, "decline": args.decline,
            "discarded_no_evidence": args.discarded},
        "brief_changed": args.brief_changed == "yes",
        "outcome": args.outcome,
    }


# ------------------------------------------------------------------ cli

def _yes_no(value: str) -> bool:
    return value.strip().lower() in ("yes", "true", "1")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", required=True)
    p.add_argument("--tier", default="")
    p.add_argument("--failover-action", default="")
    p.add_argument("--scope-files", type=int, default=0)
    p.add_argument("--scope-paths", default="")
    p.add_argument("--has-criteria", default="yes")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("packet")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", required=True)
    p.add_argument("--issue-file", required=True)
    p.add_argument("--brief-file", required=True)
    p.add_argument("--base-dir", default=None)
    p.add_argument("--reading", default="")
    p.add_argument("--out", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("parse")
    p.add_argument("--report", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("log")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--trigger", required=True, choices=TRIGGERS)
    p.add_argument("--tier", default="")
    p.add_argument("--tokens-out", type=int, default=0)
    p.add_argument("--steps", default="unknown")
    p.add_argument("--findings", type=int, default=0)
    p.add_argument("--blockers", type=int, default=0)
    p.add_argument("--propose-fix", type=int, default=0)
    p.add_argument("--contest", type=int, default=0)
    p.add_argument("--escalate", type=int, default=0)
    p.add_argument("--decline", type=int, default=0)
    p.add_argument("--discarded", type=int, default=0)
    p.add_argument("--brief-changed", default="no")
    p.add_argument("--outcome", required=True, choices=OUTCOMES)
    p.add_argument("--ledger", default=None)
    p.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    policy = policy_from_config()

    if args.cmd in ("check", "packet") and _disabled(policy):
        print("goldfish disabled in deployment config "
              "(workers.goldfish.enabled is not true) — dispatch "
              "without review", file=sys.stderr)
        return 2

    if args.cmd == "check":
        result = decide_trigger(
            policy, tier=args.tier,
            failover_action=args.failover_action,
            scope_files=args.scope_files,
            scope_paths=[s for s in args.scope_paths.split(",") if s],
            has_criteria=_yes_no(args.has_criteria))
        if args.json:
            print(json.dumps(result, indent=1))
        elif result["triggered"]:
            print(f"TRIGGERED: {args.repo}#{args.issue} — "
                  f"trigger={result['trigger']}")
        else:
            print(f"NOT TRIGGERED: {args.repo}#{args.issue} — "
                  f"dispatch without Goldfish review")
        return 0

    if args.cmd == "packet":
        for path in (args.issue_file, args.brief_file):
            if not os.path.exists(path):
                print(f"input not found: {path}", file=sys.stderr)
                return 2
        issue_text = open(args.issue_file, encoding="utf-8").read()
        brief_text = open(args.brief_file, encoding="utf-8").read()
        forbidden = find_forbidden(issue_text, brief_text)
        if forbidden:
            print("PACKET REJECTED: supervisor-context marker(s) in "
                  "the issue/brief text: " + ", ".join(forbidden)
                  + " — rebuild the packet from a redacted copy of "
                  "the brief (the worker brief itself keeps them); "
                  "do not review in the supervisor's own context "
                  "instead", file=sys.stderr)
            return 3
        reading = [s for s in args.reading.split(",") if s]
        text, manifest = build_packet(args.repo, args.issue,
                                      issue_text, brief_text, reading,
                                      args.base_dir)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        if args.json:
            print(json.dumps(manifest, indent=1))
        else:
            print(f"PACKET: {args.repo}#{args.issue} -> {args.out} "
                  f"({len(manifest['reading_list'])} reading file(s), "
                  f"{len(manifest['excluded_reading'])} excluded)")
        return 0

    if args.cmd == "parse":
        if not os.path.exists(args.report):
            print(f"report not found: {args.report}", file=sys.stderr)
            return 2
        result = parse_findings(
            open(args.report, encoding="utf-8").read())
        if args.json:
            print(json.dumps(result, indent=1))
        else:
            print(f"FINDINGS: {len(result['findings'])} kept, "
                  f"{result['discarded_no_evidence']} discarded "
                  f"(no evidence), {result['invalid']} invalid")
            for f in result["findings"]:
                print(f"  [{f['severity']}/{f['lens']}] {f['finding']} "
                      f"(evidence: {f['evidence']})")
        return 0

    if args.cmd == "log":
        row = ledger_row(policy, args)
        path = args.ledger or default_goldfish_ledger_path()
        if not path:
            print("no goldfish ledger path resolvable",
                  file=sys.stderr)
            return 2
        os.makedirs(os.path.dirname(os.path.abspath(
            os.path.expanduser(path))), exist_ok=True)
        with open(os.path.expanduser(path), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        if args.json:
            print(json.dumps(row, indent=1))
        else:
            print(f"LOGGED: {args.repo}#{args.issue} -> {path}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
