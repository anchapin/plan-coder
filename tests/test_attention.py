"""Tests for bin/attention.py (attention readout, issue #125).

The contract these tests pin:

  * the frozen unit definition from the #125 brief: flag presentations
    counted per shepherd row, flag episodes as maximal runs of
    consecutive shepherd cycles (>30 minute gap rule), fix attempts,
    reruns, re-dispatches (worker runs beyond the first), escalations,
    and both totals always reported, never summed;
  * outcome classes, including the censoring rule: the headline
    median / p90 is computed over correctly_finished tasks only when
    that n is at least 5, otherwise over merged_censored tasks and
    labelled "proxy, censored";
  * the rework reading: a source issue counts only when it was
    reopened after the merge (closed at or before the merge, open
    again after, inside 14 days); standing open is not rework, and a
    gh failure degrades to merged_censored with rework_check
    "unavailable", never to correctly_finished;
  * the Phase 0 regression fixture: a synthetic ledger pair encoding
    the Phase 0 window's per-task structure (report Table B) must
    reproduce the corrected frozen figures (28 tasks, 32 presentation
    units, 19 distinct units, 81.2% on not-correctly-finished tasks,
    19 merged tasks all merged_censored, proxy median 0 / p90 1).

Run: python3 -m pytest tests/test_attention.py -q
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

SINCE = datetime(2026, 11, 17, 11, 22, 49, tzinfo=UTC)
UNTIL = datetime(2026, 11, 18, 13, 29, 9, tzinfo=UTC)


def worker(repo, issue, ts, pr=None, outcome="pr_opened"):
    row = {"ts": ts, "repo": repo, "issue": issue, "outcome": outcome, "pr_url": ""}
    if pr is not None:
        row["pr_url"] = f"https://github.com/anchapin/{repo}/pull/{pr}"
    return row


def shepherd(repo, pr, action, ts):
    return {"ts": ts, "repo": f"anchapin/{repo}", "pr": pr, "action": action}


def iso(stamp):
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# episodes and cycles
# ---------------------------------------------------------------------------
class TestEpisodes(unittest.TestCase):
    def test_consecutive_cycles_are_one_episode(self):
        events = [{"cycle": c, "kind": "flag"} for c in (0, 1, 2)]
        self.assertEqual(attention.count_episodes(events), 1)

    def test_gap_cycle_starts_a_new_episode(self):
        events = [{"cycle": c, "kind": "flag"} for c in (0, 1, 3)]
        self.assertEqual(attention.count_episodes(events), 2)

    def test_terminal_disposition_breaks_the_run(self):
        events = [
            {"cycle": 0, "kind": "flag"},
            {"cycle": 1, "kind": "terminal"},
            {"cycle": 2, "kind": "flag"},
        ]
        self.assertEqual(attention.count_episodes(events), 2)

    def test_duplicate_presentations_in_one_cycle_count_once(self):
        events = [{"cycle": 0, "kind": "flag"}, {"cycle": 0, "kind": "flag"}]
        self.assertEqual(attention.count_episodes(events), 1)

    def test_cycle_gap_rule(self):
        base = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
        rows = [
            {"ts": iso(base)},
            {"ts": iso(base + timedelta(minutes=20))},
            {"ts": iso(base + timedelta(minutes=40))},
            {"ts": iso(base + timedelta(minutes=71))},
        ]
        cycled = attention.assign_cycles(rows)
        self.assertEqual([r["_cycle"] for r in cycled], [0, 0, 0, 1])


# ---------------------------------------------------------------------------
# unit counting on a small fixture
# ---------------------------------------------------------------------------
class TestUnitCounting(unittest.TestCase):
    def test_all_unit_types(self):
        base = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
        workers = [
            worker("r", 1, iso(base), pr=10),
            worker("r", 1, iso(base + timedelta(minutes=5)), pr=10),
            worker("r", 1, iso(base + timedelta(minutes=9)), pr=10, outcome="escalated"),
        ]
        shepherds = [
            shepherd("r", 10, "needs_alex", iso(base + timedelta(hours=1))),
            shepherd("r", 10, "needs_operator", iso(base + timedelta(hours=2, minutes=50))),
            shepherd("r", 99, "other", iso(base + timedelta(hours=1, minutes=45))),
            shepherd("r", 10, "fix_attempt", iso(base + timedelta(hours=3))),
            shepherd("r", 10, "rerun", iso(base + timedelta(hours=3, minutes=30))),
            shepherd("r", 10, "merged_by_shepherd", iso(base + timedelta(hours=4))),
        ]
        summary = attention.build_summary(
            workers, shepherds, SINCE, UNTIL, use_gh=False
        )
        self.assertEqual(summary["n_tasks"], 1)
        task = summary["tasks"][0]
        self.assertEqual(task["units"]["flag_presentations"], 2)
        self.assertEqual(task["units"]["flag_episodes"], 2)
        self.assertEqual(task["units"]["fix_attempts"], 1)
        self.assertEqual(task["units"]["reruns"], 1)
        self.assertEqual(task["units"]["redispatches"], 2)
        self.assertEqual(task["units"]["escalations"], 1)
        self.assertEqual(task["units_total_presentations"], 2 + 1 + 1 + 2 + 1)
        self.assertEqual(task["units_total_distinct"], 2 + 1 + 1 + 2 + 1)
        self.assertEqual(task["outcome"], "merged_censored")
        self.assertIn("Ledger-visible attention only", summary["missing_units_note"])


# ---------------------------------------------------------------------------
# rework: reopened only
# ---------------------------------------------------------------------------
MERGED_AT = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def timeline(*pairs):
    return [{"event": name, "created_at": iso(stamp)} for name, stamp in pairs]


class TestReopenedAfterMerge(unittest.TestCase):
    def test_closed_then_reopened_within_window_is_rework(self):
        events = timeline(
            ("closed", MERGED_AT),
            ("reopened", MERGED_AT + timedelta(days=3)),
        )
        self.assertTrue(attention.reopened_after_merge(events, MERGED_AT))

    def test_standing_open_is_not_rework(self):
        events = timeline(("mentioned", MERGED_AT - timedelta(days=2)))
        self.assertFalse(attention.reopened_after_merge(events, MERGED_AT))
        self.assertFalse(attention.reopened_after_merge([], MERGED_AT))

    def test_reopen_after_the_window_is_not_rework(self):
        events = timeline(
            ("closed", MERGED_AT),
            ("reopened", MERGED_AT + timedelta(days=20)),
        )
        self.assertFalse(attention.reopened_after_merge(events, MERGED_AT))

    def test_reopen_without_a_prior_close_is_not_rework(self):
        events = timeline(("reopened", MERGED_AT + timedelta(days=1)))
        self.assertFalse(attention.reopened_after_merge(events, MERGED_AT))

    def test_close_after_the_merge_does_not_arm_the_rule(self):
        events = timeline(
            ("closed", MERGED_AT + timedelta(days=2)),
            ("reopened", MERGED_AT + timedelta(days=3)),
        )
        self.assertFalse(attention.reopened_after_merge(events, MERGED_AT))


def merged_fixture(merged_stamp):
    workers = [worker("r", 1, iso(merged_stamp - timedelta(minutes=30)), pr=10)]
    shepherds = [shepherd("r", 10, "merged_by_shepherd", iso(merged_stamp))]
    return workers, shepherds


class TestOutcomeClassification(unittest.TestCase):
    def classify(self, verdict, merged_stamp, readout):
        workers, shepherds = merged_fixture(merged_stamp)
        with mock.patch.object(attention, "gh_rework_check", return_value=verdict):
            summary = attention.build_summary(
                workers, shepherds, SINCE, UNTIL, use_gh=True, now=readout
            )
        return summary["tasks"][0]

    def test_clean_and_old_enough_is_correctly_finished(self):
        merged = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
        task = self.classify("clean", merged, merged + timedelta(days=20))
        self.assertEqual(task["outcome"], "correctly_finished")
        self.assertEqual(task["rework_check"], "clean")

    def test_clean_but_young_is_censored(self):
        merged = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
        task = self.classify("clean", merged, merged + timedelta(days=2))
        self.assertEqual(task["outcome"], "merged_censored")

    def test_rework_is_finished_with_rework(self):
        merged = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
        task = self.classify("rework", merged, merged + timedelta(days=2))
        self.assertEqual(task["outcome"], "finished_with_rework")

    def test_unavailable_degrades_to_censored_never_finished(self):
        merged = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
        task = self.classify("unavailable", merged, merged + timedelta(days=30))
        self.assertEqual(task["outcome"], "merged_censored")
        self.assertEqual(task["rework_check"], "unavailable")

    def test_no_pr_is_failed_abandoned_and_open_pr_is_unresolved(self):
        base = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
        workers = [
            worker("r", 1, iso(base), outcome="failed:worker-error"),
            worker("r", 2, iso(base), pr=11),
        ]
        summary = attention.build_summary(workers, [], SINCE, UNTIL, use_gh=False)
        outcomes = {t["issue"]: t["outcome"] for t in summary["tasks"]}
        self.assertEqual(outcomes[1], "failed_abandoned")
        self.assertEqual(outcomes[2], "open_unresolved")


class TestCensoringLabel(unittest.TestCase):
    def test_proxy_label_below_five_correctly_finished(self):
        merged = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
        workers, shepherds = merged_fixture(merged)
        with mock.patch.object(attention, "gh_rework_check", return_value="clean"):
            summary = attention.build_summary(
                workers, shepherds, SINCE, UNTIL, use_gh=True,
                now=merged + timedelta(days=20),
            )
        apcf = summary["attention_per_correctly_finished"]
        self.assertEqual(apcf["label"], "proxy, censored")
        self.assertEqual(apcf["basis"], "merged_censored")

    def test_no_proxy_label_at_five_correctly_finished(self):
        merged = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
        workers, shepherds = [], []
        for n in range(5):
            w, s = merged_fixture(merged)
            workers.append(worker("r", 100 + n, iso(merged - timedelta(minutes=30)), pr=200 + n))
            shepherds.append(shepherd("r", 200 + n, "merged_by_shepherd", iso(merged)))
        with mock.patch.object(attention, "gh_rework_check", return_value="clean"):
            summary = attention.build_summary(
                workers, shepherds, SINCE, UNTIL, use_gh=True,
                now=merged + timedelta(days=20),
            )
        apcf = summary["attention_per_correctly_finished"]
        self.assertIsNone(apcf["label"])
        self.assertEqual(apcf["basis"], "correctly_finished")
        self.assertEqual(apcf["n"], 5)


# ---------------------------------------------------------------------------
# Phase 0 regression fixture (synthetic; encodes report Table B)
# ---------------------------------------------------------------------------
# (repo, issue, runs, prs, kind) with kind merged / open / failed.
PHASE0_TASKS = [
    ("portkit-v2", 282, 2, [459], "merged"),
    ("planar-nexus", 2948, 2, [], "failed"),
    ("portkit-v2", 302, 1, [458], "merged"),
    ("plan-coder", 72, 1, [75], "merged"),
    ("plan-coder", 73, 1, [76], "merged"),
    ("plan-coder", 74, 1, [77], "open"),
    ("planar-nexus", 2976, 1, [], "failed"),
    ("planar-nexus", 2975, 1, [2979], "merged"),
    ("planar-nexus", 2978, 1, [2980], "merged"),
    ("passive-income-pocs", 1, 1, [4], "open"),
    ("portkit-v2", 299, 2, [460, 462], "merged"),
    ("plan-coder", 86, 2, [92], "merged"),
    ("passive-income-pocs", 2, 1, [5], "merged"),
    ("matchline", 1409, 1, [1410], "open"),
    ("passive-income-pocs", 3, 1, [6], "open"),
    ("portkit-v2", 287, 3, [], "failed"),
    ("plan-coder", 80, 1, [111], "merged"),
    ("home-energy-twin", 6, 1, [18], "merged"),
    ("portkit-v2", 243, 1, [461], "merged"),
    ("home-energy-twin", 2, 1, [19], "merged"),
    ("home-energy-twin", 4, 1, [20], "merged"),
    ("home-energy-twin", 3, 2, [22], "merged"),
    ("home-energy-twin", 7, 1, [21], "merged"),
    ("planar-nexus", 2419, 1, [2981], "merged"),
    ("planar-nexus", 2411, 1, [2983], "open"),
    ("planar-nexus", 2984, 1, [2985], "open"),
    ("home-energy-twin", 8, 1, [23], "merged"),
    ("portkit-v2", 463, 1, [466], "merged"),
]

# Flag presentation cycles per PR (cycle k is a synthetic shepherd
# cycle; the patterns encode the episode counts the #125 build's live
# replay verified: PR 77 = 4 episodes, PR 4 = 3, PR 1410 = 3,
# PRs 75 and 76 = 1 each, 12 episodes in total).
PHASE0_FLAGS = {
    77: [0, 1, 2, 4, 5, 7, 8, 9, 11],
    4: [0, 1, 2, 4, 5, 7, 8],
    1410: [1, 2, 3, 5, 6, 8, 9],
    75: [0],
    76: [1],
}
PHASE0_FLAG_REPO = {
    77: "plan-coder",
    4: "passive-income-pocs",
    1410: "matchline",
    75: "plan-coder",
    76: "plan-coder",
}
CYCLE0 = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def cycle_stamp(cycle, minute=0):
    return CYCLE0 + timedelta(minutes=75 * cycle + minute)


def phase0_ledgers():
    workers, shepherds = [], []
    run_stamp = datetime(2026, 11, 17, 11, 41, tzinfo=UTC)
    for repo, issue, runs, prs, kind in PHASE0_TASKS:
        for run in range(runs):
            pr = prs[run] if run < len(prs) else (prs[0] if prs else None)
            workers.append(
                worker(repo, issue, iso(run_stamp + timedelta(minutes=7 * run)), pr=pr)
            )
        run_stamp += timedelta(minutes=53)
    offsets = {}
    for pr, cycles in PHASE0_FLAGS.items():
        minute = offsets.get(pr, 0)
        for cyc in cycles:
            shepherds.append(
                shepherd(
                    PHASE0_FLAG_REPO[pr], pr, "needs_alex",
                    iso(cycle_stamp(cyc, minute)),
                )
            )
        offsets[pr] = minute + 2
    # A non-unit row in cycle 10 keeps that cycle present in the
    # grouping, so PR 77's cycle 9 and cycle 11 flags stay separate
    # episodes, exactly as in the live ledger.
    shepherds.append(shepherd("planar-nexus", 2985, "other", iso(cycle_stamp(10))))
    merged_block = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    tick = 0
    for repo, issue, runs, prs, kind in PHASE0_TASKS:
        if kind != "merged":
            continue
        for pr in prs:
            shepherds.append(
                shepherd(repo, pr, "merged_by_shepherd",
                         iso(merged_block + timedelta(seconds=30 * tick)))
            )
            tick += 1
    return workers, shepherds


class TestPhase0Regression(unittest.TestCase):
    def test_phase0_window_reproduces_the_frozen_figures(self):
        workers, shepherds = phase0_ledgers()
        summary = attention.build_summary(workers, shepherds, SINCE, UNTIL, use_gh=False)
        self.assertEqual(summary["n_tasks"], 28)
        self.assertEqual(summary["totals"]["units_total_presentations"], 32)
        self.assertEqual(summary["totals"]["units_total_distinct"], 19)
        share = summary["share_not_correctly_finished"]
        self.assertEqual(share["presentations_n"], "26/32")
        self.assertEqual(share["presentations_pct"], 81.2)
        self.assertEqual(summary["tasks_by_outcome"].get("merged_censored"), 19)
        self.assertEqual(summary["tasks_by_outcome"].get("open_unresolved"), 6)
        self.assertEqual(summary["tasks_by_outcome"].get("failed_abandoned"), 3)
        self.assertEqual(summary["tasks_by_outcome"].get("finished_with_rework", 0), 0)
        apcf = summary["attention_per_correctly_finished"]
        self.assertEqual(apcf["label"], "proxy, censored")
        self.assertEqual(apcf["n"], 19)
        self.assertEqual(apcf["median"], 0)
        self.assertEqual(apcf["p90"], 1)
        top = {t["task"]: t["flag_presentations"] for t in summary["top_flag_presentations"]}
        self.assertEqual(top.get("plan-coder#74"), 9)
        self.assertEqual(top.get("matchline#1409"), 7)
        self.assertEqual(top.get("passive-income-pocs#1"), 7)


class TestCliContract(unittest.TestCase):
    def test_missing_ledger_exits_2(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "attention.py"), "summary",
                 "--since", "2026-11-17", "--state-dir", tmp, "--json"],
                capture_output=True, text=True,
            )
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
