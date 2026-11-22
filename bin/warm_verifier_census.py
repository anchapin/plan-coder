#!/usr/bin/env python3
"""Warm-verifier cold-start census (plan-coder issue #154, Phase 0).

Phase 0 is measurement only.  No verifier is pooled, no server is
started, no MCP wrapper is written, no prompt / config / harness file is
touched.  This tool only *measures* what a warm verifier could save, so
that the issue's gate can be answered with numbers instead of a hunch.

The issue's question: for recent verification-heavy runs, split the
wall-clock into

    cold_start   environment / solver / service startup -- the part a
                 warm verifier would amortise away;
    verification the actual verification work;
    teardown     stopping services and cleaning up;
    other        everything else (edits, reading, chat-only steps) --
                 never silently folded into cold start.

and report, per verifier type, the cold-start share and the invocation
frequency (runs/week), then name the one verifier type with the
highest aggregate cold-start cost.

Gate (issue #154, quoted):

    Proceed to a Phase 1 design only if cold start is >= 25% of
    aggregate verification wall-clock for the top candidate AND the
    candidate is invoked >= 10 times/week.

    Kill clause: if cold start is < 10% everywhere, the census itself
    is the deliverable and the line closes.

The gate arithmetic lives in code (the ``GATE_*`` / ``KILL_*``
constants below) and is pinned by ``tests/test_warm_verifier_census.py``.

Every classification rule is a named constant with a one-line rationale
(the rationale is also emitted in the tool's own JSON output, under
``definitions``, so the operator can audit exactly what counted as cold
start before believing the number).

Usage:
    python3 bin/warm_verifier_census.py census --transcripts-dir DIR \
        [--since ISO] [--until ISO] [--json]

Exit 0 on success; exit 2 when the transcripts directory is missing or
unreadable (or when --since / --until cannot be parsed).

READ ONLY: transcripts are read, never written.  Zero LLM tokens: the
tool does deterministic pattern matching over recorded shell commands.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime

GATE_COLD_START_SHARE_PCT = 25.0  # proceed needs cold start >= 25% of the wall-clock
GATE_RUNS_PER_WEEK = 10.0  # ... and >= 10 invocations per week
KILL_COLD_START_SHARE_PCT = 10.0  # kill when cold start is < 10% everywhere

EXEC_TOOLS = frozenset({"exec", "bash", "Bash", "exec_command", "shell"})

COLD_START_PATTERNS = (
    ("python_virtualenv",
     re.compile(r"python3?\s+-m\s+venv\b|\bvirtualenv\b|\buv\s+venv\b"),
     "creating a python virtualenv is environment startup"),  # env startup
    ("pip_install",
     re.compile(r"\bpip3?\s+install\b|\buv\s+pip\s+install\b|\bpoetry\s+install\b|\bpip\s+install\b"),
     "installing python dependencies is environment startup"),  # env startup
    ("node_dependency_install",
     re.compile(r"\bnpm\s+(ci|install)\b|\byarn\s+install\b|\bpnpm\s+install\b"),
     "installing node dependencies is environment startup"),  # env startup
    ("toolchain_install",
     re.compile(r"\brustup\s+(toolchain\s+install|component\s+add|\bdefault\b)"
                r"|\bapt(-get)?\s+install\b|\byum\s+install\b|\bdnf\s+install\b"
                r"|\bapk\s+add\b|\bbrew\s+install\b|\basdf\s+install\b"
                r"|\bconda\s+(install|create)\b"),
     "installing a toolchain or compiler is toolchain startup"),  # toolchain startup
    ("cargo_build",
     re.compile(r"\bcargo\s+(build|fetch)\b|\bcargo\s+install\b"),
     "compiling or fetching dependencies is toolchain startup, not a check"),  # toolchain startup
    ("project_build",
     re.compile(r"\bmake\b(?!\s+(test|check|tests))|\bcmake\s+--build\b|\bninja\b"
                r"|\bgo\s+build\b|\bgradle\b(?!\s+test)|\bmvn\b(?!\s+test)"),
     "building the project under test is toolchain startup"),  # toolchain startup
    ("service_boot",
     re.compile(r"\bdocker(\s+compose|-compose)\s+up\b|\bsystemctl\s+start\b"
                r"|\bservice\s+\S+\s+start\b|\buvicorn\b|\bgunicorn\b|\bpostgres\b"
                r"|\bredis-server\b|\bmongod\b|\bkafka-server-start\b"
                r"|\bopenstudio\s+server\b|\bnohup\b.*&\s*$"),
     "booting a service (docker, systemd, a server) is service startup"),  # service startup
    ("solver_startup",
     re.compile(r"\bz3\s+-in\b|\bz3\s+-server\b|\bcvc5\s+--server\b|\blean\s+--server\b"
                r"|\bminisat\b|\bcadical\b|\bpicosat\b"),
     "starting a solver server is solver startup"),  # solver startup
    ("simulation_preprocess",
     re.compile(r"\bexpandobjects\b|\bconvertESOMTR\b|\breadvarseso\b|\bepw\b.*download"),
     "preprocessing a simulation model or weather file is solver startup"),  # solver startup
)

VERIFICATION_PATTERNS = {
    "pytest": (
        ("pytest_suite", re.compile(r"\bpytest\b|\bpy\.test\b"),
         "a pytest suite is the python verification"),  # verification
        ("unittest_suite", re.compile(r"python3?\s+-m\s+unittest\b|\bunittest\s+discover\b"),
         "a stdlib unittest suite is the python verification"),  # verification
        ("tox_nox", re.compile(r"\btox\b|\bnox\b"),
         "tox and nox run a test suite"),  # verification
    ),
    "cargo": (
        ("cargo_check", re.compile(r"\bcargo\s+check\b"),
         "cargo check is the rust verification"),  # verification
        ("cargo_test", re.compile(r"\bcargo\s+(test|clippy)\b"),
         "cargo test and clippy are the rust verification"),  # verification
    ),
    "energyplus_openstudio": (
        ("energyplus_run", re.compile(r"\benergyplus\b|\brunenergyplus\b|\benergyplus\.py\b"),
         "an EnergyPlus run is the building-simulation verification"),  # verification
        ("openstudio_run", re.compile(r"\bopenstudio\b"),
         "an OpenStudio run is the building-simulation verification"),  # verification
    ),
    "formal_property": (
        ("lean", re.compile(r"\blean\b|\blake\b(?!up)"),
         "lean proof checking is the formal verification"),  # verification
        ("coq_isabelle", re.compile(r"\bcoqc\b|\bcoqtop\b|\bisabelle\b"),
         "coq and isabelle proof checking are the formal verification"),  # verification
        ("smt_solver", re.compile(r"\bz3\b|\bcvc5\b|\bcvc4\b|\byices\b|\bboolector\b"),
         "an SMT solver run is the formal verification"),  # verification
        ("property_tester", re.compile(r"\bstateright\b|\bhypothesis\b|\bquickcheck\b"
                                       r"|\bproptest\b|\bkani\b|\bverus\b|\bcreusot\b"
                                       r"|\bporcupine\b|\bjepsen\b"),
         "a property-based tester is the formal verification"),  # verification
    ),
}

TEARDOWN_PATTERNS = (
    ("service_stop",
     re.compile(r"\bdocker(\s+compose|-compose)\s+(down|stop|rm)\b|\bsystemctl\s+stop\b"
                r"|\bservice\s+\S+\s+stop\b|\bpkill\b|\bkillall\b|\bshutdown\b"),
     "stopping a service is teardown"),  # teardown
    ("cleanup",
     re.compile(r"\brm\s+-[a-zA-Z]*[rf]|\bgit\s+clean\b|\brmdir\b|\btruncate\b"),
     "removing scratch or build artifacts is teardown"),  # teardown
)

VERIFIER_TYPE_MARKERS = {
    "pytest": (re.compile(r"\bpytest\b|\bpy\.test\b|\bunittest\b|\btox\b|\bnox\b"),
               "python test-suite runs"),
    "cargo": (re.compile(r"\bcargo\b|\brustc\b|\bcargo-nextest\b"),
              "rust toolchain runs"),
    "energyplus_openstudio": (re.compile(r"\benergyplus\b|\bopenstudio\b|\brunenergyplus\b"
                                         r"|\bexpandobjects\b"),
                              "building-simulation runs"),
    "formal_property": (re.compile(r"\blean\b|\blake\b|\bcoq\b|\bisabelle\b|\bz3\b|\bcvc5\b"
                                   r"|\bstateright\b|\bhypothesis\b|\bquickcheck\b|\bproptest\b"
                                   r"|\bkani\b|\bverus\b|\bcreusot\b|\bporcupine\b|\bjepsen\b"),
                         "formal or property-checking runs"),
}

BUCKETS = ("cold_start", "verification", "teardown", "other")


def parse_ts(value):
    """ISO-8601 timestamp -> aware UTC datetime, or None when unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _as_float(value):
    """Coerce a JSON value to float (0.0 when it is not a number)."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _parse_json_line(text):
    """One JSONL line -> dict, or None when the line is not a JSON object."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def extract_command(call):
    """The shell command of one tool call, or None when it is not a command.

    agent.py stores tool-call arguments as a JSON *string* truncated to
    500 characters, so the JSON may be unparseable; a regex fallback
    recovers the command field in that case.
    """
    if not isinstance(call, dict):
        return None
    if call.get("name") not in EXEC_TOOLS:
        return None
    args = call.get("args")
    command = None
    if isinstance(args, dict):
        command = args.get("command")
    elif isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            command = parsed.get("command")
        else:
            match = re.search(r'"command"\s*:\s*"(.*?)"', args)
            command = match.group(1) if match else None
    return command if isinstance(command, str) else None


def _matches_any(patterns, command):
    """Names of the patterns that match a command (empty list when none)."""
    return [name for name, regex, _ in patterns if regex.search(command)]


def _matches_verification(command):
    """(verifier types, pattern names) matched by a command."""
    types, names = [], []
    for verifier_type, patterns in VERIFICATION_PATTERNS.items():
        hit = _matches_any(patterns, command)
        if hit:
            types.append(verifier_type)
            names.extend(f"{verifier_type}:{name}" for name in hit)
    return types, names


def load_run(path):
    """Parse one transcript jsonl into a run record (read-only).

    The shape follows what agent.py writes: a "started" event, one
    record per step (with ``ts``, ``duration_ms`` and ``tool_calls``)
    and a "finished" event.
    """
    records = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            obj = _parse_json_line(text)
            if obj is not None:
                records.append(obj)

    run_id = None
    start_ts = None
    end_ts = None
    finished_duration_ms = None
    steps = []
    previous_ts = None
    for obj in records:
        stamp = parse_ts(obj.get("ts"))
        event = obj.get("event")
        if event == "started":
            run_id = obj.get("run_id") or run_id
            if stamp is not None:
                start_ts = stamp if start_ts is None else min(start_ts, stamp)
            continue
        if event == "finished":
            if stamp is not None:
                end_ts = stamp if end_ts is None else max(end_ts, stamp)
            finished_duration_ms = obj.get("duration_ms")
            continue
        calls = obj.get("tool_calls")
        if not isinstance(calls, list):
            continue  # gate_trigger / aborted / token_cap carry no tool payload
        duration_ms = obj.get("duration_ms")
        if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
            duration = float(duration_ms) / 1000.0
        elif stamp is not None and previous_ts is not None:
            duration = max(0.0, (stamp - previous_ts).total_seconds())
        else:
            duration = 0.0
        commands = []
        for call in calls:
            command = extract_command(call)
            if command:
                commands.append(command)
        steps.append({"ts": stamp, "duration": duration, "commands": commands})
        if stamp is not None:
            previous_ts = stamp if previous_ts is None else max(previous_ts, stamp)

    if run_id is None:
        run_id = os.path.splitext(os.path.basename(path))[0]
    if start_ts is None:
        start_ts = next((s["ts"] for s in steps if s["ts"] is not None), None)
    if end_ts is None:
        end_ts = next((s["ts"] for s in reversed(steps) if s["ts"] is not None), None)
    if start_ts is None:
        start_ts = datetime.fromtimestamp(os.path.getmtime(path), UTC)
    if end_ts is None:
        end_ts = start_ts

    wall_clock = (end_ts - start_ts).total_seconds()
    if wall_clock <= 0:
        wall_clock = sum(step["duration"] for step in steps)
        if wall_clock <= 0:
            wall_clock = _as_float(finished_duration_ms) / 1000.0

    return {
        "file": os.path.basename(path),
        "path": path,
        "run_id": run_id,
        "start": start_ts,
        "end": end_ts,
        "wall_clock": max(0.0, wall_clock),
        "steps": steps,
    }


def classify_run(run):
    """Split one run's wall-clock into the four buckets.

    Step classification (deterministic, precedence fixed):
      1. a step whose commands match a cold-start pattern   -> cold_start
      2. else a step whose commands match a verification pattern
         -> verification
      3. else a step whose commands match a teardown pattern -> teardown
      4. else                                                  -> other

    Cold start additionally carries the issue's "run start -> first
    non-setup action" interval.  Because that interval already contains
    the leading setup steps, only setup steps *after* the first
    non-setup action are added on top of it (no double counting).
    """
    steps = run["steps"]
    kinds = []
    for step in steps:
        commands = step["commands"]
        cold = any(_matches_any(COLD_START_PATTERNS, cmd) for cmd in commands)
        verifier_types = []
        for cmd in commands:
            types, _names = _matches_verification(cmd)
            verifier_types.extend(types)
        teardown = any(_matches_any(TEARDOWN_PATTERNS, cmd) for cmd in commands)
        if cold:
            kinds.append("cold_start")
        elif verifier_types:
            kinds.append("verification")
        elif teardown:
            kinds.append("teardown")
        else:
            kinds.append("other")

    first_other = next((i for i, kind in enumerate(kinds) if kind != "cold_start"), None)

    gap = 0.0
    if first_other is None:
        gap = run["wall_clock"]  # the whole run is setup
    else:
        step = steps[first_other]
        if step["ts"] is not None:
            action_start = step["ts"].timestamp() - step["duration"]
            gap = max(0.0, action_start - run["start"].timestamp())

    late_setup = 0.0
    if first_other is not None:
        for index, kind in enumerate(kinds):
            if kind == "cold_start" and index > first_other:
                late_setup += steps[index]["duration"]

    cold_start = gap + late_setup
    if run["wall_clock"]:
        cold_start = min(cold_start, run["wall_clock"])

    verification = sum((step["duration"] for step, kind in zip(steps, kinds)
                        if kind == "verification"), 0.0)
    teardown = sum((step["duration"] for step, kind in zip(steps, kinds)
                    if kind == "teardown"), 0.0)
    other = sum((step["duration"] for step, kind in zip(steps, kinds)
                 if kind == "other"), 0.0)

    verifier_types = []
    for step in steps:
        for cmd in step["commands"]:
            for verifier_type, (regex, _label) in VERIFIER_TYPE_MARKERS.items():
                if regex.search(cmd) and verifier_type not in verifier_types:
                    verifier_types.append(verifier_type)

    buckets = {
        "cold_start": cold_start,
        "verification": verification,
        "teardown": teardown,
        "other": other,
    }
    attributed = sum(buckets.values(), 0.0)
    return {
        "run_id": run["run_id"],
        "file": run["file"],
        "verifier_types": sorted(verifier_types),
        "wall_clock_seconds": round(run["wall_clock"], 3),
        "cold_start_seconds": round(buckets["cold_start"], 3),
        "verification_seconds": round(buckets["verification"], 3),
        "teardown_seconds": round(buckets["teardown"], 3),
        "other_seconds": round(buckets["other"], 3),
        "attributed_seconds": round(attributed, 3),
        "unattributed_seconds": round(max(0.0, run["wall_clock"] - attributed), 3),
        "steps": len(steps),
        "cold_start_gap_seconds": round(gap, 3),
        "cold_start_setup_seconds": round(late_setup, 3),
    }


def _pct(part, whole):
    """Part as a percentage of whole (None when the whole is zero)."""
    if not whole:
        return None
    return round(100.0 * part / whole, 3)


def aggregate(rows, window_seconds):
    """Per-verifier-type aggregate table plus the gate evaluation."""
    weeks = window_seconds / (7 * 24 * 3600.0)
    weeks = max(weeks, 1.0 / 7.0)  # a window shorter than a day counts as one day
    types = {}
    for row in rows:
        for verifier_type in row["verifier_types"]:
            entry = types.setdefault(verifier_type, {
                "verifier_type": verifier_type,
                "runs": 0,
                "wall_clock_seconds": 0.0,
                "cold_start_seconds": 0.0,
                "verification_seconds": 0.0,
                "teardown_seconds": 0.0,
                "other_seconds": 0.0,
            })
            entry["runs"] += 1
            entry["wall_clock_seconds"] += row["wall_clock_seconds"]
            entry["cold_start_seconds"] += row["cold_start_seconds"]
            entry["verification_seconds"] += row["verification_seconds"]
            entry["teardown_seconds"] += row["teardown_seconds"]
            entry["other_seconds"] += row["other_seconds"]

    table = []
    for entry in types.values():
        share = _pct(entry["cold_start_seconds"], entry["wall_clock_seconds"])
        table.append({
            "verifier_type": entry["verifier_type"],
            "runs": entry["runs"],
            "runs_per_week": round(entry["runs"] / weeks, 3),
            "wall_clock_seconds": round(entry["wall_clock_seconds"], 3),
            "cold_start_seconds": round(entry["cold_start_seconds"], 3),
            "cold_start_share_pct": share,
            "verification_seconds": round(entry["verification_seconds"], 3),
            "teardown_seconds": round(entry["teardown_seconds"], 3),
            "other_seconds": round(entry["other_seconds"], 3),
        })
    table.sort(key=lambda e: (-e["cold_start_seconds"],
                              -(e["cold_start_share_pct"] or 0.0),
                              e["verifier_type"]))

    top = table[0] if table else None
    max_share = max((e["cold_start_share_pct"] or 0.0) for e in table) if table else None

    if top is None:
        verdict = "below_gate"
        reason = ("no verifier type was seen in the window: there is no "
                  "candidate, so neither the proceed nor the kill clause "
                  "applies")
    elif (top["cold_start_share_pct"] or 0.0) >= GATE_COLD_START_SHARE_PCT \
            and top["runs_per_week"] >= GATE_RUNS_PER_WEEK:
        verdict = "proceed"
        reason = (f"{top['verifier_type']} cold start is "
                  f"{top['cold_start_share_pct']}% of its wall-clock "
                  f"(>= {GATE_COLD_START_SHARE_PCT}%) and it runs "
                  f"{top['runs_per_week']}/week (>= {GATE_RUNS_PER_WEEK})")
    elif max_share is not None and max_share < KILL_COLD_START_SHARE_PCT:
        verdict = "kill"
        reason = (f"cold start is < {KILL_COLD_START_SHARE_PCT}% for every "
                  f"verifier type (max {max_share}%), so the census is the "
                  f"deliverable and the line closes")
    else:
        verdict = "below_gate"
        share = top["cold_start_share_pct"] or 0.0
        if share < GATE_COLD_START_SHARE_PCT:
            reason = (f"top candidate {top['verifier_type']} cold start is "
                      f"{share}% (< {GATE_COLD_START_SHARE_PCT}%)")
        else:
            reason = (f"top candidate {top['verifier_type']} runs "
                      f"{top['runs_per_week']}/week "
                      f"(< {GATE_RUNS_PER_WEEK})")

    gate = {
        "verdict": verdict,
        "top_candidate": top["verifier_type"] if top else None,
        "top_candidate_cold_start_share_pct": top["cold_start_share_pct"] if top else None,
        "top_candidate_runs_per_week": top["runs_per_week"] if top else None,
        "top_candidate_cold_start_seconds": top["cold_start_seconds"] if top else None,
        "max_cold_start_share_pct": max_share,
        "thresholds": {
            "proceed_cold_start_share_pct": GATE_COLD_START_SHARE_PCT,
            "proceed_runs_per_week": GATE_RUNS_PER_WEEK,
            "kill_below_share_pct": KILL_COLD_START_SHARE_PCT,
        },
        "reason": reason,
    }
    return table, gate


def definitions():
    """The audit trail: every pattern that moved time between buckets."""
    return {
        "cold_start": [
            {"pattern": name, "rationale": why}
            for name, _regex, why in COLD_START_PATTERNS
        ],
        "verification": [
            {"verifier_type": verifier_type,
             "pattern": name, "rationale": why}
            for verifier_type, patterns in VERIFICATION_PATTERNS.items()
            for name, _regex, why in patterns
        ],
        "teardown": [
            {"pattern": name, "rationale": why}
            for name, _regex, why in TEARDOWN_PATTERNS
        ],
        "step_precedence": list(BUCKETS),
        "notes": [
            "A step that matches no pattern lands in `other`; it is never "
            "folded into cold start.",
            "The cold-start bucket carries the issue's 'run start -> first "
            "non-setup action' interval; leading setup steps are inside "
            "that interval, so only later setup steps are added on top.",
            "A step's duration is agent.py's duration_ms (chat round + tool "
            "execution), so the LLM turn of a setup step is charged to "
            "cold start.",
            "Cold-start share is computed against the run's full "
            "wall-clock; `unattributed_seconds` is the slack between the "
            "wall-clock and the four buckets.",
            "A verifier type seen only inside a setup command (e.g. "
            "`pip install pytest`) still marks the run as that type, so "
            "its cold start is attributed to that type.",
            "Generic test runners that are not python (make test, ctest, "
            "npm test, go test) belong to no verifier type in Phase 0; "
            "they land in `other`.",
        ],
    }


def census(transcripts_dir, since=None, until=None):
    """Run the census over a transcripts directory (read-only)."""
    if not os.path.isdir(transcripts_dir):
        raise FileNotFoundError(transcripts_dir)
    names = sorted(name for name in os.listdir(transcripts_dir)
                   if name.endswith(".jsonl"))
    runs = []
    for name in names:
        path = os.path.join(transcripts_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            run = load_run(path)
        except OSError:
            continue
        runs.append(run)

    corpus_start = min((run["start"] for run in runs), default=None)
    corpus_end = max((run["end"] for run in runs), default=None)
    window_source = "flags"
    if since is None:
        since = corpus_start
        window_source = "corpus"
    if until is None:
        until = corpus_end
        window_source = "corpus"
    if since is None or until is None:
        since = since or datetime.now(UTC)
        until = until or since
        window_source = "default"

    selected = []
    for run in runs:
        if since <= run["start"] <= until:
            selected.append(classify_run(run))

    window_seconds = max(0.0, (until - since).total_seconds())
    table, gate = aggregate(selected, window_seconds)

    totals = {
        "runs": len(selected),
        "runs_in_corpus": len(runs),
        "wall_clock_seconds": round(sum((r["wall_clock_seconds"] for r in selected), 0.0), 3),
        "cold_start_seconds": round(sum((r["cold_start_seconds"] for r in selected), 0.0), 3),
        "verification_seconds": round(sum((r["verification_seconds"] for r in selected), 0.0), 3),
        "teardown_seconds": round(sum((r["teardown_seconds"] for r in selected), 0.0), 3),
        "other_seconds": round(sum((r["other_seconds"] for r in selected), 0.0), 3),
        "unattributed_seconds": round(sum((r["unattributed_seconds"] for r in selected), 0.0), 3),
    }
    totals["cold_start_share_pct"] = _pct(totals["cold_start_seconds"],
                                          totals["wall_clock_seconds"])

    return {
        "tool": "warm_verifier_census",
        "issue": 154,
        "phase": 0,
        "window": {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "weeks": round(window_seconds / (7 * 24 * 3600.0), 4),
            "source": window_source,
        },
        "totals": totals,
        "runs": selected,
        "aggregate": table,
        "gate": gate,
        "definitions": definitions(),
    }


def render_text(report):
    """Human-readable rendering (the --json output is the machine one)."""
    window = report["window"]
    totals = report["totals"]
    lines = [
        "warm-verifier cold-start census (issue #154, Phase 0)",
        f"window: {window['since']} .. {window['until']} "
        f"({window['weeks']} weeks, source={window['source']})",
        f"runs: {totals['runs']} (corpus {totals['runs_in_corpus']}), "
        f"wall-clock {totals['wall_clock_seconds']}s, cold start "
        f"{totals['cold_start_seconds']}s "
        f"({totals['cold_start_share_pct']}%)",
        "",
        "| verifier type | runs | runs/week | wall-clock s | cold start s | cold start % |",
        "|---|---|---|---|---|---|",
    ]
    for entry in report["aggregate"]:
        lines.append(
            f"| {entry['verifier_type']} | {entry['runs']} | "
            f"{entry['runs_per_week']} | {entry['wall_clock_seconds']} | "
            f"{entry['cold_start_seconds']} | {entry['cold_start_share_pct']} |")
    gate = report["gate"]
    lines.extend([
        "",
        f"gate: {gate['verdict']} (top candidate: {gate['top_candidate']})",
        f"reason: {gate['reason']}",
    ])
    return "\n".join(lines)


def main(argv=None) -> int:
    """The CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="warm_verifier_census.py",
        description="Phase-0 cold-start census for plan-coder issue #154.")
    sub = parser.add_subparsers(dest="command", required=True)
    census_parser = sub.add_parser("census", help="census a transcripts directory")
    census_parser.add_argument("--transcripts-dir", required=True,
                                help="directory of agent.py transcript jsonl files")
    census_parser.add_argument("--since", default=None,
                                help="window start, ISO-8601 (default: corpus start)")
    census_parser.add_argument("--until", default=None,
                                help="window end, ISO-8601 (default: corpus end)")
    census_parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)

    since = parse_ts(args.since) if args.since else None
    if args.since and since is None:
        print(f"warm_verifier_census: unparseable --since {args.since!r}",
              file=sys.stderr)
        return 2
    until = parse_ts(args.until) if args.until else None
    if args.until and until is None:
        print(f"warm_verifier_census: unparseable --until {args.until!r}",
              file=sys.stderr)
        return 2

    try:
        report = census(os.path.expanduser(args.transcripts_dir), since, until)
    except OSError as exc:
        print(f"warm_verifier_census: transcripts dir not readable: {exc}",
              file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
