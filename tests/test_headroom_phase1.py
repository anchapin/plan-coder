"""Tests for docs/spikes/headroom-phase1.md (issue #22, Phase 1).

Phase 1 ran two compatibility probes against a replay of one recorded
worker session: Probe H (Headroom proxy) and Probe C (Context Mode
pattern, native prototype).  The probe code lived in scratch space and
was deleted; the *report* is the deliverable, so what is pinned here is
the report's structure and the internal consistency of the numbers it
quotes -- a report whose own table contradicts its own headline number
is a silent regression.

No network, no scratch directory, no live corpus: the test reads the
committed report only.

Run: python3 -m pytest tests/test_headroom_phase1.py -q
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / "docs" / "spikes" / "headroom-phase1.md"


def report_text() -> str:
    return REPORT.read_text(encoding="utf-8")


class ReportStructureTests(unittest.TestCase):
    """The deliverable exists and answers the done-definition."""

    def test_report_exists(self):
        self.assertTrue(REPORT.exists(), "phase-1 report is missing")

    def test_both_probes_have_verdicts(self):
        text = report_text()
        self.assertIn("## Probe H -- Headroom proxy", text)
        self.assertIn("## Probe C -- Context Mode pattern", text)
        # a pass/fail verdict for EACH probe
        self.assertIsNotNone(re.search(
            r"Probe H -- Headroom proxy.*?Verdict:\s*\*\*FAIL", text, re.S))
        self.assertIsNotNone(re.search(
            r"Probe C -- Context Mode pattern.*?Verdict:\s*\*\*PASS", text, re.S))

    def test_phase0_gate_numbers_are_carried_over(self):
        text = report_text()
        for value in ("11.77%", "16.60%", "22.35%", "10.0%", "20.0%"):
            self.assertIn(value, text)
        self.assertIn("PHASE-1-ELIGIBLE", text)

    def test_replay_setup_is_named(self):
        text = report_text()
        self.assertIn("fluxion-4689-1794225608c.jsonl", text)
        self.assertIn("transcripts", text)
        self.assertIn("tokens_in", text)

    def test_each_probe_has_a_go_nogo_recommendation(self):
        text = report_text()
        self.assertIn("## Recommendations", text)
        self.assertIn("No-go", text)
        self.assertIn("Go --", text)

    def test_probe_h_records_the_exact_failure_mode(self):
        text = report_text()
        self.assertIn("No matching distribution found for headroom", text)
        self.assertIn("wrong version number", text)

    def test_no_adoption_claims(self):
        text = report_text()
        self.assertIn("nothing was adopted", text)


class QuotedNumberConsistencyTests(unittest.TestCase):
    """The numbers in the report must agree with each other."""

    def _sum_row(self) -> tuple[float, float, float]:
        text = report_text()
        match = re.search(
            r"sum of `tokens_in` over the 60 steps \| ([\d,\.]+) \| ([\d,\.]+) \|"
            r" \*\*-([\d\.]+)%\*\*", text)
        self.assertIsNotNone(match, "the headline totals row is missing")
        full = float(match.group(1).replace(",", ""))
        comp = float(match.group(2).replace(",", ""))
        drop = float(match.group(3))
        return full, comp, drop

    def test_headline_drop_matches_its_own_totals(self):
        full, comp, drop = self._sum_row()
        self.assertAlmostEqual((full - comp) / full * 100.0, drop, places=1)

    def test_final_step_row_matches_the_headline_direction(self):
        text = report_text()
        match = re.search(
            r"`tokens_in` of the final step \| ([\d,\.]+) \| ([\d,\.]+) \|"
            r" \*\*-([\d\.]+)%\*\*", text)
        self.assertIsNotNone(match)
        full = float(match.group(1).replace(",", ""))
        comp = float(match.group(2).replace(",", ""))
        drop = float(match.group(3))
        self.assertLess(comp, full)
        self.assertAlmostEqual((full - comp) / full * 100.0, drop, places=1)

    def test_per_step_table_deltas_are_arithmetically_right(self):
        text = report_text()
        rows = re.findall(
            r"^\| (\d+) \| ([\d,\.]+) \| ([\d,\.]+) \| ([\d,\.]+) \|",
            text, re.M)
        self.assertGreaterEqual(len(rows), 8)
        for step, full, comp, delta in rows:
            f = float(full.replace(",", ""))
            c = float(comp.replace(",", ""))
            d = float(delta.replace(",", ""))
            self.assertLess(c, f, "step %s: compression must drop tokens" % step)
            self.assertAlmostEqual(f - c, d, delta=0.15)

    def test_retrieval_cost_claim_is_consistent(self):
        text = report_text()
        match = re.search(
            r"Cost of serving every one of them: ([\d,]+) chars = ([\d,]+) tokens",
            text)
        self.assertIsNotNone(match)
        chars = float(match.group(1).replace(",", ""))
        tokens = float(match.group(2).replace(",", ""))
        self.assertAlmostEqual(chars / 4.0, tokens, delta=2.0)

    def test_same_trajectory_digest_is_quoted_for_both_replays(self):
        text = report_text()
        digests = set(re.findall(r"\b[0-9a-f]{16}\b", text))
        self.assertEqual(len(digests), 1,
                         "the two replays must quote the same trajectory digest")


if __name__ == "__main__":
    unittest.main()
