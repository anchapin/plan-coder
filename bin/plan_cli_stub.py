#!/usr/bin/env python3
"""Reference CLI-route stub: record an invocation, never execute one.

This is the single stubbed CLI harness used by the plan/route reference
slice.  It is intentionally generic: the route resolver supplies a harness
name and model, and this script records the command that a production
runner would receive.  It never contacts a vendor and never reads
credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from typing import Any


def _atomic_json_write(path: str, value: Any) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".plan-cli-stub-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--harness", required=True,
                    help="generic CLI harness name (for example, claude)")
    ap.add_argument("--model", required=True, help="model id")
    ap.add_argument("--record", required=True,
                    help="path where the would-be invocation is recorded")
    args = ap.parse_args(argv)
    invocation = {
        "argv": [args.harness, "--model", args.model],
        "harness": args.harness,
        "model": args.model,
        "stub": True,
    }
    _atomic_json_write(args.record, invocation)
    print(json.dumps(invocation, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
