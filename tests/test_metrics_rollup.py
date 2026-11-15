"""Tests for bin/metrics_rollup.py (issue #59, docs/METRICS.md Phase 0).

The rollup joins the worker and the shepherd ledger into PR-lifecycle
rows and daily per-stratum rows.  What the tests pin is the *method*,
against synthetic ledger fixtures in this repository (never the
operator's real state dir):

  * every PR-level number counts distinct PRs, not raw ledger rows
    (a shepherd ledger row is a polling event, not a PR);
  * a merged PR with no worker row and a PR opened before the shepherd
    ledger starts are FLAGGED on their row, never dropped;
  * run-level aggregates agree with bin/trace_summary.py and the
    shepherd action counts agree with bin/shepherd_ledger.py summary;
  * the ledgers are never written to -- the only output is
    metrics_rollup.jsonl.

Run: python3 -m pytest tests/test_metrics_rollup.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "metrics_rollup.py"
TRACE_SUMMARY = REPO / "bin" / "trace_summary.py"
SHEPHERD_LEDGER = REPO / "bin" / "shepherd_ledger.py"
FIXTURE = REPO / "tests" / "fixtures" / "metrics_rollup_state"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_rollup():
    return load_module(SCRIPT, "plan_coder_metrics_rollup")


def run_rollup(module, argv):
    """Run main() and return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue()


def read_jsonl(path: Path) -> list:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def fingerprint(directory: Path) -> dict:
    """Content hash + size of every file under `directory`."""
    out = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            out[str(path.relative_to(directory))] = (
                hashlib.sha256(data).hexdigest(), len(data))
    return out


@unittest.skipUnless(SCRIPT.exists(), "bin/metrics_rollup.py not present")
class RollupFixtureTest(unittest.TestCase):
    """The fixture rollup: distinct PRs, flags, and the baseline numbers."""

    def setUp(self):
        self.module = load_rollup()
        self.tmp = Path(tempfile.mkdtemp(prefix="metrics-rollup-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(FIXTURE, self.tmp / "state")
        self.state = self.tmp / "state"
        code, out, err = run_rollup(self.module,
                                    ["--state-dir", str(self.state)])
        self.assertEqual(code, 0, err)
        self.summary = json.loads(
            run_rollup(self.module, ["--state-dir", str(self.state),
                                      "--json"])[1])
        self.rows = read_jsonl(self.state / "metrics_rollup.jsonl")
        self.pr_rows = [r for r in self.rows if r["type"] == "pr"]
        self.daily_rows = [r for r in self.rows if r["type"] == "daily"]
        self.by_url = {r["pr_url"]: r for r in self.pr_rows if r["pr_url"]}

    def test_exit_zero_and_rollup_file_written(self):
        self.assertTrue((self.state / "metrics_rollup.jsonl").exists())
        self.assertEqual(self.summary["rows"]["pr"], len(self.pr_rows))
        self.assertEqual(self.summary["rows"]["daily"], len(self.daily_rows))

    def test_run_level_numbers_match_trace_summary(self):
        trace = load_module(TRACE_SUMMARY, "plan_coder_trace_summary")
        worker_rows = read_jsonl(self.state / "worker_ledger.jsonl")
        expected = trace.summarize(worker_rows)["overall"]
        self.assertEqual(self.summary["worker"], expected)
        self.assertEqual(self.summary["baseline"]["worker_runs"], 4)
        self.assertEqual(self.summary["baseline"]["pr_opened"], 3)
        self.assertEqual(self.summary["baseline"]["pr_opened_rate"], 0.75)

    def test_shepherd_action_counts_match_shepherd_ledger_summary(self):
        shepherd = load_module(SHEPHERD_LEDGER, "plan_coder_shepherd_ledger")
        expected = shepherd.summarize(
            str(self.state / "shepherd_ledger.jsonl"))
        self.assertEqual(self.summary["shepherd"]["by_action"],
                         expected["by_action"])

    def test_one_row_per_distinct_pr_not_per_ledger_row(self):
        # The shepherd ledger has three rows for PR 101 and one for PR
        # 999; the rollup has one lifecycle row per distinct PR.
        keys = [r["pr_key"] for r in self.pr_rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(self.pr_rows), 5)
        self.assertEqual(self.summary["baseline"]["distinct_prs_opened"], 3)
        self.assertEqual(
            self.summary["baseline"]["distinct_merged_prs"], 3)

    def test_duplicate_shepherd_rows_count_as_action_days(self):
        row = self.by_url["https://github.com/you/repo-a/pull/101"]
        self.assertEqual(row["shepherd_actions"], 4)
        # three distinct days: 2026-11-09, 2026-11-10 (and the
        # duplicate rows land on the same day)
        self.assertEqual(row["shepherd_action_days"], 2)
        self.assertEqual(row["outcome"], "merged")
        self.assertIn("needs_alex", row["flags"])

    def test_unjoinable_merged_pr_is_flagged_not_dropped(self):
        row = self.by_url["https://github.com/you/repo-c/pull/999"]
        self.assertTrue(row["merged"])
        self.assertFalse(row["opened"])
        self.assertIn("no_worker_row", row["flags"])
        self.assertIsNone(row["tokens_out"])
        self.assertEqual(
            self.summary["baseline"]["merged_prs_unjoinable"], 1)
        self.assertEqual(
            self.summary["baseline"]["merged_prs_joinable"], 2)

    def test_pr_opened_before_the_shepherd_gap_is_flagged(self):
        row = self.by_url["https://github.com/you/repo-a/pull/102"]
        self.assertTrue(row["opened"])
        self.assertIn("shepherd_gap", row["flags"])
        self.assertIn("no_shepherd_activity", row["flags"])
        self.assertEqual(row["outcome"], "open")

    def test_unstable_merge_is_flagged(self):
        row = self.by_url["https://github.com/you/repo-a/pull/104"]
        self.assertIn("unstable_merge", row["flags"])

    def test_tokens_per_merged_pr_uses_joined_prs_only(self):
        base = self.summary["baseline"]
        self.assertEqual(base["tokens_out_per_merged_pr_n"], 2)
        # PR 101 -> 5k, PR 104 -> 70k
        self.assertEqual(base["tokens_out_per_merged_pr_median"], 37500)
        self.assertEqual(base["tokens_out_per_merged_pr_mean"], 37500)

    def test_until_filter_reproduces_an_as_of_snapshot(self):
        # The spec's baseline is an as-of snapshot: --until keeps only
        # the rows up to that day, so the same numbers can be
        # regenerated from a grown ledger.
        code, out, err = run_rollup(self.module,
                                    ["--state-dir", str(self.state),
                                     "--until", "2026-11-09", "--json"])
        self.assertEqual(code, 0, err)
        summary = json.loads(out)
        self.assertEqual(summary["as_of_date"], "2026-11-09")
        self.assertEqual(summary["window"]["until"], "2026-11-09")
        # the fixture's 2026-11-01 and two 2026-11-02 runs
        self.assertEqual(summary["baseline"]["worker_runs"], 3)
        # the 2026-11-10 merge is outside the window; only the
        # shepherd-only merge of 2026-11-09 is inside it
        self.assertEqual(summary["baseline"]["distinct_merged_prs"], 1)
        self.assertEqual(summary["baseline"]["merged_prs_unjoinable"], 1)

    def test_null_tokens_do_not_break_the_rollup(self):
        row = self.by_url["https://github.com/you/repo-a/pull/102"]
        self.assertEqual(row["tokens_out"], 30000)
        failed = [r for r in self.daily_rows
                  if r["day"] == "2026-11-02" and r["scope"] == "stratum"
                  and r["repo"] == "repo-b"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["runs"], 1)
        self.assertIsNone(failed[0]["tokens_out_median"])

    def test_daily_rows_are_per_day_per_stratum(self):
        strata = {(r["day"], r["repo"], r["tier"], r["provider"])
                  for r in self.daily_rows if r["scope"] == "stratum"}
        self.assertIn(("2026-11-01", "repo-a", "cheap", "minimax"), strata)
        self.assertIn(("2026-11-02", "repo-a", "balanced", "minimax"), strata)
        self.assertIn(("2026-11-10", "repo-a", "flagship", "minimax"), strata)
        totals = [r for r in self.daily_rows if r["scope"] == "day_total"]
        # 2026-11-01, 2026-11-02 and 2026-11-10 have worker runs;
        # 2026-11-09 and 2026-11-12 have only merges -- a merge-only
        # day still gets a per-day total row, so merged PRs are never
        # invisible.
        self.assertEqual(len(totals), 5)
        self.assertEqual({r["day"] for r in totals},
                         {"2026-11-01", "2026-11-02", "2026-11-09",
                          "2026-11-10", "2026-11-12"})
        merged = [r for r in totals if r["day"] == "2026-11-12"]
        self.assertEqual(merged[0]["merged_prs"], 1)
        self.assertEqual(merged[0]["runs"], 0)

    def test_rollup_is_re_runnable_and_stable(self):
        before = read_jsonl(self.state / "metrics_rollup.jsonl")
        code, _out, err = run_rollup(self.module,
                                    ["--state-dir", str(self.state)])
        self.assertEqual(code, 0, err)
        after = read_jsonl(self.state / "metrics_rollup.jsonl")
        self.assertEqual(before, after)


@unittest.skipUnless(SCRIPT.exists(), "bin/metrics_rollup.py not present")
class RollupReadOnlyTest(unittest.TestCase):
    """The rollup never writes to the ledgers it reads."""

    def setUp(self):
        self.module = load_rollup()
        self.tmp = Path(tempfile.mkdtemp(prefix="metrics-rollup-ro-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(FIXTURE, self.tmp / "state")
        self.state = self.tmp / "state"

    def test_ledgers_are_untouched(self):
        before = fingerprint(self.state)
        code, _out, err = run_rollup(self.module,
                                    ["--state-dir", str(self.state)])
        self.assertEqual(code, 0, err)
        after = fingerprint(self.state)
        self.assertEqual(after["worker_ledger.jsonl"],
                         before["worker_ledger.jsonl"])
        self.assertEqual(after["shepherd_ledger.jsonl"],
                         before["shepherd_ledger.jsonl"])
        self.assertEqual(set(after) - set(before), {"metrics_rollup.jsonl"})

    def test_stdout_output_writes_no_file(self):
        before = fingerprint(self.state)
        code, out, err = run_rollup(self.module,
                                    ["--state-dir", str(self.state),
                                     "--out", "-"])
        self.assertEqual(code, 0, err)
        self.assertEqual(fingerprint(self.state), before)
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertTrue(lines)
        for line in lines:
            self.assertIn(json.loads(line)["type"], ("pr", "daily"))


@unittest.skipUnless(SCRIPT.exists(), "bin/metrics_rollup.py not present")
class RollupMalformedLedgerTest(unittest.TestCase):
    """A ledger line that is not a JSON object is skipped, not fatal."""

    def setUp(self):
        self.module = load_rollup()
        self.tmp = Path(tempfile.mkdtemp(prefix="metrics-rollup-bad-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = self.tmp / "state"
        self.state.mkdir()
        good = (FIXTURE / "worker_ledger.jsonl").read_text(
            encoding="utf-8").splitlines()
        (self.state / "worker_ledger.jsonl").write_text(
            "\n".join(good[:2] + ["this line is not json",
                                 '["not", "an", "object"]']) + "\n",
            encoding="utf-8")
        (self.state / "shepherd_ledger.jsonl").write_text(
            (FIXTURE / "shepherd_ledger.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8")

    def test_unparseable_lines_are_skipped(self):
        code, _out, err = run_rollup(self.module,
                                    ["--state-dir", str(self.state)])
        self.assertEqual(code, 0, err)
        summary = json.loads(run_rollup(self.module,
                                        ["--state-dir", str(self.state),
                                         "--json"])[1])
        self.assertEqual(summary["baseline"]["worker_runs"], 2)
        # 2 worker PRs + the 4 shepherd-only/known PRs (101 is shared)
        self.assertEqual(summary["rows"]["pr"], 5)


@unittest.skipUnless(SCRIPT.exists(), "bin/metrics_rollup.py not present")
class RollupMissingLedgerTest(unittest.TestCase):
    """Missing ledgers degrade gracefully, they do not crash."""

    def setUp(self):
        self.module = load_rollup()
        self.tmp = Path(tempfile.mkdtemp(prefix="metrics-rollup-missing-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_missing_worker_ledger_exits_two(self):
        code, _out, err = run_rollup(self.module,
                                    ["--state-dir", str(self.tmp)])
        self.assertEqual(code, 2)
        self.assertIn("worker ledger not found", err)

    def test_missing_shepherd_ledger_still_rolls_up(self):
        state = self.tmp / "state"
        state.mkdir()
        shutil.copy(FIXTURE / "worker_ledger.jsonl", state)
        code, _out, err = run_rollup(self.module, ["--state-dir", str(state)])
        self.assertEqual(code, 0, err)
        rows = read_jsonl(state / "metrics_rollup.jsonl")
        pr_rows = [r for r in rows if r["type"] == "pr"]
        self.assertEqual(len(pr_rows), 3)
        for row in pr_rows:
            self.assertIn("no_shepherd_activity", row["flags"])
        summary = json.loads(run_rollup(self.module,
                                        ["--state-dir", str(state),
                                         "--json"])[1])
        self.assertTrue(summary["shepherd"]["missing"])
        self.assertIsNone(summary["baseline"]["shepherd_ledger_start"])

    def test_empty_ledgers_produce_an_empty_rollup(self):
        state = self.tmp / "empty"
        state.mkdir()
        (state / "worker_ledger.jsonl").write_text("", encoding="utf-8")
        (state / "shepherd_ledger.jsonl").write_text("", encoding="utf-8")
        code, out, err = run_rollup(self.module, ["--state-dir", str(state)])
        self.assertEqual(code, 0, err)
        self.assertIn("no worker runs", out)
        rows = read_jsonl(state / "metrics_rollup.jsonl")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
