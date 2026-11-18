#!/usr/bin/env python3
"""Batched flag view: group the operator's flagged PRs by flag class.

Spike: feedback-surface flag review, Phase 1 (parent measurement: the
Phase 0 reconstruction found the flagged-PR stream is mostly repeat
rows for a small set of PRs, re-surfaced per PR in arrival order).
This module renders ONE deduplicated line per flagged PR, grouped by
flag class, so a reviewer can clear same-kind flags in one pass and a
lesson from one ruling visibly applies to the whole group.

Presentation only. The output is a view for the operator's flag
review: it never clears a flag, never changes a merge decision, and
never feeds dispatch, merge, or tune logic. The only sanctioned
consumer of the counts ledger it appends is tune-pass reporting.

Classification is deterministic and ledger-based: each flagged PR's
primary class is derived from its own shepherd-ledger rows (the ci,
merge_state, outcome, and detail fields), using the same closed
class names as the flag-resolution verifier (ci-none, ci-reds,
ci-unclassified, workflow-kind, security-scan, conflict-stale,
physics-gate, held-green), plus unverifiable when the ledger carries
no codable signal for the PR. A future revision should prefer the
verifier's evaluate() when that module is publishable alongside this
one; until then this classifier is the presentation classifier, and
its keyword rules are covered by tests so any drift is visible.

Config (deployment config, workers.flagbatch):
  enabled: false (default) - every command exits 2 and does nothing.

Usage:
  flagbatch.py check [--config PATH]
  flagbatch.py generate --state-dir DIR --out FILE
                        [--config PATH] [--json]
"""

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trace_summary import _load_deployment_config

# Priority order for picking a PR's primary class: the first class in
# this order that the PR's ledger rows support wins. The first two
# (plus unverifiable) are individual-review classes: they render
# first, under an individual-review heading, and are never counted
# into any batch total.
PRIORITY = (
    "security-scan",
    "physics-gate",
    "workflow-kind",
    "conflict-stale",
    "ci-unclassified",
    "ci-reds",
    "ci-none",
    "held-green",
)
INDIVIDUAL = ("security-scan", "physics-gate", "unverifiable")
CLASSES = PRIORITY + ("unverifiable",)

_SECURITY_RE = re.compile(r"codeql|security|critical alert", re.I)
_PHYSICS_RE = re.compile(
    r"physics|output[- ]value|re-?pin|tolerance|fitted value", re.I
)
_WORKFLOW_RE = re.compile(r"workflow", re.I)
_CONFLICT_RE = re.compile(r"conflict", re.I)
_CI_NONE_RE = re.compile(r"no ci signal|no ci\b|parks for a human", re.I)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config(explicit):
    if explicit:
        with open(explicit) as fh:
            return json.load(fh)
    return _load_deployment_config() or {}


def policy_from_config(cfg):
    pol = dict(((cfg.get("workers") or {}).get("flagbatch") or {}))
    pol.setdefault("enabled", False)
    return pol


def operator_name(cfg):
    return str(((cfg.get("operator") or {}).get("name")) or "operator")


def load_jsonl(path):
    if not path or not os.path.exists(path):
        return []
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


def parse_pr_key(key):
    """Parse a needs-operator entry into (repo_slug, pr_number).

    Accepts a PR URL (https://host/<owner>/<repo>/pull/<n>) or a
    '<repo>-<n>' shorthand. Returns None when neither matches.
    """
    text = str(key).strip()
    m = re.match(r"https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)", text)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"(.+)-(\d+)$", text)
    if m:
        return m.group(1), int(m.group(2))
    return None


def classify(rows):
    """Primary flag class for one PR from its shepherd-ledger rows.

    rows: every ledger row for the PR (any action). Deterministic;
    returns 'unverifiable' when no row carries a codable signal.
    """
    blob = " ".join(
        " ".join(
            str(r.get(f) or "") for f in ("outcome", "detail", "ci", "merge_state")
        )
        for r in rows
    )
    ci_text = " ".join(str(r.get("ci") or "") for r in rows)
    merge_states = {str(r.get("merge_state") or "") for r in rows}
    if _SECURITY_RE.search(blob):
        return "security-scan"
    if _PHYSICS_RE.search(blob):
        return "physics-gate"
    if _WORKFLOW_RE.search(blob):
        return "workflow-kind"
    if _CONFLICT_RE.search(blob) or "DIRTY" in merge_states:
        return "conflict-stale"
    if _CI_NONE_RE.search(blob):
        return "ci-none"
    if "fail" in ci_text.lower():
        return "ci-reds"
    if not ci_text.strip() and merge_states <= {"", "UNKNOWN"}:
        return "ci-none"
    if "CLEAN" in merge_states and (
        "all pass" in ci_text.lower() or "park" in blob.lower()
    ):
        return "held-green"
    if ci_text.strip():
        return "ci-unclassified"
    return "unverifiable"


def build_entries(shepherd, ledger_rows):
    """One entry per flagged PR in shepherd['needs_alex'].

    Each entry: {key, repo, pr, url, cls, count, first, last, reason}.
    count/first/last/reason come from the PR's needs_alex-action
    ledger rows (all rows when the PR has none, for robustness).
    """
    by_pr = {}
    for row in ledger_rows:
        by_pr.setdefault(
            (str(row.get("repo") or ""), str(row.get("pr") or "")), []
        ).append(row)
    entries = []
    for key in shepherd.get("needs_alex") or []:
        parsed = parse_pr_key(key)
        if parsed is None:
            entries.append(
                {
                    "key": str(key),
                    "repo": "",
                    "pr": "",
                    "url": "",
                    "cls": "unverifiable",
                    "count": 0,
                    "first": "",
                    "last": "",
                    "reason": "(unparseable flag entry)",
                }
            )
            continue
        slug, num = parsed
        rows = by_pr.get((slug, str(num))) or []
        flag_rows = [
            r for r in rows if r.get("action") in ("needs_alex", "needs_operator")
        ]
        use = flag_rows or rows
        stamps = sorted(str(r.get("ts") or "") for r in use if r.get("ts"))
        reason = ""
        if use:
            latest = max(use, key=lambda r: str(r.get("ts") or ""))
            reason = str(latest.get("outcome") or latest.get("detail") or "")[:110]
        entries.append(
            {
                "key": str(key),
                "repo": slug,
                "pr": num,
                "url": str(key) if str(key).startswith("http") else "",
                "cls": classify(rows),
                "count": len(flag_rows),
                "first": stamps[0][:10] if stamps else "",
                "last": stamps[-1][:10] if stamps else "",
                "reason": reason or "(no reason recorded)",
            }
        )
    return entries


def group_entries(entries):
    """Group entries by class in render order (individual first)."""
    order = [c for c in CLASSES if c in INDIVIDUAL]
    order += [c for c in PRIORITY if c not in INDIVIDUAL]
    groups = []
    for cls in order:
        members = [e for e in entries if e["cls"] == cls]
        if members:
            groups.append(
                (cls, sorted(members, key=lambda e: (e["repo"], e["pr"] or 0)))
            )
    return groups


def render(entries, operator):
    groups = group_entries(entries)
    batched = sum(len(m) for c, m in groups if c not in INDIVIDUAL)
    lines = [
        f"## Needs {operator} — by flag class",
        "",
        (
            f"{len(entries)} flagged PR(s), one line each "
            f"({batched} in batchable classes). Presentation only: "
            "this view never clears a flag and never feeds "
            "a decision."
        ),
        "",
    ]
    for cls, members in groups:
        if cls in INDIVIDUAL:
            lines.append(
                f"### {cls} ({len(members)}) — INDIVIDUAL REVIEW, never batch-approve"
            )
        else:
            lines.append(f"### {cls} ({len(members)})")
        for e in members:
            label = e["url"] or e["key"]
            meta = []
            if e["count"]:
                meta.append(f"flagged ×{e['count']}")
            if e["first"]:
                meta.append(f"first {e['first']}")
            if e["last"] and e["last"] != e["first"]:
                meta.append(f"latest {e['last']}")
            suffix = f" ({', '.join(meta)})" if meta else ""
            lines.append(f"- {label} — {e['reason']}{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", groups


def counts_row(entries, groups, flat_lines):
    by_class = {cls: len(members) for cls, members in groups}
    batched_lines = sum(len(m) for c, m in groups if c not in INDIVIDUAL)
    return {
        "ts": now_iso(),
        "needs_operator_count": len(entries),
        "by_class": by_class,
        "flat_lines": flat_lines,
        "batched_lines": batched_lines,
    }


def cmd_check(args):
    cfg = load_config(args.config)
    if not policy_from_config(cfg).get("enabled"):
        return 2
    return 0


def cmd_generate(args):
    cfg = load_config(args.config)
    if not policy_from_config(cfg).get("enabled"):
        return 2
    state_dir = os.path.expanduser(args.state_dir)
    with open(os.path.join(state_dir, "shepherd.json")) as fh:
        shepherd = json.load(fh)
    ledger_rows = load_jsonl(os.path.join(state_dir, "shepherd_ledger.jsonl"))
    entries = build_entries(shepherd, ledger_rows)
    text, groups = render(entries, operator_name(cfg))
    with open(args.out, "w") as fh:
        fh.write(text)
    row = counts_row(entries, groups, flat_lines=len(entries))
    with open(os.path.join(state_dir, "flag_batch_ledger.jsonl"), "a") as fh:
        fh.write(json.dumps(row) + "\n")
    if args.json:
        print(json.dumps(row))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Batched flag view generator (presentation only)."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check")
    chk.add_argument("--config", default=None)
    gen = sub.add_parser("generate")
    gen.add_argument("--state-dir", required=True)
    gen.add_argument("--out", required=True)
    gen.add_argument("--config", default=None)
    gen.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args)
    return cmd_generate(args)


if __name__ == "__main__":
    sys.exit(main())
