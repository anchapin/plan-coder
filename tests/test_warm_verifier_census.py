"""Tests for bin/warm_verifier_census.py (issue #154, Phase 0).

The Phase-0 census measures how much of a verification-heavy run's
wall-clock is cold start (environment / solver / service startup) --
the part a warm verifier would amortise -- and evaluates the issue's
gate on the result.

What these tests pin:

  * the four buckets (cold_start / verification / teardown / other),
    including that a step matching no pattern lands in `other` and is
    never folded into cold start;
  * the cold-start gap: the issue's "run start -> first non-setup
    action" interval, with leading setup steps counted once;
  * window filtering (--since / --until);
  * the gate arithmetic, for all three outcomes: proceed (>= 25% cold
    start AND >= 10 runs/week), kill (cold start < 10% for every
    verifier type), below_gate (everything else);
  * the CLI contract: `census --transcripts-dir DIR [--since ISO]
    [--until ISO] --json`, exit 2 when the transcripts dir is missing.

All fixtures are synthetic transcripts written in the format agent.py
emits (a "started" event, one record per step, a "finished" event) and
live in a temporary directory: no test reads anything outside the repo.

Run: python3 -m pytest tests/test_warm_verifier_census.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"

_spec = importlib.util.spec_from_file_location(
    "warm_verifier_census", BIN / "warm_verifier_census.py")
census_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(census_mod)

BASE = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)
WINDOW_SINCE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
WINDOW_UNTIL = datetime(2026, 1, 8, 0, 0, 0, tzinfo=UTC)


def iso(stamp):
    return stamp.astimezone(UTC).isoformat()


def write_transcript(path, run_id, start, steps):
    """Write one synthetic transcript in the format agent.py emits.

    `steps` is a list of (command, duration_seconds) pairs; the record's
    `ts` is written when the step *finished*, like agent.py does.
    """
    offset = 0.0
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "ts": iso(start),
            "event": "started",
            "run_id": run_id,
            "tier": "cheap",
        }) + "\n")
        for index, (command, duration) in enumerate(steps, start=1):
            offset += duration
            handle.write(json.dumps({
                "ts": iso(start + timedelta(seconds=offset)),
                "step": index,
                "provider": "test/model",
                "duration_ms": int(duration * 1000),
                "tokens_in": 1000,
                "tokens_out": 100,
                "assistant_text": "",
                "tool_calls": [{"name": "exec",
                                "args": json.dumps({"command": command})}],
                "tool_results": ["ok"],
                "tool_result_kinds": ["exec"],
            }) + "\n")
        handle.write(json.dumps({
            "ts": iso(start + timedelta(seconds=offset)),
            "event": "finished",
            "steps": len(steps),
            "duration_ms": int(offset * 1000),
            "total_in": 1000,
            "total_out": 100,
        }) + "\n")
    return str(path)


SETUP_CMD = "python3 -m venv .venv && pip install -r requirements.txt"
VERIFY_CMD = "pytest -q tests/"
OTHER_CMD = "echo hello world"


class ClassificationTests(unittest.TestCase):
    """The four buckets of one run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def run_rows(self, steps, start=BASE):
        path = os.path.join(self.dir, "run-1.jsonl")
        write_transcript(path, "run-1", start, steps)
        report = census_mod.census(self.dir, WINDOW_SINCE, WINDOW_UNTIL)
        self.assertEqual(len(report["runs"]), 1)
        return report["runs"][0], report

    def test_setup_dominated_run_splits_into_cold_start_and_verification(self):
        row, report = self.run_rows([(SETUP_CMD, 60.0), (VERIFY_CMD, 40.0)])
        self.assertEqual(row["cold_start_seconds"], 60.0)
        self.assertEqual(row["verification_seconds"], 40.0)
        self.assertEqual(row["teardown_seconds"], 0.0)
        self.assertEqual(row["other_seconds"], 0.0)
        self.assertEqual(row["wall_clock_seconds"], 100.0)
        self.assertEqual(row["verifier_types"], ["pytest"])
        # the gap covers the leading setup step exactly once
        self.assertEqual(row["cold_start_gap_seconds"], 60.0)
        self.assertEqual(row["cold_start_setup_seconds"], 0.0)
        self.assertEqual(report["totals"]["cold_start_share_pct"], 60.0)

    def test_run_without_setup_has_no_cold_start(self):
        row, _ = self.run_rows([(VERIFY_CMD, 40.0), (VERIFY_CMD, 30.0)])
        self.assertEqual(row["cold_start_seconds"], 0.0)
        self.assertEqual(row["verification_seconds"], 70.0)
        self.assertEqual(row["wall_clock_seconds"], 70.0)

    def test_unclassifiable_step_lands_in_other(self):
        row, _ = self.run_rows([(VERIFY_CMD, 40.0), (OTHER_CMD, 10.0)])
        self.assertEqual(row["other_seconds"], 10.0)
        self.assertEqual(row["verification_seconds"], 40.0)
        self.assertEqual(row["cold_start_seconds"], 0.0)

    def test_teardown_step_is_its_own_bucket(self):
        row, _ = self.run_rows([
            (VERIFY_CMD, 40.0),
            ("docker compose down -v", 5.0),
        ])
        self.assertEqual(row["teardown_seconds"], 5.0)
        self.assertEqual(row["verification_seconds"], 40.0)
        self.assertEqual(row["other_seconds"], 0.0)

    def test_setup_after_the_first_verification_is_added_to_cold_start(self):
        # The first action is already a verification, so the issue's
        # "run start -> first non-setup action" gap is empty; the later
        # setup step is charged to cold start on its own.
        row, _ = self.run_rows([
            (VERIFY_CMD, 40.0),
            ("pip install -r extra.txt", 20.0),
            (VERIFY_CMD, 40.0),
        ])
        self.assertEqual(row["cold_start_gap_seconds"], 0.0)
        self.assertEqual(row["cold_start_setup_seconds"], 20.0)
        self.assertEqual(row["cold_start_seconds"], 20.0)
        self.assertEqual(row["verification_seconds"], 80.0)


class WindowTests(unittest.TestCase):
    """--since / --until filter the corpus."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_runs_outside_the_window_are_excluded(self):
        write_transcript(os.path.join(self.dir, "old.jsonl"), "old",
                         BASE - timedelta(days=30),
                         [(SETUP_CMD, 60.0), (VERIFY_CMD, 40.0)])
        write_transcript(os.path.join(self.dir, "new.jsonl"), "new", BASE,
                         [(SETUP_CMD, 60.0), (VERIFY_CMD, 40.0)])
        report = census_mod.census(self.dir, WINDOW_SINCE, WINDOW_UNTIL)
        self.assertEqual([r["run_id"] for r in report["runs"]], ["new"])
        self.assertEqual(report["totals"]["runs_in_corpus"], 2)

    def test_window_defaults_to_the_corpus_extent(self):
        write_transcript(os.path.join(self.dir, "a.jsonl"), "a", BASE,
                         [(VERIFY_CMD, 10.0)])
        write_transcript(os.path.join(self.dir, "b.jsonl"), "b",
                         BASE + timedelta(days=3), [(VERIFY_CMD, 10.0)])
        report = census_mod.census(self.dir)
        self.assertEqual(report["window"]["source"], "corpus")
        self.assertEqual(report["totals"]["runs"], 2)


class GateTests(unittest.TestCase):
    """The issue's gate arithmetic, for all three outcomes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def _write_runs(self, count, steps, start=BASE, step_hours=6):
        for index in range(count):
            write_transcript(
                os.path.join(self.dir, f"run-{index}.jsonl"), f"run-{index}",
                start + timedelta(hours=step_hours * index), steps)

    def test_proceed_when_cold_start_is_25_percent_and_10_runs_per_week(self):
        # 10 runs in a one-week window: 10 runs/week; 60% cold start.
        self._write_runs(10, [(SETUP_CMD, 60.0), (VERIFY_CMD, 40.0)])
        report = census_mod.census(self.dir, WINDOW_SINCE, WINDOW_UNTIL)
        gate = report["gate"]
        self.assertEqual(gate["verdict"], "proceed")
        self.assertEqual(gate["top_candidate"], "pytest")
        self.assertEqual(gate["top_candidate_cold_start_share_pct"], 60.0)
        self.assertEqual(gate["top_candidate_runs_per_week"], 10.0)

    def test_kill_when_cold_start_is_below_10_percent_everywhere(self):
        self._write_runs(2, [("pip install -e .", 5.0), (VERIFY_CMD, 95.0)])
        report = census_mod.census(self.dir, WINDOW_SINCE, WINDOW_UNTIL)
        gate = report["gate"]
        self.assertEqual(gate["verdict"], "kill")
        self.assertLess(gate["max_cold_start_share_pct"], 10.0)

    def test_below_gate_when_cold_start_is_high_but_frequency_is_low(self):
        # 60% cold start, but only 2 runs/week -> neither clause fires.
        self._write_runs(2, [(SETUP_CMD, 60.0), (VERIFY_CMD, 40.0)])
        report = census_mod.census(self.dir, WINDOW_SINCE, WINDOW_UNTIL)
        gate = report["gate"]
        self.assertEqual(gate["verdict"], "below_gate")
        self.assertEqual(gate["top_candidate_cold_start_share_pct"], 60.0)
        self.assertEqual(gate["top_candidate_runs_per_week"], 2.0)

    def test_below_gate_when_share_is_low_but_frequency_is_high(self):
        # 20% cold start (>= 10%, so not a kill) with 10 runs/week.
        self._write_runs(10, [("pip install -e .", 20.0), (VERIFY_CMD, 80.0)])
        report = census_mod.census(self.dir, WINDOW_SINCE, WINDOW_UNTIL)
        gate = report["gate"]
        self.assertEqual(gate["verdict"], "below_gate")
        self.assertEqual(gate["top_candidate_runs_per_week"], 10.0)

    def test_gate_thresholds_are_the_issue_thresholds(self):
        self.assertEqual(census_mod.GATE_COLD_START_SHARE_PCT, 25.0)
        self.assertEqual(census_mod.GATE_RUNS_PER_WEEK, 10.0)
        self.assertEqual(census_mod.KILL_COLD_START_SHARE_PCT, 10.0)

    def test_top_candidate_is_the_type_with_the_highest_cold_start_cost(self):
        # a cargo run pays far more cold start than the pytest runs.
        self._write_runs(3, [(SETUP_CMD, 10.0), (VERIFY_CMD, 90.0)],
                         start=BASE, step_hours=1)
        write_transcript(os.path.join(self.dir, "cargo.jsonl"), "cargo",
                         BASE + timedelta(hours=10),
                         [("cargo build --release", 300.0), ("cargo test", 100.0)])
        report = census_mod.census(self.dir, WINDOW_SINCE, WINDOW_UNTIL)
        self.assertEqual(report["gate"]["top_candidate"], "cargo")
        self.assertEqual(report["gate"]["verdict"], "below_gate")


class CliTests(unittest.TestCase):
    """The CLI contract: arguments, JSON shape, exit codes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(BIN / "warm_verifier_census.py"), *args],
            capture_output=True, text=True, cwd=str(REPO), check=False)

    def test_cli_emits_runs_aggregate_and_gate_json(self):
        write_transcript(os.path.join(self.dir, "run-0.jsonl"), "run-0", BASE,
                         [(SETUP_CMD, 60.0), (VERIFY_CMD, 40.0)])
        result = self._run("census", "--transcripts-dir", self.dir,
                           "--since", iso(WINDOW_SINCE),
                           "--until", iso(WINDOW_UNTIL), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["issue"], 154)
        self.assertEqual(len(payload["runs"]), 1)
        self.assertEqual(payload["aggregate"][0]["verifier_type"], "pytest")
        self.assertIn(payload["gate"]["verdict"], ("proceed", "kill", "below_gate"))
        self.assertIn("cold_start", payload["definitions"])

    def test_cli_exits_2_when_the_transcripts_dir_is_missing(self):
        result = self._run("census", "--transcripts-dir",
                           os.path.join(self.dir, "nope"), "--json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not readable", result.stderr)

    def test_cli_exits_2_on_unparseable_since(self):
        result = self._run("census", "--transcripts-dir", self.dir,
                           "--since", "not-a-date", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unparseable", result.stderr)


if __name__ == "__main__":
    unittest.main()
