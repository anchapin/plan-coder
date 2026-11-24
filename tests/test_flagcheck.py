"""Tests for bin/flagcheck.py — flag-resolution verifier spike
(Phase 0 PASS 2026-11-11; Phase 1 built 2026-11-11, ships
disabled). Hermetic: fixtures are the Phase 0 sample's facts,
injected via --facts-file; temp configs and state dirs.
Run: python3 -m unittest tests.test_flagcheck
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FC = REPO / "bin" / "flagcheck.py"
FD = REPO / "bin" / "flightdeck.py"
sys.path.insert(0, str(REPO / "bin"))
import flagcheck  # noqa: E402

EXEMPT = {"anchapin/fluxion": ["ASHRAE 140 Strict Energy Gate",
                               "ASHRAE 140 Fabric Harness Gate",
                               "Scripts Test Suite",
                               "Gate - scripts-test-suite"]}
POL = {"enabled": True, "exempt_checks": EXEMPT}


def chk(name, conclusion, started="2026-11-10T00:00:00Z"):
    return {"name": name, "conclusion": conclusion,
            "status": "COMPLETED", "startedAt": started}


def facts(repo, pr, checks, **kw):
    f = {"repo": repo, "pr": pr, "checks": checks,
         "mergeable": "MERGEABLE", "files": [],
         "workflow_files": [], "workflow_diff": "",
         "linked_issue_closed": False}
    f.update(kw)
    return f


F4651 = facts("anchapin/fluxion", 4651, [
    chk("ASHRAE 140 Fabric Harness Gate (Issue #3986-A+2)", "FAILURE"),
    chk("ASHRAE 140 Strict Energy Gate (Issue #1333)", "FAILURE"),
    chk("ASHRAE 140 Strict Energy Gate (Issue #1333)", "FAILURE"),
    chk("Build", "SUCCESS")])
F4647 = facts("anchapin/fluxion", 4647, [
    chk("Scripts Test Suite (Issue #2817)", "FAILURE"),
    chk("Gate - scripts-test-suite", "FAILURE"),
    chk("FFI Feature Check (GH)", "FAILURE"),
    chk("Clippy (GH)", "FAILURE"),
    chk("ASHRAE 140 Strict Energy Gate (Issue #1333)", "FAILURE"),
    chk("Build", "SUCCESS")], mergeable="UNSTABLE")
F1352 = facts("anchapin/matchline", 1352, [
    {"name": "Sourcery review", "conclusion": "SKIPPED",
     "status": "COMPLETED", "startedAt": "2026-11-10T03:33:48Z"}])
COUNT_DIFF = ("diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
              "-          EXPECTED_TEST_COUNT=4114  # +7 tests (#1341)\n"
              "+          EXPECTED_TEST_COUNT=4117  # +3 tests (#1354)\n")
SUBSTANTIVE_DIFF = ("diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
                    "-  cross-browser-e2e:\n-    runs-on: ubuntu-latest\n"
                    "+  nightly-e2e:\n+    runs-on: ubuntu-latest\n")
F1349 = facts("anchapin/matchline", 1349,
              [chk(f"check-{i}", "SUCCESS",
                   "2026-11-11T00:42:49Z") for i in range(16)],
              files=[".github/workflows/ci.yml", "src/lib.rs"],
              workflow_files=[".github/workflows/ci.yml"],
              workflow_diff=COUNT_DIFF)
F2899 = facts("anchapin/planar-nexus", 2899,
              [chk("CodeQL", "FAILURE"), chk("build", "SUCCESS")],
              files=[".github/workflows/ci.yml", "app.ts"],
              workflow_files=[".github/workflows/ci.yml"],
              workflow_diff=SUBSTANTIVE_DIFF)


class ClassifyCase(unittest.TestCase):
    def test_4651_exempt_reds_resolve(self):
        r = flagcheck.evaluate(F4651, POL, [])
        self.assertEqual(r["classes"]["ci-reds"]["state"],
                         "resolved_verified")
        self.assertEqual(r["overall"], "resolved_verified")

    def test_4647_no_false_exempt(self):
        r = flagcheck.evaluate(F4647, POL, [])
        u = r["classes"]["ci-unclassified"]
        self.assertEqual(u["state"], "unresolved")
        self.assertIn("FFI Feature Check (GH)", u["evidence"])
        self.assertIn("Clippy (GH)", u["evidence"])
        self.assertNotIn("Scripts Test Suite", u["evidence"])
        self.assertEqual(r["overall"], "unresolved")

    def test_1352_ci_none(self):
        r = flagcheck.evaluate(F1352, POL, [])
        self.assertEqual(r["classes"]["ci-none"]["state"], "unresolved")
        self.assertEqual(r["overall"], "unresolved")

    def test_1349_count_only_and_held_green(self):
        r = flagcheck.evaluate(F1349, POL, [])
        self.assertEqual(r["classes"]["workflow-kind"]["state"],
                         "resolved_verified")
        hg = r["classes"]["held-green"]
        self.assertEqual(hg["state"], "unresolved")
        self.assertIn("2026-11-11T00:42:49Z", hg["evidence"])
        self.assertEqual(r["overall"], "unresolved")

    def test_1349_held_green_resolution_by_operator_word(self):
        res = [{"repo": "anchapin/matchline", "pr": 1349,
                "class": "held-green", "disposition": "merge",
                "ts": "2026-11-11T00:48:00Z"}]
        r = flagcheck.evaluate(F1349, POL, res)
        self.assertEqual(r["classes"]["held-green"]["state"],
                         "resolved_verified")
        self.assertEqual(r["overall"], "resolved_verified")

    def test_2899_security_and_substantive_workflow(self):
        r = flagcheck.evaluate(F2899, POL, [])
        self.assertEqual(r["classes"]["security-scan"]["state"],
                         "unresolved")
        self.assertEqual(r["classes"]["workflow-kind"]["state"],
                         "unresolved")
        self.assertEqual(r["overall"], "unresolved")

    def test_workflow_kind_units(self):
        self.assertEqual(flagcheck.workflow_kind(COUNT_DIFF), "count_only")
        self.assertEqual(flagcheck.workflow_kind(SUBSTANTIVE_DIFF),
                         "substantive")
        self.assertEqual(flagcheck.workflow_kind(""), "none")

    def test_physics_gate_unverifiable_then_resolved_by_word(self):
        f = facts("anchapin/fluxion", 9999,
                  [chk("Build", "SUCCESS")], mergeable="UNKNOWN",
                  files=["src/physics/solver.rs"])
        r = flagcheck.evaluate(f, POL, [])
        self.assertEqual(r["classes"]["physics-gate"]["state"],
                         "unverifiable")
        self.assertEqual(r["overall"], "unverifiable")
        res = [{"repo": "anchapin/fluxion", "pr": 9999,
                "class": "physics-gate", "disposition": "baselines ok",
                "ts": "2026-11-11T01:00:00Z"}]
        r2 = flagcheck.evaluate(f, POL, res)
        self.assertEqual(r2["classes"]["physics-gate"]["state"],
                         "resolved_verified")

    def test_conflicting_is_unresolved(self):
        f = facts("anchapin/fluxion", 1111, [chk("Build", "SUCCESS")],
                  mergeable="CONFLICTING")
        r = flagcheck.evaluate(f, POL, [])
        self.assertEqual(r["classes"]["conflict-stale"]["state"],
                         "unresolved")


def run(script, *argv):
    return subprocess.run([sys.executable, str(script), *argv],
                          capture_output=True, text=True, check=False)


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sd = Path(self.tmp.name) / "state"
        self.sd.mkdir()
        self.cfg_on = Path(self.tmp.name) / "on.json"
        self.cfg_on.write_text(json.dumps(
            {"workers": {"flagcheck": {"enabled": True,
                                       "exempt_checks": EXEMPT}},
             "state": {"dir": str(self.sd)}}))
        self.cfg_off = Path(self.tmp.name) / "off.json"
        self.cfg_off.write_text(json.dumps(
            {"workers": {"flagcheck": {"enabled": False}},
             "state": {"dir": str(self.sd)}}))
        self.facts = Path(self.tmp.name) / "facts.json"
        self.facts.write_text(json.dumps(F4647))

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_disabled_exits_2(self):
        r = run(FC, "check", "--repo", "anchapin/fluxion", "--pr", "4647",
                "--config", str(self.cfg_off),
                "--facts-file", str(self.facts))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_log_appends_only_on_change(self):
        args = ("log", "--repo", "anchapin/fluxion", "--pr", "4647",
                "--config", str(self.cfg_on), "--state-dir", str(self.sd),
                "--facts-file", str(self.facts), "--json")
        r1 = run(FC, *args)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertTrue(json.loads(r1.stdout)["appended"])
        r2 = run(FC, *args)
        self.assertFalse(json.loads(r2.stdout)["appended"])
        rows = (self.sd / "flagcheck_ledger.jsonl").read_text().strip().split("\n")
        self.assertEqual(len(rows), 1)
        # state change -> second row
        self.facts.write_text(json.dumps(F4651 | {"pr": 4647}))
        r3 = run(FC, *args)
        self.assertTrue(json.loads(r3.stdout)["appended"])
        rows = (self.sd / "flagcheck_ledger.jsonl").read_text().strip().split("\n")
        self.assertEqual(len(rows), 2)

    def test_summary_reads_ledger_flag_independent(self):
        run(FC, "log", "--repo", "anchapin/fluxion", "--pr", "4647",
            "--config", str(self.cfg_on), "--state-dir", str(self.sd),
            "--facts-file", str(self.facts), "--json")
        r = run(FC, "summary", "--config", str(self.cfg_off),
                "--state-dir", str(self.sd), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["prs"], 1)
        self.assertEqual(out["latest_by_overall"], {"unresolved": 1})
        self.assertIn("FFI Feature Check (GH)",
                      out["unclassified_failing_names"])

    def test_flightdeck_board_carries_flagcheck_word(self):
        run(FC, "log", "--repo", "anchapin/fluxion", "--pr", "4647",
            "--config", str(self.cfg_on), "--state-dir", str(self.sd),
            "--facts-file", str(self.facts), "--json")
        (self.sd / "workers.json").write_text(json.dumps(
            {"fluxion-1": {"status": "working", "issue": 1}}))
        (self.sd / "shepherd.json").write_text(json.dumps(
            {"tracked_prs": [], "needs_alex": ["fluxion-4647"],
             "prs": {}, "pending_trigger": None, "last_trigger": None}))
        (self.sd / "shepherd_ledger.jsonl").write_text(json.dumps(
            {"ts": "2026-11-11T03:00:00Z", "repo": "anchapin/fluxion",
             "pr": 4647, "action": "needs_alex", "outcome": "flagged"}) + "\n")
        (self.sd / "worker_ledger.jsonl").write_text("")
        out = self.sd / "FLIGHT_DECK.md"
        r = run(FD, "generate", "--state-dir", str(self.sd),
                "--out", str(out), "--config", str(self.cfg_off))
        self.assertEqual(r.returncode, 0, r.stderr)
        board = out.read_text()
        self.assertIn("[flagcheck: unresolved as of 2026-11-10T00:00:00Z]",
                      board)


class Phase1RegressionCase(unittest.TestCase):
    """#163 Phase 1: regression tests for the three defects named
    by the Phase 0 constructed-oracle corpus (recovery 218/224
    before these fixes; the corpus scenarios are A01/A06/A07)."""

    def test_exempt_match_only_at_separator(self):
        pats = EXEMPT["anchapin/fluxion"]
        # Approved shapes: the gate name itself, and the gate
        # name extended after a separator.
        self.assertTrue(flagcheck.exempt_match(
            "ASHRAE 140 Strict Energy Gate", pats))
        self.assertTrue(flagcheck.exempt_match(
            "ASHRAE 140 Strict Energy Gate (Issue #1333)", pats))
        self.assertTrue(flagcheck.exempt_match(
            "Scripts Test Suite (Issue #2817)", pats))
        self.assertTrue(flagcheck.exempt_match(
            "Gate - scripts-test-suite", pats))
        # Mid-token extensions are different checks, not the gate.
        self.assertFalse(flagcheck.exempt_match(
            "ASHRAE 140 Strict Energy Gatekeeper (experimental)",
            pats))
        self.assertFalse(flagcheck.exempt_match(
            "Scripts Test Suites", pats))

    def test_a01_gatekeeper_name_is_unclassified_not_exempt(self):
        f = facts("anchapin/fluxion", 7101, [
            chk("ASHRAE 140 Strict Energy Gatekeeper (experimental)",
                "FAILURE", "2026-11-20T10:00:00Z"),
            chk("Build", "SUCCESS", "2026-11-20T10:00:00Z")],
            files=["src/lib.rs"])
        r = flagcheck.evaluate(f, POL, [])
        self.assertNotIn("ci-reds", r["classes"])
        u = r["classes"]["ci-unclassified"]
        self.assertEqual(u["state"], "unresolved")
        self.assertIn("Gatekeeper", u["evidence"])
        self.assertEqual(r["overall"], "unresolved")

    def test_a06_empty_workflow_diff_is_unverifiable(self):
        f = facts("anchapin/matchline", 7107,
                  [chk("Build", "SUCCESS", "2026-11-20T10:00:00Z")],
                  files=[".github/workflows/ci.yml"],
                  workflow_files=[".github/workflows/ci.yml"],
                  workflow_diff="")
        r = flagcheck.evaluate(f, POL, [])
        wk = r["classes"]["workflow-kind"]
        self.assertEqual(wk["state"], "unverifiable")
        self.assertNotIn("substantive", wk["evidence"])
        self.assertIn("no changed lines", wk["evidence"])

    def test_a07_unnamed_failure_is_not_dropped(self):
        self.assertEqual(
            flagcheck.failing_names(
                [{"name": "", "conclusion": "FAILURE"}]),
            [flagcheck.UNNAMED_FAILING])
        f = facts("anchapin/fluxion", 7108, [
            chk("Build", "SUCCESS", "2026-11-20T10:00:00Z"),
            {"name": "", "conclusion": "FAILURE",
             "status": "COMPLETED",
             "startedAt": "2026-11-20T10:00:00Z"}])
        r = flagcheck.evaluate(f, POL, [])
        self.assertNotIn("ci-reds", r["classes"])
        self.assertNotIn("held-green", r["classes"])
        u = r["classes"]["ci-unclassified"]
        self.assertEqual(u["state"], "unresolved")
        self.assertIn(flagcheck.UNNAMED_FAILING, u["evidence"])


if __name__ == "__main__":
    unittest.main()
