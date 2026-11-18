#!/usr/bin/env python3
"""Refresh the Leviathan lookup index over the plan-coder ledgers + transcripts.

Leviathan trial Phase 1 (2026-11-13): the index at <state>/leviathan/index.db
accelerates harness-tune ledger/transcript lookups (measured: median 444
tokens per lookup vs 2,887 for two-stage ripgrep at the same 94% hit rate).
It is a view, never a source: the ledgers and transcripts win any conflict.

What this does:
  1. Secret-shape scan of every source file. A hit in a LEDGER aborts the
     refresh (exit 2) — a secret in a ledger is an incident, not a skip.
     A hit in a transcript excludes that one file (named in the output):
     the data index has no ingest redaction, so nothing secret-shaped is
     ever copied into the corpus.
  2. Copies the five ledgers and the clean transcripts into
     <state>/leviathan/corpus/ (fresh snapshot each run).
  3. Rebuilds the index with the pinned binary + config beside it.

Usage: python3 bin/leviathan_refresh.py [--state-dir DIR] [--json]
Exit: 0 refreshed (or already current), 2 secret-shape hit in a ledger,
      1 any other failure.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys

def _load_deployment_config():
    """Same search order as trace_summary.py / agent.py: $PLAN_CODER_CONFIG,
    then config/deployment.local.json next to the repo root, then the public
    clone's config (when this copy runs from the deployed skill dir), then
    ~/.plan-coder/deployment.json. No deployment paths are hard-coded here:
    the publish gate scans this file for internal identifiers."""
    candidates = []
    env_path = os.environ.get("PLAN_CODER_CONFIG")
    if env_path:
        candidates.append(env_path)
    repo_root = os.path.dirname(os.path.dirname(
        os.path.realpath(os.path.abspath(__file__))))
    candidates.append(os.path.join(
        repo_root, "config", "deployment.local.json"))
    candidates.append(os.path.expanduser(
        "~/workspace/plan-coder-repo/config/deployment.local.json"))
    candidates.append(os.path.expanduser("~/.plan-coder/deployment.json"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and (data.get("state") or {}).get("dir"):
                return data
        except Exception:  # noqa: BLE001 - config is a convenience
            continue
    return {}


def resolve_state_dir(explicit):
    if explicit:
        return os.path.expanduser(explicit)
    cfg = _load_deployment_config()
    return os.path.expanduser(
        ((cfg.get("state") or {}).get("dir")) or "~/.plan-coder/state")


LEDGERS = ["worker_ledger.jsonl", "shepherd_ledger.jsonl", "receipts_ledger.jsonl",
           "goldfish_ledger.jsonl", "jittest_ledger.jsonl"]
SECRET_PATTERNS = {
    "sk-key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "gh-token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "aws-key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{20,}\."),
    "password-assign": re.compile(r"(?i)password\s*[:=]\s*\S{6,}"),
    "bearer": re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
    "private-key": re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
}


def scan_file(path):
    hits = []
    with open(path, errors="ignore") as f:
        for line in f:
            for name, rx in SECRET_PATTERNS.items():
                if rx.search(line):
                    hits.append(name)
    return sorted(set(hits))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=None,
                    help="default: the deployment config's state.dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    state = resolve_state_dir(args.state_dir)
    lev_dir = os.path.join(state, "leviathan")
    binary = os.path.join(lev_dir, "leviathan")
    config = os.path.join(lev_dir, "leviathan.toml")
    index = os.path.join(lev_dir, "index.db")
    corpus = os.path.join(lev_dir, "corpus")
    for p in (binary, config):
        if not os.path.exists(p):
            print(f"missing: {p}", file=sys.stderr)
            return 1

    # Serialize builders (the shepherd refreshes on a 30-minute cadence and
    # can collide with the weekly tune pass): a second refresh while one is
    # running is a no-op success — the existing index stays in place and the
    # running refresh leaves a fresh one. Searches only ever read index.db,
    # which is swapped atomically below, so readers never see a partial build.
    lock_fd = open(os.path.join(lev_dir, "refresh.lock"), "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        msg = "leviathan refresh already in progress; existing index left in place"
        print(json.dumps({"skipped": "locked"}) if args.json else msg)
        return 0

    # 1. scan
    excluded = []
    for name in LEDGERS:
        hits = scan_file(os.path.join(state, name))
        if hits:
            print(f"ABORT: secret-shape {hits} in ledger {name}; not indexing",
                  file=sys.stderr)
            return 2
    tr_src = os.path.join(state, "transcripts")
    clean = []
    for name in sorted(os.listdir(tr_src)):
        if not name.endswith(".jsonl"):
            continue
        hits = scan_file(os.path.join(tr_src, name))
        if hits:
            excluded.append({"file": name, "patterns": hits})
        else:
            clean.append(name)

    # 2. snapshot copies
    if os.path.exists(corpus):
        shutil.rmtree(corpus)
    os.makedirs(os.path.join(corpus, "ledgers"))
    os.makedirs(os.path.join(corpus, "transcripts"))
    for name in LEDGERS:
        shutil.copy(os.path.join(state, name), os.path.join(corpus, "ledgers", name))
    for name in clean:
        shutil.copy(os.path.join(tr_src, name), os.path.join(corpus, "transcripts", name))

    # 3. build to a temp file, then atomically swap into place
    tmp_index = index + ".tmp"
    if os.path.exists(tmp_index):
        os.remove(tmp_index)
    p = subprocess.run([binary, "--index", tmp_index, "index", "-c", config],
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout[-1500:], file=sys.stderr)
        print(p.stderr[-1500:], file=sys.stderr)
        return 1
    os.replace(tmp_index, index)
    m = re.search(r"now ([\d,]+) records", p.stdout)
    summary = {
        "records": int(m.group(1).replace(",", "")) if m else None,
        "transcripts_indexed": len(clean),
        "transcripts_excluded_secret_shape": excluded,
        "index_bytes": os.path.getsize(index) if os.path.exists(index) else None,
        "build_report_tail": (p.stdout.strip().splitlines() or [""])[-6:],
    }
    if args.json:
        print(json.dumps(summary, indent=1))
    else:
        print(f"leviathan index refreshed: {summary['records']} records, "
              f"{summary['transcripts_indexed']} transcripts, "
              f"{len(excluded)} excluded (secret shape)")
        for e in excluded:
            print(f"  excluded: {e['file']} ({', '.join(e['patterns'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
