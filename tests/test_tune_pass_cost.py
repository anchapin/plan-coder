"""Tests for the harness-tune pass-cost ledger (issue #86, Finding 2).

A tune pass's own token cost was recorded nowhere. The record now
lives in its own append-only ledger, ``<state.dir>/harness_tune_passes.jsonl``,
one row per pass: {ts, tokens_in, tokens_out, wall_s, source}.

The contract these tests pin:

  * the row shape parses and carries the five named fields;
  * the token source is NAMED on the row (the scheduler's cron run
    record, the pass's own transcript, or "not_available");
  * when no source is reachable the row records tokens_in/tokens_out
    as the string "not_available" and still records wall_s and the
    source — that fallback is a valid row, not a failure;
  * the ledger is its own file: nothing is appended to the worker
    ledger or the spike ledgers (they feed the overhead denominators).

Run: python3 -m pytest tests/test_tune_pass_cost.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "tune_pass_cost.py"
LEDGER = "harness_tune_passes.jsonl"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(REPO),
                          check=False)


class TunePassCostCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-tune-cost-"))
        self.state = self.tmp / "state"
        self.state.mkdir()

    def rows(self) -> list[dict]:
        path = self.state / LEDGER
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestRecordShape(TunePassCostCase):

    def test_fallback_row_shape(self):
        """No token source available: the defined fallback row."""
        r = run("append", "--state-dir", str(self.state),
                "--wall-s", "42.5")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(set(row),
                         {"ts", "tokens_in", "tokens_out", "wall_s", "source"})
        self.assertEqual(row["tokens_in"], "not_available")
        self.assertEqual(row["tokens_out"], "not_available")
        self.assertEqual(row["wall_s"], 42.5)
        self.assertIn("not_available", row["source"])
        self.assertRegex(row["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_cron_run_record_source(self):
        """The named source: the scheduler's cron run record."""
        rec = self.tmp / "cron-run.json"
        rec.write_text(json.dumps({"usage": {"prompt_tokens": 1200,
                                              "completion_tokens": 340},
                                    "duration_s": 91}))
        r = run("append", "--state-dir", str(self.state),
                "--cron-run", str(rec))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.rows()[0]
        self.assertEqual(row["tokens_in"], 1200)
        self.assertEqual(row["tokens_out"], 340)
        self.assertEqual(row["wall_s"], 91)
        self.assertIn("scheduler_cron_run_record", row["source"])

    def test_transcript_source_sums_usage(self):
        """The named source: the pass's own transcript, summed the way
        bin/jittest.py sums transcript usage."""
        tr = self.tmp / "pass.jsonl"
        tr.write_text("".join(json.dumps(r) + "\n" for r in (
            {"tokens_in": 100, "tokens_out": 20},
            {"tokens_in": 150, "tokens_out": 35},
            {"note": "no counters"})))
        r = run("append", "--state-dir", str(self.state),
                "--transcript", str(tr), "--wall-s", "10")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.rows()[0]
        self.assertEqual(row["tokens_in"], 250)
        self.assertEqual(row["tokens_out"], 55)
        self.assertIn("pass_transcript", row["source"])

    def test_last_reports_the_previous_pass_with_its_source(self):
        """The next tune report can state the previous pass's cost."""
        rec = self.tmp / "cron-run.json"
        rec.write_text(json.dumps({"usage": {"prompt_tokens": 10,
                                              "completion_tokens": 20},
                                    "duration_s": 5}))
        run("append", "--state-dir", str(self.state), "--cron-run", str(rec))
        r = run("last", "--state-dir", str(self.state), "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = json.loads(r.stdout)
        self.assertEqual(row["tokens_in"], 10)
        self.assertEqual(row["tokens_out"], 20)
        self.assertIn("source", row)

    def test_no_pass_recorded_yet_is_not_an_error(self):
        r = run("last", "--state-dir", str(self.state))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no harness-tune pass recorded", r.stdout)

    def test_ledger_is_its_own_file(self):
        """The pass cost never lands in the worker ledger or the spike
        ledgers — those feed the overhead denominators."""
        run("append", "--state-dir", str(self.state), "--wall-s", "1")
        self.assertTrue((self.state / LEDGER).exists())
        for other in ("worker_ledger.jsonl", "goldfish_ledger.jsonl",
                      "jittest_ledger.jsonl", "shepherd_ledger.jsonl"):
            self.assertFalse((self.state / other).exists(), other)


if __name__ == "__main__":
    unittest.main()
