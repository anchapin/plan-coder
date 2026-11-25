#!/usr/bin/env python3
"""Atomic commit for the harness's JSON state files (loop audit, 2026-11-08).

workers.json and shepherd.json used to be rewritten in place by the
supervisor / shepherd prompts. Both files truncated in production
(workers.json 2026-10-23; shepherd.json to 0 bytes 2026-10-26) when a
write failed mid-way, and both had to be reconstructed from the
append-only worker ledger. agent.py's own file tools already write
atomically (temp file + os.replace); the cron agents' state saves did
not go through those tools.

The pattern this script enforces: the agent writes the full new state to
a staging file (<dest>.new) with its normal file tools — a failed or
truncated staging write harms only the staging file — then commits it
here. Commit (1) refuses a staging file that does not parse as JSON,
and one that has the wrong shape for a known state file, so the live
file stays untouched; (2) copies the current live file to a rotating
backup (<dest>.statebak-<UTC ts>, newest --keep kept), and (3) writes
the staging bytes to a temp file, fsyncs, and os.replace()s it over
the destination, so a reader never sees a partial file.

Usage:
    python3 bin/state_write.py validate <path> [--json]
    python3 bin/state_write.py commit <staging> <dest> [--keep N] [--json]

Exit 0 on success; exit 1 when commit refuses (staging == dest, invalid
JSON, or a wrong-shaped staging file); exit 2 when a file cannot be read.
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
    data = load_staging(raw, dest)  # ValueError if bad JSON or shape

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


def _shape_name(value) -> str:
    """The JSON-ish name of a parsed value, for the error message."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def _workers_shape_error(data) -> str | None:
    """The shape error for a workers.json payload, or None when it fits.

    `dashboard.worker_entries()` reads workers.json as a mapping of
    worker keys to entry objects (or a "workers" object/array), and
    `plan_coder.worker_view()` reads the entry fields off those
    objects, so an entry that is not an object is a broken state file.
    """
    if not isinstance(data, dict):
        return ("workers.json must be a JSON object mapping worker keys to "
                f"entry objects, got {_shape_name(data)}")
    if "workers" in data:
        raw = data["workers"]
        if not isinstance(raw, (dict, list)):
            return ("workers.json 'workers' must be an object or an array, "
                    f"got {_shape_name(raw)}")
        pairs = (list(raw.items()) if isinstance(raw, dict)
                 else list(enumerate(raw)))
    else:
        pairs = list(data.items())
    for key, entry in pairs:
        if not isinstance(entry, dict):
            return ("workers.json entries must be objects, but entry "
                    f"{key!r} is a {_shape_name(entry)}")
    return None


def _shepherd_shape_error(data) -> str | None:
    """The shape error for a shepherd.json payload, or None when it fits.

    `dashboard.tracked_prs()` reads "tracked_prs", `flagbatch.py` reads
    the "needs_alex" array, and `dashboard.py` reads "seen_merged" /
    "jittest_catch_prs" / "pending_trigger" / "last_trigger"; the file
    itself is one JSON object (ROADMAP.md). "last_trigger" is an ISO
    timestamp string in the live file (the readers also tolerate an
    object), so either shape fits for that key.
    """
    if not isinstance(data, dict):
        return f"shepherd.json must be a JSON object, got {_shape_name(data)}"
    if "tracked_prs" in data:
        tracked = data["tracked_prs"]
        if not isinstance(tracked, (dict, list)):
            return ("shepherd.json 'tracked_prs' must be an object or an "
                    f"array, got {_shape_name(tracked)}")
    for key in ("needs_alex", "seen_merged", "jittest_catch_prs"):
        if key in data and not isinstance(data[key], list):
            return (f"shepherd.json {key!r} must be an array, got "
                    f"{_shape_name(data[key])}")
    if "pending_trigger" in data and not isinstance(data["pending_trigger"], dict):
        return ("shepherd.json 'pending_trigger' must be an object, got "
                f"{_shape_name(data['pending_trigger'])}")
    if "last_trigger" in data and not isinstance(data["last_trigger"], (str, dict)):
        return ("shepherd.json 'last_trigger' must be a string or an "
                f"object, got {_shape_name(data['last_trigger'])}")
    return None


def shape_error(dest: str, data) -> str | None:
    """The refusal message when `data` has the wrong shape for `dest`.

    Only the known state files have a shape: workers.json and
    shepherd.json.  Every other destination keeps the parse-only
    behaviour, so the other state_write consumers are unaffected.
    """
    name = os.path.basename(dest)
    if name == "workers.json":
        return _workers_shape_error(data)
    if name == "shepherd.json":
        return _shepherd_shape_error(data)
    return None


def load_staging(raw: bytes, dest: str):
    """Parse the staging bytes and check the shape the readers rely on.

    Raises ValueError when the bytes are not JSON (from the parse) or
    when they parse but have the wrong shape for `dest`.  The caller
    runs this before it touches the destination, so a refused staging
    file leaves the live file untouched and keeps the staging file for
    diagnosis.
    """
    data = _load_json_bytes(raw)
    problem = shape_error(dest, data)
    if problem:
        raise ValueError(f"wrong shape: {problem}")
    return data


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
