#!/usr/bin/env python3
"""Publish gate for the plan-coder public repo.

Since the 2026-10-24 unification refactor, the live harness RUNS the
repo's code: the live skill directories' bin/agent.py and
quick_check.py are symlinks into this repo's bin/. There is no
translation step anymore. This gate lives in the repo it publishes
(bin/sync_public.py) so a clone is self-contained.

This script is the fail-closed gate between the live working tree and
the public remote:

1. Scan the whole public tree for internal identifiers and secret
   shapes. Any hit aborts before commit. (Credential *labels* such as
   "custom.zai" are configuration names, not secrets, and are allowed;
   key material never appears in this repo by design.)
2. py_compile every Python file under bin/.
3. Report whether the live sources behind the hand-generalized docs /
   prompt templates (OPERATIONS.md) changed since the last run
   (`docs_may_be_stale`) — templates are refreshed by the tune agent,
   never auto-generated.
4. If the tree is dirty: commit (dated message naming the changed
   files) and push via the normal OAuth git remote. No workflow files
   are ever involved, so no PAT is needed.

Usage: sync_public.py [--dry-run] [--no-push] [--message "..."]
State: local/sync_state.json in the repo (gitignored; never published).
Exit codes: 0 ok (published or no changes), 2 aborted (scan/compile),
3 push failed after a local commit was made.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
STATE = os.path.join(REPO, "local", "sync_state.json")


def _doc_sources() -> list[str]:
    """Live sources behind the hand-generalized docs, from the
    deployment config (publish_gate.doc_sources). Empty when no
    config or no sources are configured — the staleness signal is
    then simply off."""
    candidates = []
    env_path = os.environ.get("PLAN_CODER_CONFIG")
    if env_path:
        candidates.append(env_path)
    candidates.append(os.path.join(REPO, "config", "deployment.local.json"))
    candidates.append(os.path.expanduser("~/.plan-coder/deployment.json"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
            sources = (cfg.get("publish_gate") or {}).get("doc_sources") or []
            return [os.path.expanduser(s) for s in sources]
        except Exception:  # noqa: BLE001
            continue
    return []

# The forbidden literals are assembled from fragments on purpose:
# this script lives inside the tree it scans, and the scan must cover
# every published file — including this one — with no exemptions.
FORBIDDEN = [
    "/home/" + "hatch", "/opt/" + "hatch", "token-plan-coding-" + "manager",
    "bf353" + "87", "451bd" + "38f",
    "flux" + "ion", "staging-" + "studio", "match" + "line",
    "planar-" + "nexus", "armored-" + "archer",
]
OWNER_SLUG = "ancha" + "pin"
OWNER_NAME = "Al" + "ex"
SECRET_SHAPES = [
    r"sk-[A-Za-z0-9_-]{10,}", r"ghp_[A-Za-z0-9]{10,}",
    r"github_pat_[A-Za-z0-9]{10,}", r"AKIA[0-9A-Z]{16}",
    r"BEGIN [A-Z ]*PRIVATE KEY",
]
ALLOWED_NAME_FILES = {"LICENSE"}  # carries the copyright holder's name

DOC_SOURCES = _doc_sources()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def scan_tree() -> list[str]:
    # Scan exactly what git would publish: tracked files plus untracked
    # files NOT covered by .gitignore. Gitignored deployment files
    # (config/deployment.local.json, local/ renders) legitimately contain
    # the deployment's real values and must never be scanned or committed.
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO, capture_output=True, text=True)
    files = [f for f in listed.stdout.split("\0") if f]
    hits = []
    for rel in files:
        fn = os.path.basename(rel)
        path = os.path.join(REPO, rel)
        if True:
            try:
                text = open(path, encoding="utf-8", errors="strict").read()
            except (UnicodeDecodeError, OSError):
                continue
            for bad in FORBIDDEN:
                if bad.lower() in text.lower():
                    hits.append(f"{path}: forbidden string {bad!r}")
            for pat in SECRET_SHAPES:
                if re.search(pat, text):
                    hits.append(f"{path}: secret shape {pat}")
            if OWNER_SLUG in text and fn not in ALLOWED_NAME_FILES:
                hits.append(f"{path}: contains the repo owner's name outside LICENSE")
            if OWNER_NAME in text and fn not in ALLOWED_NAME_FILES:
                hits.append(f"{path}: contains the repo owner's first name")
    return hits


def run(cmd, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def main() -> int:
    dry = "--dry-run" in sys.argv
    push = "--no-push" not in sys.argv
    message = None
    if "--message" in sys.argv:
        message = sys.argv[sys.argv.index("--message") + 1]

    # 1. Scan (fail closed).
    hits = scan_tree()
    if hits:
        print("ABORT: public-tree scan hits:")
        print("\n".join(hits))
        return 2

    # 2. Compile every Python file in bin/.
    bin_dir = os.path.join(REPO, "bin")
    for fn in sorted(os.listdir(bin_dir)):
        if fn.endswith(".py"):
            r = run([sys.executable, "-m", "py_compile", os.path.join(bin_dir, fn)])
            if r.returncode != 0:
                print(f"ABORT: py_compile failed for bin/{fn}:\n{r.stderr}")
                return 2

    # 2b. Run the repo's test suite when present — a failing suite
    # means the code being published is broken; abort like a scan hit.
    tests_dir = os.path.join(REPO, "tests")
    if os.path.isdir(tests_dir):
        r = run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                cwd=REPO)
        if r.returncode != 0:
            print(f"ABORT: repo test suite failed:\n{r.stdout}\n{r.stderr}")
            return 2

    # 3. Docs staleness signal.
    state = {}
    if os.path.exists(STATE):
        state = json.load(open(STATE))
    doc_hashes = {}
    for src in DOC_SOURCES:
        if os.path.exists(src):
            doc_hashes[src] = sha(open(src, encoding="utf-8").read())
    prev = state.get("doc_hashes", {})
    docs_stale = any(doc_hashes.get(k) != v for k, v in prev.items()) or \
        (bool(prev) and set(doc_hashes) != set(prev))

    status = run(["git", "status", "--porcelain"], cwd=REPO)
    dirty_files = [l[3:] for l in status.stdout.splitlines() if l.strip()]
    result = {"changed_files": dirty_files,
              "docs_may_be_stale": bool(docs_stale)}

    if dry:
        result["status"] = "dry-run"
        print(json.dumps(result))
        return 0
    if not dirty_files:
        result["status"] = "no changes"
        print(json.dumps(result))
        state.update({"doc_hashes": doc_hashes,
                      "last_sync_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "last_result": result})
        json.dump(state, open(STATE, "w"), indent=1)
        return 0

    run(["git", "add", "-A"], cwd=REPO)
    msg = message or ("Weekly sync from live harness: "
                      + ", ".join(dirty_files[:6])
                      + ("…" if len(dirty_files) > 6 else ""))
    r = run(["git", "commit", "-m", msg], cwd=REPO)
    if r.returncode != 0:
        print(f"ABORT: git commit failed:\n{r.stdout}\n{r.stderr}")
        return 2
    head = run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO).stdout.strip()
    result["commit"] = head
    if push:
        r = run(["git", "push", "origin", "main"], cwd=REPO)
        if r.returncode != 0:
            print(f"PUSH FAILED after local commit {head}:\n{r.stderr}")
            print(json.dumps(result))
            return 3
        result["pushed"] = True
    result["status"] = "synced"
    print(json.dumps(result))
    state.update({"doc_hashes": doc_hashes,
                  "last_sync_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "last_result": result, "last_commit": head})
    json.dump(state, open(STATE, "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
