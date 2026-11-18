"""Tests for bin/flagbatch.py (batched flag view, Phase 1).

The contract these tests pin:

  * classification is deterministic and priority-ordered: a PR whose
    rows support several classes lands in the earliest class in
    flagbatch.PRIORITY;
  * security-scan, physics-gate, and unverifiable are individual
    classes: they render under an individual-review heading and are
    never counted as batched lines;
  * one entry per flagged PR, with the repeat count, first, and
    latest dates taken from that PR's needs_alex ledger rows;
  * a disabled config makes check and generate exit 2 and write
    nothing.

Run: python3 -m pytest tests/test_flagbatch.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import flagbatch  # noqa: E402


def row(
    repo,
    pr,
    action="needs_alex",
    outcome="",
    detail="",
    ci="",
    merge_state="",
    ts="2026-11-10T00:00:00Z",
):
    return {
        "repo": repo,
        "pr": pr,
        "action": action,
        "outcome": outcome,
        "detail": detail,
        "ci": ci,
        "merge_state": merge_state,
        "ts": ts,
    }


class TestClassify(unittest.TestCase):
    def test_security_beats_workflow(self):
        rows = [
            row(
                "o/r",
                1,
                outcome="workflow touch",
                ci="sole fail CodeQL (2 new critical alerts)",
            )
        ]
        self.assertEqual(flagbatch.classify(rows), "security-scan")

    def test_physics_beats_ci_reds(self):
        rows = [row("o/r", 2, detail="simulation physics, judgment", ci="3 fails")]
        self.assertEqual(flagbatch.classify(rows), "physics-gate")

    def test_workflow_kind(self):
        rows = [row("o/r", 3, outcome="workflow count bump = hard stop")]
        self.assertEqual(flagbatch.classify(rows), "workflow-kind")

    def test_ci_none_from_empty_fields(self):
        rows = [row("o/r", 4)]
        self.assertEqual(flagbatch.classify(rows), "ci-none")

    def test_ci_reds(self):
        rows = [row("o/r", 5, ci="6 fails: gate A, gate B")]
        self.assertEqual(flagbatch.classify(rows), "ci-reds")

    def test_unverifiable_when_no_signal(self):
        rows = [row("o/r", 6, outcome="retained", merge_state="UNSTABLE")]
        self.assertEqual(flagbatch.classify(rows), "unverifiable")


class TestEntriesAndRender(unittest.TestCase):
    def setUp(self):
        self.shepherd = {
            "needs_alex": [
                "https://example.com/o/r/pull/1",
                "https://example.com/o/r/pull/2",
                "https://example.com/o/s/pull/3",
            ]
        }
        self.ledger = [
            row("o/r", 1, ci="sole fail CodeQL", ts="2026-11-09T00:00:00Z"),
            row("o/r", 1, ci="sole fail CodeQL", ts="2026-11-11T00:00:00Z"),
            row("o/r", 1, ci="sole fail CodeQL", ts="2026-11-12T00:00:00Z"),
            row(
                "o/r",
                2,
                outcome="diff sound; workflow bump needed",
                ts="2026-11-10T00:00:00Z",
            ),
            row(
                "o/s",
                3,
                outcome="diff sound; workflow bump needed",
                ts="2026-11-10T00:00:00Z",
            ),
        ]

    def test_one_entry_per_pr_with_repeat_count(self):
        entries = flagbatch.build_entries(self.shepherd, self.ledger)
        self.assertEqual(len(entries), 3)
        first = entries[0]
        self.assertEqual(first["cls"], "security-scan")
        self.assertEqual(first["count"], 3)
        self.assertEqual(first["first"], "2026-11-09")
        self.assertEqual(first["last"], "2026-11-12")

    def test_individual_classes_never_batched(self):
        entries = flagbatch.build_entries(self.shepherd, self.ledger)
        text, groups = flagbatch.render(entries, "operator")
        self.assertIn("INDIVIDUAL REVIEW", text)
        counts = flagbatch.counts_row(entries, groups, flat_lines=3)
        # Only the two workflow-kind PRs are batchable lines.
        self.assertEqual(counts["batched_lines"], 2)
        self.assertEqual(counts["by_class"]["security-scan"], 1)
        self.assertEqual(counts["by_class"]["workflow-kind"], 2)

    def test_parse_pr_key_forms(self):
        self.assertEqual(flagbatch.parse_pr_key("https://h/o/r/pull/42"), ("o/r", 42))
        self.assertEqual(flagbatch.parse_pr_key("r-7"), ("r", 7))
        self.assertIsNone(flagbatch.parse_pr_key("nonsense"))


class TestDisabledGate(unittest.TestCase):
    def test_check_and_generate_exit_2_and_write_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "cfg.json"
            cfg.write_text(json.dumps({"workers": {"flagbatch": {"enabled": False}}}))
            state = Path(td) / "state"
            state.mkdir()
            (state / "shepherd.json").write_text(json.dumps({"needs_alex": []}))
            out = Path(td) / "FLAG_BATCH.md"
            for argv in (
                ["check", "--config", str(cfg)],
                [
                    "generate",
                    "--state-dir",
                    str(state),
                    "--out",
                    str(out),
                    "--config",
                    str(cfg),
                ],
            ):
                proc = subprocess.run(
                    [sys.executable, str(ROOT / "bin" / "flagbatch.py"), *argv],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 2, argv)
            self.assertFalse(out.exists())
            self.assertFalse((state / "flag_batch_ledger.jsonl").exists())

    def test_generate_enabled_writes_view_and_counts(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "operator": {"name": "operator"},
                        "workers": {"flagbatch": {"enabled": True}},
                    }
                )
            )
            state = Path(td) / "state"
            state.mkdir()
            (state / "shepherd.json").write_text(
                json.dumps({"needs_alex": ["https://example.com/o/r/pull/9"]})
            )
            (state / "shepherd_ledger.jsonl").write_text(
                json.dumps(row("o/r", 9, outcome="workflow bump needed")) + "\n"
            )
            out = Path(td) / "FLAG_BATCH.md"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "flagbatch.py"),
                    "generate",
                    "--state-dir",
                    str(state),
                    "--out",
                    str(out),
                    "--config",
                    str(cfg),
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("workflow-kind (1)", out.read_text())
            counts = json.loads((state / "flag_batch_ledger.jsonl").read_text().strip())
            self.assertEqual(counts["needs_operator_count"], 1)
            self.assertEqual(counts["batched_lines"], 1)


if __name__ == "__main__":
    unittest.main()
