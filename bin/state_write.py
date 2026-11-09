#!/usr/bin/env python3
"""Atomic commit for the harness's JSON state files (loop audit, 2026-11-08).

workers.json and shepherd.json used to be rewritten in place by the
supervisor / shepherd prompts. Both files truncated in production
(workers.json 2026-10-23; shepherd.json to 0 bytes 2026-10-26) when a
write failed mid-way, and both had to be reconstructed from the
append-only worker ledger. agent.py's own file tools already write
atomically (temp file + os.replace); the cron agents' state saves did
not go through those tools.

The pattern this script enforces: the agent writes the full new state
to a staging file (<dest>.new) with its normal file tools — a failed
or truncated staging write harms only the staging file — then commits
it here. Commit (1) refuses a staging file that does not parse as
JSON, leaving the live file untouched, (2) copies the current live
file to a rotating backup (<dest>.statebak-<UTC ts>, newest --keep
kept), and (3) writes the staging bytes to a temp file in the same
directory, fsyncs, and os.replace()s it over the destination, so a
reader never sees a partial file.

Usage:
    python3 bin/state_write.py validate <path> [--json]
    python3 bin/state_write.py commit <staging> <dest> [--keep N] [--json]

Exit 0 on success; exit 1 when commit refuses (staging == dest, or the
staging file is not valid JSON); exit 2 when a file cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

BACKUP_INFIX = ".statebak-"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_json_bytes(raw: bytes):
    return json.loads(raw.decode("utf-8"))


def describe(data) -> dict:
    if isinstance(data, dict):
        return {"type": "object", "keys": sorted(str(k) for k in data),
                "entries": len(data)}
    if isinstance(data, list):
        return {"type": "array", "entries": len(data)}
    return {"type": type(data).__name__}


def validate(path: str) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read()
    data = _load_json_bytes(raw)
    out = {"path": path, "bytes": len(raw), "valid": True}
    out.update(describe(data))
    return out


def _prune_backups(dest: str, keep: int) -> list:
    directory = os.path.dirname(os.path.abspath(dest))
    prefix = os.path.basename(dest) + BACKUP_INFIX
    names = sorted(n for n in os.listdir(directory) if n.startswith(prefix))
    removed = []
    while len(names) > keep:
        victim = names.pop(0)
        os.remove(os.path.join(directory, victim))
        removed.append(victim)
    return removed


def commit(staging: str, dest: str, keep: int = 5) -> dict:
    if os.path.abspath(staging) == os.path.abspath(dest):
        raise ValueError("staging file must differ from the destination "
                         "(write to <dest>.new first, then commit)")
    with open(staging, "rb") as fh:
        raw = fh.read()
    data = _load_json_bytes(raw)  # ValueError / UnicodeDecodeError if bad

    dest_dir = os.path.dirname(os.path.abspath(dest))
    os.makedirs(dest_dir, exist_ok=True)
    backup = None
    if os.path.exists(dest):
        backup = dest + BACKUP_INFIX + _utc_stamp()
        # Two commits inside one second: suffix until the name is free.
        n = 1
        while os.path.exists(backup):
            n += 1
            backup = f"{dest}{BACKUP_INFIX}{_utc_stamp()}-{n}"
        shutil.copy2(dest, backup)

    tmp = os.path.join(dest_dir,
                       f".{os.path.basename(dest)}.tmp-{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, dest)
    try:
        dir_fd = os.open(dest_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass  # directory fsync is best-effort on some filesystems
    removed = _prune_backups(dest, keep) if keep >= 0 else []
    out = {"committed": dest, "bytes": len(raw), "backup": backup,
           "pruned": removed}
    out.update(describe(data))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_val = sub.add_parser("validate", help="check a JSON file parses")
    p_val.add_argument("path")
    p_val.add_argument("--json", action="store_true")
    p_com = sub.add_parser("commit", help="atomically replace dest with staging")
    p_com.add_argument("staging")
    p_com.add_argument("dest")
    p_com.add_argument("--keep", type=int, default=5,
                       help="rotating backups to keep (default 5; -1 = all)")
    p_com.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "validate":
            result = validate(args.path)
        else:
            result = commit(args.staging, args.dest, keep=args.keep)
    except FileNotFoundError as exc:
        print(f"state_write: file not found: {exc.filename}", file=sys.stderr)
        return 2
    except (ValueError, UnicodeDecodeError) as exc:
        print(f"state_write: REFUSED: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"state_write: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=1))
    elif args.cmd == "validate":
        print(f"OK: {result['path']} is valid JSON "
              f"({result['type']}, {result.get('entries', 0)} entries, "
              f"{result['bytes']} bytes)")
    else:
        print(f"COMMITTED: {result['committed']} "
              f"({result['bytes']} bytes, backup: "
              f"{result['backup'] or 'none (new file)'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
