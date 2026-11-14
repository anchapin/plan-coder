"""Tests for bin/trajectory_coupling_phase0.py (issue #38, Phase 0).

The Phase-0 script clusters worker-ledger rows into cohorts and
classifies the coupling of same-cohort run pairs.  Both steps decide
what the report says, so they are pinned here with synthetic fixtures:
no ledger of the live supervisor is read and no network is used.

Run: python3 -m pytest tests/test_trajectory_coupling_phase0.py -q
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"

_spec = importlib.util.spec_from_file_location(
    "trajectory_coupling_phase0", BIN / "trajectory_coupling_phase0.py")
tcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tcp)


def worker_row(ts, repo="fluxion", run_id="fluxion-1-1700000000", **extra):
    """Build a synthetic worker-ledger row."""
    row = {
        "ts": ts,
        "repo": repo,
        "issue": 1,
        "branch": "worker/1",
        "run_id": run_id,
        "tier": "cheap",
        "providers_used": "test/model",
        "tokens_in": 100,
        "tokens_out": 200,
        "steps": "1",
        "outcome": "pr_opened",
        "pr_url": "https://github.com/anchapin/fluxion/pull/1",
        "gate_triggers": "",
    }
    row.update(extra)
    return row


class CohortTests(unittest.TestCase):
    """Cohort clustering rule."""

    def test_rows_within_window_share_a_cohort(self):
        rows = [
            worker_row("2026-11-01T00:00:00Z", run_id="a-1700000000"),
            worker_row("2026-11-01T01:59:00Z", run_id="b-1700000001"),
        ]
        cohorts, unassigned = tcp.assign_cohorts(rows, 2)
        self.assertEqual(unassigned, [])
        self.assertEqual(len(cohorts), 1)
        self.assertEqual(len(cohorts[0]["rows"]), 2)

    def test_row_beyond_window_opens_a_new_cohort(self):
        rows = [
            worker_row("2026-11-01T00:00:00Z", run_id="a-1700000000"),
            worker_row("2026-11-01T02:00:01Z", run_id="b-1700000001"),
        ]
        cohorts, _unassigned = tcp.assign_cohorts(rows, 2)
        self.assertEqual(len(cohorts), 2)
        self.assertEqual([len(c["rows"]) for c in cohorts], [1, 1])

    def test_window_is_anchored_at_the_first_row_not_chained(self):
        """00:00, 01:30 and 02:45: the third row is 2h45m after the first."""
        rows = [
            worker_row("2026-11-01T00:00:00Z", run_id="a-1700000000"),
            worker_row("2026-11-01T01:30:00Z", run_id="b-1700000001"),
            worker_row("2026-11-01T02:45:00Z", run_id="c-1700000002"),
        ]
        cohorts, _unassigned = tcp.assign_cohorts(rows, 2)
        self.assertEqual([len(c["rows"]) for c in cohorts], [2, 1])

    def test_different_repos_never_share_a_cohort(self):
        rows = [
            worker_row("2026-11-01T00:00:00Z", repo="fluxion",
                       run_id="a-1700000000"),
            worker_row("2026-11-01T00:10:00Z", repo="matchline",
                       run_id="b-1700000001"),
        ]
        cohorts, _unassigned = tcp.assign_cohorts(rows, 2)
        self.assertEqual(len(cohorts), 2)

    def test_repo_prefix_is_normalised(self):
        rows = [
            worker_row("2026-11-01T00:00:00Z", repo="anchapin/fluxion",
                       run_id="a-1700000000"),
            worker_row("2026-11-01T00:10:00Z", repo="fluxion",
                       run_id="b-1700000001"),
        ]
        cohorts, _unassigned = tcp.assign_cohorts(rows, 2)
        self.assertEqual(len(cohorts), 1)

    def test_rows_without_ts_or_repo_are_not_assigned(self):
        rows = [
            worker_row("2026-11-01T00:00:00Z"),
            worker_row("not-a-timestamp"),
            worker_row("2026-11-01T00:10:00Z", repo=None),
        ]
        cohorts, unassigned = tcp.assign_cohorts(rows, 2)
        self.assertEqual(len(cohorts), 1)
        self.assertEqual(sorted(reason for _row, reason in unassigned),
                         ["no-repo", "no-ts"])


class ClassificationTests(unittest.TestCase):
    """Coupling classification of one run pair."""

    def test_disjoint(self):
        klass, _detail = tcp.classify_pair(
            {"src/a.rs"}, {"docs/b.md"}, set())
        self.assertEqual(klass, "disjoint")

    def test_same_module(self):
        klass, detail = tcp.classify_pair(
            {"src/sim/a.rs"}, {"src/sim/b.rs"}, set())
        self.assertEqual(klass, "same-module")
        self.assertEqual(detail, "src/sim")

    def test_file_overlap(self):
        klass, detail = tcp.classify_pair(
            {"src/a.rs", "src/b.rs"}, {"src/b.rs", "src/c.rs"}, set())
        self.assertEqual(klass, "file-overlap")
        self.assertEqual(detail, "src/b.rs")

    def test_shared_ratchet_wins_over_file_overlap(self):
        klass, detail = tcp.classify_pair(
            {"src/b.rs", ".github/workflows/ci.yml"},
            {"src/b.rs", ".github/workflows/ci.yml"},
            {".github/workflows/ci.yml"},
        )
        self.assertEqual(klass, "shared-ratchet")
        self.assertEqual(detail, ".github/workflows/ci.yml")

    def test_unclassifiable_without_file_evidence(self):
        self.assertEqual(tcp.classify_pair(set(), {"src/a.rs"}, set())[0],
                         "unclassifiable")
        self.assertEqual(tcp.classify_pair({"src/a.rs"}, set(), set())[0],
                         "unclassifiable")


class RatchetTests(unittest.TestCase):
    """Shared-ratchet file list is derived from the data, not hardcoded."""

    def test_ratchet_files_come_from_pr_counts(self):
        pr_files = {
            ("fluxion", 1): {"a.rs", ".github/workflows/ci.yml"},
            ("fluxion", 2): {".github/workflows/ci.yml"},
            ("fluxion", 3): {".github/workflows/ci.yml"},
            ("fluxion", 4): {"b.rs"},
        }
        ratchets = tcp.ratchet_files_by_repo(pr_files, 3)
        self.assertEqual(ratchets["fluxion"], {".github/workflows/ci.yml"})

    def test_threshold_of_one_marks_every_file(self):
        pr_files = {("fluxion", 1): {"a.rs"}}
        ratchets = tcp.ratchet_files_by_repo(pr_files, 1)
        self.assertEqual(ratchets["fluxion"], {"a.rs"})


class EvidenceTests(unittest.TestCase):
    """Ledger path extraction and normalisation."""

    def test_paths_are_extracted_from_free_text(self):
        found = tcp.extract_paths(
            "contract_touch: .github/workflows/ci.yml is CONTRACT named area")
        self.assertIn(".github/workflows/ci.yml", found)

    def test_bare_name_is_rewritten_to_its_unique_full_path(self):
        canonical = tcp.canonicalize_paths({
            ("fluxion", 1): {"ci.yml", ".github/workflows/ci.yml"},
        })
        self.assertEqual(canonical[("fluxion", 1)],
                         {".github/workflows/ci.yml"})

    def test_ambiguous_bare_name_is_kept(self):
        canonical = tcp.canonicalize_paths({
            ("fluxion", 1): {"mod.rs", "src/mod.rs", "tests/mod.rs"},
        })
        self.assertIn("mod.rs", canonical[("fluxion", 1)])


class LedgerParsingTests(unittest.TestCase):
    """Small parsing helpers used by the measurement."""

    def test_outcome_class(self):
        self.assertEqual(tcp.outcome_class("pr_opened"), "pr_opened")
        self.assertEqual(tcp.outcome_class("pr_opened_supervisor_salvage"),
                         "pr_opened")
        self.assertEqual(tcp.outcome_class("failed:empty-diff"), "failed")
        self.assertEqual(tcp.outcome_class("stale"), "stale")

    def test_parse_pr_url(self):
        self.assertEqual(
            tcp.parse_pr_url("https://github.com/anchapin/fluxion/pull/4225"),
            ("anchapin", "fluxion", 4225))
        self.assertIsNone(tcp.parse_pr_url("https://example.com/x"))

    def test_dispatch_epoch_from_run_id(self):
        row = worker_row("2026-11-01T00:00:00Z",
                         run_id="fluxion-4224-1790642079")
        self.assertEqual(tcp.dispatch_epoch_of(row), 1790642079)

    def test_dispatch_epoch_absent(self):
        self.assertIsNone(
            tcp.dispatch_epoch_of(worker_row("2026-11-01T00:00:00Z",
                                             run_id="pre-transcript-logging")))


class ShepherdJoinTests(unittest.TestCase):
    """Shepherd side of the join."""

    def test_summary_counts_fix_attempts_and_conflicts(self):
        rows = [
            {"repo": "anchapin/fluxion", "pr": 7, "action": "fix_attempt",
             "outcome": "fix pushed", "merge_state": ""},
            {"repo": "anchapin/fluxion", "pr": 7, "action": "merge_conflicts",
             "outcome": "CONFLICTING/DIRTY", "merge_state": ""},
            {"repo": "anchapin/fluxion", "pr": 8, "action": "merged",
             "outcome": "merged", "merge_state": "CLEAN"},
        ]
        _by_pr, summary = tcp.build_shepherd_index(rows)
        pr7 = summary[("fluxion", 7)]
        self.assertEqual(pr7["fix_attempts"], 1)
        self.assertTrue(pr7["conflict"])
        self.assertFalse(pr7["merged"])
        self.assertTrue(summary[("fluxion", 8)]["merged"])


if __name__ == "__main__":
    unittest.main()
