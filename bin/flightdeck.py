#!/usr/bin/env python3
"""Flight-deck for the plan-coder harness (product-engineering
spike, Part C — Phase 0 PASS 2026-11-10: a hand-generated board
audited 46/46 lines correct against live state, needs-Alex 8/8;
see ~/workspace/product-engineering-phase0/RESULTS.md).

The board is ONE generated file — FLIGHT_DECK.md — regenerated
from machine-readable state only (workers.json, shepherd.json,
and the append-only ledgers), in Huryn's shape: Done / In
progress / Next / Needs Alex / Spike windows. It exists because
cycle reports live in chat and die in compaction; the board is
the status that survives. Every content line carries its source
(file + key/row) so a wrong line is traceable to exactly one
state file. The board is a VIEW: on any conflict the ledgers
win, and nothing in the harness may treat the board as an input
to a dispatch, merge, or tune decision.

Phase 0 design fixes carried in:
  * Needs-Alex reason text comes from the PR's LATEST ledger
    row of ANY action (Phase 0's board quoted the latest
    needs_alex-action row, which for two PRs was older than
    their latest merge_conflicts row — accurately sourced but
    stale). The row used is named in the line's source.
  * Spike windows are read from an optional spike_windows.json
    registry in the state dir ({name, go_nogo_end, note} rows).
    When the registry is absent the section says so
    (not_available) — window dates are NEVER hardcoded here
    (Phase 0's windows had to come from memory/briefs because
    no registry existed).

Rules:
  * generate refuses to write when a REQUIRED source
    (workers.json or shepherd.json) is missing or unreadable,
    and logs the refusal: a stale board is worse than none.
    Optional ledgers (worker / goldfish / jittest / receipts)
    may be absent — that is noted, not an error.
  * generate is read-only with respect to the harness and is
    NOT policy-gated (the edd.py regress/summary precedent:
    auditing and smoke runs must stay possible while the spike
    is disabled). The prompt wiring gates live use behind
    `check`: exit 2 = disabled, proceed exactly as before and
    do not mention flight-deck.
  * Zero LLM tokens, by construction. Stdlib only.

Policy (workers.flightdeck in the deployment config) ships
DISABLED in the example config. Enabling is a separate
operator decision, per the goldfish/jittest/receipt precedent.

Usage:
    python3 bin/flightdeck.py check [--config F] [--json]
    python3 bin/flightdeck.py generate --state-dir D --out F
        [--config F] [--now ISO] [--json]

Exit codes: 0 = ok, 1 = bad input / required source missing
(generate wrote nothing), 2 = disabled (check only).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config  # noqa: E402

NA = "not_available"
MERGED_ACTIONS = ("merged", "merged_by_shepherd")
LEDGER_FILES = ("worker_ledger.jsonl", "shepherd_ledger.jsonl",
                "goldfish_ledger.jsonl", "jittest_ledger.jsonl",
                "receipts_ledger.jsonl", "flagcheck_ledger.jsonl")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_config(explicit: str | None) -> dict:
    if explicit:
        with open(os.path.expanduser(explicit), encoding="utf-8") as fh:
            return json.load(fh)
    return _load_deployment_config() or {}


def policy_from_config(cfg: dict) -> dict:
    pol = dict(((cfg.get("workers") or {}).get("flightdeck") or {}))
    pol.setdefault("enabled", False)
    return pol


def _short(repo: str) -> str:
    return str(repo or "").replace("anchapin/", "")


def _read_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_ledger(path: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def build_board(state_dir: str, now: datetime) -> list:
    """Return [(section, text, source)] — the board's content
    lines. Raises FileNotFoundError / ValueError when a required
    source is missing or unreadable; the caller refuses to
    write in that case."""
    workers_path = os.path.join(state_dir, "workers.json")
    shepherd_path = os.path.join(state_dir, "shepherd.json")
    for required in (workers_path, shepherd_path):
        if not os.path.isfile(required):
            raise FileNotFoundError(
                f"required state file missing: {required}")
    try:
        workers = _read_json(workers_path)
        shepherd = _read_json(shepherd_path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"required state file unreadable: {exc}")
    if not isinstance(workers, dict) or not isinstance(shepherd, dict):
        raise ValueError("workers.json / shepherd.json must be objects")

    ledgers = {}
    for name in LEDGER_FILES:
        path = os.path.join(state_dir, name)
        ledgers[name] = _read_ledger(path) if os.path.isfile(path) else None
    sled = ledgers["shepherd_ledger.jsonl"] or []
    gled = ledgers["goldfish_ledger.jsonl"]

    lines = []
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    lines.append(("HEADER",
                  f"FLIGHT DECK — generated {stamp} (state as of "
                  f"this read; shepherd interval ~30m)",
                  "generator clock"))
    lines.append(("HEADER",
                  "GENERATED from state by bin/flightdeck.py — "
                  "never hand-edit; edits are lost on regeneration",
                  "bin/flightdeck.py"))

    # ---- Done: merged in the last 7 days, deduped by PR, latest wins
    cut7 = now - timedelta(days=7)
    merged = {}
    for row in sled:
        if row.get("action") not in MERGED_ACTIONS:
            continue
        ts = parse_ts(row.get("ts"))
        if ts is None or ts < cut7:
            continue
        key = (_short(row.get("repo")), str(row.get("pr")))
        if key not in merged or str(row.get("ts")) > str(merged[key].get("ts")):
            merged[key] = row
    for (repo, pr), row in sorted(
            merged.items(), key=lambda kv: str(kv[1].get("ts")),
            reverse=True):
        lines.append(("DONE",
                      f"{repo} PR #{pr} — {row.get('action')} "
                      f"{row.get('ts')}",
                      f"shepherd_ledger.jsonl action={row.get('action')} "
                      f"ts={row.get('ts')}"))

    # ---- In progress: working/collecting runs + open tracked PRs
    for key, val in sorted(workers.items()):
        if isinstance(val, dict) and val.get("status") in (
                "working", "collecting"):
            lines.append(("IN_PROGRESS",
                          f"run {key} — status {val.get('status')}",
                          f"workers.json[{key}].status"))
    latest_ci = {}
    for row in sled:
        url = row.get("pr_url") or ""
        if url and row.get("ci"):
            if url not in latest_ci or str(row.get("ts")) > str(
                    latest_ci[url].get("ts")):
                latest_ci[url] = row
    for url in shepherd.get("tracked_prs") or []:
        ci = latest_ci.get(url, {})
        m = re.search(r"/([^/]+/[^/]+)/pull/(\d+)", str(url))
        label = f"{m.group(1)} #{m.group(2)}" if m else str(url)
        lines.append(("IN_PROGRESS",
                      f"open PR {label} — latest CI: "
                      f"{ci.get('ci', 'not recorded')} "
                      f"(merge_state {ci.get('merge_state', '')})",
                      f"shepherd.json[tracked_prs] + "
                      f"shepherd_ledger.jsonl latest ci row "
                      f"ts={ci.get('ts', 'none')}"))

    # ---- Next: pending_trigger + last trigger. (A queued-backlog
    # list has no machine-readable source in state; Phase 0's board
    # carried exactly these two lines for the same reason.)
    pending = shepherd.get("pending_trigger")
    lines.append(("NEXT",
                  f"pending_trigger: "
                  f"{json.dumps(pending) if pending else 'none recorded'}",
                  "shepherd.json[pending_trigger]"))
    lines.append(("NEXT",
                  f"last supervisor trigger requested: "
                  f"{shepherd.get('last_trigger')}",
                  "shepherd.json[last_trigger]"))

    # ---- Needs Alex: needs_alex PRs (reason from the PR's LATEST
    # ledger row of any action — Phase 0 design fix), decision-
    # waiting runs, and spike flags present in state.
    by_pr = {}
    for row in sled:
        by_pr.setdefault((_short(row.get("repo")), str(row.get("pr"))),
                         []).append(row)
    for key in shepherd.get("needs_alex") or []:
        m = re.match(r"(.+)-(\d+)$", str(key))
        reason, src_ts, src_action = "", "none", "none"
        if m:
            rows = by_pr.get((m.group(1), m.group(2))) or []
            if rows:
                latest = max(rows, key=lambda r: str(r.get("ts")))
                reason = str(latest.get("outcome") or "")[:110]
                src_ts, src_action = latest.get("ts"), latest.get("action")
        # Flag-resolution state word (flagcheck spike): the
        # latest flagcheck row for this PR, when one exists —
        # display only, never a decision input.
        fc_word = ""
        if m:
            fc_rows = [r for r in (ledgers.get("flagcheck_ledger.jsonl")
                                   or [])
                       if _short(r.get("repo")) == m.group(1)
                       and str(r.get("pr")) == m.group(2)]
            if fc_rows:
                latest_fc = max(fc_rows, key=lambda r: str(r.get("ts")))
                fc_word = (f" [flagcheck: {latest_fc.get('overall')} "
                           f"as of {latest_fc.get('as_of')}]")
        lines.append(("NEEDS_ALEX",
                      f"{key} — {reason}{fc_word}",
                      f"shepherd.json[needs_alex] + shepherd_ledger.jsonl "
                      f"latest row action={src_action} ts={src_ts}"))
    for key, val in sorted(workers.items()):
        if isinstance(val, dict) and val.get("status") in (
                "escalated", "split_suggested"):
            lines.append(("NEEDS_ALEX",
                          f"run {key} — status {val.get('status')} "
                          f"(awaiting supervisor/Alex decision)",
                          f"workers.json[{key}].status"))
    prs = shepherd.get("prs") or {}
    catches = sorted(k for k, v in prs.items()
                     if isinstance(v, dict) and v.get("jittest:catch"))
    lines.append(("NEEDS_ALEX",
                  f"jittest:catch flags in shepherd.json[prs]: "
                  f"{catches if catches else 'none'}",
                  "shepherd.json[prs]"))
    esc = [r for r in (gled or [])
           if "escalate" in str(r.get("outcome") or "")]
    if gled is None:
        lines.append(("NEEDS_ALEX",
                      "goldfish escalations: "
                      "not_available: goldfish_ledger.jsonl absent",
                      "goldfish_ledger.jsonl"))
    else:
        esc_txt = (", ".join(f"{_short(r.get('repo'))}-{r.get('issue')}"
                             for r in esc) if esc else "none")
        lines.append(("NEEDS_ALEX",
                      f"goldfish escalations in goldfish_ledger.jsonl: "
                      f"{esc_txt}",
                      "goldfish_ledger.jsonl outcome contains escalate"))

    # ---- Spike windows: registry in state, or an explicit gap.
    registry_path = os.path.join(state_dir, "spike_windows.json")
    if os.path.isfile(registry_path):
        try:
            registry = _read_json(registry_path)
        except (OSError, ValueError):
            registry = None
        rows = registry if isinstance(registry, list) else []
        if not rows:
            lines.append(("WINDOWS",
                          "not_available: spike_windows.json present "
                          "but unreadable or not a list",
                          "spike_windows.json"))
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            lines.append(("WINDOWS",
                          f"{entry.get('name')}: go/no-go ends "
                          f"{entry.get('go_nogo_end')}"
                          + (f" — {entry.get('note')}"
                             if entry.get("note") else ""),
                          "spike_windows.json"))
    else:
        lines.append(("WINDOWS",
                      "not_available: no spike_windows registry in "
                      "state dir",
                      "spike_windows.json (absent)"))
    for name in LEDGER_FILES:
        rows = ledgers[name]
        if rows is None:
            lines.append(("WINDOWS",
                          f"{name}: not_available: absent from state dir",
                          f"{name} (absent)"))
        else:
            lines.append(("WINDOWS",
                          f"{name} coverage: {len(rows)} rows",
                          f"{name} wc"))
    return lines


def render_board(lines: list) -> str:
    out = ["# FLIGHT DECK", ""]
    cur = None
    for sec, text, src in lines:
        if sec != cur:
            out += ["", f"## {sec}"]
            cur = sec
        out.append(f"- {text}  [source: {src}]")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check", help="exit 0 when enabled, "
                                       "2 when disabled")
    chk.add_argument("--config", default=None)
    chk.add_argument("--json", action="store_true")
    gen = sub.add_parser("generate", help="regenerate the board")
    gen.add_argument("--state-dir", required=True)
    gen.add_argument("--out", required=True)
    gen.add_argument("--now", default=None,
                     help="override the generation clock (ISO, tests)")
    gen.add_argument("--config", default=None)
    gen.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"flightdeck: config unreadable: {exc}", file=sys.stderr)
        return 1
    policy = policy_from_config(cfg)

    if args.cmd == "check":
        if policy.get("enabled") is not True:
            print("flightdeck disabled in deployment config "
                  "(workers.flightdeck.enabled is not true) — proceed "
                  "exactly as before")
            return 2
        print("flightdeck enabled")
        return 0

    # generate
    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print(f"flightdeck: bad --now value: {args.now}", file=sys.stderr)
        return 1
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    state_dir = os.path.expanduser(args.state_dir)
    try:
        lines = build_board(state_dir, now)
    except (FileNotFoundError, ValueError) as exc:
        print(f"flightdeck: REFUSED to generate board: {exc} — "
              f"no board written (a stale board is worse than none)",
              file=sys.stderr)
        return 1
    out_path = os.path.expanduser(args.out)
    board = render_board(lines)
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(board)
    os.replace(tmp_path, out_path)
    if args.json:
        print(json.dumps({"out": out_path, "lines": len(lines),
                          "sections": sorted({s for s, _, _ in lines})}))
    else:
        print(f"flightdeck: wrote {out_path} ({len(lines)} content lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
