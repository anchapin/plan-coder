"""Tests for bin/delete_comment_phase0.py (issue #142, Phase 0).

The Phase-0 script extracts comment blocks from ``bin/*.py``, classifies every
block into exactly one of the five categories and writes the classification
ledger.  The extractor and the ledger contract are pinned here; nothing is
deleted and no file outside the temporary directory is written.

Run: python3 -m pytest tests/test_delete_comment_phase0.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "bin" / "delete_comment_phase0.py"


def load_tool():
    """Import the Phase-0 tool as a module."""
    name = "delete_comment_phase0"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ExtractorTests(unittest.TestCase):
    """The comment-block extractor."""

    def setUp(self):
        self.tool = load_tool()

    def test_consecutive_comment_lines_form_one_block(self):
        source = "# first\n# second\nx = 1\n"
        blocks = self.tool.extract_blocks(source, "t.py")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].start_line, 1)
        self.assertEqual(blocks[0].end_line, 2)
        self.assertEqual(blocks[0].evidence, "first")

    def test_code_line_separates_two_blocks(self):
        source = "# one\nx = 1\n# two\n"
        blocks = self.tool.extract_blocks(source, "t.py")
        self.assertEqual([b.start_line for b in blocks], [1, 3])

    def test_inline_comment_is_not_a_block(self):
        source = "x = 1  # trailing comment\n"
        self.assertEqual(self.tool.extract_blocks(source, "t.py"), [])

    def test_docstring_is_not_a_block(self):
        source = '"""Module docstring.\n\nMore prose.\n"""\nx = 1\n'
        self.assertEqual(self.tool.extract_blocks(source, "t.py"), [])

    def test_shebang_and_encoding_are_not_blocks(self):
        source = ("#!/usr/bin/env python3\n"
                  "# -*- coding: utf-8 -*-\n"
                  "# a real comment\n")
        blocks = self.tool.extract_blocks(source, "t.py")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].start_line, 3)

    def test_special_rows_are_reported(self):
        source = "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n"
        _comments, _code, special = self.tool.comment_only_lines(source)
        self.assertEqual(special.get(1), "shebang")
        self.assertEqual(special.get(2), "encoding")

    def test_block_key_is_file_and_start_line(self):
        blocks = self.tool.extract_blocks("# a\n# b\n", "bin/x.py")
        self.assertEqual(blocks[0].key, "bin/x.py:1")


class LedgerTests(unittest.TestCase):
    """The classification ledger contract."""

    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()
        cls.ledger = cls.tool.build_ledger(str(REPO))

    def test_every_block_is_classified_exactly_once(self):
        categories = set(self.tool.CATEGORIES)
        self.assertGreater(self.ledger["counts"]["blocks"], 0)
        for record in self.ledger["records"]:
            self.assertIn(record["category"], categories)

    def test_every_record_has_an_evidence_quote(self):
        for record in self.ledger["records"]:
            self.assertTrue(record["evidence"].strip(),
                            f"no evidence quote for {record['file']}"
                            f":{record['start_line']}")

    def test_evidence_quote_comes_from_the_block(self):
        for record in self.ledger["records"]:
            self.assertIn(record["evidence"], record["block_text"])

    def test_counts_match_the_records(self):
        counts = {category: 0 for category in self.tool.CATEGORIES}
        for record in self.ledger["records"]:
            counts[record["category"]] += 1
        self.assertEqual(counts, self.ledger["counts"]["by_category"])
        self.assertEqual(len(self.ledger["records"]),
                         self.ledger["counts"]["blocks"])

    def test_excuse_blocks_carry_a_cluster(self):
        for record in self.ledger["records"]:
            if record["category"] == "Excuse":
                self.assertTrue(record["cluster"])

    def test_flood_ratio_is_raw_over_clusters(self):
        clusters = self.ledger["excuse_clusters"]
        self.assertEqual(clusters["cluster_count"],
                         len(clusters["clusters"]))
        if clusters["cluster_count"]:
            self.assertAlmostEqual(
                clusters["flood_ratio"],
                round(clusters["raw_excuse_blocks"] / clusters["cluster_count"], 3),
            )

    def test_every_block_is_classified_by_the_table(self):
        """No block falls back to the keyword rules in the measured census."""
        for record in self.ledger["records"]:
            self.assertEqual(record["method"], "table",
                             f"rule fallback for {record['file']}"
                             f":{record['start_line']}")


class ReproducibilityTests(unittest.TestCase):
    """Two independent runs must produce the same census."""

    def test_two_runs_give_the_same_counts(self):
        tool = load_tool()
        first = tool.build_ledger(str(REPO))
        second = tool.build_ledger(str(REPO))
        self.assertEqual(first["counts"], second["counts"])
        self.assertEqual(
            [(r["file"], r["start_line"], r["category"])
             for r in first["records"]],
            [(r["file"], r["start_line"], r["category"])
             for r in second["records"]],
        )

    def test_cli_json_output_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "ledger.json")
            first = subprocess.run(
                [sys.executable, str(TOOL), "--json", "--out", out],
                capture_output=True, text=True, check=True)
            second = subprocess.run(
                [sys.executable, str(TOOL), "--json", "--out", out],
                capture_output=True, text=True, check=True)
            self.assertEqual(first.stdout, second.stdout)
            with open(out, encoding="utf-8") as handle:
                ledger = json.load(handle)
            self.assertEqual(ledger["counts"]["blocks"],
                             len(ledger["records"]))


if __name__ == "__main__":
    unittest.main()
