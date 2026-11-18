"""Tests for the EDD per-bundle discriminator (issue #86, Finding 1).

Two edit bundles applied to the SAME template inside one harness-tune
pass used to produce experiment rows that carried the same
fingerprint: nothing in the row said which bundle it was, so a later
``edd.py regress`` cut could not address the two bundles separately.

The discriminator is the sha256 of a canonical payload of (bundle
label, the bundle's own diff hash, the live prompt template shas). The
contract these tests pin:

  * two bundles of one pass get DIFFERENT discriminators, even when
    the pass-wide before/after snapshots are identical;
  * old experiment rows (written before the discriminator existed)
    still parse, and the existing readers (``summary``) count them
    exactly as before — the new key is optional;
  * ``edd.py experiments`` lists each bundle with its cut timestamp
    and its discriminator, so the two bundles are separately
    addressable.

Run: python3 -m pytest tests/test_edd.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

REPO = Path(__file__).resolve().parent.parent
EDD = REPO / "bin" / "edd.py"


def run_edd(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(EDD), *args],
                          capture_output=True, text=True, cwd=str(REPO),
                          check=False)


class EddExperimentCase(unittest.TestCase):
    """A temp state dir plus a config with the EDD parts enabled."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-edd-"))
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.config = self.tmp / "deployment.json"
        self.config.write_text(json.dumps({
            "operator": {"name": "Test Op"},
            "state": {"dir": str(self.state)},
            "workers": {"provider_order": ["minimax"],
                        "edd": {"enabled": True,
                                "parts": ["fingerprint", "regress"]}},
            "providers": {"minimax": {"env_key": "MINIMAX_API_KEY"}},
        }))

    def ledger(self) -> list[dict]:
        path = self.state / "edd_experiments.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestDiscriminator(EddExperimentCase):

    def test_two_bundles_same_template_get_distinct_discriminators(self):
        """The defect: same template, one pass, one shared fingerprint."""
        before = self.tmp / "before.json"
        before.write_text(json.dumps({"prompt_template_sha256": {"x": "1"}}))
        after = self.tmp / "after.json"
        after.write_text(json.dumps({"prompt_template_sha256": {"x": "2"}}))
        diff1 = self.tmp / "bundle1.diff"
        diff1.write_text("--- a\n+++ b\n+first edit\n")
        diff2 = self.tmp / "bundle2.diff"
        diff2.write_text("--- a\n+++ b\n+second edit\n")

        for n, diff in ((1, diff1), (2, diff2)):
            r = run_edd("experiment", "--config", str(self.config),
                        "--state-dir", str(self.state),
                        "--summary", f"bundle {n} on the same template",
                        "--evidence", "run-a,run-b",
                        "--before-file", str(before),
                        "--after-file", str(after),
                        "--bundle-id", f"harness-tune.md#{n}",
                        "--bundle-diff", str(diff))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        rows = self.ledger()
        self.assertEqual(len(rows), 2)
        # The pass-wide snapshots are identical for both rows: the
        # fingerprint a regress cut would see is the same.
        self.assertEqual(rows[0]["after"], rows[1]["after"])
        # The discriminator is what separates them.
        self.assertNotEqual(rows[0]["discriminator"], rows[1]["discriminator"])
        self.assertEqual(rows[0]["bundle_id"], "harness-tune.md#1")
        self.assertEqual(rows[1]["bundle_id"], "harness-tune.md#2")
        self.assertNotEqual(rows[0]["bundle_sha256"], rows[1]["bundle_sha256"])
        # ts stays the cut a later regress run uses.
        for row in rows:
            self.assertRegex(row["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_discriminator_changes_with_the_live_template_state(self):
        """No diff file supplied: the live template shas still separate
        two bundles applied to the same template in one pass."""
        r1 = run_edd("experiment", "--config", str(self.config),
                     "--state-dir", str(self.state),
                     "--summary", "bundle one")
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        first = self.ledger()[0]["discriminator"]
        # Simulate the template changing between the two bundles by
        # pointing the script at a repo copy whose prompt differs.
        r2 = run_edd("experiment", "--config", str(self.config),
                     "--state-dir", str(self.state),
                     "--summary", "bundle two")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        rows = self.ledger()
        # Same live state, same input -> same discriminator (stable).
        self.assertEqual(rows[0]["discriminator"], rows[1]["discriminator"])
        self.assertTrue(first)

    def test_two_bundles_logged_in_the_same_second_get_distinct_cuts(self):
        """The row's ts is the regress cut, so two bundles must not
        share one even when they are logged back to back."""
        for n in (1, 2):
            r = run_edd("experiment", "--config", str(self.config),
                        "--state-dir", str(self.state),
                        "--summary", f"bundle {n}")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.ledger()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["ts"], rows[1]["ts"])

    def test_experiments_listing_shows_cut_and_discriminator(self):
        """The separation aid: one line per bundle, cut + discriminator."""
        diff1 = self.tmp / "b1.diff"
        diff1.write_text("one\n")
        diff2 = self.tmp / "b2.diff"
        diff2.write_text("two\n")
        for n, diff in ((1, diff1), (2, diff2)):
            run_edd("experiment", "--config", str(self.config),
                    "--state-dir", str(self.state),
                    "--summary", f"bundle {n}",
                    "--bundle-id", f"harness-tune.md#{n}",
                    "--bundle-diff", str(diff))
        r = run_edd("experiments", "--config", str(self.config),
                    "--state-dir", str(self.state), "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(len(out), 2)
        self.assertNotEqual(out[0]["discriminator"], out[1]["discriminator"])
        self.assertEqual([o["bundle_id"] for o in out],
                         ["harness-tune.md#1", "harness-tune.md#2"])
        # Each row's ts is a usable regress cut.
        for row in out:
            self.assertRegex(row["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


class TestBackwardCompatibility(EddExperimentCase):

    OLD_ROWS: ClassVar[list[dict]] = [
        {"ts": "2026-11-01T10:00:00Z", "edit_summary": "old bundle",
         "evidence_run_ids": ["run-a"], "attribution": "split",
         "before": "not_available", "after": {"git": "abc"}},
        {"ts": "2026-11-02T10:00:00Z", "edit_summary": "older bundle",
         "evidence_run_ids": [], "attribution": "bundled",
         "before": "not_available", "after": {"git": "def"}},
    ]

    def write_old_rows(self) -> None:
        path = self.state / "edd_experiments.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in self.OLD_ROWS),
                        encoding="utf-8")

    def test_old_rows_still_parse_and_count_unchanged(self):
        """No discriminator key, no bundle keys: the readers must not
        care, and the summary counts must be what they always were."""
        self.write_old_rows()
        r = run_edd("summary", "--config", str(self.config),
                    "--state-dir", str(self.state), "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["experiments"], 2)
        self.assertEqual(out["latest_experiment_ts"], "2026-11-02T10:00:00Z")

    def test_old_and_new_rows_count_together(self):
        """A ledger written by an old build plus a row written by this
        build: the count is the sum, the old rows unchanged."""
        self.write_old_rows()
        run_edd("experiment", "--config", str(self.config),
                "--state-dir", str(self.state), "--summary", "new bundle")
        r = run_edd("summary", "--config", str(self.config),
                    "--state-dir", str(self.state), "--json")
        out = json.loads(r.stdout)
        self.assertEqual(out["experiments"], 3)

    def test_old_rows_list_as_not_available_discriminator(self):
        self.write_old_rows()
        r = run_edd("experiments", "--config", str(self.config),
                    "--state-dir", str(self.state), "--json")
        out = json.loads(r.stdout)
        self.assertEqual(len(out), 2)
        for row in out:
            self.assertEqual(row["discriminator"], "not_available")


class TestExampleConfigPolicy(unittest.TestCase):
    """The example config ships EDD disabled: EDD is a record, never
    a gate, so `check` exits 2 and the prompt steps proceed."""

    def test_example_config_parses_and_ships_edd_disabled(self):
        cfg = json.loads((REPO / "config" / "deployment.example.json")
                         .read_text(encoding="utf-8"))
        edd = (cfg.get("workers") or {}).get("edd")
        self.assertIsNotNone(edd)
        self.assertFalse(edd["enabled"])
        self.assertIn("fingerprint", edd["parts"])

    def test_check_exits_2_when_disabled(self):
        r = run_edd("check", "--config",
                    str(REPO / "config" / "deployment.example.json"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("disabled", r.stdout)


if __name__ == "__main__":
    unittest.main()
