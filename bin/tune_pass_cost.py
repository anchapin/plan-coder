#!/usr/bin/env python3
"""Record (and read back) the token cost of one harness-tune pass.

The weekly tune pass is itself a cron run, so it has a token cost —
and until now that cost was recorded nowhere: the worker ledger
counts worker runs, the goldfish / jittest ledgers count their own
spike runs, and no ledger counted the tune pass's own tokens. This
script closes that gap with ONE append-only ledger of its own:

    <state.dir>/harness_tune_passes.jsonl

    one JSON object per completed pass:
        {ts, tokens_in, tokens_out, wall_s, source}

This ledger is deliberately NOT the worker ledger and NOT the spike
ledgers: those two feed the overhead denominators and the loss-signal
aggregation that the tune pass itself measures, so a tune row in
them would perturb the very measurement this ledger exists to
support. Nothing here is a gate — the pass continues when the append
fails.

Token source. No collector measures a cron pass's tokens today: the
spike ledgers get tokens_out from in-process wrappers
(bin/goldfish.py, bin/jittest.py read their own run's usage), and
the worker ledger's counts come from worker runs. So the source is
named explicitly on every row and is resolved in this order:

  1. --cron-run FILE      the scheduler's cron run record for this
                          pass (a JSON file). Token fields are read
                          from usage.prompt_tokens /
                          usage.completion_tokens, usage.input_tokens
                          / usage.output_tokens, or tokens_in /
                          tokens_out.
                          source: "scheduler_cron_run_record:<file>"
  2. --transcript FILE    this pass's own transcript (JSONL), summed
                          the way bin/jittest.py sums transcript
                          usage (per-row tokens_in / tokens_out).
                          source: "pass_transcript:<file>"
  3. neither available   tokens_in and tokens_out are the STRING
                          "not_available" and source says so, e.g.
                          "not_available: no cron run record and no
                          pass transcript supplied". This is a valid
                          row, not an error: the pass cost is
                          unknown, the pass still recorded that it
                          happened, and the wall time is still there.

Wall time: --wall-s if given, else the cron run record's own
duration field, else the elapsed seconds since --start-ts (or since
the script started).

Usage:
    python3 bin/tune_pass_cost.py append [--state-dir D] [--config F]
        [--cron-run FILE] [--transcript FILE] [--wall-s N]
        [--start-ts TS] [--json]
    python3 bin/tune_pass_cost.py last [--state-dir D] [--config F] [--json]
    python3 bin/tune_pass_cost.py list [--state-dir D] [--config F] [--json]

Exit codes: 0 = ok, 1 = bad input, 2 = no state dir resolvable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config

NA = "not_available"
LEDGER_NAME = "harness_tune_passes.jsonl"
TOKEN_KEY_PAIRS = (("prompt_tokens", "completion_tokens"),
                   ("input_tokens", "output_tokens"),
                   ("tokens_in", "tokens_out"))


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_state_dir(explicit: str | None, config_path: str | None = None) -> str | None:
    if explicit:
        return os.path.expanduser(explicit)
    cfg = {}
    if config_path:
        try:
            with open(os.path.expanduser(config_path), encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, json.JSONDecodeError):
            cfg = {}
    if not isinstance(cfg, dict) or not cfg:
        cfg = _load_deployment_config() or {}
    d = (cfg.get("state") or {}).get("dir")
    return os.path.expanduser(d) if d else None


def append_jsonl(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_jsonl(path: str):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _num(value):
    """A number as a number; None for anything that is not one.

    Integers stay integers; a float that is whole becomes an int, a
    fractional float stays fractional (a wall time of 512.5 s must not
    become 512)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    return None


def tokens_from_cron_run(path: str):
    """(tokens_in, tokens_out, wall_s or None) from a cron run record.

    Returns (None, None, None) when the file cannot be read or holds
    no recognisable token counts — the caller then falls back to the
    transcript source."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else data
    for in_key, out_key in TOKEN_KEY_PAIRS:
        t_in, t_out = _num(usage.get(in_key)), _num(usage.get(out_key))
        if t_in is not None or t_out is not None:
            return (t_in if t_in is not None else 0,
                    t_out if t_out is not None else 0,
                    _wall_from_record(data))
    return None, None, None


def _wall_from_record(data: dict):
    for key in ("wall_s", "duration_s", "wall_seconds"):
        value = _num(data.get(key))
        if value is not None:
            return value
    ms = _num(data.get("duration_ms"))
    if ms is not None:
        return round(ms / 1000.0, 3)
    return None


def tokens_from_transcript(path: str):
    """Sum tokens_in / tokens_out over a JSONL transcript.

    The same sum bin/jittest.py does for a run's transcript usage
    (per-row counters), extended to the input side."""
    t_in = t_out = 0
    seen = False
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                seen = True
                value = _num(row.get("tokens_in"))
                if value is not None:
                    t_in += value
                value = _num(row.get("tokens_out"))
                if value is not None:
                    t_out += value
    except OSError:
        return None, None
    if not seen:
        return None, None
    return t_in, t_out


def build_row(args) -> dict:
    tokens_in = tokens_out = None
    source = NA
    wall_s = None
    if args.cron_run:
        t_in, t_out, rec_wall = tokens_from_cron_run(args.cron_run)
        if t_in is not None or t_out is not None:
            tokens_in, tokens_out = t_in, t_out
            source = f"scheduler_cron_run_record:{os.path.basename(args.cron_run)}"
            wall_s = rec_wall
    if tokens_in is None and args.transcript:
        t_in, t_out = tokens_from_transcript(args.transcript)
        if t_in is not None or t_out is not None:
            tokens_in, tokens_out = t_in, t_out
            source = f"pass_transcript:{os.path.basename(args.transcript)}"
    if tokens_in is None:
        tokens_in = NA
        tokens_out = NA
        source = (f"{NA}: no cron run record and no pass transcript supplied")
    if args.wall_s is not None:
        wall_s = float(args.wall_s)
    elif wall_s is None and args.start_ts:
        try:
            start = datetime.fromisoformat(args.start_ts.replace("Z", "+00:00"))
            wall_s = round((datetime.now(UTC) - start).total_seconds(), 3)
        except ValueError:
            wall_s = None
    if wall_s is None:
        wall_s = NA
    return {"ts": now_iso(), "tokens_in": tokens_in, "tokens_out": tokens_out,
            "wall_s": wall_s, "source": source}


def cmd_append(args) -> int:
    sd = resolve_state_dir(args.state_dir, args.config)
    if not sd:
        print("tune_pass_cost: no state dir resolvable")
        return 2
    row = build_row(args)
    path = os.path.join(sd, LEDGER_NAME)
    append_jsonl(path, row)
    if args.json:
        print(json.dumps({"path": path, "row": row}, indent=1))
    else:
        print(f"tune_pass_cost: pass cost logged -> {path} "
              f"(tokens_in={row['tokens_in']} tokens_out={row['tokens_out']} "
              f"wall_s={row['wall_s']} source={row['source']})")
    return 0


def cmd_last(args) -> int:
    sd = resolve_state_dir(args.state_dir, args.config)
    if not sd:
        print("tune_pass_cost: no state dir resolvable")
        return 2
    rows = load_jsonl(os.path.join(sd, LEDGER_NAME))
    if not rows:
        print("tune_pass_cost: no harness-tune pass recorded yet")
        return 0
    row = rows[-1]
    if args.json:
        print(json.dumps(row, indent=1))
    else:
        print(f"last harness-tune pass: ts={row.get('ts')} "
              f"tokens_in={row.get('tokens_in')} "
              f"tokens_out={row.get('tokens_out')} "
              f"wall_s={row.get('wall_s')} source={row.get('source')}")
    return 0


def cmd_list(args) -> int:
    sd = resolve_state_dir(args.state_dir, args.config)
    if not sd:
        print("tune_pass_cost: no state dir resolvable")
        return 2
    rows = load_jsonl(os.path.join(sd, LEDGER_NAME))
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        for row in rows:
            print(f"{row.get('ts')}  tokens_in={row.get('tokens_in')} "
                  f"tokens_out={row.get('tokens_out')} "
                  f"wall_s={row.get('wall_s')} source={row.get('source')}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("append", "last", "list"):
        p = sub.add_parser(name)
        p.add_argument("--config", default=None)
        p.add_argument("--state-dir", default=None)
        p.add_argument("--json", action="store_true")
    ap_add = sub.choices["append"]
    ap_add.add_argument("--cron-run", default=None,
                        help="scheduler cron run record (JSON) for this pass")
    ap_add.add_argument("--transcript", default=None,
                        help="this pass's transcript (JSONL) to sum usage from")
    ap_add.add_argument("--wall-s", type=float, default=None,
                        help="the pass's wall time in seconds")
    ap_add.add_argument("--start-ts", default=None,
                        help="ISO timestamp the pass started at")
    args = ap.parse_args(argv)
    return {"append": cmd_append, "last": cmd_last,
            "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
