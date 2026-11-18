#!/usr/bin/env python3
"""Claim-typing lint for proposal artifacts (product-engineering
spike, Part D — Phase 0 PASS 2026-11-10: 48 load-bearing claims
across 3 POC reports + 2 spike briefs re-typed without
re-arguing any verdict, and 2 premise-functioning [unsupported]
claims found, both in the SS-DFA POC — the assumption set the
whole build rested on, and the uncited 7-bit precision premise
behind its noise gate; see
~/workspace/product-engineering-phase0/RESULTS.md).

The discipline, mined from phuryn/product-engineering's
walkthrough and renamed to this harness's vocabulary: every
load-bearing claim in a proposal artifact — a spike brief, a
POC proposal, a POC report — lives in a dedicated `## Claims`
section and carries one tag:

  [observed]    a measured fact, WITH its source (a command, a
                file:line, a ledger count, a URL)
  [inference]   a conclusion drawn from cited observations
  [unsupported] an assumption carried forward, named as such —
                it may NEVER be a premise for a Go/No-go, a
                dispatch, or a merge
  [decision]    the operator's call, with its date — a decision
                is not evidence

This lint is ADVISORY: exit 1 lists violations and blocks
nothing. The point is that an untagged or premise-functioning
unsupported claim is visible, not that a script polices prose.
Scope is deliberately narrow (brief §3 Part D): spike briefs
and POC proposals/reports only — never worker briefs (Goldfish
already demands evidence there) and never cycle reports. The
lint is run by the artifact's author; it has no prompt wiring.

Checks (mechanical, in this order):
  1. The artifact must HAVE a `## Claims` section. None = exit 1
     with that stated — never a crash.
  2. Every claim line in the Claims section (a `- ` bullet)
     carries exactly one of the four tags. Untagged = violation.
  3. Every [observed] claim carries a source citation in the
     same line: a `source:` marker, a URL, or a path/command in
     backticks or path-like form.
  4. No [unsupported] claim may function as a premise: an
     [unsupported]-tagged line inside a `## Verdict` or `## Ask`
     section, or a Claims-section line tagged [unsupported] that
     is also marked `premise:`, is a violation.

`check` is the policy gate for prompt-free use: exit 2 =
disabled in the deployment config (workers.claimcheck),
proceed exactly as before. `lint` itself is NOT policy-gated
(the edd.py precedent: auditing a disabled spike's artifacts
must stay possible). Policy ships DISABLED in the example
config; enabling is a separate operator decision.

Usage:
    python3 bin/claimcheck.py check [--config F]
    python3 bin/claimcheck.py lint <file> [--config F] [--json]

Exit codes: 0 = clean / enabled, 1 = violations (advisory) or
bad input, 2 = disabled (check only).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config  # noqa: E402

TAGS = ("[observed]", "[inference]", "[unsupported]", "[decision]")
SOURCE_RE = re.compile(
    r"source\s*:|https?://|`[^`]+`|"
    r"[\w.~/-]+\.(?:py|md|json|jsonl|txt|toml|ya?ml|csv)\b",
    re.IGNORECASE)
VERDICT_SECTIONS = {"verdict", "ask"}


def load_config(explicit: str | None) -> dict:
    if explicit:
        with open(os.path.expanduser(explicit), encoding="utf-8") as fh:
            return json.load(fh)
    return _load_deployment_config() or {}


def policy_from_config(cfg: dict) -> dict:
    pol = dict(((cfg.get("workers") or {}).get("claimcheck") or {}))
    pol.setdefault("enabled", False)
    return pol


def split_sections(text: str):
    """Yield (section_title_lower, [(lineno, line), ...]). Lines
    before the first `## ` header land in section ''."""
    sections = []
    title, body = "", []
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            sections.append((title, body))
            title, body = m.group(1).strip().lower(), []
        else:
            body.append((lineno, line))
    sections.append((title, body))
    return sections


def lint_text(text: str) -> list:
    """Return the violations list: [{line, kind, text}]."""
    violations = []
    sections = split_sections(text)
    claims = None
    for title, body in sections:
        if title.startswith("claims"):
            claims = body
            break
    if claims is None:
        return [{"line": 0, "kind": "no_claims_section",
                 "text": "no ## Claims section found — claim-typing "
                         "requires a Claims section"}]
    for lineno, line in claims:
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        tags = [t for t in TAGS if t in stripped]
        if not tags:
            violations.append({"line": lineno, "kind": "untagged_claim",
                               "text": stripped[:160]})
            continue
        if len(tags) > 1:
            violations.append({"line": lineno,
                               "kind": "multiple_tags",
                               "text": stripped[:160]})
            continue
        if tags[0] == "[observed]" and not SOURCE_RE.search(stripped):
            violations.append({"line": lineno,
                               "kind": "observed_without_source",
                               "text": stripped[:160]})
        if tags[0] == "[unsupported]" and "premise:" in stripped.lower():
            violations.append(
                {"line": lineno, "kind": "unsupported_as_premise",
                 "text": stripped[:160]})
    for title, body in sections:
        head = title.split("—")[0].split("(")[0].strip()
        if head in VERDICT_SECTIONS or title.startswith(
                ("verdict", "ask")):
            for lineno, line in body:
                if "[unsupported]" in line:
                    violations.append(
                        {"line": lineno,
                         "kind": "unsupported_in_verdict_section",
                         "text": line.strip()[:160]})
    return violations


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check", help="exit 0 when enabled, "
                                       "2 when disabled")
    chk.add_argument("--config", default=None)
    chk.add_argument("--json", action="store_true")
    lin = sub.add_parser("lint", help="lint one artifact file")
    lin.add_argument("file")
    lin.add_argument("--config", default=None)
    lin.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"claimcheck: config unreadable: {exc}", file=sys.stderr)
        return 1
    policy = policy_from_config(cfg)

    if args.cmd == "check":
        if policy.get("enabled") is not True:
            print("claimcheck disabled in deployment config "
                  "(workers.claimcheck.enabled is not true) — proceed "
                  "exactly as before")
            return 2
        print("claimcheck enabled")
        return 0

    path = os.path.expanduser(args.file)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"claimcheck: cannot read {path}: {exc}", file=sys.stderr)
        return 1
    violations = lint_text(text)
    if args.json:
        print(json.dumps({"file": path, "violations": violations,
                          "clean": not violations}))
    else:
        for v in violations:
            where = f"line {v['line']}" if v["line"] else "file"
            print(f"claimcheck: {where}: {v['kind']}: {v['text']}")
        if violations:
            print(f"claimcheck: {len(violations)} violation(s) in "
                  f"{path} (advisory — blocks nothing)")
        else:
            print(f"claimcheck: clean — {path}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
