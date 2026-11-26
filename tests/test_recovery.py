"""Tests for bin/recovery.py (trouble-recovery readout, issue #194).

The contract these tests pin:

  * the frozen Phase 0 trouble definition: a worker row whose outcome
    starts with "failed" or is "escalated", or a shepherd row for one
    of the task's PRs with ci_checks.failure at least 1; untroubled
    tasks are excluded;
  * outcomes are attention.py's classes, reused not redefined:
    recovered is correctly_finished or merged_censored (never
    finished_with_rework), abandoned is failed_abandoned, open is
    open_unresolved;
  * the breakdowns carry n per cell, and cells under the readable
    floor are labelled too small to read;
  * the redispatch fields narrowed_brief and
    failover_reflection_carried: absent means unknown, never false,
    old rows without the fields count exactly as before, and coverage
    is reported when the fields are present.

Run: python3 -m pytest tests/test_recovery.py -q
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import attention  # noqa: E402
import recovery  # noqa: E402

SINCE = datetime(2026, 11, 1, tzinfo=UTC)
UNTIL = datetime(2026, 11, 20, tzinfo=UTC)
BASE = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def iso(stamp):
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def worker(repo, issue, ts, outcome="pr_opened", pr=None, tier="balanced", **extra):
    row = {
        "ts": iso(ts),
        "repo": repo,
        "issue": issue,
        "outcome": outcome,
        "tier": tier,
        "tokens_in": 100,
        "tokens_out": 50,
        "pr_url": "",
    }
    if pr is not None:
        row["pr_url"] = f"https://github.com/example/{repo}/pull/{pr}"
    row.update(extra)
    return row


def shepherd(repo, pr, action, ts, **extra):
    row = {"ts": iso(ts), "repo": f"example/{repo}", "pr": pr, "action": action}
    row.update(extra)
    return row


def summary(workers, shepherds, use_gh=False):
    return recovery.build_summary(workers, shepherds, SINCE, UNTIL, use_gh=use_gh)


class TestTroubleDetection(unittest.TestCase):
    def test_failed_worker_row_is_trouble_and_untroubled_task_excluded(self):
        workers = [
            worker("r", 1, BASE, outcome="failed:worker-error"),
            worker("r", 2, BASE, outcome="pr_opened", pr=20),
        ]
        shepherds = [shepherd("r", 20, "merged", BASE + timedelta(hours=1))]
        result = summary(workers, shepherds)
        self.assertEqual(result["n_tasks"], 2)
        self.assertEqual(result["n_troubled"], 1)
        self.assertEqual(result["tasks"][0]["task"], "r#1")
        self.assertEqual(result["tasks"][0]["trouble_source"], "worker")

    def test_escalated_worker_row_is_trouble(self):
        workers = [worker("r", 1, BASE, outcome="escalated")]
        result = summary(workers, [])
        self.assertEqual(result["n_troubled"], 1)

    def test_shepherd_ci_failure_is_trouble(self):
        workers = [worker("r", 1, BASE, outcome="pr_opened", pr=10)]
        shepherds = [
            shepherd(
                "r",
                10,
                "other",
                BASE + timedelta(hours=1),
                ci_checks={"total": 3, "success": 2, "failure": 1},
            ),
            shepherd("r", 10, "merged", BASE + timedelta(hours=2)),
        ]
        result = summary(workers, shepherds)
        self.assertEqual(result["n_troubled"], 1)
        self.assertEqual(result["tasks"][0]["trouble_source"], "shepherd")
        self.assertTrue(result["tasks"][0]["recovered"])

    def test_shepherd_ci_success_is_not_trouble(self):
        workers = [worker("r", 1, BASE, outcome="pr_opened", pr=10)]
        shepherds = [
            shepherd(
                "r",
                10,
                "other",
                BASE + timedelta(hours=1),
                ci_checks={"total": 3, "success": 3, "failure": 0},
            ),
            shepherd("r", 10, "merged", BASE + timedelta(hours=2)),
        ]
        result = summary(workers, shepherds)
        self.assertEqual(result["n_troubled"], 0)


class TestOutcomes(unittest.TestCase):
    def test_recovered_abandoned_and_open_counts(self):
        workers = [
            worker("r", 1, BASE, outcome="failed:x"),
            worker("r", 1, BASE + timedelta(hours=1), pr=10),
            worker("r", 2, BASE, outcome="failed:y"),
            worker("r", 3, BASE, outcome="failed:z", pr=30),
        ]
        shepherds = [
            shepherd("r", 10, "merged", BASE + timedelta(hours=2)),
        ]
        result = summary(workers, shepherds)
        self.assertEqual(result["n_troubled"], 3)
        self.assertEqual(result["recovered"], {"n": 1, "pct": 33.3})
        self.assertEqual(result["abandoned"], {"n": 1, "pct": 33.3})
        self.assertEqual(result["open_unresolved"], {"n": 1, "pct": 33.3})
        by_task = {t["task"]: t for t in result["tasks"]}
        self.assertTrue(by_task["r#1"]["recovered"])
        self.assertEqual(by_task["r#2"]["outcome"], "failed_abandoned")
        self.assertEqual(by_task["r#3"]["outcome"], "open_unresolved")

    def test_extra_runs_and_tokens_after_first_trouble(self):
        workers = [
            worker("r", 1, BASE, outcome="failed:x", tokens_in=1000, tokens_out=0),
            worker("r", 1, BASE + timedelta(hours=1), tokens_in=200, tokens_out=100),
            worker("r", 1, BASE + timedelta(hours=2), pr=10, tokens_total=500),
        ]
        shepherds = [shepherd("r", 10, "merged", BASE + timedelta(hours=3))]
        result = summary(workers, shepherds)
        task = result["tasks"][0]
        self.assertEqual(task["extra_runs"], 2)
        self.assertEqual(task["extra_tokens"], 800)
        self.assertEqual(result["extra_after_trouble"]["median_runs"], 2)
        self.assertEqual(result["extra_after_trouble"]["total_tokens"], 800)

    def test_finished_with_rework_is_not_recovered(self):
        workers = [
            worker("r", 1, BASE, outcome="failed:x"),
            worker("r", 1, BASE + timedelta(hours=1), pr=10),
        ]
        shepherds = [shepherd("r", 10, "merged", BASE + timedelta(hours=2))]
        with mock.patch.object(attention, "gh_rework_check", return_value="rework"):
            result = summary(workers, shepherds, use_gh=True)
        self.assertEqual(result["recovered"]["n"], 0)
        self.assertEqual(result["troubled_by_outcome"], {"finished_with_rework": 1})


class TestBreakdowns(unittest.TestCase):
    def test_tier_change_breakdown(self):
        workers = [
            worker("r", 1, BASE, outcome="failed:x", tier="balanced"),
            worker("r", 1, BASE + timedelta(hours=1), pr=10, tier="flagship"),
            worker("r", 2, BASE, outcome="failed:y", tier="cheap"),
            worker("r", 2, BASE + timedelta(hours=1), pr=20, tier="cheap"),
        ]
        shepherds = [
            shepherd("r", 10, "merged", BASE + timedelta(hours=2)),
            shepherd("r", 20, "merged", BASE + timedelta(hours=2)),
        ]
        result = summary(workers, shepherds)
        cells = result["breakdowns"]["tier_change"]
        self.assertEqual(cells["tier_changed"]["n"], 1)
        self.assertEqual(cells["tier_changed"]["recovered"], 1)
        self.assertEqual(cells["no_tier_change"]["n"], 1)
        self.assertEqual(result["breakdowns"]["trouble_tier"]["cheap"]["n"], 1)
        self.assertEqual(result["breakdowns"]["repo"]["r"]["n"], 2)

    def test_small_cells_labelled_too_small_to_read(self):
        workers = [worker("r", 1, BASE, outcome="failed:x")]
        result = summary(workers, [])
        cell = result["breakdowns"]["repo"]["r"]
        self.assertFalse(cell["readable"])
        self.assertEqual(cell["note"], "too small to read")

    def test_split_suggested_breakdown(self):
        workers = [
            worker("r", 1, BASE, outcome="failed:x"),
            worker("r", 1, BASE + timedelta(hours=1), outcome="split_suggested"),
        ]
        result = summary(workers, [])
        cells = result["breakdowns"]["split_suggested"]
        self.assertEqual(cells["split_suggested"]["n"], 1)
        self.assertNotIn("no_split", cells)


class TestRedispatchFields(unittest.TestCase):
    def test_absent_fields_are_unknown_not_false(self):
        workers = [
            worker("r", 1, BASE, outcome="failed:x"),
            worker("r", 1, BASE + timedelta(hours=1), pr=10),
        ]
        shepherds = [shepherd("r", 10, "merged", BASE + timedelta(hours=2))]
        result = summary(workers, shepherds)
        for field in recovery.REDISPATCH_FIELDS:
            report = result["redispatch_fields"][field]
            self.assertEqual(report["coverage"]["recorded_rows"], 0)
            self.assertEqual(report["coverage"]["tasks_unknown"], 1)
            self.assertEqual(report["breakdown"]["unknown"]["n"], 1)
            self.assertNotIn("false", report["breakdown"])

    def test_present_fields_classify_true_and_false_with_coverage(self):
        workers = [
            worker("r", 1, BASE, outcome="failed:x"),
            worker(
                "r",
                1,
                BASE + timedelta(hours=1),
                pr=10,
                narrowed_brief=True,
                failover_reflection_carried=False,
            ),
            worker("r", 2, BASE, outcome="failed:y"),
            worker(
                "r",
                2,
                BASE + timedelta(hours=1),
                narrowed_brief=False,
                failover_reflection_carried=True,
            ),
        ]
        shepherds = [shepherd("r", 10, "merged", BASE + timedelta(hours=2))]
        result = summary(workers, shepherds)
        narrowed = result["redispatch_fields"]["narrowed_brief"]
        self.assertEqual(narrowed["coverage"]["post_trouble_rows"], 2)
        self.assertEqual(narrowed["coverage"]["recorded_rows"], 2)
        self.assertEqual(narrowed["coverage"]["tasks_recorded"], 2)
        self.assertEqual(narrowed["breakdown"]["true"]["n"], 1)
        self.assertEqual(narrowed["breakdown"]["true"]["recovered"], 1)
        self.assertEqual(narrowed["breakdown"]["false"]["n"], 1)
        reflection = result["redispatch_fields"]["failover_reflection_carried"]
        self.assertEqual(reflection["breakdown"]["true"]["n"], 1)
        self.assertEqual(reflection["breakdown"]["false"]["n"], 1)

    def test_non_bool_field_value_is_not_recorded(self):
        workers = [
            worker("r", 1, BASE, outcome="failed:x"),
            worker("r", 1, BASE + timedelta(hours=1), narrowed_brief="yes"),
        ]
        result = summary(workers, [])
        report = result["redispatch_fields"]["narrowed_brief"]
        self.assertEqual(report["coverage"]["recorded_rows"], 0)
        self.assertEqual(report["breakdown"]["unknown"]["n"], 1)


class TestCli(unittest.TestCase):
    def test_missing_ledger_exits_2(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin" / "recovery.py"),
                "summary",
                "--since",
                "2026-11-01",
                "--state-dir",
                "/nonexistent-state-dir",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
