"""Tests for bin/schema_inventory_phase0.py (issue #147 Part F3, Phase 0).

Phase 0 is measurement only: the census script inventories the artifacts
the harness emits and marks, for each, whether a machine-readable schema
exists and whether a conformance check exists.  These tests check the
*script's own* honesty:

  * the census really enumerates emission sites in bin/ (it is not a
    hard-coded list pretending to be a scan);
  * every inventory row's claim verifies against the repo -- producer
    line, schema file, conformance test, example instance;
  * the coverage headline is the arithmetic of the rows, and the
    Kill/Proceed read follows from it;
  * the script runs end to end and emits valid JSON.

Nothing here builds a schema, changes an artifact format, or writes
outside a temporary directory.

Run: python3 -m pytest tests/test_schema_inventory_phase0.py -q
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"
SCRIPT = BIN / "schema_inventory_phase0.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "schema_inventory_phase0", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["schema_inventory_phase0"] = module
    spec.loader.exec_module(module)
    return module


inv = _load()


class CensusTests(unittest.TestCase):
    """The census enumerates emission sites from the code."""

    def test_census_scans_the_tools_directory(self):
        census = inv.scan_census(str(REPO))
        self.assertGreater(census["files_scanned"], 20)
        self.assertIn("bin/*.py", census["scope"])
        names = {site["file"] for site in census["write_sites"]}
        self.assertIn("bin/receipt.py", names)
        self.assertIn("bin/agent.py", names)

    def test_census_finds_the_named_patterns(self):
        census = inv.scan_census(str(REPO))
        kinds = census["by_kind"]
        for kind in ("file-open-append", "file-open-write", "json-dump",
                     "json-dumps", "jsonl-append"):
            self.assertIn(kind, kinds)
            self.assertGreater(kinds[kind], 0, kind)

    def test_census_patterns_are_named(self):
        census = inv.scan_census(str(REPO))
        ids = [pattern["id"] for pattern in census["patterns"]]
        self.assertEqual(ids, ["P1", "P2", "P3", "P4", "P5", "P6", "P7"])

    def test_census_links_a_jsonl_append_to_its_producer_function(self):
        census = inv.scan_census(str(REPO))
        sites = inv.sites_in_function(census, "bin/receipt.py", "append_row")
        self.assertTrue(sites)
        self.assertTrue(any(site["kind"] == "jsonl-append" for site in sites))


class InventoryTests(unittest.TestCase):
    """Every inventory row verifies against the repo."""

    @classmethod
    def setUpClass(cls):
        cls.rows = inv.build_rows(str(REPO))

    def test_every_row_verifies(self):
        for row in self.rows:
            self.assertEqual(row["problems"], [], row["artifact"])

    def test_every_row_cites_a_producer_line(self):
        for row in self.rows:
            self.assertNotIn("NOT FOUND", row["producer"], row["artifact"])
            file_part, _, line = row["producer"].rpartition(":")
            self.assertTrue(file_part.startswith(("bin/", "prompts/")),
                            row["artifact"])
            self.assertTrue(line.isdigit(), row["artifact"])
            self.assertGreater(int(line), 0, row["artifact"])

    def test_declared_schema_files_exist_and_are_json_schemas(self):
        for row in self.rows:
            if row["schema_kind"] != "json-schema":
                continue
            path = REPO / row["schema"]
            self.assertTrue(path.exists(), row["artifact"])
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("$schema", data)
            self.assertIn("properties", data)

    def test_code_schema_names_a_validator_that_exists(self):
        for row in self.rows:
            if row["schema_kind"] != "code-schema":
                continue
            file_part, _, func = row["schema"].partition(":")
            text = (REPO / file_part).read_text(encoding="utf-8")
            self.assertIn(f"def {func}(", text, row["artifact"])

    def test_conformance_checks_name_a_test_that_exists(self):
        for row in self.rows:
            if row["conformance"] == "none":
                continue
            file_part, _, name = row["conformance"].partition("::")
            text = (REPO / file_part).read_text(encoding="utf-8")
            self.assertIn(f"def {name}(", text, row["artifact"])

    def test_examples_exist_when_declared(self):
        for row in self.rows:
            if row["example"] == "none":
                continue
            self.assertTrue((REPO / row["example"]).exists(), row["artifact"])

    def test_prose_only_families_are_not_counted_as_schemas(self):
        """A docstring description is not a schema."""
        for row in self.rows:
            if row["schema"] == "none":
                self.assertFalse(row["schema_ok"], row["artifact"])
                self.assertTrue(row["schema_note"], row["artifact"])

    def test_the_inventory_covers_the_families_the_brief_named(self):
        names = " | ".join(row["artifact"] for row in self.rows)
        for needle in ("worker_ledger.jsonl", "receipts.jsonl",
                       "shepherd_ledger.jsonl", "verification_ledger.jsonl",
                       "goldfish_ledger.jsonl", "jittest_ledger.jsonl",
                       "edd_experiments.jsonl", "experiments.jsonl",
                       "workers.json", "shepherd.json", "WORKER_REPORT.md"):
            self.assertIn(needle, names, needle)


class CoverageTests(unittest.TestCase):
    """The headline figure is the arithmetic of the rows."""

    @classmethod
    def setUpClass(cls):
        cls.rows = inv.build_rows(str(REPO))
        cls.cov = inv.coverage(cls.rows)

    def test_coverage_is_the_arithmetic_of_the_rows(self):
        self.assertEqual(self.cov["artifacts"], len(self.rows))
        self.assertEqual(
            self.cov["with_both"],
            sum(1 for row in self.rows if row["covered"]))
        self.assertEqual(
            self.cov["with_schema"],
            sum(1 for row in self.rows if row["schema_ok"]))
        self.assertEqual(
            self.cov["with_conformance_check"],
            sum(1 for row in self.rows if row["conformance_ok"]))

    def test_a_row_counts_only_with_both(self):
        for row in self.rows:
            self.assertEqual(row["covered"],
                             bool(row["schema_ok"] and row["conformance_ok"]))

    def test_coverage_pct_matches_the_rows(self):
        expected = round(100.0 * self.cov["with_both"] / self.cov["artifacts"],
                         1)
        self.assertEqual(self.cov["coverage_pct"], expected)

    def test_decision_follows_from_the_coverage(self):
        decision = inv.read_decision(self.rows, self.cov)
        if self.cov["coverage_pct"] >= 90.0:
            self.assertTrue(decision.startswith("KILL F3"), decision)
        else:
            self.assertTrue(decision.startswith("PROCEED F3"), decision)

    def test_decision_kills_a_complete_census(self):
        rows = [{"covered": True, "schema_ok": True, "conformance_ok": True}]
        cov = inv.coverage(rows)
        self.assertTrue(inv.read_decision(rows, cov).startswith("KILL F3"))


class NotFoundTests(unittest.TestCase):
    """Families the brief named that the census could not find."""

    @classmethod
    def setUpClass(cls):
        cls.rows = inv.not_found_rows(str(REPO))

    def test_claimlink_and_flight_deck_are_reported_as_not_found(self):
        names = " | ".join(row["name"] for row in self.rows)
        self.assertIn("claimlink", names)
        self.assertIn("flight-deck", names)

    def test_claimlink_has_no_producer_in_the_repo(self):
        row = next(r for r in self.rows if "claimlink" in r["name"])
        self.assertEqual(row["hits"], 0)

    def test_flight_deck_named_path_is_absent(self):
        row = next(r for r in self.rows if "flight-deck" in r["name"])
        self.assertFalse(row["expected_path_exists"])


class CliTests(unittest.TestCase):
    """The script runs end to end."""

    def test_json_output_is_valid_and_complete(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--json"],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        for key in ("census", "inventory", "coverage", "decision",
                    "not_found"):
            self.assertIn(key, payload)
        self.assertGreater(len(payload["inventory"]), 10)
        self.assertIn("coverage_pct", payload["coverage"])

    def test_verify_mode_passes_on_the_current_tree(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--verify"],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("BROKEN INVENTORY CLAIMS", result.stderr)

    def test_markdown_report_carries_the_table_and_the_headline(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("| artifact | producer (file:line) | format | schema |",
                      result.stdout)
        self.assertIn("Coverage:", result.stdout)
        self.assertIn("PROCEED F3", result.stdout)


class PhaseZeroGuardTests(unittest.TestCase):
    """Phase 0 is measurement only: no schema was built by this change."""

    def test_no_new_schema_file_was_added(self):
        schema_files = sorted(p.name for p in (REPO / "schemas").glob("*.json"))
        self.assertEqual(schema_files, ["ledger-line.schema.json",
                                        "next-actions.schema.json",
                                        "shepherd.schema.json",
                                        "workers.schema.json"])

    def test_the_census_script_writes_nothing_to_the_repo(self):
        before = {p: p.stat().st_mtime for p in REPO.glob("bin/*.py")}
        subprocess.run([sys.executable, str(SCRIPT), "--json"],
                       capture_output=True, text=True, cwd=str(REPO))
        after = {p: p.stat().st_mtime for p in REPO.glob("bin/*.py")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
