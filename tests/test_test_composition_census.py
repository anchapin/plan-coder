"""Tests for bin/test_composition_census.py (plan-coder issue #71, Phase 0).

The classifier is the measurement instrument of the Phase 0 test-composition
census, so its own behaviour is pinned here:

  * one synthetic test file per class is classified into that class;
  * a file whose (a)-side and (b)-side signals conflict falls back to class
    (c) and is reported as uncertain, with the conflict named;
  * a synthetic tests/ tree is covered completely -- no file is dropped and
    no file is counted twice;
  * the CLI emits JSON that covers every tests/test_*.py file exactly once;
  * the wall-clock share is computed from a pytest --durations=0 output.

The synthetic trees are built in a temporary directory, so the tests do not
depend on the live tests/ tree except for the one integration check that the
classifier covers the real tests/ tree exactly once.

Run: python3 -m pytest tests/test_test_composition_census.py -q
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "test_composition_census.py"


def load_classifier():
    """Load bin/test_composition_census.py under a distinct module name."""
    spec = importlib.util.spec_from_file_location(
        "bin_test_composition_census", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bin_test_composition_census"] = module
    spec.loader.exec_module(module)
    return module


census = load_classifier()

GOLDEN_FILE = '''\
"""A golden test: a fixture goes in, the output is compared to it."""
FIXTURE = "tests/fixtures/expected_output.json"


def test_render_matches_the_stored_artifact():
    produced = render()
    with open(FIXTURE, encoding="utf-8") as handle:
        expected = handle.read()
    assert produced == expected
'''

PROPERTY_FILE = '''\
"""A property test: the law holds for every generated input."""
from hypothesis import given, strategies as st


@given(st.integers())
def test_the_law_holds_for_every_input(value):
    assert double(value) == value * 2
'''

QUANTIFIED_FILE = '''\
"""An invariant test: the property holds for every case of a kind."""
CASES = [1, 2, 3]


def test_invariant_holds_for_every_case():
    for case in CASES:
        assert double(case) == case * 2
'''

UNIT_FILE = '''\
"""A per-unit test: one helper is called directly with hand-built inputs."""
import double


def test_the_helper_doubles_a_number():
    assert double.double(3) == 6
'''

CONFLICT_FILE = '''\
"""A file with both an artifact comparison and a quantified invariant."""
FIXTURE = "tests/fixtures/expected_output.json"
CASES = [1, 2, 3]


def test_artifact_and_invariant_together():
    with open(FIXTURE, encoding="utf-8") as handle:
        expected = handle.read()
    for case in CASES:
        assert case > 0
    assert expected
'''

SILENT_FILE = '''\
"""A file with no class signal at all."""


def test_nothing_is_asserted_here():
    pass
'''


def write_tree(root: Path, files: dict[str, str]) -> Path:
    """Create a synthetic repo: root/tests/<name> plus root/bin/double.py."""
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "bin" / "double.py").write_text("def double(x):\n    return x * 2\n",
                                            encoding="utf-8")
    for name, body in files.items():
        (root / "tests" / name).write_text(body, encoding="utf-8")
    return root


class SingleClassTests(unittest.TestCase):
    """One synthetic file per class is classified into that class."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def classify(self, name: str, body: str) -> dict:
        root = write_tree(self.tmp, {name: body})
        records = census.collect(root)
        self.assertEqual(len(records), 1)
        return records[0]

    def test_golden_file_is_class_a(self):
        record = self.classify("test_golden.py", GOLDEN_FILE)
        self.assertEqual(record["class"], census.CLASS_A)
        self.assertEqual(record["rule"], "R2-artifact-compare")
        self.assertFalse(record["uncertain"])
        self.assertTrue(any(s.startswith("artifact-read")
                            for s in record["signals"]["a_side"]))

    def test_property_framework_file_is_class_b(self):
        record = self.classify("test_property.py", PROPERTY_FILE)
        self.assertEqual(record["class"], census.CLASS_B)
        self.assertEqual(record["rule"], "R3-property-framework")
        self.assertFalse(record["uncertain"])

    def test_quantified_invariant_file_is_class_b(self):
        record = self.classify("test_quantified.py", QUANTIFIED_FILE)
        self.assertEqual(record["class"], census.CLASS_B)
        self.assertEqual(record["rule"], "R4-quantified-invariant")
        self.assertFalse(record["uncertain"])

    def test_per_unit_file_is_class_c(self):
        record = self.classify("test_unit.py", UNIT_FILE)
        self.assertEqual(record["class"], census.CLASS_C)
        self.assertEqual(record["rule"], "R5-per-unit")
        self.assertFalse(record["uncertain"])

    def test_conflicting_signals_fall_back_to_c_and_are_uncertain(self):
        record = self.classify("test_conflict.py", CONFLICT_FILE)
        self.assertEqual(record["class"], census.CLASS_C)
        self.assertEqual(record["rule"], "R1-conflict-artifact-and-property")
        self.assertTrue(record["uncertain"])
        self.assertTrue(record["reasons"])
        self.assertIn("conflicting signals", record["reasons"][0])

    def test_file_without_any_signal_falls_back_to_c_and_is_uncertain(self):
        record = self.classify("test_silent.py", SILENT_FILE)
        self.assertEqual(record["class"], census.CLASS_C)
        self.assertEqual(record["rule"], "R6-fallback-no-signal")
        self.assertTrue(record["uncertain"])
        self.assertIn("absent signals", record["reasons"][0])


class CoverageTests(unittest.TestCase):
    """A synthetic tests/ tree is covered exactly once."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.files = {
            "test_golden.py": GOLDEN_FILE,
            "test_property.py": PROPERTY_FILE,
            "test_quantified.py": QUANTIFIED_FILE,
            "test_unit.py": UNIT_FILE,
            "test_conflict.py": CONFLICT_FILE,
            "test_silent.py": SILENT_FILE,
        }
        self.tests_dir = write_tree(self.tmp, self.files)

    def test_every_file_appears_exactly_once(self):
        records = census.collect(self.tmp)
        names = [r["file"] for r in records]
        self.assertEqual(len(names), len(self.files))
        self.assertEqual(len(set(names)), len(self.files))
        self.assertEqual(
            set(names),
            {f"tests/{name}" for name in self.files})

    def test_class_counts_add_up_to_the_file_count(self):
        records = census.collect(self.tmp)
        data = census.shares(records)
        counted = sum(data[cls]["files"]
                      for cls in (census.CLASS_A, census.CLASS_B,
                                  census.CLASS_C))
        self.assertEqual(counted, data["totals"]["files"])
        self.assertEqual(data["totals"]["files"], len(self.files))

    def test_every_file_gets_a_class_and_a_rule(self):
        records = census.collect(self.tmp)
        for record in records:
            self.assertIn(record["class"],
                          (census.CLASS_A, census.CLASS_B, census.CLASS_C))
            self.assertTrue(record["rule"])
            self.assertTrue(record["signals"])

    def test_cli_json_covers_the_synthetic_tree(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.tmp), "--json"],
            capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        names = [r["file"] for r in payload["files"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names),
                         {f"tests/{name}" for name in self.files})


class RepoIntegrationTests(unittest.TestCase):
    """The classifier covers this repo's own tests/ tree exactly once."""

    def test_real_tree_is_covered_once(self):
        records = census.collect(REPO)
        on_disk = sorted(p.relative_to(REPO).as_posix()
                         for p in (REPO / "tests").glob("test_*.py")
                         if p.is_file())
        names = [r["file"] for r in records]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(sorted(names), on_disk)

    def test_real_tree_classes_are_known(self):
        records = census.collect(REPO)
        for record in records:
            self.assertIn(record["class"],
                          (census.CLASS_A, census.CLASS_B, census.CLASS_C))


class DurationTests(unittest.TestCase):
    """The wall-clock share is read out of a pytest --durations=0 output."""

    def test_durations_are_grouped_by_class(self):
        records = [
            {"class": census.CLASS_A, "file": "tests/test_a.py"},
            {"class": census.CLASS_B, "file": "tests/test_b.py"},
        ]
        text = (
            "0.50s call     tests/test_a.py::test_one\n"
            "1.00s call     tests/test_b.py::test_two\n"
            "0.25s setup    tests/test_a.py::test_one\n"
        )
        data = census.duration_shares(records, text)
        self.assertAlmostEqual(data[census.CLASS_A]["seconds"], 0.75, places=3)
        self.assertAlmostEqual(data[census.CLASS_B]["seconds"], 1.00, places=3)
        self.assertAlmostEqual(data["totals"]["seconds"], 1.75, places=3)
        self.assertAlmostEqual(data[census.CLASS_B]["share"], 1.0 / 1.75,
                               places=3)


if __name__ == "__main__":
    unittest.main()
