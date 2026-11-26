"""Tests for bin/playbook.py (per-repo learned playbooks, #184).

Pinned contract:
  * match returns active/stale entries on a keyword, failure-class,
    path or text signal, and never returns pending or archived ones;
  * lifecycle is active <=14d, stale 15-30d, archived >30d, computed
    from last activity (creation or last recorded usage), and the
    report deletes/moves nothing;
  * propose stages a PENDING entry that does not match (not active);
  * record-usage is append-only: earlier ledger lines are untouched.
Hermetic: temp dirs only, no network, no credentials.
"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import playbook  # noqa: E402


def write_entry(store, repo="fluxion", entry_id="e-cargo",
                status="active", created="2026-11-01",
                keywords="cargo, build", failures="step-cap",
                paths="Cargo.toml", body="Warm the cargo build first."):
    folder = Path(store) / repo
    if status == "pending":
        folder = folder / "pending"
    if status == "archived":
        folder = folder / "archive"
    folder.mkdir(parents=True, exist_ok=True)
    entry = {"id": entry_id, "repo": repo, "title": f"Title {entry_id}",
             "status": status, "created": created,
             "keywords": [k.strip() for k in keywords.split(",")],
             "failure_classes": [f.strip() for f in failures.split(",")],
             "paths": [p.strip() for p in paths.split(",")],
             "body": body}
    (folder / f"{entry_id}.md").write_text(
        playbook.render_entry(entry), encoding="utf-8")
    return entry


class StoreCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="playbook-test-")
        self.store = str(Path(self.tmp) / "store")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestMatch(StoreCase):
    def test_keyword_match(self):
        write_entry(self.store)
        hits = playbook.match_entries(self.store, "fluxion",
                                      keywords=["cargo"])
        self.assertEqual([h["id"] for h in hits], ["e-cargo"])

    def test_failure_class_and_path_match(self):
        write_entry(self.store)
        by_class = playbook.match_entries(
            self.store, "fluxion", failure_class="step-cap")
        by_path = playbook.match_entries(
            self.store, "fluxion", paths=["Cargo.toml"])
        self.assertEqual(len(by_class), 1)
        self.assertEqual(len(by_path), 1)

    def test_no_signal_no_match_and_wrong_repo(self):
        write_entry(self.store)
        self.assertEqual(playbook.match_entries(
            self.store, "fluxion", keywords=["unrelated"]), [])
        self.assertEqual(playbook.match_entries(
            self.store, "matchline", keywords=["cargo"]), [])

    def test_pending_and_archived_never_match(self):
        write_entry(self.store, entry_id="e-pend", status="pending")
        write_entry(self.store, entry_id="e-arch", status="archived")
        self.assertEqual(playbook.match_entries(
            self.store, "fluxion", keywords=["cargo"]), [])


class TestLifecycle(StoreCase):
    def test_transitions(self):
        write_entry(self.store, created="2026-11-01")
        usage = []
        entry = playbook.load_entries(self.store)[0]
        self.assertEqual(playbook.lifecycle_status(
            entry, usage, date(2026, 11, 15)), "active")
        self.assertEqual(playbook.lifecycle_status(
            entry, usage, date(2026, 11, 20)), "stale")
        self.assertEqual(playbook.lifecycle_status(
            entry, usage, date(2026, 12, 5)), "archived")

    def test_usage_refreshes_activity_and_report_deletes_nothing(self):
        write_entry(self.store, created="2026-11-01")
        playbook.record_usage(self.store, "fluxion", "e-cargo",
                              ts="2026-11-28T00:00:00+00:00")
        report = playbook.lifecycle_report(
            self.store, now=date(2026, 12, 5))
        self.assertEqual(report["counts"]["active"], 1)
        self.assertTrue(
            (Path(self.store) / "fluxion" / "e-cargo.md").exists())

    def test_archived_dir_stays_archived(self):
        write_entry(self.store, entry_id="e-arch", status="archived",
                    created="2026-11-25")
        report = playbook.lifecycle_report(
            self.store, now=date(2026, 11, 26))
        self.assertEqual(report["counts"]["archived"], 1)


class TestPropose(StoreCase):
    def test_propose_is_pending_and_not_active(self):
        entry = playbook.propose_entry(
            self.store, "fluxion", "New lesson", "Body text.",
            keywords=["cargo"], now=date(2026, 11, 26))
        self.assertEqual(entry["status"], "pending")
        self.assertTrue(Path(entry["source"]).exists())
        self.assertIn("pending", entry["source"])
        self.assertEqual(playbook.match_entries(
            self.store, "fluxion", keywords=["cargo"],
            now=date(2026, 11, 26)), [])
        listed = playbook.load_entries(self.store)
        self.assertEqual(listed[0]["status"], "pending")
        report = playbook.lifecycle_report(
            self.store, now=date(2026, 12, 30))
        self.assertEqual(report["counts"]["pending"], 1)

    def test_propose_refuses_duplicate(self):
        playbook.propose_entry(self.store, "fluxion", "Dup", "B.",
                               entry_id="dup", now=date(2026, 11, 26))
        with self.assertRaises(FileExistsError):
            playbook.propose_entry(self.store, "fluxion", "Dup", "B.",
                                   entry_id="dup",
                                   now=date(2026, 11, 26))


class TestUsageAppendOnly(StoreCase):
    def test_append_only(self):
        write_entry(self.store)
        playbook.record_usage(self.store, "fluxion", "e-cargo",
                              issue=1, ts="2026-11-02T00:00:00+00:00")
        path = Path(self.store) / "usage.jsonl"
        first = path.read_text(encoding="utf-8")
        playbook.record_usage(self.store, "fluxion", "e-cargo",
                              issue=2, ts="2026-11-03T00:00:00+00:00")
        second = path.read_text(encoding="utf-8")
        self.assertTrue(second.startswith(first))
        rows = [json.loads(x) for x in second.splitlines()]
        self.assertEqual([r["issue"] for r in rows], [1, 2])


class TestSeedExamples(unittest.TestCase):
    def test_four_fluxion_seeds_parse_and_match(self):
        seed = ROOT / "examples" / "playbooks"
        entries = playbook.load_entries(str(seed))
        self.assertEqual(len(entries), 4)
        self.assertTrue(all(e["repo"] == "fluxion" for e in entries))
        hits = playbook.match_entries(
            str(seed), "fluxion", failure_class="incomplete-report",
            now=date(2026, 11, 26))
        self.assertEqual([h["id"] for h in hits],
                         ["fluxion-measurement-report-first"])


class TestCli(StoreCase):
    def test_list_and_propose_exit_codes(self):
        write_entry(self.store)
        self.assertEqual(playbook.main(
            ["--store", self.store, "list", "--repo", "fluxion"]), 0)
        self.assertEqual(playbook.main(
            ["--store", self.store, "propose", "--repo", "fluxion",
             "--title", "Via CLI", "--body", "Text."]), 0)
        self.assertEqual(playbook.main(
            ["--store", "/nonexistent-store-xyz", "list"]), 2)


if __name__ == "__main__":
    unittest.main()
