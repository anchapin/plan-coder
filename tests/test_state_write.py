"""Tests for bin/state_write.py's shape refusal (plan-coder issue #174).

A staging file that parses as JSON can still be the wrong shape: an
empty array committed over `shepherd.json` would leave every reader
(`dashboard.py`, `plan_coder.py`, `flagbatch.py`) with nothing to
read.  `commit()` therefore refuses a parseable-but-wrong-shaped
staging file for the known state files, before any backup or replace
of the live file, and leaves the staging file in place for diagnosis.

Run: python3 -m pytest tests/test_state_write.py -q
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE_WRITE = REPO / "bin" / "state_write.py"

WORKERS = {
    "repo-a-123": {
        "branch": "worker/123-retry-backoff",
        "issue": 123,
        "repo": "repo-a",
        "status": "working",
        "tokens": 100,
    },
}

SHEPHERD = {
    "tracked_prs": {
        "https://github.com/you/repo-a/pull/456": {
            "repo": "repo-a", "pr": 456, "rerun_count": 0,
            "ci_fix_attempts": 0,
        },
    },
    "seen_merged": ["https://github.com/you/repo-a/pull/1"],
    "needs_alex": [],
}


class StateWriteShapeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-state-shape-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sw(self, *argv):
        return subprocess.run(
            [sys.executable, str(STATE_WRITE), *argv],
            capture_output=True, text=True, timeout=60, check=False)

    def write(self, name: str, payload) -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def backups(self, dest: Path):
        return list(self.tmp.glob(dest.name + ".statebak-*"))


class TestShapeRefusal(StateWriteShapeCase):
    def test_commit_refuses_wrong_shaped_shepherd_and_keeps_live(self):
        dest = self.write("shepherd.json", SHEPHERD)
        live_before = dest.read_text(encoding="utf-8")
        staging = self.write("shepherd.json.new", [])

        r = self.run_sw("commit", str(staging), str(dest))

        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)
        self.assertIn("shape", r.stderr.lower())
        self.assertIn("shepherd.json", r.stderr)
        # the live file is untouched, no backup was taken, and the
        # staging file is preserved for diagnosis
        self.assertEqual(dest.read_text(encoding="utf-8"), live_before)
        self.assertEqual(json.loads(dest.read_text(encoding="utf-8")),
                         SHEPHERD)
        self.assertEqual(self.backups(dest), [])
        self.assertTrue(staging.exists())
        self.assertEqual(json.loads(staging.read_text(encoding="utf-8")), [])

    def test_commit_refuses_wrong_shaped_workers_and_keeps_live(self):
        dest = self.write("workers.json", WORKERS)
        live_before = dest.read_text(encoding="utf-8")
        staging = self.write("workers.json.new", [])

        r = self.run_sw("commit", str(staging), str(dest))

        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)
        self.assertIn("shape", r.stderr.lower())
        self.assertIn("workers.json", r.stderr)
        self.assertEqual(dest.read_text(encoding="utf-8"), live_before)
        self.assertEqual(self.backups(dest), [])
        self.assertTrue(staging.exists())

    def test_commit_refuses_wrong_typed_inner_key(self):
        dest = self.write("shepherd.json", SHEPHERD)
        live_before = dest.read_text(encoding="utf-8")
        staging = self.write("shepherd.json.new",
                             {"tracked_prs": "not-an-object"})

        r = self.run_sw("commit", str(staging), str(dest))

        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)
        self.assertIn("tracked_prs", r.stderr)
        self.assertEqual(dest.read_text(encoding="utf-8"), live_before)
        self.assertEqual(self.backups(dest), [])

    def test_commit_refuses_non_object_worker_entries(self):
        dest = self.write("workers.json", WORKERS)
        live_before = dest.read_text(encoding="utf-8")
        staging = self.write("workers.json.new", {"repo-a-123": "working"})

        r = self.run_sw("commit", str(staging), str(dest))

        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)
        self.assertIn("workers.json", r.stderr)
        self.assertEqual(dest.read_text(encoding="utf-8"), live_before)
        self.assertEqual(self.backups(dest), [])


class TestWellShapedCommit(StateWriteShapeCase):
    def test_commit_replaces_well_shaped_shepherd_atomically(self):
        dest = self.write("shepherd.json", SHEPHERD)
        staging = self.write("shepherd.json.new",
                             {"tracked_prs": {"https://x/pull/1": {"repo": "x",
                                                                 "pr": 1}}})

        r = self.run_sw("commit", str(staging), str(dest), "--json")

        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)
        self.assertEqual(result["committed"], str(dest))
        self.assertEqual(result["type"], "object")
        committed = json.loads(dest.read_text(encoding="utf-8"))
        self.assertEqual(committed["tracked_prs"],
                         {"https://x/pull/1": {"repo": "x", "pr": 1}})
        backups = self.backups(dest)
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")),
                         SHEPHERD)

    def test_commit_replaces_well_shaped_workers_atomically(self):
        dest = self.write("workers.json", WORKERS)
        staging = self.write("workers.json.new",
                             {"repo-b-78": {"repo": "repo-b", "issue": 78,
                                            "status": "done"}})

        r = self.run_sw("commit", str(staging), str(dest))

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(dest.read_text(encoding="utf-8")),
                         {"repo-b-78": {"repo": "repo-b", "issue": 78,
                                        "status": "done"}})
        backups = self.backups(dest)
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")),
                         WORKERS)

    def test_commit_accepts_workers_wrapper_object(self):
        dest = self.write("workers.json", WORKERS)
        staging = self.write("workers.json.new", {"workers": WORKERS})

        r = self.run_sw("commit", str(staging), str(dest))

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(dest.read_text(encoding="utf-8")),
                         {"workers": WORKERS})

    def test_commit_accepts_shepherd_with_string_last_trigger(self):
        # Regression (supervisor collection, 2026-11-25): the live
        # shepherd.json carries last_trigger as an ISO timestamp string;
        # a shape check that demands an object refuses the real file.
        payload = dict(SHEPHERD, last_trigger="2026-11-25T13:23:50Z")
        dest = self.write("shepherd.json", SHEPHERD)
        staging = self.write("shepherd.json.new", payload)

        r = self.run_sw("commit", str(staging), str(dest))

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(dest.read_text(encoding="utf-8")),
                         payload)

    def test_unknown_destination_keeps_parse_only_behaviour(self):
        dest = self.tmp / "worktree_slots.json"
        staging = self.write("worktree_slots.json.new", [1, 2, 3])

        r = self.run_sw("commit", str(staging), str(dest))

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(dest.read_text(encoding="utf-8")),
                         [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
