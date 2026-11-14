"""Regression test for bin/ast_grep_phase0_scan.py (issue #42, PR #43).

The harness advertises a scan-only mode (`--no-seed-check`).  In that mode
the seeded-violation check is skipped, so the seed mapping handed to the
report renderer is empty.  The renderer must still produce a report: the
scan-only mode is a documented CLI entry point and must not blow up.

The test drives the REAL entry point (`main()`), with a stub ast-grep
binary created by the test itself (prints a version for `--version`, emits
no matches for `scan`) and a tiny fixture tree.  No network, no wall clock,
no unseeded randomness.

Run: python3 -m pytest tests/test_ast_grep_phase0_scan.py -q
"""
from __future__ import annotations

import importlib.util
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "ast_grep_phase0_scan.py"

STUB_AST_GREP = """#!/usr/bin/env python3
import sys

if "--version" in sys.argv:
    sys.stdout.write("ast-grep 0.0.0-stub\\n")
    sys.exit(0)

# `scan` (with or without --inline-rules): report no matches.
sys.exit(0)
"""


def load_module():
    spec = importlib.util.spec_from_file_location("ast_grep_phase0_scan", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the dataclass decorator can resolve
    # cls.__module__ through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(SCRIPT.exists(), "bin/ast_grep_phase0_scan.py not present")
class NoSeedCheckModeTest(unittest.TestCase):
    """`--no-seed-check` must produce a report instead of raising."""

    def _write_stub(self, directory: Path) -> Path:
        stub = directory / "ast-grep"
        stub.write_text(STUB_AST_GREP, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return stub

    def test_no_seed_check_produces_report(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stub = self._write_stub(tmp_path)

            tree = tmp_path / "tree"
            tree.mkdir()
            (tree / "sample.py").write_text(
                '"""Sample module."""\n\n\ndef go():\n    print("go")\n',
                encoding="utf-8",
            )

            out_path = tmp_path / "report.md"
            rc = module.main(
                [
                    "--repo",
                    str(tree),
                    "--ast-grep",
                    str(stub),
                    "--no-seed-check",
                    "--out",
                    str(out_path),
                ]
            )

            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists(), "no report file was written")
            report = out_path.read_text(encoding="utf-8")
            self.assertIn("C1", report)
            self.assertNotIn("KeyError", report)


if __name__ == "__main__":
    unittest.main()
