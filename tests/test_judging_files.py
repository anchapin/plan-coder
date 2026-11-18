"""Tests for bin/judging_files.py (issue #96).

Unit tests cover the flag rules (routine classified, creation never
flagged, modification of a pre-existing judging file flagged), the
base-SHA list rule (a PR that edits the list cannot shrink its own
judging set), and the disabled-config exit. The replay fixture
(tests/fixtures/verification_phase0_pr_files.json — Phase 0's 492-PR
universe — plus verification_phase0_part_c_core.csv, its core-19 set)
asserts the check reproduces Phase 0's core set exactly. Phase 0's
universe carries no per-file status, so replay treats listed files
as modifications, exactly as Phase 0's own census did.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import judging_files as jf

FIX_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class JudgeClassTests(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(jf.judge_class("scripts/check_foo.py"),
                         "gate_script")
        self.assertEqual(jf.judge_class("bem/export_gate.py"),
                         "gate_script")
        self.assertEqual(jf.judge_class("ruff.toml"), "lint_config")
        self.assertEqual(jf.judge_class("deny.toml"), "lint_config")
        self.assertEqual(jf.judge_class("CONTRACT.md"), "contract")
        self.assertEqual(jf.judge_class(".github/workflows/ci.yml"),
                         "ci_workflow")
        self.assertEqual(jf.judge_class("src/main.py"), None)


class FlagRuleTests(unittest.TestCase):
    def test_modification_of_gate_script_flags(self):
        out = jf.classify_pr([("M", "scripts/check_foo.py")])
        self.assertTrue(out["flagged"])
        self.assertEqual(out["flags"][0]["flag_class"], "modification")
        self.assertIn("check_foo.py", out["reason"])

    def test_deletion_of_lint_config_flags(self):
        out = jf.classify_pr([("D", "ruff.toml")])
        self.assertTrue(out["flagged"])
        self.assertEqual(out["flags"][0]["flag_class"], "lint-config")

    def test_contract_modification_flags(self):
        out = jf.classify_pr([("M", "CONTRACT.md")])
        self.assertTrue(out["flagged"])
        self.assertEqual(out["flags"][0]["flag_class"], "contract")

    def test_creation_never_flags(self):
        out = jf.classify_pr([("A", "scripts/check_new_gate.py"),
                              ("A", "CONTRACT.md"),
                              ("A", "eslint.config.mjs")])
        self.assertFalse(out["flagged"])
        self.assertEqual(out["counts"].get("creation"), 3)

    def test_routine_classified_not_flagged(self):
        out = jf.classify_pr([
            ("M", "scripts/check_test_inventory_drift.py"),
            ("M", "config/ashrae_140_cases_ratchet.json"),
            ("M", "tests/test_inventory.json"),
        ])
        self.assertFalse(out["flagged"])
        dispositions = {f["path"]: f["disposition"] for f in out["files"]}
        self.assertTrue(all(d == "routine"
                            for d in dispositions.values()))

    def test_ci_and_test_files_counted_not_flagged(self):
        out = jf.classify_pr([("M", ".github/workflows/ci.yml"),
                              ("M", "tests/test_x.py")])
        self.assertFalse(out["flagged"])
        self.assertEqual(out["counts"].get("ci_workflow"), 1)
        self.assertEqual(out["counts"].get("test_file"), 1)

    def test_rename_is_a_modification(self):
        parsed = jf.parse_changed_files("R100 old/check_a.py -> "
                                        "scripts/check_a.py")
        self.assertEqual(parsed, [("M", "scripts/check_a.py")])
        self.assertTrue(jf.classify_pr(parsed)["flagged"])


class BaseShaListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "t")
        with open(os.path.join(self.repo, "judging_files.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"extra": ["custom/gate_x.py"],
                       "exclude": []}, fh)
        self._git("add", ".")
        self._git("commit", "-qm", "base")
        self.base = self._git("rev-parse", "HEAD").strip()

    def _git(self, *args):
        proc = subprocess.run(["git", "-C", self.repo, *args],
                              capture_output=True, text=True, check=True)
        return proc.stdout

    def test_list_read_at_base_not_working_tree(self):
        # The "PR" edits the list in the working tree, removing the
        # extra entry and excluding a check script. Reading at the
        # base SHA must still see the base version of the list.
        with open(os.path.join(self.repo, "judging_files.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"extra": [],
                       "exclude": ["scripts/check_foo.py"]}, fh)
        lst = jf.load_list_at_base(self.repo, self.base,
                                   "judging_files.json")
        self.assertEqual(lst["extra"], ["custom/gate_x.py"])
        self.assertEqual(lst["exclude"], [])
        out = jf.classify_pr(
            [("M", "custom/gate_x.py"), ("M", "scripts/check_foo.py")],
            extra=set(lst["extra"]), exclude=set(lst["exclude"]))
        self.assertTrue(out["flagged"])
        self.assertEqual(len(out["flags"]), 2)

    def test_missing_list_at_base_is_none(self):
        self.assertIsNone(jf.load_list_at_base(
            self.repo, self.base, "no_such_file.json"))


class DisabledGateTests(unittest.TestCase):
    def test_exits_2_when_disabled(self):
        rc = jf.main(["--changed-files", "M scripts/check_foo.py"])
        self.assertEqual(rc, 2)


class Phase0ReplayTests(unittest.TestCase):
    def test_reproduces_core_19(self):
        with open(os.path.join(
                FIX_DIR, "verification_phase0_pr_files.json"),
                encoding="utf-8") as fh:
            universe = json.load(fh)
        with open(os.path.join(
                FIX_DIR, "verification_phase0_part_c_core.csv"),
                newline="", encoding="utf-8") as fh:
            core_expected = {row["pr"] for row in csv.DictReader(fh)}
        self.assertEqual(len(core_expected), 19)
        core_found = set()
        for key, entry in universe.items():
            if not isinstance(entry, dict) or "files" not in entry:
                continue
            changed = [("M", f) for f in entry["files"]]
            out = jf.classify_pr(changed)
            if any(f["judge_class"] in jf.FLAG_CLASSES
                   for f in out["files"]):
                core_found.add(key)
        self.assertEqual(core_found, core_expected)


if __name__ == "__main__":
    unittest.main()
