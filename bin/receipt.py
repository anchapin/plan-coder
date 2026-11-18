#!/usr/bin/env python3
"""Sealed task receipts for the plan-coder harness (receipt spike v2,
approved by the operator 2026-11-10; pattern mined from Eq. 9 of an
X-post harness formalization — the author's own, not an Anthropic
paper; see the spike brief).

Phase 0 retrospective assembly (2026-11-10) scored the v1 design
4/10 complete against its own bar: the joins worked wherever the
source data existed — every missing field was a *capture* failure.
Facts evaporated because they were never written down at the
moment they existed (briefs are deleted after collection, the
goldfish ledger logs reviews only so an absent row is ambiguous,
external merges discovered by the shepherd carried no CI verdict,
some worker rows carry grade: null with no reason). V2 therefore
captures at the source:

  capture   At dispatch the supervisor records the facts that only
            exist then — brief hash, SCOPE paths, criteria flag,
            base + CONTRACT.md hashes, the exact goldfish review
            timestamp for this cycle — to
            receipt_capture/<run_id>.json in the state dir
            (atomic write, fsync'd).
  assemble  At collection (draft) and again at seal time, join the
            four ledgers + the capture record + gh + git into one
            receipt.json per PR. No field is inferred: anything a
            source does not provide is the string "not_available"
            with the reason in gaps[]. The ledgers stay the source
            of truth (ledgers_win_on_conflict, in every receipt).
  seal      At the shepherd's terminal disposition: recompute,
            stamp the shepherd's explicit clean-base verdict, seal
            with a SHA-256 hash of the canonical body (a hash seal,
            NOT a signature — it proves a receipt was not edited
            after sealing, nothing about whether its facts were
            true), append one pointer line to receipts_ledger.jsonl,
            and archive the capture record.
  verify    Recompute a sealed receipt's hash. Works regardless of
            the enabled flag — auditing old receipts after a No-go
            disable must stay possible.
  summary   Aggregate receipts_ledger.jsonl for harness-tune.

Goldfish is a tri-state in the receipt (reviewed / not_triggered /
unavailable), recorded at collection from the supervisor's own
trigger evaluation, with the join on the capture record's exact
review timestamp; capture-less runs fall back to a 24h window
before PR creation (Phase 0: every true join was <= ~1.5h before
the worker row, the one false join >24h stale — a sample heuristic,
recorded in the receipt as join: heuristic_window).

A failed capture/assemble/seal must never block a dispatch, a
merge, or a close: the prompt steps log receipt:unavailable and
proceed, exactly like shepherd_ledger appends.

Policy (workers.receipt in the deployment config) ships DISABLED:
enabled=false means capture/assemble/seal exit 2 and every prompt
step behaves exactly as before. Enabling is a separate operator
decision, per the goldfish/jittest precedent.

Usage:
    python3 bin/receipt.py capture --run-id R --repo S --issue N
        --brief-file F --base-branch B --base-sha H
        [--scope-paths a,b,c] [--has-criteria yes|no]
        [--contract-file F] [--goldfish-review-ts TS]
        [--state-dir D] [--json]
    python3 bin/receipt.py assemble --repo S --pr N
        [--state-dir D] [--gh-json F] [--diff-file F]
        [--goldfish-state reviewed|not_triggered|unavailable]
        [--grade-reason R] [--lessons "line 1" ...] [--json]
    python3 bin/receipt.py seal --repo S --pr N
        --clean-base-verified yes|no|not_claimed
        [--state-dir D] [--gh-json F] [--diff-file F] [--supersede]
        [--json]
    python3 bin/receipt.py verify --file F [--json]
    python3 bin/receipt.py summary [--since YYYY-MM-DD]
        [--state-dir D] [--json]

Exit codes: 0 = ok, 1 = verify mismatch, 2 = disabled / cannot
assemble or seal (non-terminal, already sealed, missing input).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config, default_ledger_path  # noqa: E402

NA = "not_available"
GRADE_REASONS = ("transcript_missing", "grader_error",
                 "pre_grader_run", "not_graded_by_policy")
GOLDFISH_STATES = ("reviewed", "not_triggered", "unavailable")
TERMINAL_DISPOSITIONS = ("merged", "merged_unstable", "closed_unmerged")
GOLDFISH_WINDOW_HOURS = 24


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def policy_from_config() -> dict:
    cfg = _load_deployment_config()
    pol = dict(((cfg.get("workers") or {}).get("receipt") or {}))
    pol.setdefault("enabled", False)
    return pol


def state_dir(explicit: str | None) -> str | None:
    if explicit:
        return os.path.expanduser(explicit)
    ledger = default_ledger_path()
    if ledger:
        return os.path.dirname(ledger)
    cfg = _load_deployment_config()
    d = (cfg.get("state") or {}).get("dir")
    return os.path.expanduser(d) if d else None


def load_jsonl(path: str) -> list | None:
    if not path or not os.path.exists(path):
        return None
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def atomic_write_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def append_jsonl(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def seal_of(receipt: dict) -> str:
    canon = json.dumps({k: v for k, v in receipt.items() if k != "seal"},
                       sort_keys=True, separators=(",", ":"))
    return sha256_text(canon)


def short_repo(repo: str) -> str:
    return str(repo or "").split("/")[-1]


def parse_ts(ts: str):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- capture

def parse_scope_paths(brief_text: str) -> list:
    """Paths named in the brief's SCOPE block (up to the ROOM block)."""
    scope = brief_text
    m = re.search(r"SCOPE:(.*?)(?:\nROOM:|\Z)", brief_text, re.S)
    if m:
        scope = m.group(1)
    paths = []
    for tok in re.findall(r"`([^`]+)`|((?:[\w.-]+/)+[\w.*/-]*)", scope):
        cand = (tok[0] or tok[1] or "").strip()
        if cand and ("/" in cand or cand.endswith(
                (".py", ".md", ".rs", ".yml", ".yaml", ".toml", ".json"))):
            if cand not in paths:
                paths.append(cand)
    return paths


def do_capture(args, sd: str) -> dict:
    with open(args.brief_file, encoding="utf-8") as fh:
        brief_text = fh.read()
    scope = ([p.strip() for p in args.scope_paths.split(",") if p.strip()]
             if args.scope_paths else parse_scope_paths(brief_text))
    contract_hash = NA
    if args.contract_file and os.path.exists(args.contract_file):
        with open(args.contract_file, encoding="utf-8") as fh:
            contract_hash = sha256_text(fh.read())
    record = {
        "run_id": args.run_id, "repo": short_repo(args.repo),
        "issue": args.issue, "ts_dispatch": now_iso(),
        "brief_sha256": sha256_text(brief_text),
        "scope_paths": scope,
        "acceptance_criteria_present": (
            None if args.has_criteria is None else args.has_criteria == "yes"),
        "base_branch": args.base_branch, "base_sha": args.base_sha,
        "contract_md_sha256": contract_hash,
        "goldfish_review_ts": args.goldfish_review_ts or None,
    }
    path = os.path.join(sd, "receipt_capture", f"{args.run_id}.json")
    atomic_write_json(path, record)
    record["_path"] = path
    return record


# ---------------------------------------------------------------- assemble

def gh_pr_view(slug: str, pr: int, gh_json: str | None) -> dict:
    if gh_json:
        with open(gh_json, encoding="utf-8") as fh:
            return json.load(fh)
    url = f"https://github.com/{slug}/pull/{pr}"
    out = subprocess.run(
        ["gh", "pr", "view", url, "--json",
         "number,title,state,url,baseRefName,headRefName,baseRefOid,"
         "headRefOid,mergedAt,mergeCommit,files,additions,deletions,createdAt"],
        capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"gh pr view failed for {url}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def gh_diff(slug: str, pr: int, diff_file: str | None) -> str:
    if diff_file:
        with open(diff_file, encoding="utf-8") as fh:
            return fh.read()
    url = f"https://github.com/{slug}/pull/{pr}"
    out = subprocess.run(["gh", "pr", "diff", url],
                         capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        return ""
    return out.stdout


def find_capture(sd: str, run_id: str | None) -> dict | None:
    if not run_id:
        return None
    path = os.path.join(sd, "receipt_capture", f"{run_id}.json")
    if not os.path.exists(path):
        path = os.path.join(sd, "receipt_capture", "sealed", f"{run_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def join_goldfish(grows: list, repo: str, issue, capture: dict | None,
                  worker_ts: str, pr_created: str,
                  forced_state: str | None, gaps: list) -> dict:
    """Return the reviews.goldfish block per v2 §2/§3."""
    if forced_state and forced_state != "reviewed":
        return {"state": forced_state, "join": "none"}
    rows = [r for r in (grows or [])
            if short_repo(r.get("repo", "")) == repo
            and r.get("issue") == issue]
    picked, join = None, "none"
    if capture and capture.get("goldfish_review_ts"):
        for r in rows:
            if r.get("ts") == capture["goldfish_review_ts"]:
                picked, join = r, "exact"
                break
        if picked is None:
            gaps.append("reviews.goldfish: capture names a review ts "
                        "with no matching goldfish ledger row")
    if picked is None and (forced_state == "reviewed" or capture is None
                           or not (capture and capture.get("goldfish_review_ts"))):
        anchor = parse_ts(worker_ts) or parse_ts(pr_created)
        if anchor and rows:
            lo = anchor - timedelta(hours=GOLDFISH_WINDOW_HOURS)
            hi = parse_ts(pr_created) or anchor
            in_window = [r for r in rows
                         if (parse_ts(r.get("ts") or "") or lo) >= lo
                         and (parse_ts(r.get("ts") or "") or hi) <= hi]
            if in_window:
                picked, join = in_window[-1], "heuristic_window"
            elif rows and forced_state != "reviewed":
                gaps.append("reviews.goldfish: goldfish rows exist for "
                            "repo+issue but all outside the 24h join "
                            "window (stale; not joined)")
    if picked is None:
        if forced_state == "reviewed":
            gaps.append("reviews.goldfish: state recorded as reviewed "
                        "but no row joined")
        elif grows is None:
            gaps.append("reviews.goldfish: goldfish ledger absent")
        else:
            gaps.append("reviews.goldfish: no row joined and no "
                        "tri-state recorded (ambiguous absence, v1 gap)")
        return {"state": NA, "join": join}
    block = {"state": "reviewed", "join": join,
             "trigger": picked.get("trigger"), "findings": picked.get("findings"),
             "blockers": picked.get("blockers"),
             "dispositions": picked.get("dispositions"),
             "brief_changed": picked.get("brief_changed"),
             "tokens_out": picked.get("tokens_out")}
    return block


def do_assemble(args, sd: str, allow_sealed: bool = False) -> dict:
    slug = args.repo if "/" in args.repo else args.repo
    rs = short_repo(args.repo)
    pr = args.pr
    workers = load_jsonl(os.path.join(sd, "worker_ledger.jsonl")) or []
    shepherd = load_jsonl(os.path.join(sd, "shepherd_ledger.jsonl")) or []
    goldfish = load_jsonl(os.path.join(sd, "goldfish_ledger.jsonl"))
    jittest = load_jsonl(os.path.join(sd, "jittest_ledger.jsonl"))
    # slug for gh: prefer the pr_url's slug from a ledger row, else the
    # worker ledger's convention is short names; gh accepts the URL form
    # only with a slug — assemble callers pass --repo as a slug.
    gh = gh_pr_view(slug, pr, args.gh_json)
    url = gh.get("url") or f"https://github.com/{slug}/pull/{pr}"
    gaps: list = []

    wrows = [r for r in workers
             if (r.get("pr_url") or "").rstrip("/") == url.rstrip("/")]
    w = wrows[-1] if wrows else None
    srows = sorted([r for r in shepherd
                    if short_repo(r.get("repo", "")) == rs
                    and r.get("pr") == pr],
                   key=lambda r: r.get("ts", ""))
    origin = ("supervisor_run" if w else
              "shepherd_discovered" if srows else "manual_or_pre_ledger")
    if args.origin:
        origin = args.origin
    if not w:
        gaps.append(f"execution: no worker_ledger row for this PR "
                    f"(origin {origin}; block honestly empty)")

    capture = find_capture(sd, (w or {}).get("run_id"))
    issue = (w or {}).get("issue")
    if issue is None:
        m = re.search(r"#(\d+)", gh.get("title") or "")
        if m:
            issue = int(m.group(1))
            gaps.append("identity.issue: from PR title (no worker row)")

    # contract block
    if capture:
        contract = {"brief_sha256": capture.get("brief_sha256", NA),
                    "contract_md_sha256": capture.get("contract_md_sha256", NA),
                    "scope_paths": capture.get("scope_paths", NA),
                    "acceptance_criteria_present":
                        capture.get("acceptance_criteria_present"),
                    "capture": "exact"}
    else:
        contract = {"brief_sha256": NA, "contract_md_sha256": NA,
                    "scope_paths": NA, "acceptance_criteria_present": NA,
                    "capture": "degraded_no_capture_record"}
        gaps.append("contract: no capture record (pre-v2 or desktop "
                    "run); brief facts not_available")

    # harness block (EDD spike Part A): the fingerprint edd.py capture
    # merged into the capture record at dispatch; NA + gap when absent.
    if capture and isinstance(capture.get("harness"), dict):
        harness = capture["harness"]
    else:
        harness = NA
        gaps.append("harness: no edd fingerprint in capture record "
                    "(pre-EDD run or edd capture did not run)")

    # execution block
    if w:
        grade = w.get("grade")
        grade_reason = args.grade_reason
        if grade is None:
            if not grade_reason:
                gaps.append("execution.grade: worker row grade is null "
                            "and no grade_reason recorded")
            grade_val = NA
        else:
            grade_val = grade
        execution = {"run_id": w.get("run_id"), "tier": w.get("tier"),
                     "providers_used": w.get("providers_used"),
                     "tokens_in": w.get("tokens_in"),
                     "tokens_out": w.get("tokens_out"),
                     "steps": w.get("steps"), "grade": grade_val,
                     "grade_reason": grade_reason,
                     "gate_triggers": w.get("gate_triggers") or "",
                     "worker_outcome": w.get("outcome")}
    else:
        execution = {k: NA for k in ("run_id", "tier", "providers_used",
                                     "tokens_in", "tokens_out", "steps",
                                     "grade", "gate_triggers",
                                     "worker_outcome")}
        execution["grade_reason"] = NA

    gf_block = join_goldfish(goldfish or [], rs, issue, capture,
                             (w or {}).get("ts", ""), gh.get("createdAt", ""),
                             args.goldfish_state, gaps)
    if goldfish is None:
        gaps.append("reviews.goldfish: goldfish_ledger.jsonl absent "
                    "(systemic)")

    if jittest is None:
        jt_block = NA
        gaps.append("reviews.jittest: jittest_ledger.jsonl absent from "
                    "state dir (systemic)")
    else:
        jrows = [r for r in jittest
                 if short_repo(r.get("repo", "")) == rs
                 and r.get("pr") == pr]
        if jrows:
            r = jrows[-1]
            jt_block = {"trigger": r.get("trigger"),
                        "survived": r.get("survived"),
                        "three_state_class": r.get("three_state_class"),
                        "judge": r.get("judge"),
                        "alex_disposition": r.get("alex_disposition"),
                        "outcome": r.get("outcome")}
        else:
            jt_block = NA
            gaps.append("reviews.jittest: no row for this PR")

    merged_rows = [r for r in srows
                   if r.get("action") in ("merged", "merged_by_shepherd")]
    last_merge = merged_rows[-1] if merged_rows else None
    merge_state = (last_merge or {}).get("merge_state") or ""
    external = bool(last_merge and "external" in str(
        (last_merge or {}).get("outcome", "")))
    if gh.get("state") == "MERGED" and not merge_state:
        gaps.append("verification.merge_state: merged but the shepherd "
                    "row carries no merge_state (external merge without "
                    "the v2 discovery capture)")
    claim_text = " ".join(str(r.get("outcome", "")) + " "
                          + str(r.get("detail", "")) for r in srows)
    claimed = []
    if merge_state == "UNSTABLE" or "exempt" in claim_text \
            or "pre-existing" in claim_text:
        claimed = ["UNSTABLE/exempt-only fails per shepherd rows "
                   "(checks not itemized in ledger)"]
    clean_base = ("not_claimed" if not claimed else
                  "yes" if re.search(r"clean (develop|base|main)",
                                      claim_text) else "no")
    decision_src = last_merge or (srows[-1] if srows else {})
    if gh.get("state") == "MERGED":
        disposition = ("merged_unstable" if merge_state == "UNSTABLE"
                       else "merged")
    elif gh.get("state") == "CLOSED":
        disposition = "closed_unmerged"
    elif any(r.get("action") == "needs_alex" for r in srows):
        disposition = "pending_needs_alex"
    else:
        disposition = "open"

    diff_text = gh_diff(slug, pr, args.diff_file)
    receipt = {
        "schema_version": 2,
        "source_of_truth": "ledgers_win_on_conflict",
        "receipt_id": f"{rs}-{pr}-{(w or {}).get('run_id', NA)}",
        "status": "draft", "version": 1, "supersedes": None,
        "ts_created": now_iso(), "ts_sealed": None,
        "identity": {"repo": rs, "issue": issue if issue is not None else NA,
                     "pr": pr, "pr_url": url,
                     "branch": gh.get("headRefName") or NA,
                     "base_branch": gh.get("baseRefName") or NA,
                     "base_sha": gh.get("baseRefOid") or NA,
                     "head_sha": gh.get("headRefOid") or NA,
                     "origin": origin},
        "contract": contract,
        "harness": harness,
        "execution": execution,
        "reviews": {"goldfish": gf_block, "jittest": jt_block,
                    "contract_touch": any(
                        "contract" in str(r.get("detail", ""))
                        for r in srows)},
        "verification": {
            "ci_summary": decision_src.get("ci") or
            decision_src.get("outcome") or NA,
            "ci_source": ("gh_at_discovery" if external else
                          "shepherd_at_merge" if last_merge else NA),
            "external_merge": external,
            "merge_state": merge_state if merge_state else NA,
            "preexisting_failures_claimed": claimed,
            "clean_base_verified": clean_base},
        "diff": {"diff_sha256": sha256_text(diff_text) if diff_text else NA,
                 "files_changed": [f["path"] for f in gh.get("files") or []] or NA,
                 "insertions": gh.get("additions"),
                 "deletions": gh.get("deletions")},
        "decision": {
            "shepherd_actions": [{"action": r.get("action"),
                                  "ts": r.get("ts"),
                                  "outcome": r.get("outcome")} for r in srows],
            "reruns": sum(1 for r in srows if r.get("action") == "rerun"),
            "fix_attempts": sum(1 for r in srows
                                if r.get("action") == "fix_attempt"),
            "disposition": disposition,
            "merge_method": "--merge" if gh.get("state") == "MERGED" else "",
            "merged_at": gh.get("mergedAt") or NA,
            "merge_commit_sha": (gh.get("mergeCommit") or {}).get("oid") or NA,
            "hard_stop_reason": next(
                (str(r.get("detail") or r.get("outcome")) for r in reversed(srows)
                 if r.get("action") == "needs_alex"), "")},
        "lessons_proposed": list(args.lessons or []),
        "gaps": gaps,
    }
    path = os.path.join(sd, "receipts", rs, f"{pr}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            old = json.load(fh)
        if old.get("status") == "sealed" and not allow_sealed:
            raise RuntimeError(
                "receipt already sealed; re-assembly refused "
                "(use seal --supersede)")
        receipt["version"] = old.get("version", 1)
        receipt["ts_created"] = old.get("ts_created", receipt["ts_created"])
    atomic_write_json(path, receipt)
    receipt["_path"] = path
    return receipt


# ---------------------------------------------------------------- seal

def do_seal(args, sd: str) -> dict:
    rs = short_repo(args.repo)
    path = os.path.join(sd, "receipts", rs, f"{args.pr}.json")
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            old = json.load(fh)
    receipt = do_assemble(args, sd, allow_sealed=args.supersede)
    receipt.pop("_path", None)
    if old and old.get("status") == "sealed":
        if not args.supersede:
            raise RuntimeError("receipt already sealed (use --supersede)")
        receipt["version"] = old.get("version", 1) + 1
        receipt["supersedes"] = (old.get("seal") or {}).get("sha256")
    disp = receipt["decision"]["disposition"]
    if disp not in TERMINAL_DISPOSITIONS:
        raise RuntimeError(f"refusing to seal non-terminal PR "
                           f"(disposition {disp})")
    receipt["verification"]["clean_base_verified"] = args.clean_base_verified
    receipt["status"] = "sealed"
    receipt["ts_sealed"] = now_iso()
    receipt["seal"] = {"sha256": seal_of(receipt), "sealed_by": "pr-shepherd"}
    atomic_write_json(path, receipt)
    append_jsonl(os.path.join(sd, "receipts_ledger.jsonl"), {
        "ts": receipt["ts_sealed"], "repo": rs,
        "issue": receipt["identity"]["issue"], "pr": args.pr,
        "receipt_id": receipt["receipt_id"],
        "seal_sha256": receipt["seal"]["sha256"],
        "disposition": disp,
        "merge_state": receipt["verification"]["merge_state"],
        "grade": receipt["execution"]["grade"],
        "origin": receipt["identity"]["origin"],
        "gaps": len(receipt["gaps"])})
    run_id = receipt["execution"].get("run_id")
    if run_id and run_id != NA:
        src = os.path.join(sd, "receipt_capture", f"{run_id}.json")
        if os.path.exists(src):
            dst = os.path.join(sd, "receipt_capture", "sealed",
                               f"{run_id}.json")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
    receipt["_path"] = path
    return receipt


# ---------------------------------------------------------------- verify / summary

def do_verify(path: str) -> tuple[bool, dict]:
    with open(path, encoding="utf-8") as fh:
        receipt = json.load(fh)
    stored = (receipt.get("seal") or {}).get("sha256")
    return (stored is not None and stored == seal_of(receipt)), receipt


def do_summary(sd: str, since: str | None) -> dict:
    rows = load_jsonl(os.path.join(sd, "receipts_ledger.jsonl")) or []
    if since:
        rows = [r for r in rows if str(r.get("ts", ""))[:10] >= since]
    return {"rows": len(rows),
            "by_disposition": dict(Counter(r.get("disposition") for r in rows)),
            "by_origin": dict(Counter(r.get("origin") for r in rows)),
            "by_merge_state": dict(Counter(r.get("merge_state") for r in rows)),
            "total_gaps": sum(r.get("gaps") or 0 for r in rows)}


# ---------------------------------------------------------------- main

# ---------------------------------------------------------------------------
# Proof receipts (proof-packet issue #8, from plan-coder main): merged into
# this file at the 2026-11-17 sync — main and the live harness had evolved
# two different receipt.py tools under one name. check/record classify and
# log PR proof coverage; summary --ledger reports it. The sealed-receipt
# commands above (capture/assemble/seal/verify, summary without --ledger)
# are the receipt-spike v2 tool. Neither half gates the other.
PROOF_TYPES = ("visual", "numeric", "docs")
PRESENT = "present"
GAPS = "gaps"
ABSENT = "absent"
NOT_REQUIRED = "not_required"


def default_proof_ledger_path() -> str | None:
    worker = default_ledger_path()
    if not worker:
        return None
    return os.path.join(os.path.dirname(worker), "receipts.jsonl")


def _proof_items(value: str | None) -> list[str]:
    """Comma-separated (or newline-separated) item list -> clean list."""
    if not value:
        return []
    out = []
    for raw in value.replace("\n", ",").split(","):
        item = raw.strip()
        if item:
            out.append(item)
    return out


def _proof_base_item(entry: str) -> str:
    """'after screenshot (no display)' -> 'after screenshot'."""
    return entry.split(" (", 1)[0].strip()


def classify_proof(required: list[str], present: list[str],
             not_available: list[str] | None = None) -> dict:
    """Decide the proof status of one PR from its required/present items."""
    not_available = list(not_available or [])
    unavailable_items = {_proof_base_item(e) for e in not_available}
    present_items = [_proof_base_item(e) for e in present
                     if _proof_base_item(e) not in unavailable_items]
    present_set = set(present_items)

    gaps = []
    for item in required:
        if item in present_set:
            continue
        reason = next((e for e in not_available
                       if _proof_base_item(e) == item), None)
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


def append_proof_row(ledger_path: str, row: dict) -> dict:
    row = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           **row}
    os.makedirs(os.path.dirname(os.path.abspath(ledger_path)),
                exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return row


def read_proof_rows(ledger_path: str) -> list[dict]:
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


def summarize_proof(rows: list[dict], since: str | None = None,
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
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("capture")
    p.add_argument("--run-id", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--brief-file", required=True)
    p.add_argument("--base-branch", required=True)
    p.add_argument("--base-sha", required=True)
    p.add_argument("--scope-paths", default=None)
    p.add_argument("--has-criteria", choices=("yes", "no"), default=None)
    p.add_argument("--contract-file", default=None)
    p.add_argument("--goldfish-review-ts", default=None)
    p.add_argument("--state-dir", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("assemble")
    p.add_argument("--repo", required=True, help="repo slug, e.g. you/alpha")
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--state-dir", default=None)
    p.add_argument("--gh-json", default=None)
    p.add_argument("--diff-file", default=None)
    p.add_argument("--origin", default=None,
                   choices=("supervisor_run", "shepherd_discovered",
                            "manual_or_pre_ledger"))
    p.add_argument("--goldfish-state", default=None, choices=GOLDFISH_STATES)
    p.add_argument("--grade-reason", default=None)
    p.add_argument("--lessons", action="append", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("seal")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--clean-base-verified", required=True,
                   choices=("yes", "no", "not_claimed"))
    p.add_argument("--state-dir", default=None)
    p.add_argument("--gh-json", default=None)
    p.add_argument("--diff-file", default=None)
    p.add_argument("--origin", default=None,
                   choices=("supervisor_run", "shepherd_discovered",
                            "manual_or_pre_ledger"))
    p.add_argument("--goldfish-state", default=None, choices=GOLDFISH_STATES)
    p.add_argument("--grade-reason", default=None)
    p.add_argument("--lessons", action="append", default=None)
    p.add_argument("--supersede", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("verify")
    p.add_argument("--file", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("summary")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--state-dir", default=None)
    p.add_argument("--ledger", default=None,
                   help="proof-receipt ledger: summary then reports proof coverage")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("check")
    p.add_argument("--required", default="")
    p.add_argument("--present", default="")
    p.add_argument("--not-available", default="")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("record")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--url", default="")
    p.add_argument("--type", default="", choices=PROOF_TYPES)
    p.add_argument("--required", default="")
    p.add_argument("--present", default="")
    p.add_argument("--not-available", default="")
    p.add_argument("--ledger", default=None)
    p.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "verify":
        ok, receipt = do_verify(args.file)
        if args.json:
            print(json.dumps({"ok": ok, "file": args.file,
                              "receipt_id": receipt.get("receipt_id")}))
        else:
            print(f"{'OK' if ok else 'MISMATCH'}: {args.file}")
        return 0 if ok else 1

    if args.cmd == "check":
        result = classify_proof(_proof_items(args.required),
                                _proof_items(args.present),
                                _proof_items(args.not_available))
        if args.json:
            print(json.dumps(result, indent=1))
        else:
            print(f"proof: {result['proof']}")
            for line in result["gap_lines"]:
                print(line)
        return 0

    if args.cmd == "record" or (args.cmd == "summary" and args.ledger):
        path = args.ledger or default_proof_ledger_path()
        if not path:
            print("receipt: no ledger path (no deployment config)", file=sys.stderr)
            return 2
        if args.cmd == "record":
            result = classify_proof(_proof_items(args.required),
                                    _proof_items(args.present),
                                    _proof_items(args.not_available))
            row = append_proof_row(path, {
                "repo": args.repo, "pr": args.pr, "url": args.url,
                "type": args.type, "proof": result["proof"],
                "required": result["required"], "present": result["present"],
                "not_available": result["not_available"],
                "gaps": result["gaps"], "gap_lines": result["gap_lines"]})
            if args.json:
                print(json.dumps(row, indent=1))
            else:
                print(f"RECORDED: {row['repo']}#{row['pr']} proof={row['proof']} -> {path}")
                for line in row["gap_lines"]:
                    print(line)
            return 0
        if not os.path.exists(path):
            print(f"receipt: ledger not found: {path}", file=sys.stderr)
            return 2
        result = summarize_proof(read_proof_rows(path), since=args.since,
                                 until=args.until)
        result["ledger"] = path
        result["since"] = args.since
        result["until"] = args.until
        if args.json:
            print(json.dumps(result, indent=1))
        else:
            print(f"Proof receipts: {result['prs']} PR(s) in {path}")
            print(f"  by proof: {result['by_proof']}")
            print(f"  required-proof PRs: {result['required_prs']}; "
                  f"fully proved: {result['present_prs']} (coverage: {result['coverage_pct']}%)")
        return 0

    if args.cmd == "summary":
        sd = state_dir(args.state_dir)
        if not sd:
            print("receipt: no state dir", file=sys.stderr)
            return 2
        result = do_summary(sd, args.since)
        print(json.dumps(result, indent=1) if args.json else
              f"Receipts index: {result['rows']} sealed; "
              f"by disposition {result['by_disposition']}; "
              f"by origin {result['by_origin']}; "
              f"total gaps {result['total_gaps']}")
        return 0

    policy = policy_from_config()
    if policy.get("enabled") is not True:
        print("receipt: disabled (workers.receipt.enabled is not true) — "
              "proceed exactly as before", file=sys.stderr)
        return 2
    sd = state_dir(args.state_dir)
    if not sd:
        print("receipt: no state dir (no deployment config)", file=sys.stderr)
        return 2
    try:
        if args.cmd == "capture":
            result = do_capture(args, sd)
        elif args.cmd == "assemble":
            if args.grade_reason and args.grade_reason not in GRADE_REASONS \
                    and not args.grade_reason.startswith("not_graded_by_policy:"):
                print(f"receipt: grade_reason {args.grade_reason!r} not in "
                      f"the closed set {GRADE_REASONS} (+ not_graded_by_policy:<which>)",
                      file=sys.stderr)
                return 2
            result = do_assemble(args, sd)
        elif args.cmd == "seal":
            result = do_seal(args, sd)
        else:
            return 2
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"receipt: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        print(f"{args.cmd.upper()}D: {result.get('receipt_id', args.run_id if args.cmd == 'capture' else '')} -> {result.get('_path', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
