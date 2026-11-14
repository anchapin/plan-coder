"""Tests for the Phase-0 ast-grep matchline spike (plan-coder issue #18).

The spike rules live in ``spikes/astgrep-matchline/rules/*.yml`` and the
seeded fixtures in ``spikes/astgrep-matchline/fixtures/``.

Two things are checked:

1. Always (no external binary needed): every rule file is a well-formed
   ast-grep rule YAML, every rule has one seeded violation fixture and one
   clean counterpart fixture.
2. Only when a real ast-grep binary is available: the rules are run with
   ``ast-grep scan --json`` over both fixture sets — every rule must fire on
   its seeded violation file and the clean set must produce zero findings.

The ast-grep binary is NOT a hard dependency of this test: on an image where
``sg`` is the shadow-utils ``newgrp`` binary (or ast-grep is absent), the
second test is skipped.

Run: python3 -m pytest tests/test_astgrep_matchline.py -q
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPIKE = REPO / "spikes" / "astgrep-matchline"
RULES_DIR = SPIKE / "rules"
VIOLATION_DIR = SPIKE / "fixtures" / "violation"
CLEAN_DIR = SPIKE / "fixtures" / "clean"

# rule id -> (seeded violation file, clean counterpart file)
EXPECTED_PAIRS = {
    "no-sys-path-hack": ("v1_sys_path_hack.py", "c1_no_sys_path_hack.py"),
    "no-jesse-import": ("v2_jesse_import.py", "c2_no_jesse_import.py"),
    "no-xml-entity-expansion": (
        "v3_xml_entity_expansion.py",
        "c3_xml_entity_expansion.py",
    ),
    "no-hardcoded-absolute-path": (
        "v4_hardcoded_absolute_path.py",
        "c4_no_hardcoded_absolute_path.py",
    ),
    "no-subprocess-shell-true": (
        "v5_subprocess_shell_true.py",
        "c5_subprocess_shell_true.py",
    ),
}


_TOP_LEVEL_KEY = re.compile(r"^([A-Za-z_][\w-]*):(?:\s+(.*?))?\s*$")


def parse_rule_fields(text: str) -> dict[str, str]:
    """Top-level fields of an ast-grep rule file, stdlib only.

    CI installs only pytest + ruff (see .github/workflows/ci.yml), so
    this test must not depend on PyYAML.  The rule files are flat:
    scalar top-level fields plus one nested ``rule:`` block, and the
    checks below need only the top-level scalars (the block counts as
    present with an empty value).  Full YAML validation happens when
    ast-grep itself loads the rule in AstGrepScanTest.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line[0] in " \t#-":
            continue
        match = _TOP_LEVEL_KEY.match(line)
        if match:
            value = match.group(2) or ""
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            elif len(value) >= 2 and value[0] == value[-1] == "'":
                value = value[1:-1]
            fields[match.group(1)] = value
    return fields


def find_ast_grep() -> str | None:
    """Return a real ast-grep binary path, or None."""
    import os

    candidates = []
    env_bin = os.environ.get("AST_GREP_BIN")
    if env_bin:
        candidates.append(env_bin)
    for name in ("ast-grep", "sg"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for candidate in candidates:
        try:
            proc = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if "ast-grep" in ((proc.stdout or "") + (proc.stderr or "")).lower():
            return candidate
    return None


class RuleFileTest(unittest.TestCase):
    """Rule files are valid ast-grep YAML and have their fixture pair."""

    def test_rule_files_are_valid(self):
        rule_files = sorted(RULES_DIR.glob("*.yml"))
        self.assertGreaterEqual(len(rule_files), 3, "3-5 rules are required")
        self.assertLessEqual(len(rule_files), 5, "3-5 rules are required")
        seen = set()
        for path in rule_files:
            spec = parse_rule_fields(path.read_text(encoding="utf-8"))
            self.assertTrue(spec, f"{path.name} has no top-level YAML fields")
            for field in ("id", "language", "severity", "message", "note", "rule"):
                self.assertIn(field, spec, f"{path.name} misses '{field}'")
            self.assertEqual(spec["language"], "python")
            self.assertNotIn(spec["id"], seen, "rule ids must be unique")
            seen.add(spec["id"])
            self.assertTrue(spec["message"].strip(), f"{path.name} has an empty message")

    def test_every_rule_has_fixture_pair(self):
        for rule_id, (violation, clean) in EXPECTED_PAIRS.items():
            self.assertTrue(
                (VIOLATION_DIR / violation).exists(),
                f"missing seeded violation fixture for {rule_id}",
            )
            self.assertTrue(
                (CLEAN_DIR / clean).exists(),
                f"missing clean fixture for {rule_id}",
            )


@unittest.skipIf(find_ast_grep() is None, "ast-grep binary not available")
class AstGrepScanTest(unittest.TestCase):
    """Real `ast-grep scan --json` over both fixture sets."""

    def _scan(self, binary: str, target: Path) -> dict:
        counts: dict[str, int] = {}
        for rule_path in sorted(RULES_DIR.glob("*.yml")):
            spec = parse_rule_fields(rule_path.read_text(encoding="utf-8"))
            proc = subprocess.run(
                [binary, "scan", "--json", "--rule", str(rule_path), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                proc.returncode, 0, f"ast-grep failed: {proc.stderr[:400]}"
            )
            payload = json.loads(proc.stdout.strip() or "[]")
            if isinstance(payload, dict):
                payload = [payload]
            hits = sum(len(entry.get("matches", [])) for entry in payload)
            counts[spec["id"]] = hits
        return counts

    def test_violation_set_fires_every_rule(self):
        binary = find_ast_grep()
        self.assertIsNotNone(binary)
        counts = self._scan(binary, VIOLATION_DIR)
        for rule_id in EXPECTED_PAIRS:
            self.assertGreaterEqual(
                counts.get(rule_id, 0), 1, f"rule {rule_id} did not fire"
            )

    def test_clean_set_has_zero_findings(self):
        binary = find_ast_grep()
        self.assertIsNotNone(binary)
        counts = self._scan(binary, CLEAN_DIR)
        self.assertEqual(
            {k: v for k, v in counts.items() if v},
            {},
            "the clean fixture set must produce zero findings",
        )


if __name__ == "__main__":
    unittest.main()
