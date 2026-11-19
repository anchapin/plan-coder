"""Tests for bin/shepherd_ledger.py packet capture (issue #130).

The contract these tests pin (build brief §2a/§2c, from the #109
spike's Phase 0 PROCEED):

  * each new optional append argument lands under its key with the
    right shape; an absent argument produces an ABSENT key (never
    null, never an empty string);
  * `actor` is derived by the script from the action class, never
    passed; `known_failures_hash` is computed by the script from the
    deployment config, never passed, and is absent (append still
    exits 0) when the config or the value is unresolvable;
  * judging/contract touch booleans are computed from --files with
    the bin/judging_files.py classification;
  * the ci_checks encoding is capped: counts plus full entries for
    non-success checks only; an explicitly empty rollup is a truthful
    zero, key present;
  * summarize() gains only the additive packet_coverage key; every
    pre-existing output is unchanged on a mixed old/new fixture;
  * the capture-landing smoke test runs the real CLI end to end in a
    temp state dir with a fixture config and asserts the packet keys
    land in the written row (the EDD precedent: a capture step that
    does not land is the whole build).

Every subprocess run pins PLAN_CODER_CONFIG and HOME so the config
search order cannot leak a developer machine's real deployment
config into a test.

Run: python3 -m pytest tests/test_shepherd_ledger.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER_TOOL = ROOT / "bin" / "shepherd_ledger.py"

KNOWN_FAILURES = "fixture known failures: <regression tracked in #1>"
OLD_KEYS = {"ts", "repo", "pr", "pr_url", "action", "outcome", "ci",
            "merge_state", "detail"}
PACKET_ARG_KEYS = {"head_sha", "ci_checks", "judging_touch",
                   "contract_touch", "merged_by", "known_failures_hash"}


class ShepherdLedgerPacketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-packet-"))
        self.ledger = self.tmp / "shepherd_ledger.jsonl"
        self.config = self.tmp / "deployment.json"
        self.config.write_text(json.dumps({
            "state": {"dir": str(self.tmp)},
            "merge_policy": {"known_failures": KNOWN_FAILURES}}))
        self.no_config = self.tmp / "does-not-exist.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sl(self, *argv, config=None, ledger=True):
        env = dict(os.environ)
        env["PLAN_CODER_CONFIG"] = str(
            self.config if config is None else config)
        env["HOME"] = str(self.tmp)
        args = [sys.executable, str(LEDGER_TOOL), *argv]
        if ledger:
            args += ["--ledger", str(self.ledger)]
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=60, env=env, check=False)

    def append(self, *argv, **kw):
        r = self.run_sl("append", *argv, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def rows(self):
        return [json.loads(x) for x in
                self.ledger.read_text().splitlines() if x.strip()]

    # -- round-trip and absent-key semantics -------------------------

    def test_all_new_arguments_land_with_the_right_shape(self):
        self.append("--repo", "alpha", "--pr", "12",
                    "--action", "merged_by_shepherd",
                    "--outcome", "merged", "--merge-state", "UNSTABLE",
                    "--ci", "3 checks, 1 failure",
                    "--head-sha", "abc123",
                    "--ci-checks",
                    "build=SUCCESS; tests=FAILURE; lint=NEUTRAL",
                    "--files", "src/main.py,bin/check_gate.py",
                    "--merged-by", "someone")
        (row,) = self.rows()
        self.assertEqual(row["head_sha"], "abc123")
        self.assertEqual(row["ci_checks"]["total"], 3)
        self.assertTrue(row["judging_touch"])
        self.assertFalse(row["contract_touch"])
        self.assertEqual(row["merged_by"], "someone")
        self.assertEqual(row["actor"], "shepherd")
        self.assertEqual(
            row["known_failures_hash"],
            hashlib.sha256(KNOWN_FAILURES.encode("utf-8")).hexdigest())

    def test_absent_arguments_produce_absent_keys(self):
        self.append("--repo", "alpha", "--pr", "12",
                    "--action", "rerun", "--outcome", "flaky",
                    config=self.no_config)
        (row,) = self.rows()
        for key in PACKET_ARG_KEYS:
            self.assertNotIn(key, row)
        # actor is derived, not an argument: a rerun is the shepherd's
        # own action, so it lands even with no packet arguments.
        self.assertEqual(row["actor"], "shepherd")

    def test_append_row_with_old_shape_writes_exactly_the_old_keys(self):
        sys.path.insert(0, str(ROOT / "bin"))
        import shepherd_ledger
        shepherd_ledger.append_row(str(self.ledger), {
            "repo": "alpha", "pr": 1, "pr_url": "", "action": "rerun",
            "outcome": "flaky", "ci": "", "merge_state": "",
            "detail": ""})
        (row,) = self.rows()
        self.assertEqual(set(row), OLD_KEYS)

    # -- actor derivation ---------------------------------------------

    def test_actor_derivation_per_action_class(self):
        expected = {
            "merged_by_shepherd": "shepherd",
            "fix_attempt": "shepherd",
            "rerun": "shepherd",
            "merge_conflicts": "shepherd",
            "trigger_requested": "shepherd",
            "needs_operator": "shepherd",
            "merged": "observed",
            "closed_unmerged": "observed",
            "checked": None,
            "snapshot": None,
            "other": None,
        }
        for action, actor in expected.items():
            extra = (("--ci", "actor probe")
                     if action in ("merged", "merged_by_shepherd") else ())
            self.append("--repo", "alpha", "--pr", "1",
                        "--action", action, "--outcome", "x", *extra)
        rows = {r["action"]: r for r in self.rows()}
        self.assertEqual(set(rows), set(expected))
        for action, actor in expected.items():
            if actor is None:
                self.assertNotIn("actor", rows[action])
            else:
                self.assertEqual(rows[action]["actor"], actor)

    # -- known_failures_hash ------------------------------------------

    def test_hash_matches_fixture_config_value(self):
        self.append("--repo", "alpha", "--pr", "1",
                    "--action", "checked", "--outcome", "x")
        (row,) = self.rows()
        self.assertEqual(
            row["known_failures_hash"],
            hashlib.sha256(KNOWN_FAILURES.encode("utf-8")).hexdigest())

    def test_hash_absent_when_config_key_missing(self):
        bare = self.tmp / "bare.json"
        bare.write_text(json.dumps({"state": {"dir": str(self.tmp)}}))
        self.append("--repo", "alpha", "--pr", "1",
                    "--action", "checked", "--outcome", "x", config=bare)
        (row,) = self.rows()
        self.assertNotIn("known_failures_hash", row)

    def test_hash_absent_and_append_ok_when_config_missing(self):
        r = self.run_sl("append", "--repo", "alpha", "--pr", "1",
                        "--action", "checked", "--outcome", "x",
                        config=self.no_config)
        self.assertEqual(r.returncode, 0, r.stderr)
        (row,) = self.rows()
        self.assertNotIn("known_failures_hash", row)

    # -- touch booleans -------------------------------------------------

    def append_files(self, files):
        self.append("--repo", "alpha", "--pr", "1",
                    "--action", "checked", "--outcome", "x",
                    "--files", files)
        return self.rows()[-1]

    def test_touch_judging_only(self):
        row = self.append_files("src/main.py,tests/test_widget.py")
        self.assertTrue(row["judging_touch"])
        self.assertFalse(row["contract_touch"])

    def test_touch_contract_only(self):
        row = self.append_files("CONTRACT.md,src/main.py")
        self.assertTrue(row["contract_touch"])
        # CONTRACT.md is a judging file too.
        self.assertTrue(row["judging_touch"])

    def test_touch_both_and_status_prefixed_entries(self):
        row = self.append_files("M bin/check_gate.py,A CONTRACT.md")
        self.assertTrue(row["judging_touch"])
        self.assertTrue(row["contract_touch"])

    def test_touch_neither(self):
        row = self.append_files("src/main.py,README.md,docs/notes.md")
        self.assertFalse(row["judging_touch"])
        self.assertFalse(row["contract_touch"])

    def test_touch_over_100_files(self):
        # The computation covers exactly the paths it is handed; a
        # list truncated upstream (the gh pr view 100-file cap) is a
        # prompt-side concern. Here all 150 paths are handed over and
        # the judging file at the end is still seen.
        files = ",".join([f"src/mod{i}.py" for i in range(149)]
                         + ["bin/check_gate.py"])
        row = self.append_files(files)
        self.assertTrue(row["judging_touch"])
        self.assertFalse(row["contract_touch"])

    # -- ci_checks encoding ---------------------------------------------

    def append_checks(self, checks):
        self.append("--repo", "alpha", "--pr", "1",
                    "--action", "checked", "--outcome", "x",
                    "--ci-checks", checks)
        return self.rows()[-1]["ci_checks"]

    def test_ci_checks_mixed_rollup(self):
        got = self.append_checks(
            "build=SUCCESS; tests=FAILURE; lint=NEUTRAL; "
            "e2e=IN_PROGRESS; docs=SKIPPED")
        self.assertEqual(got["total"], 5)
        self.assertEqual(got["success"], 3)
        self.assertEqual(got["failure"], 1)
        self.assertEqual(got["pending"], 1)
        self.assertEqual(
            got["non_success"],
            [{"name": "tests", "conclusion": "FAILURE"},
             {"name": "e2e", "conclusion": "IN_PROGRESS"}])

    def test_ci_checks_empty_rollup_is_a_truthful_zero(self):
        got = self.append_checks("")
        self.assertEqual(got, {"total": 0, "success": 0, "failure": 0,
                               "pending": 0, "non_success": []})

    # -- capture-landing smoke test --------------------------------------

    def test_capture_landing_smoke_via_real_cli(self):
        # No --ledger: the CLI must resolve the ledger path from the
        # fixture config's state dir, exactly as the live shepherd's
        # appends do, and the packet keys must land in the row.
        r = self.run_sl("append", "--repo", "alpha", "--pr", "7",
                        "--action", "merged_by_shepherd",
                        "--outcome", "merged", "--merge-state", "CLEAN",
                        "--ci", "all green", "--head-sha", "deadbeef",
                        "--ci-checks", "build=SUCCESS",
                        "--files", "src/main.py", ledger=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        landed = self.tmp / "shepherd_ledger.jsonl"
        self.assertTrue(landed.exists())
        (row,) = [json.loads(x) for x in
                  landed.read_text().splitlines()]
        self.assertEqual(row["head_sha"], "deadbeef")
        self.assertEqual(row["ci_checks"]["total"], 1)
        self.assertEqual(row["actor"], "shepherd")
        self.assertIn("known_failures_hash", row)
        self.assertFalse(row["judging_touch"])

    # -- summary packet_coverage ------------------------------------------

    def test_summary_packet_coverage_on_mixed_fixture(self):
        old = {"ts": "2026-11-18T00:00:00Z", "repo": "alpha", "pr": 1,
               "pr_url": "", "action": "merged", "outcome": "merged",
               "ci": "ok", "merge_state": "", "detail": ""}
        new = dict(old, pr=2, action="merged_by_shepherd",
                   merge_state="UNSTABLE", head_sha="abc",
                   ci_checks={"total": 1, "success": 1, "failure": 0,
                              "pending": 0, "non_success": []},
                   known_failures_hash="f" * 64, actor="shepherd")
        self.ledger.write_text(
            json.dumps(old) + "\n" + json.dumps(new) + "\n")
        r = self.run_sl("summary", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        # Pre-existing outputs are unchanged by the new key.
        self.assertEqual(data["rows"], 2)
        self.assertEqual(data["merges"], 2)
        self.assertEqual(data["unstable_merges"], 1)
        self.assertEqual(data["by_action"],
                         {"merged": 1, "merged_by_shepherd": 1})
        self.assertEqual(data["packet_coverage"], {
            "head_sha": 0.5, "ci_checks": 0.5, "merge_state": 0.5,
            "known_failures_hash": 0.5, "actor": 0.5})


CI_SUMMARY = "build: 3 checks, all successful"
LOCAL_VERIFY = "pytest -q (23 passed, 0 failed)"


class ShepherdLedgerCiGuardTest(unittest.TestCase):
    """The --ci guard on merge actions (issue #121)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-shepherd-ci-"))
        self.ledger = self.tmp / "shepherd_ledger.jsonl"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sl(self, *argv):
        return subprocess.run(
            [sys.executable, str(LEDGER_TOOL), *argv,
             "--ledger", str(self.ledger)],
            capture_output=True, text=True, timeout=60, check=False)

    def rows(self):
        if not self.ledger.exists():
            return []
        return [json.loads(line)
                for line in self.ledger.read_text().splitlines()
                if line.strip()]

    # -- rejected ---------------------------------------------------------

    def test_empty_ci_on_merged_by_shepherd_is_rejected(self):
        r = self.run_sl("append", "--repo", "alpha", "--pr", "21",
                        "--action", "merged_by_shepherd",
                        "--merge-state", "CLEAN")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--ci", r.stderr)
        self.assertIn("required", r.stderr)
        self.assertEqual(self.rows(), [])

    def test_whitespace_only_ci_on_merged_by_shepherd_is_rejected(self):
        r = self.run_sl("append", "--repo", "alpha", "--pr", "21",
                        "--action", "merged_by_shepherd",
                        "--ci", "   \t  ")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--ci", r.stderr)
        self.assertEqual(self.rows(), [])

    def test_empty_ci_on_merged_is_rejected(self):
        r = self.run_sl("append", "--repo", "beta", "--pr", "22",
                        "--action", "merged")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--ci", r.stderr)
        self.assertEqual(self.rows(), [])

    def test_whitespace_only_ci_on_merged_is_rejected(self):
        r = self.run_sl("append", "--repo", "beta", "--pr", "22",
                        "--action", "merged", "--ci", " ")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--ci", r.stderr)
        self.assertEqual(self.rows(), [])

    # -- accepted ---------------------------------------------------------

    def test_ci_summary_on_merged_by_shepherd_is_accepted(self):
        r = self.run_sl("append", "--repo", "alpha", "--pr", "21",
                        "--action", "merged_by_shepherd",
                        "--ci", CI_SUMMARY, "--merge-state", "CLEAN")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ci"], CI_SUMMARY)
        self.assertEqual(rows[0]["action"], "merged_by_shepherd")

    def test_no_ci_local_verify_string_on_merged_is_accepted(self):
        """A repo with no CI checks: the local verify run before merging."""
        r = self.run_sl("append", "--repo", "home-energy-twin", "--pr",
                        "21", "--action", "merged",
                        "--ci", LOCAL_VERIFY)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ci"], LOCAL_VERIFY)

    def test_needs_operator_with_empty_ci_is_still_accepted(self):
        r = self.run_sl("append", "--repo", "alpha", "--pr", "7",
                        "--action", "needs_operator",
                        "--outcome", "physics")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ci"], "")
        self.assertEqual(rows[0]["action"], "needs_operator")

    def test_closed_unmerged_with_empty_ci_is_still_accepted(self):
        r = self.run_sl("append", "--repo", "alpha", "--pr", "8",
                        "--action", "closed_unmerged")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.rows()), 1)

    # -- discovered merge: otherwise unchanged ---------------------------

    def test_discovered_merge_behaviour_otherwise_unchanged(self):
        """A `merged` row (a merge the shepherd observed) still counts as
        a merge in the summary, keeps its merge_state, and needs_operator
        rows keep their own counts."""
        r = self.run_sl("append", "--repo", "alpha", "--pr", "12",
                        "--action", "merged", "--ci", CI_SUMMARY,
                        "--merge-state", "UNSTABLE")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_sl("append", "--repo", "alpha", "--pr", "12",
                        "--action", "needs_operator")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_sl("summary", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["rows"], 2)
        self.assertEqual(data["merges"], 1)
        self.assertEqual(data["unstable_merges"], 1)
        self.assertEqual(data["needs_operator_distinct_prs"], 1)
        self.assertEqual(data["by_action"]["merged"], 1)

    def test_rejected_append_leaves_the_ledger_untouched(self):
        """A refused append writes nothing and does not disturb earlier
        rows."""
        r = self.run_sl("append", "--repo", "alpha", "--pr", "1",
                        "--action", "rerun", "--ci", CI_SUMMARY)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_sl("append", "--repo", "alpha", "--pr", "1",
                        "--action", "merged_by_shepherd")
        self.assertNotEqual(r.returncode, 0)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "rerun")


if __name__ == "__main__":
    unittest.main()
