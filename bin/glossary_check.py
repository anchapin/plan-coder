#!/usr/bin/env python3
"""Glossary core-term drift check for repo UBIQUITOUS_LANGUAGE.md
files (DDD-light glossary check, approved by the operator
2026-11-11; the sibling of contract_check.py).

A glossary is only as good as its core terms staying tied to the
code. Each glossary carries a machine-checked table:

    ## Core terms (machine-checked)

    | Term | Symbol | Source |
    |---|---|---|
    | Thermal Model | ThermalModel | src/sim/thermal_model.rs |
    | Night Ventilation | — | src/sim/ventilation.rs |

For every row this script verifies, mechanically:

  defined   the Term is still defined in the glossary: some bold
            span `**...**` anywhere in the file (entries bold
            sub-terms mid-entry too) matches it. Matching is
            normalized on both sides — lowercase, camelCase split
            into words (MassClass -> "mass class"), non-
            alphanumerics stripped — and a span matches when its
            normalized form STARTS WITH the normalized term, so
            "**Internal loads / internal gains**" defines
            "Internal loads".
  source    the Source cell (a repo-relative path, file or
            directory) still exists in the checkout.
  symbol    if the Symbol cell is "—" or empty the check is
            skipped; otherwise the symbol string still appears in
            the Source file, or in any text file under the Source
            directory (bounded walk; binaries and .git skipped).

There is no LLM judgment anywhere in this script: a row is live
or dead, mechanically.

Usage:
    python3 bin/glossary_check.py --repo <checkout>
                                  [--glossary UBIQUITOUS_LANGUAGE.md]
                                  [--json]
Exit 0 = every core-term row is live.
Exit 1 = at least one dead row (glossary is stale at that row).
Exit 2 = usage error / no glossary found / no (or malformed)
         "Core terms (machine-checked)" table — a glossary with
         no machine-checked table is not checkable, and for a
         repo that carries one that is a finding, not a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

SECTION_RE = re.compile(r"^##\s+Core terms \(machine-checked\)\s*$",
                        re.IGNORECASE)
SKIP_SYMBOL = {"", "—", "–", "-"}
MAX_WALK_FILES = 5000
MAX_FILE_BYTES = 4_000_000


def normalize(text: str) -> str:
    """Lowercase, split camelCase into words, strip non-alphanumerics."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def bold_spans(text: str) -> list[str]:
    """Every **...** span in the file, normalized.

    Sequential scan for the literal "**" delimiter, pairing open
    and close in order — NOT a `\\*\\*([^*]+)\\*\\*` regex: a span
    may contain a single asterisk (e.g. "**Conductances
    (`h_tr_*`)**"), and a [^*] character class mis-pairs every
    span after it. Candidate spans longer than ~120 chars, or
    containing a newline followed by "- " (a list-item boundary),
    are runaway pairs from an unbalanced delimiter and are
    discarded.
    """
    spans = []
    pos = 0
    while True:
        open_at = text.find("**", pos)
        if open_at == -1:
            break
        close_at = text.find("**", open_at + 2)
        if close_at == -1:
            break
        candidate = text[open_at + 2:close_at]
        pos = close_at + 2
        if len(candidate) > 120 or "\n- " in candidate:
            continue
        spans.append(normalize(candidate))
    return spans


def parse_core_terms(text: str) -> list[dict] | None:
    """Rows of the Core terms table, or None if there is no
    well-formed table (section missing, header missing, or the
    table has no data rows)."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if SECTION_RE.match(line.strip()):
            start = i
            break
    if start is None:
        return None
    rows: list[dict] = []
    header_seen = False
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            if header_seen:
                break
            continue
        cells = [c.strip().strip("`").strip()
                 for c in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if [normalize(c) for c in cells[:3]] == ["term", "symbol",
                                                 "source"]:
            header_seen = True
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells[:3]):
            continue  # separator row
        if not header_seen:
            continue
        rows.append({"term": cells[0], "symbol": cells[1],
                     "source": cells[2]})
    if not header_seen or not rows:
        return None
    return rows


def _symbol_in_file(path: str, symbol: str) -> bool:
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return False
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return False
    if b"\x00" in raw[:8192]:
        return False  # binary
    return symbol in raw.decode("utf-8", errors="replace")


def _symbol_present(repo: str, source: str, symbol: str) -> bool:
    path = os.path.join(repo, source)
    if os.path.isfile(path):
        return _symbol_in_file(path, symbol)
    if os.path.isdir(path):
        seen = 0
        for root, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if d not in
                       (".git", "node_modules", "__pycache__")]
            for name in names:
                seen += 1
                if seen > MAX_WALK_FILES:
                    return False
                if _symbol_in_file(os.path.join(root, name), symbol):
                    return True
    return False


def check(repo: str, glossary_path: str) -> dict | None:
    """Return the report dict, or None when there is no
    well-formed core-terms table to check."""
    text = open(glossary_path, encoding="utf-8").read()
    rows = parse_core_terms(text)
    if rows is None:
        return None
    spans = bold_spans(text)
    live, dead = [], []
    for row in rows:
        reasons = []
        nterm = normalize(row["term"])
        if not nterm or not any(s.startswith(nterm) for s in spans):
            reasons.append(f"term {row['term']!r} not defined "
                           f"(no matching **bold** entry)")
        src_path = os.path.join(repo, row["source"])
        if not row["source"] or not os.path.exists(src_path):
            reasons.append(f"source {row['source']} not found")
        elif row["symbol"] not in SKIP_SYMBOL:
            if not _symbol_present(repo, row["source"], row["symbol"]):
                reasons.append(f"symbol {row['symbol']} not in "
                               f"{row['source']}")
        if reasons:
            dead.append({**row, "detail": "; ".join(reasons)})
        else:
            live.append(row)
    return {"glossary": glossary_path, "checked": len(rows),
            "live": live, "dead": dead}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True,
                    help="path to the repo checkout")
    ap.add_argument("--glossary", default=None,
                    help="glossary path (default: "
                         "<repo>/UBIQUITOUS_LANGUAGE.md; a relative "
                         "path is resolved inside the repo)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.repo):
        print(f"glossary_check: repo not a directory: {args.repo}",
              file=sys.stderr)
        return 2
    glossary = args.glossary
    if glossary is None:
        glossary = os.path.join(args.repo, "UBIQUITOUS_LANGUAGE.md")
    elif not os.path.isabs(glossary):
        glossary = os.path.join(args.repo, glossary)
    if not os.path.isfile(glossary):
        print(f"glossary_check: no glossary at {glossary}",
              file=sys.stderr)
        return 2

    result = check(args.repo, glossary)
    if result is None:
        print("glossary_check: no well-formed 'Core terms "
              "(machine-checked)' table in "
              f"{glossary}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"glossary_check: {result['checked']} core terms "
              f"checked, {len(result['live'])} live, "
              f"{len(result['dead'])} dead")
        for d in result["dead"]:
            print(f"  DEAD: {d['term']} — {d['detail']}")
    return 1 if result["dead"] else 0


if __name__ == "__main__":
    sys.exit(main())
