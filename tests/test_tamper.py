"""Tests for bin/test_tamper.py — the shepherd's test-tamper flag.

The check flags a worker PR that modifies or deletes EXISTING test
files without brief authorization. It flags only: it never merges,
never blocks by force and never edits a PR.

Run: python3 -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))


def _load_module():
    """Load bin/test_tamper.py under a distinct name.

    This file shares its name with the module under test, so the
    module is loaded from its path rather than imported by name.
    """
    import importlib.util
    sys.path.insert(0, str(REPO / "bin"))
    spec = importlib.util.spec_from_file_location(
        "bin_test_tamper", REPO / "bin" / "test_tamper.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bin_test_tamper"] = module
    spec.loader.exec_module(module)
    return module


test_tamper = _load_module()


# A diff that weakens an assertion in an existing test file.
SEEDED_WEAKENING = """diff --git a/tests/test_totals.py b/tests/test_totals.py
index 1111111..2222222 100644
--- a/tests/test_totals.py
+++ b/tests/test_totals.py
@@ -8,6 +8,5 @@ class TestTotals:
     def test_total(self):
         items = [1, 2, 3]
         total = sum(items)
-        assert total == 6
"""

# The same PR, but the assertion was relaxed instead of removed.
SEEDED_RELAXED = """diff --git a/tests/test_totals.py b/tests/test_totals.py
index 1111111..2222222 100644
--- a/tests/test_totals.py
+++ b/tests/test_totals.py
@@ -8,6 +8,6 @@ class TestTotals:
     def test_total(self):
         items = [1, 2, 3]
         total = sum(items)
-        assert total == 6
+        assert total >= 5
"""

# A PR that only ADDS a new test file.
NEW_TEST_ONLY = """diff --git a/tests/test_new_thing.py b/tests/test_new_thing.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/test_new_thing.py
@@ -0,0 +1,4 @@
+def test_new_thing():
+    value = 41 + 1
+    assert value == 42
"""

# A PR that adds a new test file AND deletes an existing one.
DELETES_EXISTING = """diff --git a/tests/test_old_thing.py b/tests/test_old_thing.py
deleted file mode 100644
index 4444444..0000000
--- a/tests/test_old_thing.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def test_old_thing():
-    assert True
"""

# A PR that adds a skip marker to an existing test file.
ADDED_SKIP = """diff --git a/tests/test_totals.py b/tests/test_totals.py
index 1111111..2222222 100644
--- a/tests/test_totals.py
+++ b/tests/test_totals.py
@@ -8,4 +8,5 @@ class TestTotals:
     def test_total(self):
         items = [1, 2, 3]
+        pytest.skip("flaky in CI")
         total = sum(items)
"""

# A PR that touches a source file only.
SOURCE_ONLY = """diff --git a/src/totals.py b/src/totals.py
index 5555555..6666666 100644
--- a/src/totals.py
+++ b/src/totals.py
@@ -1,4 +1,4 @@
 def total(items):
-    return sum(items) + 1
+    return sum(items)
"""


def changed(status: str, path: str) -> tuple[str, str]:
    return (status, path)


class TestTestTamperDetection(unittest.TestCase):
    """The detection core: flag / no-flag on synthetic diffs."""

    def test_seeded_assertion_removal_flags(self):
        result = test_tamper.check(
            SEEDED_WEAKENING,
            changed_files=[changed("M", "tests/test_totals.py")])
        self.assertTrue(result["flag"])
        self.assertIn("test tampering suspected", result["reason"])
        self.assertTrue(result["reason"].count("\n") == 0,
                        "reason must be one line")
        kinds = {f["kind"] for f in result["findings"]}
        self.assertIn("removed_assertion", kinds)
        self.assertTrue(any("removed assertion" in e
                            for e in result["evidence"]))

    def test_seeded_relaxed_comparison_flags(self):
        result = test_tamper.check(
            SEEDED_RELAXED,
            changed_files=[changed("M", "tests/test_totals.py")])
        self.assertTrue(result["flag"])
        kinds = {f["kind"] for f in result["findings"]}
        self.assertTrue({"relaxed_comparison", "removed_assertion"} & kinds)

    def test_added_skip_flags(self):
        result = test_tamper.check(
            ADDED_SKIP,
            changed_files=[changed("M", "tests/test_totals.py")])
        self.assertTrue(result["flag"])
        kinds = {f["kind"] for f in result["findings"]}
        self.assertIn("added_skip", kinds)

    def test_new_test_files_only_do_not_flag(self):
        result = test_tamper.check(
            NEW_TEST_ONLY,
            changed_files=[changed("A", "tests/test_new_thing.py")])
        self.assertFalse(result["flag"])
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["inspected_test_files"], [])

    def test_brief_authorized_change_does_not_flag(self):
        result = test_tamper.check(
            SEEDED_WEAKENING,
            changed_files=[changed("M", "tests/test_totals.py")],
            authorized=True)
        self.assertFalse(result["flag"])
        self.assertTrue(result["authorized"])

    def test_brief_text_authorizing_tests_does_not_flag(self):
        result = test_tamper.check(
            SEEDED_WEAKENING,
            changed_files=[changed("M", "tests/test_totals.py")],
            brief_text="Fix the total. You are authorized to change the "
                       "existing tests.")
        self.assertFalse(result["flag"])
        self.assertTrue(result["authorized"])

    def test_authorized_file_does_not_flag(self):
        result = test_tamper.check(
            SEEDED_WEAKENING,
            changed_files=[changed("M", "tests/test_totals.py")],
            authorized_files=["tests/test_totals.py"])
        self.assertFalse(result["flag"])

    def test_deleted_existing_test_file_flags(self):
        result = test_tamper.check(
            DELETES_EXISTING,
            changed_files=[changed("D", "tests/test_old_thing.py")])
        self.assertTrue(result["flag"])
        kinds = {f["kind"] for f in result["findings"]}
        self.assertIn("deleted_test_file", kinds)

    def test_source_only_change_does_not_flag(self):
        result = test_tamper.check(
            SOURCE_ONLY,
            changed_files=[changed("M", "src/totals.py")])
        self.assertFalse(result["flag"])
        self.assertEqual(result["inspected_test_files"], [])

    def test_flag_output_is_one_line_reason_plus_evidence(self):
        result = test_tamper.check(
            SEEDED_WEAKENING,
            changed_files=[changed("M", "tests/test_totals.py")])
        self.assertNotIn("\n", result["reason"])
        self.assertTrue(result["evidence"])
        for line in result["evidence"]:
            self.assertNotIn("\n", line)


class TestTestTamperCli(unittest.TestCase):
    """The CLI surface, including the disabled-by-default config gate."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-tamper-"))
        self.diff = self.tmp / "pr.diff"
        self.diff.write_text(SEEDED_WEAKENING)
        self.files = self.tmp / "pr.files"
        self.files.write_text("M\ttests/test_totals.py\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_check(self, *argv, config=None):
        env = {"PATH": os.environ["PATH"],
               "HOME": str(self.tmp),
               "PLAN_CODER_CONFIG": str(config or (self.tmp / "none.json"))}
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "test_tamper.py"), *argv],
            capture_output=True, text=True, env=env, timeout=60)

    def test_disabled_by_default_exits_2(self):
        r = self.run_check("--diff-file", str(self.diff))
        self.assertEqual(r.returncode, 2)
        self.assertIn("disabled", r.stderr)

    def test_enabled_config_flags_and_exits_1(self):
        cfg = self.tmp / "on.json"
        cfg.write_text(json.dumps(
            {"shepherd": {"test_tamper": {"enabled": True}}}))
        r = self.run_check("--diff-file", str(self.diff),
                           "--changed-files-file", str(self.files),
                           "--json", config=cfg)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["flag"])
        self.assertTrue(data["reason"])
        self.assertTrue(data["evidence"])

    def test_enabled_config_no_flag_exits_0(self):
        cfg = self.tmp / "on.json"
        cfg.write_text(json.dumps(
            {"shepherd": {"test_tamper": {"enabled": True}}}))
        new_diff = self.tmp / "new.diff"
        new_diff.write_text(NEW_TEST_ONLY)
        r = self.run_check("--diff-file", str(new_diff), "--json",
                           config=cfg)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)["flag"])

    def test_authorized_flag_suppresses_the_flag(self):
        cfg = self.tmp / "on.json"
        cfg.write_text(json.dumps(
            {"shepherd": {"test_tamper": {"enabled": True}}}))
        r = self.run_check("--diff-file", str(self.diff),
                           "--changed-files-file", str(self.files),
                           "--authorized", "--json", config=cfg)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)["flag"])

    def test_human_output_is_one_line_reason(self):
        cfg = self.tmp / "on.json"
        cfg.write_text(json.dumps(
            {"shepherd": {"test_tamper": {"enabled": True}}}))
        r = self.run_check("--diff-file", str(self.diff),
                           "--changed-files-file", str(self.files),
                           config=cfg)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        first = r.stdout.splitlines()[0]
        self.assertTrue(first.startswith("FLAG: "), first)
        self.assertNotIn("\n", first)
        self.assertGreater(len(r.stdout.splitlines()), 1)


class TestTestTamperWiring(unittest.TestCase):
    """The check is wired into the shepherd prompt, disabled by default."""

    def test_config_example_ships_the_flag_disabled(self):
        cfg = json.loads((REPO / "config" / "deployment.example.json")
                         .read_text())
        self.assertFalse(cfg["shepherd"]["test_tamper"]["enabled"])

    def test_shepherd_prompt_wires_the_check(self):
        text = (REPO / "prompts" / "pr-shepherd.md").read_text()
        self.assertIn("bin/test_tamper.py", text)
        self.assertIn("test-tamper", text.lower())
        # The check flags only: the prompt must never merge or edit it.
        self.assertIn("never merge that PR", text)

    def test_prompt_renders_with_the_flag_disabled(self):
        import shutil
        from test_harness import FULL_CONFIG
        tmp = Path(tempfile.mkdtemp(prefix="pc-tamper-render-"))
        try:
            cfg = tmp / "cfg.json"
            full = json.loads(json.dumps(FULL_CONFIG))
            full["shepherd"]["test_tamper"] = {"enabled": False}
            cfg.write_text(json.dumps(full))
            out = tmp / "out"
            r = subprocess.run(
                [sys.executable, str(REPO / "bin" / "render_prompts.py"),
                 "--config", str(cfg), "--out", str(out)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            shep = (out / "pr-shepherd.rendered.md").read_text()
            self.assertIn("bin/test_tamper.py", shep)
            self.assertNotIn("{{", shep)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
