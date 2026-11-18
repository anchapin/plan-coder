"""Tests for bin/worktree_slots.py (worktree persistence Phase 1, #89).

Covers the contract the supervisor prompt relies on: exit 2 when the
feature is disabled or the repo is not opted in (supervisor then
behaves exactly as before), record/lookup round-trip, stale-slot
dropping, and eviction order (closed issues first, then LRU) with
real git worktrees.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
SCRIPT = BIN / "worktree_slots.py"


def make_config(tmp: Path, enabled=True, repos=("alpha",), cap=2):
    cfg = {
        "state": {"dir": str(tmp / "state")},
        "workers": {"worktree_persistence": {
            "enabled": enabled, "repos": list(repos),
            "max_slots_per_repo": cap}},
    }
    p = tmp / "config.json"
    p.write_text(json.dumps(cfg))
    return p


class SlotsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wt-slots-"))
        self.config = make_config(self.tmp)
        self.state_file = self.tmp / "slots.json"

    def run_cli(self, *argv, config=None):
        env = dict(os.environ)
        env["PLAN_CODER_CONFIG"] = str(config or self.config)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *argv,
             "--state-file", str(self.state_file)],
            capture_output=True, text=True, env=env, check=False)

    def make_worktree_dir(self, name, target_bytes=0):
        wt = self.tmp / name
        wt.mkdir(parents=True)
        if target_bytes:
            tdir = wt / "target"
            tdir.mkdir()
            (tdir / "blob").write_bytes(b"x" * target_bytes)
        return wt

    def test_check_disabled_exits_2(self):
        cfg = make_config(self.tmp, enabled=False)
        r = self.run_cli("check", "--repo", "alpha", config=cfg)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_check_repo_not_opted_in_exits_2(self):
        r = self.run_cli("check", "--repo", "beta")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_check_enabled_ok(self):
        r = self.run_cli("check", "--repo", "alpha", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["enabled"])

    def test_record_lookup_roundtrip(self):
        wt = self.make_worktree_dir("alpha-worker-7", target_bytes=1234)
        r = self.run_cli("record", "--repo", "alpha", "--issue", "7",
                         "--worktree", str(wt), "--base-sha", "abc123",
                         "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        slot = json.loads(r.stdout)
        self.assertEqual(slot["target_bytes"], 1234)
        self.assertEqual(slot["base_sha"], "abc123")
        r = self.run_cli("lookup", "--repo", "alpha", "--issue", "7",
                         "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["found"])
        self.assertEqual(out["slot"]["worktree"], str(wt))

    def test_lookup_no_slot(self):
        r = self.run_cli("lookup", "--repo", "alpha", "--issue", "99",
                         "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)["found"])

    def test_lookup_drops_stale_slot(self):
        wt = self.make_worktree_dir("alpha-worker-8")
        self.run_cli("record", "--repo", "alpha", "--issue", "8",
                     "--worktree", str(wt), "--base-sha", "abc")
        wt.rmdir()  # worktree vanishes (manual cleanup, older harness)
        r = self.run_cli("lookup", "--repo", "alpha", "--issue", "8",
                         "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)["found"])
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state["slots"], [])

    def test_record_disabled_exits_2(self):
        cfg = make_config(self.tmp, enabled=False)
        wt = self.make_worktree_dir("alpha-worker-7")
        r = self.run_cli("record", "--repo", "alpha", "--issue", "7",
                         "--worktree", str(wt), "--base-sha", "abc",
                         config=cfg)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_record_missing_worktree_exits_2(self):
        r = self.run_cli("record", "--repo", "alpha", "--issue", "7",
                         "--worktree", str(self.tmp / "nope"),
                         "--base-sha", "abc")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


class EvictionCase(unittest.TestCase):
    """Eviction against real git worktrees in a temp clone."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wt-evict-"))
        self.config = make_config(self.tmp, cap=2)
        self.state_file = self.tmp / "slots.json"
        self.clone = self.tmp / "clone"
        self.clone.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "Test")
        (self.clone / "f.txt").write_text("x")
        self.git("add", ".")
        self.git("commit", "-qm", "init")

    def git(self, *argv, cwd=None):
        return subprocess.run(["git", *argv], cwd=cwd or self.clone,
                              capture_output=True, text=True, check=True)

    def run_cli(self, *argv):
        env = dict(os.environ)
        env["PLAN_CODER_CONFIG"] = str(self.config)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *argv,
             "--state-file", str(self.state_file)],
            capture_output=True, text=True, env=env, check=False)

    def add_slot(self, issue):
        wt = self.tmp / f"alpha-worker-{issue}"
        self.git("worktree", "add", "-q", str(wt), "HEAD")
        r = self.run_cli("record", "--repo", "alpha", "--issue",
                         str(issue), "--worktree", str(wt),
                         "--base-sha", "abc")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return wt

    def slots_on_disk(self):
        return json.loads(self.state_file.read_text())["slots"]

    def test_evicts_lru_over_cap(self):
        wts = [self.add_slot(i) for i in (1, 2, 3)]  # cap is 2
        r = self.run_cli("evict", "--repo", "alpha", "--clone",
                         str(self.clone), "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        evicted = json.loads(r.stdout)["evicted"]
        self.assertEqual(len(evicted), 1)
        self.assertEqual(evicted[0]["issue"], 1)  # least recently used
        self.assertFalse(wts[0].exists())
        self.assertTrue(wts[1].exists() and wts[2].exists())
        self.assertEqual(len(self.slots_on_disk()), 2)

    def test_closed_issue_evicts_first_even_under_cap(self):
        wt1 = self.add_slot(1)
        wt2 = self.add_slot(2)
        r = self.run_cli("evict", "--repo", "alpha", "--clone",
                         str(self.clone), "--closed-issues", "2",
                         "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        evicted = json.loads(r.stdout)["evicted"]
        self.assertEqual([e["issue"] for e in evicted], [2])
        self.assertFalse(wt2.exists())
        self.assertTrue(wt1.exists())

    def test_evict_disabled_exits_2(self):
        cfg = make_config(self.tmp, enabled=False)
        env = dict(os.environ)
        env["PLAN_CODER_CONFIG"] = str(cfg)
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "evict", "--repo", "alpha",
             "--clone", str(self.clone), "--state-file",
             str(self.state_file)],
            capture_output=True, text=True, env=env, check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
