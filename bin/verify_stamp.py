#!/usr/bin/env python3
"""Verification stamp at supervisor collection (issue #95, from the
#94 verification spike, Part A — Phase 0 PROCEED 2026-11-17).

Phase 0 reconstructed what the most recent 100 collected worker runs
actually verified, after the fact, from transcripts: SUITE 58,
TARGETED 11, NONE 31, E2E 0 — and the reconstruction was
classifier-sensitive (a loose parser put NONE at 12% vs the
segment-anchored classifier's 31%). This tool makes the record at
collection time instead: one deterministic parser pass over the run's
transcript, of exactly the kind grade_run.py performs, appended as a
row to verification_ledger.jsonl in the state dir.

The classifier definitions are Phase 0's segment-anchored ones,
verbatim (verification-phase0/part_a.py): commands are split into
segments on &&/;/||, leading cd/timeout/env wrappers are dropped, and
each segment is matched against test / lint-build / e2e patterns; a
test segment counts as SUITE only when it names no specific test file
(pytest with no positional .py/:: target, cargo test with no filter,
vitest/jest with no file argument, unittest discover, npm test,
node --test with no file argument).

Where the stamp lives — companion ledger, not the worker ledger:
worker_ledger.jsonl rows are written by the supervisor prompt with a
fixed field set that harness-tune and several bin/ tools already read;
a companion append-only ledger (the jittest_ledger.jsonl pattern)
carries the stamp without changing that schema. A stamp row:

  stamped_at / ts   UTC ISO of the stamp (collection time)
  run_id, repo, issue, pr_url
  verify_class      E2E | SUITE | TARGETED | NONE from the worker
                    transcript; UNKNOWN only when the transcript is
                    missing (Phase 0's A-UNKNOWN — 0 of 100 there)
  verify_commands   {"worker": [...], "collection": [...]} — the
                    test/lint/e2e segments the worker ran, and the
                    verification commands the SUPERVISOR itself re-ran
                    at collection (step 6), recorded separately: before
                    this stamp, collection re-runs left no trace
  collection_verify_class  same classifier over the collection commands
  clean_base_verified  yes | no | not_claimed — the receipt spike's
                    vocabulary, supplied by the supervisor: yes when a
                    clean-base comparison actually ran, no when a
                    pre-existing-failure claim was made without one,
                    not_claimed when no such claim was made
  clean_base_detected    transcript evidence of a stash/clean-base
                    re-run (Phase 0's detector, kept as evidence next
                    to the supervisor's stated value)
  evidence          first verification segment, truncated

OBSERVE-ONLY: nothing in the harness reads this ledger to gate,
block, flag or grade anything. It is a record to query (see the
summary subcommand). Ships disabled: `workers.verification_stamp.
enabled` in the deployment config must be true for the stamp
subcommand to run; otherwise it exits 2, exactly as test_tamper /
jittest do. `--force` runs it anyway (operator one-off use). The
summary subcommand is read-only and never gated.

Usage:
    python3 bin/verify_stamp.py stamp --run-id <id> --repo <repo>
        [--issue <n>] [--pr-url <url>]
        [--transcripts-dir DIR] [--ledger PATH]
        [--collection-command "<cmd>"]... (repeatable)
        [--clean-base-verified yes|no|not_claimed] [--force] [--json]
    python3 bin/verify_stamp.py classify --transcript <run.jsonl> [--json]
    python3 bin/verify_stamp.py summary [--ledger PATH] [--days 14] [--json]

Exit 0 = stamped / classified / summarised; 2 = disabled in the
deployment config or usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config

# ------------------------------------------------------------------
# Phase 0 segment-anchored classifier (verbatim definitions)
# ------------------------------------------------------------------

RUNTEST = re.compile(
    r"^(python3?\s+-m\s+pytest|pytest|cargo\s+test|npm\s+(run\s+)?test|"
    r"npx\s+(vitest|jest|playwright)|vitest|jest|python3?\s+-m\s+unittest|"
    r"node\s+--test|playwright\s+test|godot\b.*--headless)", re.I)
LINTBUILD = re.compile(
    r"^(ruff\s+check|ruff\s+format\s+--check|cargo\s+(check|clippy|build)|"
    r"npm\s+run\s+(build|lint)|npx\s+tsc|mypy|eslint|oxlint|npm\s+run\s+lint)",
    re.I)
E2E = re.compile(
    r"^(playwright\s+test|npx\s+playwright|cargo\s+run|python3?\s+-m\s+bem|"
    r"python3?\s+.*(simulate|export_pipeline|run_export|cli\.py)\b.*"
    r"(--|\s+\S+\.(png|jpg|dxf|json)))", re.I)
CLEAN_BASE = re.compile(
    r"(git\s+stash|stash\s+pop|clean\s+(develop|base|main)\b|"
    r"stash.*re-?run|re-?run.*stash)", re.I)

VERIFY_CLASSES = ("E2E", "SUITE", "TARGETED", "NONE", "UNKNOWN")
CLEAN_BASE_VALUES = ("yes", "no", "not_claimed")


def segments(cmd: str) -> list[str]:
    parts = re.split(r"&&|;|\|\|", cmd)
    out = []
    for p in parts:
        p = p.strip()
        p = re.sub(r"^cd\s+\S+\s*$", "", p)
        p = re.sub(r"^timeout\s+\d+\s+", "", p)
        p = re.sub(r"^[A-Z_]+=\S+\s+", "", p)
        if p:
            out.append(p)
    return out


def is_suite(seg: str) -> bool:
    if re.search(r"pytest", seg):
        toks = seg.split()
        pos = [t for t in toks if t.endswith(".py") or "::" in t]
        return not pos
    if re.search(r"cargo\s+test", seg):
        return "--" not in seg
    if re.search(r"(vitest|jest)", seg):
        return not [t for t in seg.split()
                    if t.endswith((".ts", ".tsx", ".js", ".mts"))
                    and "config" not in t]
    if re.search(r"unittest", seg):
        return "discover" in seg
    if re.search(r"npm\s+(run\s+)?test", seg):
        return True
    if re.search(r"node\s+--test", seg):
        return not [t for t in seg.split()
                    if t.endswith((".mjs", ".js"))]
    return False


def classify_segments(segs: list[str], results: list[str]) -> dict:
    """Phase 0 classification of a segment list (+ tool results for
    the pass-evidence E2E requires)."""
    test_segs = [s for s in segs if RUNTEST.search(s)]
    suite_segs = [s for s in test_segs if is_suite(s)]
    e2e_segs = [s for s in segs if E2E.search(s)]
    lint_segs = [s for s in segs if LINTBUILD.search(s)]
    pw_pass = any(re.search(r"\d+\s+passed", res) for res in results)
    counts = []
    for res in results:
        counts += re.findall(r"(\d+)\s+passed", res)
    if e2e_segs and pw_pass:
        cls = "E2E"
    elif suite_segs:
        cls = "SUITE"
    elif test_segs or lint_segs:
        cls = "TARGETED"
    else:
        cls = "NONE"
    verify_cmds = list(dict.fromkeys(test_segs + lint_segs + e2e_segs))
    return {
        "verify_class": cls,
        "verify_commands": verify_cmds,
        "evidence": (suite_segs or test_segs or e2e_segs or lint_segs
                     or [""])[0][:200].replace("\n", " "),
        "test_segments": len(test_segs),
        "max_passed": max((int(x) for x in counts), default=None),
    }


def classify_transcript(path: str) -> dict:
    segs: list[str] = []
    results: list[str] = []
    texts: list[str] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except (ValueError, TypeError):
                continue
            for tc in e.get("tool_calls") or []:
                if tc.get("name") != "exec":
                    continue
                try:
                    cmd = json.loads(tc.get("args", "{}")).get("command", "")
                except (ValueError, TypeError):
                    cmd = ""
                segs += segments(cmd)
            for tr in e.get("tool_results") or []:
                results.append(str(tr))
            if e.get("assistant_text"):
                texts.append(e["assistant_text"])
    out = classify_segments(segs, results)
    out["clean_base_detected"] = bool(
        CLEAN_BASE.search("\n".join(segs) + "\n" + "\n".join(texts)))
    return out


def find_transcript(transcripts_dir: str, run_id: str) -> str | None:
    exact = os.path.join(transcripts_dir, run_id + ".jsonl")
    if os.path.exists(exact):
        return exact
    try:
        files = sorted(os.listdir(transcripts_dir))
    except OSError:
        return None
    for f in files:
        if run_id in f:
            return os.path.join(transcripts_dir, f)
    return None


# ------------------------------------------------------------------
# config / state paths
# ------------------------------------------------------------------

def enabled_in_config() -> bool:
    """workers.verification_stamp.enabled in the deployment config."""
    cfg = _load_deployment_config() or {}
    workers = cfg.get("workers") or {}
    section = workers.get("verification_stamp") or {}
    return section.get("enabled") is True


def _state_dir() -> str:
    cfg = _load_deployment_config() or {}
    state = cfg.get("state") or {}
    return os.path.expanduser(state.get("dir") or "~/.plan-coder/state")


def _transcripts_dir() -> str:
    cfg = _load_deployment_config() or {}
    state = cfg.get("state") or {}
    return os.path.expanduser(
        state.get("transcripts_dir") or "~/.plan-coder/state/transcripts")


def _now() -> datetime:
    return datetime.now(UTC)


def build_stamp(run_id: str, transcripts_dir: str, repo: str = "",
                issue=None, pr_url: str = "",
                collection_commands: list[str] | None = None,
                clean_base_verified: str = "not_claimed",
                moment: datetime | None = None) -> dict:
    moment = moment or _now()
    path = find_transcript(transcripts_dir, run_id)
    collection_commands = collection_commands or []
    coll_segs: list[str] = []
    for cmd in collection_commands:
        coll_segs += segments(cmd)
    coll = classify_segments(coll_segs, [])
    if path is None:
        worker = {"verify_class": "UNKNOWN", "verify_commands": [],
                  "evidence": "transcript missing", "test_segments": 0,
                  "max_passed": None, "clean_base_detected": False}
    else:
        worker = classify_transcript(path)
    return {
        "ts": moment.isoformat(),
        "stamped_at": moment.isoformat(),
        "run_id": run_id,
        "repo": repo,
        "issue": issue,
        "pr_url": pr_url,
        "verify_class": worker["verify_class"],
        "verify_commands": {
            "worker": worker["verify_commands"],
            "collection": collection_commands,
        },
        "collection_verify_class": coll["verify_class"],
        "clean_base_verified": clean_base_verified,
        "clean_base_detected": worker["clean_base_detected"],
        "evidence": worker["evidence"],
        "test_segments": worker["test_segments"],
        "max_passed": worker["max_passed"],
    }


def append_stamp(ledger_path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(ledger_path)),
                exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def summarise(ledger_path: str, days: int,
              moment: datetime | None = None) -> dict:
    moment = moment or _now()
    cutoff = moment - timedelta(days=days)
    counts = {c: 0 for c in VERIFY_CLASSES}
    total = 0
    coll_counts = {c: 0 for c in VERIFY_CLASSES}
    try:
        with open(ledger_path, encoding="utf-8",
                  errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            ts = datetime.fromisoformat(row.get("stamped_at", ""))
        except (ValueError, TypeError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < cutoff:
            continue
        total += 1
        counts[row.get("verify_class", "UNKNOWN")] = \
            counts.get(row.get("verify_class", "UNKNOWN"), 0) + 1
        cvc = row.get("collection_verify_class", "UNKNOWN")
        coll_counts[cvc] = coll_counts.get(cvc, 0) + 1
    share = {c: (round(100.0 * n / total, 1) if total else 0.0)
             for c, n in counts.items()}
    return {"days": days, "total": total, "counts": counts,
            "share_pct": share, "collection_counts": coll_counts}


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("stamp", help="classify one run and append its stamp")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--repo", default="")
    sp.add_argument("--issue", type=int, default=None)
    sp.add_argument("--pr-url", default="")
    sp.add_argument("--transcripts-dir", default=None)
    sp.add_argument("--ledger", default=None,
                    help="verification ledger path (default: state dir)")
    sp.add_argument("--collection-command", action="append", default=[],
                    help="a verification command the supervisor re-ran "
                         "at collection (repeatable)")
    sp.add_argument("--clean-base-verified", choices=CLEAN_BASE_VALUES,
                    default="not_claimed")
    sp.add_argument("--force", action="store_true",
                    help="run even when disabled in the deployment config")
    sp.add_argument("--json", action="store_true")

    cp = sub.add_parser("classify", help="classify one transcript file")
    cp.add_argument("--transcript", required=True)
    cp.add_argument("--json", action="store_true")

    up = sub.add_parser("summary",
                        help="share of stamped runs by verify_class")
    up.add_argument("--ledger", default=None)
    up.add_argument("--days", type=int, default=14)
    up.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "classify":
        if not os.path.exists(args.transcript):
            print(f"verify_stamp: no transcript at {args.transcript}",
                  file=sys.stderr)
            return 2
        out = classify_transcript(args.transcript)
        if args.json:
            print(json.dumps(out, indent=1))
        else:
            print(f"{out['verify_class']} "
                  f"(test_segments={out['test_segments']}, "
                  f"clean_base_detected={out['clean_base_detected']})")
        return 0

    if args.cmd == "summary":
        ledger = args.ledger or os.path.join(
            _state_dir(), "verification_ledger.jsonl")
        out = summarise(ledger, args.days)
        if args.json:
            print(json.dumps(out, indent=1))
        else:
            print(f"verification stamps, last {out['days']}d: "
                  f"{out['total']} runs")
            for cls in VERIFY_CLASSES:
                print(f"  {cls:<9} {out['counts'].get(cls, 0):>4}  "
                      f"{out['share_pct'].get(cls, 0.0)}%")
        return 0

    # stamp
    if not args.force and not enabled_in_config():
        print("verify_stamp: disabled in the deployment config "
              "(workers.verification_stamp.enabled is not true) — "
              "no stamp written", file=sys.stderr)
        return 2
    transcripts_dir = args.transcripts_dir or _transcripts_dir()
    ledger = args.ledger or os.path.join(
        _state_dir(), "verification_ledger.jsonl")
    row = build_stamp(args.run_id, transcripts_dir, repo=args.repo,
                      issue=args.issue, pr_url=args.pr_url,
                      collection_commands=args.collection_command,
                      clean_base_verified=args.clean_base_verified)
    append_stamp(ledger, row)
    if args.json:
        print(json.dumps(row, indent=1))
    else:
        print(f"stamped {row['run_id']}: {row['verify_class']} "
              f"(collection: {row['collection_verify_class']}, "
              f"clean_base_verified: {row['clean_base_verified']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
