#!/usr/bin/env python3
"""EDD Parts A+B for the plan-coder harness (EDD spike, Phase 1 —
approved by the operator 2026-11-10 after Phase 0 PASS for Parts
A+B; Part C — fixed case set / held-out partition — FAILED Phase 0
and is NOT built here; see ~/workspace/edd-phase0/RESULTS.md).

Phase 0 in one paragraph: harness-tune judges aggregates (PR rate,
medians, grade distribution) over a moving 7-day denominator, and
on the one change with enough traffic (Goldfish enable,
2026-11-07) the aggregate's 14-point move was sample mix, not
signal. And when a regression does appear, nothing records which
prompt template, rendered cron, code version, or config produced
the baseline runs: rendered-cron sha and config-at-dispatch both
reconstructed 0/30 in Phase 0 Step B. Parts A+B fix attribution
deterministically, with zero LLM tokens:

  fingerprint  Compute the harness fingerprint (Part A): plan-coder
               git sha, prompt template sha256s, rendered cron
               sha256s (from local/cron-manifest.json), agent.py /
               grade_run.py sha256s, a sha256 of the canonicalised
               deployment config, provider_order, tier_model_map.
               The config enters as a HASH plus named non-secret
               fields only: canonicalisation drops every key whose
               name looks secret-bearing before hashing, and no
               config value beyond provider_order / tier_model_map
               is ever emitted.
  capture      Policy-gated. Merge the fingerprint into the
               receipt capture record for a run
               (receipt_capture/<run_id>.json, harness{} block),
               creating a minimal record when receipt capture has
               not run — so the fingerprint lands at dispatch
               whether or not receipts are enabled. The receipt
               assembler copies harness{} into the receipt.
               Phase 0 condition (operator-approved build gate):
               the EDD Phase 0 side finding — receipt_capture/
               absent from the live state dir — was resolved at
               build time: receipts were enabled 2026-11-11T02:45Z
               and no dispatch or terminal disposition had
               occurred between enable and the check; the capture
               tool itself was smoke-verified landing in a temp
               state dir. The capture-landing smoke test in the
               EDD test suite is the standing guard.
  experiment   Policy-gated. Append one line per tune edit bundle
               to edd_experiments.jsonl: {ts, before, after,
               edit_summary, evidence_run_ids, attribution,
               discriminator}. One bundle = one experiment; a pass
               that will not split its edits passes --attribution
               bundled. The optional `discriminator` is the
               per-bundle fingerprint that separates two edits to
               the SAME template inside one pass (the ts of the
               experiment row is the cut a later `edd.py regress`
               runs at, the discriminator says WHICH bundle that
               cut belongs to). It is the sha256 of a canonical
               payload {bundle_id, bundle_sha256, live prompt
               template shas} — so it changes whenever the bundle's
               diff changes or the edited template's content
               changes. Rows written before the discriminator
               existed still parse (the key is optional; readers
               treat a missing discriminator as "not_available").
               The row's ts is the cut a later `regress --cut` runs
               at, and it is unique inside the ledger: two bundles
               logged in the same second get consecutive timestamps
               so each bundle owns its own cut.
  regress      Part B report (read-only, works regardless of the
               enabled flag — auditing after a No-go disable must
               stay possible). Compare worker-ledger windows on
               either side of a cut (a fingerprint change): the
               existing aggregates PLUS the per-case list —
               repo x tier cells whose PR rate fell >= 10 points
               (n >= 3 both sides), one-sided cells, and outcome /
               failure classes that appeared or vanished. Every
               rate carries its n.
  summary      Harness-tune's read: fingerprint coverage over
               receipt capture records, experiment count / latest,
               with n. Read-only, flag-independent.
  experiments  Read-only listing of the experiment ledger: one line
               per bundle with its timestamp (the cut a later
               `regress` runs at) and its discriminator (which
               bundle that cut belongs to). Read-only,
               flag-independent.
  check        Exit 0 + the enabled parts when the spike is on;
               exit 2 when disabled — prompt steps proceed exactly
               as before and do not mention EDD.

Policy (workers.edd in the deployment config) ships DISABLED in
the example config: enabled=false means capture/experiment exit 2
and check exits 2. Enabling (globally or per part) is a separate
operator decision, per the goldfish/jittest/receipt precedent.
EDD output is visibility only: never a merge gate, never a
dispatch gate.

Usage:
    python3 bin/edd.py fingerprint [--config F] [--json]
    python3 bin/edd.py check [--config F] [--json]
    python3 bin/edd.py capture --run-id R [--state-dir D] [--config F] [--json]
    python3 bin/edd.py experiment --summary S [--evidence a,b]
        [--before-file F] [--after-file F] [--attribution split|bundled]
        [--bundle-id ID] [--bundle-diff FILE] [--state-dir D]
        [--config F] [--json]
    python3 bin/edd.py experiments [--since YYYY-MM-DD] [--state-dir D]
        [--config F] [--json]
    python3 bin/edd.py regress --cut TS [--days 7] [--ledger F]
        [--state-dir D] [--json]
    python3 bin/edd.py summary [--since YYYY-MM-DD] [--state-dir D] [--json]

Exit codes: 0 = ok, 1 = bad input, 2 = disabled / cannot proceed
(non-blocking by design: prompt steps log and continue).
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
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config, default_ledger_path

NA = "not_available"
SECRET_KEY = re.compile(r"key|token|secret|password|credential|auth|cookie",
                        re.IGNORECASE)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config(explicit: str | None) -> dict:
    if explicit:
        with open(os.path.expanduser(explicit), encoding="utf-8") as fh:
            return json.load(fh)
    return _load_deployment_config() or {}


def policy_from_config(cfg: dict) -> dict:
    pol = dict(((cfg.get("workers") or {}).get("edd") or {}))
    pol.setdefault("enabled", False)
    pol.setdefault("parts", ["fingerprint", "regress"])
    return pol


def part_enabled(pol: dict, part: str) -> bool:
    return bool(pol.get("enabled")) and part in (pol.get("parts") or [])


def state_dir(explicit: str | None) -> str | None:
    if explicit:
        return os.path.expanduser(explicit)
    ledger = default_ledger_path()
    if ledger:
        return os.path.dirname(ledger)
    cfg = _load_deployment_config() or {}
    d = (cfg.get("state") or {}).get("dir")
    return os.path.expanduser(d) if d else None


def sha256_file(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return NA


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_config(cfg: dict):
    """Config with secret-bearing keys dropped recursively. Returns
    (canonical_obj, dropped_key_names) — values of dropped keys never
    leave this function."""
    dropped = []

    def clean(obj, path=""):
        if isinstance(obj, dict):
            out = {}
            for k in sorted(obj):
                if SECRET_KEY.search(str(k)):
                    dropped.append(f"{path}{k}")
                    continue
                out[k] = clean(obj[k], f"{path}{k}.")
            return out
        if isinstance(obj, list):
            return [clean(v, path) for v in obj]
        return obj

    return clean(cfg), dropped


def live_prompt_shas() -> dict:
    """sha256 of each prompt template as it is on disk RIGHT NOW.

    The live shas are the per-bundle discriminator's third input:
    two edit bundles applied to the same template in one pass land
    on different file contents, so the live shas differ even when
    the pass-wide before/after snapshots do not."""
    out = {}
    for fn in ("backlog-supervisor.md", "pr-shepherd.md", "harness-tune.md"):
        out[fn[:-3]] = sha256_file(os.path.join(REPO_ROOT, "prompts", fn))
    return out


def experiment_discriminator(bundle_id: str | None, bundle_sha256: str | None,
                             prompt_shas: dict) -> str:
    """The per-bundle discriminator for an experiment row.

    sha256 over a canonical payload of (bundle label, the bundle's
    own diff hash, the live prompt template shas). Two bundles of one
    pass differ in at least one input (a different diff, a different
    template state, or a different label), so they get different
    discriminators — that is what lets `edd.py regress` cuts at the
    two experiment timestamps address the two bundles separately."""
    payload = json.dumps({"bundle_id": bundle_id or "",
                          "bundle_sha256": bundle_sha256 or "",
                          "prompt_template_sha256": prompt_shas or {}},
                         sort_keys=True)
    return sha256_text(payload)


def compute_fingerprint(cfg: dict) -> dict:
    workers = cfg.get("workers") or {}
    provider_order = workers.get("provider_order") or []
    providers_cfg = cfg.get("providers") or {}
    tier_model_map = {}
    for name in provider_order:
        tiers = (providers_cfg.get(name) or {}).get("tiers")
        tier_model_map[name] = tiers if tiers else "builtin:agent.py"
    prompts = live_prompt_shas()
    rendered = {}
    manifest_path = os.path.join(REPO_ROOT, "local", "cron-manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        for cron_id, info in (manifest.get("crons") or {}).items():
            rendered[cron_id] = info.get("sha256", NA)
    except (OSError, json.JSONDecodeError):
        rendered = {}
    git_sha = NA
    try:
        p = subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"],
                           check=False, capture_output=True, text=True, timeout=15)
        if p.returncode == 0:
            git_sha = p.stdout.strip()
    except Exception:  # noqa: BLE001 - git unavailable: NA, not a crash
        pass
    canon, _dropped = canonical_config(cfg)
    return {
        "plan_coder_git_sha": git_sha,
        "prompt_template_sha256": prompts,
        "rendered_cron_sha256": rendered or NA,
        "agent_py_sha256": sha256_file(os.path.join(REPO_ROOT, "bin", "agent.py")),
        "grade_run_sha256": sha256_file(os.path.join(REPO_ROOT, "bin", "grade_run.py")),
        "config_sha256": sha256_text(json.dumps(canon, sort_keys=True)),
        "provider_order": provider_order,
        "tier_model_map": tier_model_map,
    }


def atomic_write_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def append_jsonl(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_jsonl(path: str):
    if not os.path.exists(path):
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


# --------------------------------------------------------------- commands

def cmd_fingerprint(args) -> int:
    cfg = load_config(args.config)
    fp = compute_fingerprint(cfg)
    if args.json:
        print(json.dumps(fp, indent=1))
    else:
        print(f"git {fp['plan_coder_git_sha'][:12]}  config {fp['config_sha256'][:12]}  "
              f"providers {','.join(fp['provider_order'])}")
    return 0


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    pol = policy_from_config(cfg)
    if not pol.get("enabled"):
        print("edd: disabled")
        return 2
    out = {"enabled": True, "parts": pol.get("parts")}
    print(json.dumps(out) if args.json else f"edd: enabled parts={out['parts']}")
    return 0


def cmd_capture(args) -> int:
    cfg = load_config(args.config)
    pol = policy_from_config(cfg)
    if not part_enabled(pol, "fingerprint"):
        print("edd: disabled (fingerprint part off)")
        return 2
    sd = state_dir(args.state_dir)
    if not sd:
        print("edd: no state dir resolvable")
        return 2
    fp = compute_fingerprint(cfg)
    cap_dir = os.path.join(sd, "receipt_capture")
    path = os.path.join(cap_dir, f"{args.run_id}.json")
    record = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, json.JSONDecodeError):
            record = {}
    record.setdefault("run_id", args.run_id)
    record["harness"] = fp
    record["harness_captured_ts"] = now_iso()
    atomic_write_json(path, record)
    out = {"run_id": args.run_id, "path": path,
           "merged_into_existing": bool(record.get("brief_sha256"))}
    print(json.dumps(out) if args.json else
          f"edd: fingerprint captured for {args.run_id} -> {path}")
    return 0


def unique_ts(path: str, candidate: str) -> str:
    """A timestamp no other row of the ledger already uses.

    The experiment row's ts IS the cut a later `regress --cut` runs
    at, so two bundles logged inside the same second would share one
    cut and could not be addressed separately. When that happens the
    new row's ts is advanced by one second per collision. The field
    name and format are unchanged (still %Y-%m-%dT%H:%M:%SZ); only the
    value moves, by at most a second or two."""
    used = set()
    for row in load_jsonl(path) or []:
        ts = row.get("ts")
        if isinstance(ts, str):
            used.add(ts)
    if candidate not in used:
        return candidate
    moment = _parse_ts(candidate)
    while candidate in used:
        moment = moment + timedelta(seconds=1)
        candidate = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return candidate


def cmd_experiment(args) -> int:
    cfg = load_config(args.config)
    pol = policy_from_config(cfg)
    if not part_enabled(pol, "fingerprint"):
        print("edd: disabled (fingerprint part off)")
        return 2
    sd = state_dir(args.state_dir)
    if not sd:
        print("edd: no state dir resolvable")
        return 2

    def load_fp(path):
        if not path:
            return None
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return json.load(fh)

    before = load_fp(args.before_file)
    after = load_fp(args.after_file) or compute_fingerprint(cfg)
    path = os.path.join(sd, "edd_experiments.jsonl")
    bundle_id = (args.bundle_id or "").strip() or None
    bundle_sha256 = None
    if args.bundle_diff:
        bundle_sha256 = sha256_file(os.path.expanduser(args.bundle_diff))
    row = {"ts": unique_ts(path, now_iso()), "edit_summary": args.summary,
           "evidence_run_ids": [s for s in (args.evidence or "").split(",") if s],
           "attribution": args.attribution,
           "before": before if before is not None else NA,
           "after": after}
    if bundle_id:
        row["bundle_id"] = bundle_id
    if bundle_sha256:
        row["bundle_sha256"] = bundle_sha256
    row["discriminator"] = experiment_discriminator(
        bundle_id, bundle_sha256, live_prompt_shas())
    append_jsonl(path, row)
    print(json.dumps({"path": path, "ts": row["ts"],
                      "discriminator": row["discriminator"]}) if args.json else
          f"edd: experiment logged -> {path}")
    return 0


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def regress_report(rows, cut: str, days: int) -> dict:
    c = _parse_ts(cut)
    lo = (c - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hi = (c + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    before = [r for r in rows if lo <= (r.get("ts") or "") < cut]
    after = [r for r in rows if cut <= (r.get("ts") or "") < hi]

    def is_pr(r):
        return (r.get("outcome") or "").startswith("pr_opened")

    def rate(rs):
        return (sum(is_pr(r) for r in rs) / len(rs)) if rs else None

    cells = {}
    keys = {(r.get("repo"), r.get("tier")) for r in before + after}
    regressions, improvements, one_sided = [], [], []
    for k in sorted(keys, key=str):
        b = [r for r in before if (r.get("repo"), r.get("tier")) == k]
        a = [r for r in after if (r.get("repo"), r.get("tier")) == k]
        entry = {"cell": f"{k[0]}/{k[1]}", "before_n": len(b),
                 "before_pr": sum(is_pr(r) for r in b),
                 "before_rate": rate(b), "after_n": len(a),
                 "after_pr": sum(is_pr(r) for r in a), "after_rate": rate(a),
                 "before_run_ids": [r.get("run_id") for r in b],
                 "after_run_ids": [r.get("run_id") for r in a]}
        cells[entry["cell"]] = entry
        if not b or not a:
            one_sided.append(entry)
        elif len(b) >= 3 and len(a) >= 3:
            d = entry["after_rate"] - entry["before_rate"]
            if d <= -0.10:
                regressions.append(entry)
            elif d >= 0.10:
                improvements.append(entry)
    cb, ca = Counter(r.get("outcome") for r in before), Counter(r.get("outcome") for r in after)
    return {"cut": cut, "days": days,
            "before": {"window": [lo, cut], "n": len(before), "pr_rate": rate(before)},
            "after": {"window": [cut, hi], "n": len(after), "pr_rate": rate(after)},
            "cells": cells, "regressions": regressions,
            "improvements": improvements, "one_sided_cells": one_sided,
            "classes_new_after": sorted(set(ca) - set(cb)),
            "classes_gone_after": sorted(set(cb) - set(ca)),
            "class_counts_before": dict(cb), "class_counts_after": dict(ca)}


def cmd_experiments(args) -> int:
    """Read-only listing of the experiment ledger, one line per bundle.

    This is the separation aid: each row carries the timestamp a
    `regress --cut` run uses and the discriminator that says WHICH
    bundle that cut belongs to. Rows written before the discriminator
    existed still list (their discriminator reads not_available)."""
    sd = state_dir(args.state_dir)
    if not sd:
        print("edd: no state dir resolvable")
        return 2
    rows = load_jsonl(os.path.join(sd, "edd_experiments.jsonl")) or []
    if args.since:
        rows = [r for r in rows if (r.get("ts") or "") >= args.since]
    out = [{"ts": r.get("ts"), "bundle_id": r.get("bundle_id"),
            "bundle_sha256": r.get("bundle_sha256"),
            "discriminator": r.get("discriminator") or NA,
            "edit_summary": r.get("edit_summary")} for r in rows]
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        for r in out:
            print(f"{r['ts']}  bundle={r['bundle_id'] or NA}  "
                  f"disc={r['discriminator']}  {r['edit_summary']}")
    return 0


def cmd_regress(args) -> int:
    sd = state_dir(args.state_dir)
    ledger = args.ledger or (os.path.join(sd, "worker_ledger.jsonl") if sd else None)
    if not ledger or not os.path.exists(ledger):
        print("edd: worker ledger not found")
        return 1
    rows = load_jsonl(ledger) or []
    rep = regress_report(rows, args.cut, args.days)
    if args.json:
        print(json.dumps(rep, indent=1))
    else:
        b, a = rep["before"], rep["after"]
        print(f"cut {rep['cut']} (±{rep['days']}d): before n={b['n']} "
              f"PR rate={b['pr_rate']} | after n={a['n']} PR rate={a['pr_rate']}")
        for e in rep["regressions"]:
            print(f"  REGRESSED {e['cell']}: {e['before_pr']}/{e['before_n']}="
                  f"{e['before_rate']:.2f} -> {e['after_pr']}/{e['after_n']}={e['after_rate']:.2f}")
        for e in rep["improvements"]:
            print(f"  improved {e['cell']}: {e['before_rate']:.2f} -> {e['after_rate']:.2f} "
                  f"(n {e['before_n']}->{e['after_n']})")
        print(f"  one-sided cells: {[e['cell'] for e in rep['one_sided_cells']]}")
        print(f"  new classes after: {rep['classes_new_after']}")
        print(f"  gone classes after: {rep['classes_gone_after']}")
    return 0


def cmd_summary(args) -> int:
    sd = state_dir(args.state_dir)
    if not sd:
        print("edd: no state dir resolvable")
        return 2
    caps = []
    cap_dir = os.path.join(sd, "receipt_capture")
    if os.path.isdir(cap_dir):
        for fn in sorted(os.listdir(cap_dir)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(cap_dir, fn), encoding="utf-8") as fh:
                        caps.append(json.load(fh))
                except (OSError, json.JSONDecodeError):
                    continue
    with_fp = [c for c in caps if isinstance(c.get("harness"), dict)]
    exps = load_jsonl(os.path.join(sd, "edd_experiments.jsonl")) or []
    if args.since:
        exps = [e for e in exps if (e.get("ts") or "") >= args.since]
    out = {"capture_records": len(caps), "with_harness": len(with_fp),
           "fingerprint_coverage": (len(with_fp) / len(caps)) if caps else None,
           "experiments": len(exps),
           "latest_experiment_ts": exps[-1].get("ts") if exps else None}
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        print(f"edd summary: capture records {out['capture_records']} "
              f"(harness on {out['with_harness']}), experiments {out['experiments']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fingerprint", "check", "capture", "experiment", "regress",
                 "summary", "experiments"):
        p = sub.add_parser(name)
        p.add_argument("--config", default=None)
        p.add_argument("--state-dir", default=None)
        p.add_argument("--json", action="store_true")
    sub.choices["capture"].add_argument("--run-id", required=True)
    ex = sub.choices["experiment"]
    ex.add_argument("--summary", required=True)
    ex.add_argument("--evidence", default="")
    ex.add_argument("--before-file", default=None)
    ex.add_argument("--after-file", default=None)
    ex.add_argument("--attribution", default="split",
                    choices=("split", "bundled"))
    ex.add_argument("--bundle-id", default=None,
                    help="label of this edit bundle (e.g. 'harness-tune.md#2')")
    ex.add_argument("--bundle-diff", default=None,
                    help="file holding this bundle's diff; its sha256 is "
                         "the bundle's own discriminator input")
    rg = sub.choices["regress"]
    rg.add_argument("--cut", required=True)
    rg.add_argument("--days", type=int, default=7)
    rg.add_argument("--ledger", default=None)
    sub.choices["summary"].add_argument("--since", default=None)
    sub.choices["experiments"].add_argument("--since", default=None)
    args = ap.parse_args(argv)
    return {"fingerprint": cmd_fingerprint, "check": cmd_check,
            "capture": cmd_capture, "experiment": cmd_experiment,
            "regress": cmd_regress, "summary": cmd_summary,
            "experiments": cmd_experiments}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
