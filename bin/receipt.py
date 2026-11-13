#!/usr/bin/env python3
"""Proof receipts for PRs (proof-packet issue #8, 2026-11-13).

A PR body carries a `## Proof` section assembled by the supervisor at
PR-creation time from evidence the run already produced (machine
manifest + worker report + the manager's own re-runs). This script is
the mechanical half of that: it decides whether the proof a brief
required is present, gaps, or absent, and it keeps a per-PR receipt so
proof coverage can be reported over a window.

Conventions (the receipt's own, no new ones):
  proof: present | gaps | absent | not_required
    present       every required proof item is in the PR body
    gaps          a required item is missing, but the gap is known:
                  some proof material exists, or the missing item was
                  recorded as not_available with a reason
    absent        a required item existed, but no proof material and no
                  recorded reason reached the PR body (a silent gap)
    not_required  the brief required no proof items and none were made
  gaps[]         the missing items, one entry per gap
  not_available[] items that could not be produced, "<item> (<reason>)"

Proof is NOT a merge gate: nothing here blocks a PR from opening, and
nothing here changes the shepherd's merge policy or any hard stop.

Usage:
    python3 bin/receipt.py check --required "<a,b>" --present "<b,c>"
                                 [--not-available "<d (no display)>"]
                                 [--json]
    python3 bin/receipt.py record --repo <slug> --pr <n> [--url <url>]
                                  [--type visual|numeric|docs]
                                  --required "<a,b>" --present "<b>"
                                  [--not-available "<d (reason)>"]
                                  [--ledger PATH] [--json]
    python3 bin/receipt.py summary [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                                   [--ledger PATH] [--json]

--ledger defaults to receipts.jsonl in the deployment config's state dir
(same search order as trace_summary.py). Exit 0 on success; exit 2 when
the ledger cannot be read (summary).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import default_ledger_path  # noqa: E402

PROOF_TYPES = ("visual", "numeric", "docs")
PRESENT = "present"
GAPS = "gaps"
ABSENT = "absent"
NOT_REQUIRED = "not_required"


def default_receipt_path() -> str | None:
    worker = default_ledger_path()
    if not worker:
        return None
    return os.path.join(os.path.dirname(worker), "receipts.jsonl")


def _items(value: str | None) -> list[str]:
    """Comma-separated (or newline-separated) item list -> clean list."""
    if not value:
        return []
    out = []
    for raw in value.replace("\n", ",").split(","):
        item = raw.strip()
        if item:
            out.append(item)
    return out


def _base_item(entry: str) -> str:
    """'after screenshot (no display)' -> 'after screenshot'."""
    return entry.split(" (", 1)[0].strip()


def classify(required: list[str], present: list[str],
             not_available: list[str] | None = None) -> dict:
    """Decide the proof status of one PR from its required/present items."""
    not_available = list(not_available or [])
    unavailable_items = {_base_item(e) for e in not_available}
    present_items = [_base_item(e) for e in present
                     if _base_item(e) not in unavailable_items]
    present_set = set(present_items)

    gaps = []
    for item in required:
        if item in present_set:
            continue
        reason = next((e for e in not_available
                       if _base_item(e) == item), None)
        if reason and reason != item:
            gaps.append({"item": item, "reason": reason[len(item):].strip(" ()")})
        elif reason:
            gaps.append({"item": item, "reason": "not available"})
        else:
            gaps.append({"item": item, "reason": "missing"})

    gap_lines = []
    for gap in gaps:
        if gap["reason"] in ("missing", "not available"):
            gap_lines.append(f"proof gap: {gap['item']}")
        else:
            gap_lines.append(
                f"proof gap: {gap['item']} (not available: {gap['reason']})")

    if not required and not present_items:
        status = NOT_REQUIRED
    elif not gaps:
        status = PRESENT
    elif present_items or not_available:
        # some material made it, or the missing item carries an
        # explicit not_available reason — a known gap
        status = GAPS
    else:
        status = ABSENT

    return {
        "proof": status,
        "required": list(required),
        "present": present_items,
        "not_available": not_available,
        "gaps": gaps,
        "gap_lines": gap_lines,
    }


def append_row(ledger_path: str, row: dict) -> dict:
    row = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           **row}
    os.makedirs(os.path.dirname(os.path.abspath(ledger_path)),
                exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return row


def read_rows(ledger_path: str) -> list[dict]:
    rows = []
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def summarize(rows: list[dict], since: str | None = None,
              until: str | None = None) -> dict:
    """Proof coverage over a window (the latest receipt per PR wins)."""
    latest: dict[tuple, dict] = {}
    for row in rows:
        ts = str(row.get("ts") or "")
        day = ts[:10]
        if since and day < since:
            continue
        if until and day > until:
            continue
        key = (str(row.get("repo") or ""), str(row.get("pr") or ""))
        if key not in latest or str(latest[key].get("ts") or "") <= ts:
            latest[key] = row

    by_proof = Counter()
    by_type = Counter()
    by_repo = Counter()
    gap_counter = Counter()
    gap_prs = []
    required_prs = 0
    present_prs = 0
    material_prs = 0
    for (repo, pr), row in sorted(latest.items()):
        status = str(row.get("proof") or ABSENT)
        by_proof[status] += 1
        if row.get("type"):
            by_type[str(row["type"])] += 1
        if repo:
            by_repo[repo] += 1
        required = row.get("required") or []
        if required:
            required_prs += 1
            if status == PRESENT:
                present_prs += 1
            if status in (PRESENT, GAPS):
                material_prs += 1
        gaps = row.get("gaps") or []
        for gap in gaps:
            item = gap.get("item") if isinstance(gap, dict) else str(gap)
            if item:
                gap_counter[item] += 1
        if gaps:
            gap_prs.append({
                "repo": repo, "pr": pr, "url": row.get("url") or "",
                "gaps": [g.get("item") if isinstance(g, dict) else str(g)
                         for g in gaps],
            })

    total = len(latest)
    return {
        "prs": total,
        "by_proof": dict(by_proof),
        "by_type": dict(by_type),
        "by_repo": dict(by_repo),
        "required_prs": required_prs,
        "present_prs": present_prs,
        "material_prs": material_prs,
        "coverage_pct": (round(100.0 * present_prs / required_prs, 1)
                         if required_prs else None),
        "material_pct": (round(100.0 * material_prs / required_prs, 1)
                         if required_prs else None),
        "gap_prs": gap_prs,
        "top_gaps": dict(gap_counter.most_common()),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_chk = sub.add_parser("check", help="classify one PR's proof")
    p_chk.add_argument("--required", default="",
                       help="comma-separated proof items the brief required")
    p_chk.add_argument("--present", default="",
                       help="comma-separated proof items actually in the PR")
    p_chk.add_argument("--not-available", default="",
                       help="items that could not be produced, "
                            "'<item> (<reason>)'")
    p_chk.add_argument("--json", action="store_true")

    p_rec = sub.add_parser("record", help="record one PR's proof receipt")
    p_rec.add_argument("--repo", required=True)
    p_rec.add_argument("--pr", required=True, type=int)
    p_rec.add_argument("--url", default="")
    p_rec.add_argument("--type", default="", choices=PROOF_TYPES)
    p_rec.add_argument("--required", default="")
    p_rec.add_argument("--present", default="")
    p_rec.add_argument("--not-available", default="")
    p_rec.add_argument("--ledger", default=None)
    p_rec.add_argument("--json", action="store_true")

    p_sum = sub.add_parser("summary", help="proof coverage over a window")
    p_sum.add_argument("--since", default=None,
                       help="YYYY-MM-DD; receipts with an earlier ts are skipped")
    p_sum.add_argument("--until", default=None,
                       help="YYYY-MM-DD; receipts with a later ts are skipped")
    p_sum.add_argument("--ledger", default=None)
    p_sum.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "check":
        result = classify(_items(args.required), _items(args.present),
                          _items(args.not_available))
        if args.json:
            print(json.dumps(result, indent=1))
        else:
            print(f"proof: {result['proof']}")
            for line in result["gap_lines"]:
                print(line)
        return 0

    path = args.ledger or default_receipt_path()
    if not path:
        print("receipt: no ledger path (no deployment config)",
              file=sys.stderr)
        return 2

    if args.cmd == "record":
        result = classify(_items(args.required), _items(args.present),
                          _items(args.not_available))
        row = append_row(path, {
            "repo": args.repo, "pr": args.pr, "url": args.url,
            "type": args.type,
            "proof": result["proof"],
            "required": result["required"],
            "present": result["present"],
            "not_available": result["not_available"],
            "gaps": result["gaps"],
            "gap_lines": result["gap_lines"],
        })
        if args.json:
            print(json.dumps(row, indent=1))
        else:
            print(f"RECORDED: {row['repo']}#{row['pr']} proof={row['proof']}"
                  f" -> {path}")
            for line in row["gap_lines"]:
                print(line)
        return 0

    if not os.path.exists(path):
        print(f"receipt: ledger not found: {path}", file=sys.stderr)
        return 2
    result = summarize(read_rows(path), since=args.since, until=args.until)
    result["ledger"] = path
    result["since"] = args.since
    result["until"] = args.until
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"Proof receipts: {result['prs']} PR(s)"
              + (f" since {args.since}" if args.since else "")
              + (f" until {args.until}" if args.until else "")
              + f" in {path}")
        print(f"  by proof: {result['by_proof']}")
        print(f"  by type: {result['by_type']}")
        print(f"  by repo: {result['by_repo']}")
        print(f"  required-proof PRs: {result['required_prs']}; "
              f"fully proved: {result['present_prs']} "
              f"(coverage: {result['coverage_pct']}%)")
        if result["gap_prs"]:
            print(f"  PRs with proof gaps: {len(result['gap_prs'])}")
            for entry in result["gap_prs"]:
                print(f"    {entry['repo']}#{entry['pr']}: "
                      f"{', '.join(entry['gaps'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
