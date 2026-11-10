#!/usr/bin/env python3
"""Contract citation drift check for repo CONTRACT.md files
(repo-contract spike, Part B, approved by the operator 2026-11-10;
pattern mined from Addy Osmani's "Your coding agents made you a
manager" — the contract / PRESS.md idea).

A CONTRACT.md line is only as good as its citation. This script is
the mechanical drift check from the spike brief (§B4): it parses a
repo's CONTRACT.md, extracts the citations Phase 0's traceability
standard produced, and verifies each checkout-checkable citation
still exists:

  file      a cited path (backticked or in a parenthetical
            reference) exists in the checkout. Bare filenames are
            resolved at the repo root first, then by unique
            basename match anywhere in the tree.
  symbol    a `file.py:symbol` citation — the symbol string
            appears in that file.
  section   a `FILE §Section Name` citation — the section text
            appears in that file.

Citations that cannot be checked against a checkout — operator
standing directives, GitHub issue numbers, anything in the
operator's own records — are reported as "external", never as
dead. There is no LLM judgment anywhere in this script: a citation
is live, dead, or external, mechanically.

Usage:
    python3 bin/contract_check.py --repo <checkout> [--contract P]
                                  [--json]
Exit 0 = every checkable citation is live.
Exit 1 = at least one dead citation (contract is stale; per the
         brief, that repo's briefs revert to carrying rules inline
         until the contract is repaired).
Exit 2 = usage error / no CONTRACT.md found.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

FILE_EXTS = (".md", ".py", ".rs", ".yml", ".yaml", ".json", ".toml",
             ".txt", ".cfg", ".sh", ".js", ".ts")
# Tokens that look path-like but are not citations of repo files.
NOT_FILES = {"main", "develop"}


def _looks_like_path(tok: str) -> bool:
    tok = tok.strip().strip(",;:()")
    if not tok or " " in tok or tok.startswith("#"):
        return False
    if tok in NOT_FILES:
        return False
    if tok.endswith("/") and "/" not in tok[:-1]:
        return True  # top-level directory, e.g. `detector/`
    if "/" in tok:
        return True
    return tok.endswith(FILE_EXTS)


def extract_citations(text: str) -> list[dict]:
    """Return [{raw, kind, target, symbol?, section?}] from contract
    text. Kinds: file, symbol, section, external."""
    out: list[dict] = []
    seen: set[tuple] = set()

    def add(entry: dict) -> None:
        key = (entry["kind"], entry.get("target"), entry.get("symbol"),
               entry.get("section"))
        if key not in seen:
            seen.add(key)
            out.append(entry)

    # External citations: standing directives and issue references
    # are operator/GitHub records, not checkout content.
    for m in re.finditer(r"[Ss]tanding directive[^)]*", text):
        add({"raw": m.group(0).strip(), "kind": "external",
             "target": "operator standing directive"})
    for m in re.finditer(r"Issue #\d+", text):
        add({"raw": m.group(0), "kind": "external",
             "target": "GitHub issue"})

    # Backticked tokens: paths, `file:symbol`, directories.
    # A token immediately negated ("not `path/`") cites where a
    # thing is NOT — existence is not the claim; skip it.
    for m in re.finditer(r"`([^`]{1,120})`", text):
        tok = m.group(1).strip()
        before = text[max(0, m.start() - 8):m.start()]
        if re.search(r"\bnot\s*$", before) or "(not " in before:
            continue
        sym = re.fullmatch(r"([\w./-]+\.py):(\w+)", tok)
        if sym:
            add({"raw": tok, "kind": "symbol", "target": sym.group(1),
                 "symbol": sym.group(2)})
        elif _looks_like_path(tok):
            add({"raw": tok, "kind": "file", "target": tok})

    # Named-section citations: (RULES.md §Must-Always 1) — the §
    # binds only to the file token immediately before it.
    for m in re.finditer(
            r"([\w./-]+\.(?:md|py|rs|yml|yaml|json|toml))"
            r"\s*§\s*([A-Za-z][\w-]*)", text):
        if m.group(2) != "Issue":
            add({"raw": f"{m.group(1)} §{m.group(2)}", "kind": "section",
                 "target": m.group(1), "section": m.group(2)})

    # Parenthetical references: (.github/ci-gates.yaml header),
    # (AGENTS.md, Quick Rules)...
    for m in re.finditer(r"\(([^()]{1,160})\)", text):
        body = m.group(1)
        if "standing directive" in body.lower():
            continue
        toks = re.split(r"[\s,;]+", body)
        for i, tok in enumerate(toks):
            tok = tok.strip("`")
            if i > 0 and toks[i - 1].strip("`").lower() == "not":
                continue  # negated citation — existence not claimed
            if _looks_like_path(tok):
                add({"raw": tok, "kind": "file", "target": tok})

    # Inline symbol mentions tied to a file named in the same line:
    # e.g. `run_pipeline.py` ... `_roof_simplify_enabled`.
    for line in text.splitlines():
        files = [c["target"] for c in out
                 if c["kind"] == "file" and c["target"] in line]
        for m in re.finditer(r"`(_\w+)`", line):
            if files:
                add({"raw": m.group(1), "kind": "symbol",
                     "target": files[0], "symbol": m.group(1)})
    return out


def _resolve(repo: str, target: str) -> str | None:
    """Resolve a cited path inside the checkout, or None."""
    import glob as _glob
    target = target.rstrip(",;:")
    if "*" in target:  # glob citation, e.g. `.github/workflows/*.yml`
        hits = _glob.glob(os.path.join(repo, target))
        return hits[0] if hits else None
    direct = os.path.join(repo, target)
    if os.path.exists(direct):
        return direct
    if "/" not in target:
        # Bare filename: the citation claims the file exists in the
        # repo (commonly in a conventional home like tests/);
        # one or more matches make it live.
        for root, dirs, names in os.walk(repo):
            dirs[:] = [d for d in dirs
                       if d not in (".git", "node_modules", "__pycache__")]
            if target in names or target.rstrip("/") in dirs:
                return os.path.join(root, target)
    return None


def check(repo: str, contract_path: str) -> dict:
    text = open(contract_path, encoding="utf-8").read()
    citations = extract_citations(text)
    live, dead, external = [], [], []
    for c in citations:
        if c["kind"] == "external":
            external.append(c)
            continue
        path = _resolve(repo, c["target"])
        if path is None:
            dead.append({**c, "detail": "cited path not found"})
            continue
        if c["kind"] == "file":
            live.append(c)
        elif c["kind"] == "symbol":
            try:
                body = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                body = ""
            if c["symbol"] in body:
                live.append(c)
            else:
                dead.append({**c, "detail":
                             f"symbol {c['symbol']} not in {c['target']}"})
        elif c["kind"] == "section":
            try:
                body = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                body = ""
            if c["section"] in body:
                live.append(c)
            else:
                dead.append({**c, "detail":
                             f"section {c['section']} not in {c['target']}"})
    return {"contract": contract_path, "checked": len(live) + len(dead),
            "live": live, "dead": dead, "external": external}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True,
                    help="path to the repo checkout")
    ap.add_argument("--contract", default=None,
                    help="CONTRACT.md path (default: <repo>/CONTRACT.md)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.repo):
        print(f"contract_check: repo not a directory: {args.repo}",
              file=sys.stderr)
        return 2
    contract = args.contract or os.path.join(args.repo, "CONTRACT.md")
    if not os.path.isfile(contract):
        print(f"contract_check: no CONTRACT.md at {contract}",
              file=sys.stderr)
        return 2

    result = check(args.repo, contract)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"contract_check: {result['checked']} citations checked, "
              f"{len(result['live'])} live, {len(result['dead'])} dead, "
              f"{len(result['external'])} external (not checked)")
        for d in result["dead"]:
            print(f"  DEAD: {d['raw']} — {d['detail']}")
    return 1 if result["dead"] else 0


if __name__ == "__main__":
    sys.exit(main())
