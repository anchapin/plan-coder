#!/usr/bin/env python3
"""Worktree persistence slots for the backlog supervisor (Phase 1 of
the worktree-persistence spike, plan-coder #87 Phase 0 / #89).

Phase 0 measured (#87): 57% of fluxion worker runs are repeat attempts
on an issue that already ran, 84% of repeats start within 24 h, and
every repeat pays a full cold compile because the supervisor removes
the worker's worktree — build artifacts included — at collection and
again before re-dispatch. Keeping the worktree and its in-tree target/
at the SAME path and advancing the base cost 64 s vs 707-899 s cold
(~92% saving). A copied target handed to a DIFFERENT path reused
nothing (335/335 units re-checked): cargo's fingerprints are
path-bound, so this tool never copies, moves, or shares a target
across paths. The only mechanism here is same-path slot reuse.

This script is the mechanical half (goldfish.py is the model): it
keeps the slot registry (which (repo, issue) has a retained worktree,
where, built at which base, when last used) and enforces the
per-repo slot cap. The supervisor prompt (steps 4d and 8 in
prompts/backlog-supervisor.md) drives it:

  check    Is persistence enabled for this repo? Policy lives in the
           deployment config (workers.worktree_persistence). Disabled
           or repo not opted in -> exit 2, and the supervisor creates
           and removes worktrees exactly as before.
  lookup   Does a live slot exist for (repo, issue)? A slot whose
           worktree path has vanished is dropped as stale and
           reported as not found — the supervisor then falls back to
           a fresh worktree. A broken slot never blocks a dispatch.
  record   At collection (after grading), retain the worker's
           worktree as the slot for (repo, issue) instead of removing
           it. Records the base sha it was cut from and the size of
           its target/ dir.
  evict    Enforce max_slots_per_repo for a repo: slots for closed
           issues evict first, then least-recently-used. Eviction is
           the only deletion path and it uses the same
           `git worktree remove --force` + prune the supervisor uses
           today.

Policy (workers.worktree_persistence in the deployment config) ships
DISABLED: enabled=false means every subcommand exits 2 and the
supervisor behaves exactly as before. Enabling, and opting a repo
in, are separate operator decisions.

State: worktree_slots.json in the deployment config's state dir,
written only through bin/state_write.py (stage + atomic commit, per
the harness's state rule). Kept separate from the worker ledger so
failover/grade counting is never polluted.

Exit codes: 0 = ok, 2 = persistence disabled / repo not opted in /
input missing.

Usage:
    python3 bin/worktree_slots.py check --repo R [--json]
    python3 bin/worktree_slots.py lookup --repo R --issue N [--json]
    python3 bin/worktree_slots.py record --repo R --issue N
        --worktree PATH --base-sha SHA [--json]
    python3 bin/worktree_slots.py evict --repo R --clone PATH
        [--closed-issues 1,2,3] [--json]
    (every subcommand also accepts --state-file PATH, for tests)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config  # noqa: E402

DEFAULT_POLICY = {
    "enabled": False,
    "repos": [],
    "max_slots_per_repo": 8,
}


def _policy(cfg: dict | None = None) -> dict:
    cfg = cfg if cfg is not None else _load_deployment_config()
    raw = ((cfg.get("workers") or {}).get("worktree_persistence") or {})
    pol = dict(DEFAULT_POLICY)
    for key in DEFAULT_POLICY:
        if key in raw:
            pol[key] = raw[key]
    return pol


def _enabled_for(pol: dict, repo: str) -> bool:
    return bool(pol.get("enabled")) and repo in (pol.get("repos") or [])


def default_state_path(cfg: dict | None = None) -> str | None:
    cfg = cfg if cfg is not None else _load_deployment_config()
    state_dir = ((cfg.get("state") or {}).get("dir") or "")
    if not state_dir:
        return None
    return os.path.join(os.path.expanduser(state_dir),
                        "worktree_slots.json")


def _load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("slots"), list):
            return data
    except Exception:  # noqa: BLE001 — corrupt state reads as empty;
        pass  # the supervisor falls back to fresh worktrees either way
    return {"slots": []}


class StateWriteFailed(Exception):
    pass


def _save_state(path: str, state: dict) -> None:
    """Write via the harness's atomic-commit path (AGENTS.md: state
    files are written only through bin/state_write.py — stage the
    full new content at <dest>.new, then commit). Never fall back to
    a direct write when the commit fails."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    staging = path + ".new"
    with open(staging, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    state_write = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "state_write.py")
    r = subprocess.run([sys.executable, state_write, "commit",
                        staging, path],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise StateWriteFailed(
            f"STATE WRITE FAILED: {path} ({r.stderr.strip()[:200]})")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _target_bytes(worktree: str) -> int:
    target = os.path.join(worktree, "target")
    total = 0
    for root, _dirs, files in os.walk(target):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _find_slot(state: dict, repo: str, issue: int) -> dict | None:
    for slot in state["slots"]:
        if slot.get("repo") == repo and slot.get("issue") == issue:
            return slot
    return None


def cmd_check(args, pol, _state_path) -> int:
    if not _enabled_for(pol, args.repo):
        print(f"worktree persistence disabled or repo not opted in: "
              f"{args.repo}")
        return 2
    if args.json:
        print(json.dumps({"enabled": True, "repo": args.repo,
                          "max_slots_per_repo":
                              pol["max_slots_per_repo"]}))
    else:
        print(f"worktree persistence enabled for {args.repo} "
              f"(max {pol['max_slots_per_repo']} slots)")
    return 0


def cmd_lookup(args, pol, state_path) -> int:
    if not _enabled_for(pol, args.repo):
        return 2
    state = _load_state(state_path)
    slot = _find_slot(state, args.repo, args.issue)
    found = False
    if slot is not None:
        if os.path.isdir(slot.get("worktree", "")):
            slot["last_used_ts"] = _now()
            _save_state(state_path, state)
            found = True
        else:
            # Stale slot: the worktree is gone (removed by hand, an
            # older harness, a cleanup). Drop it and report not
            # found so the supervisor falls back to a fresh add.
            state["slots"].remove(slot)
            _save_state(state_path, state)
            slot = None
    if args.json:
        print(json.dumps({"found": found, "slot": slot if found else None}))
    else:
        print(f"slot found: {slot['worktree']}" if found else "no slot")
    return 0


def cmd_record(args, pol, state_path) -> int:
    if not _enabled_for(pol, args.repo):
        return 2
    if not os.path.isdir(args.worktree):
        print(f"worktree does not exist: {args.worktree}")
        return 2
    state = _load_state(state_path)
    slot = _find_slot(state, args.repo, args.issue)
    now = _now()
    if slot is None:
        slot = {"repo": args.repo, "issue": args.issue}
        state["slots"].append(slot)
    slot.update({
        "worktree": args.worktree,
        "base_sha": args.base_sha,
        "target_bytes": _target_bytes(args.worktree),
        "recorded_ts": now,
        "last_used_ts": now,
    })
    _save_state(state_path, state)
    if args.json:
        print(json.dumps(slot))
    else:
        print(f"slot recorded: {args.repo}#{args.issue} "
              f"{args.worktree} target={slot['target_bytes']}B")
    return 0


def _eviction_order(slots: list, closed: set) -> list:
    """Closed-issue slots first (oldest recorded first within the
    group), then least-recently-used by last_used_ts."""
    def key(slot):
        group = 0 if slot.get("issue") in closed else 1
        return (group, slot.get("last_used_ts", ""),
                slot.get("recorded_ts", ""))
    return sorted(slots, key=key)


def cmd_evict(args, pol, state_path) -> int:
    if not _enabled_for(pol, args.repo):
        return 2
    closed = set()
    if args.closed_issues:
        for part in args.closed_issues.split(","):
            part = part.strip()
            if part:
                try:
                    closed.add(int(part))
                except ValueError:
                    continue
    state = _load_state(state_path)
    mine = [s for s in state["slots"] if s.get("repo") == args.repo]
    cap = int(pol.get("max_slots_per_repo") or 0)
    evicted = []
    if cap > 0:
        # Closed-issue slots evict even when under the cap: their
        # worktree can never be reused (the issue will not recur).
        # Beyond those, evict least-recently-used until at the cap.
        ordered = _eviction_order(mine, closed)
        victims = [s for s in ordered if s.get("issue") in closed]
        remaining = len(mine) - len(victims)
        for slot in ordered:
            if remaining <= cap:
                break
            if slot not in victims:
                victims.append(slot)
                remaining -= 1
        for slot in victims:
            worktree = slot.get("worktree", "")
            if worktree and os.path.isdir(worktree):
                subprocess.run(
                    ["git", "-C", args.clone, "worktree", "remove",
                     "--force", worktree],
                    capture_output=True, text=True, check=False)
            state["slots"].remove(slot)
            evicted.append({"repo": slot.get("repo"),
                            "issue": slot.get("issue"),
                            "worktree": worktree})
        if evicted:
            subprocess.run(["git", "-C", args.clone, "worktree", "prune"],
                           capture_output=True, text=True, check=False)
            _save_state(state_path, state)
    if args.json:
        print(json.dumps({"evicted": evicted}))
    else:
        for e in evicted:
            print(f"evicted slot: {e['repo']}#{e['issue']} {e['worktree']}")
        if not evicted:
            print("no slots evicted")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "lookup", "record", "evict"):
        p = sub.add_parser(name)
        p.add_argument("--repo", required=True)
        p.add_argument("--json", action="store_true")
        p.add_argument("--state-file", default=None)
        if name in ("lookup", "record"):
            p.add_argument("--issue", type=int, required=True)
        if name == "record":
            p.add_argument("--worktree", required=True)
            p.add_argument("--base-sha", required=True)
        if name == "evict":
            p.add_argument("--clone", required=True)
            p.add_argument("--closed-issues", default="")
    args = ap.parse_args()

    cfg = _load_deployment_config()
    pol = _policy(cfg)
    state_path = args.state_file or default_state_path(cfg)
    if state_path is None:
        print("no state dir in deployment config")
        return 2
    handler = {"check": cmd_check, "lookup": cmd_lookup,
               "record": cmd_record, "evict": cmd_evict}[args.cmd]
    try:
        return handler(args, pol, state_path)
    except StateWriteFailed as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
