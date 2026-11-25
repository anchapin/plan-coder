"""Exec-guard tests (plan-coder issue #170).

The guard is a harness-side check on the command string a worker asks to
run, applied before the shell runs it.  These tests pin the two things
issue #170 is about:

  * the example deployment config ships the guard ON (or, if an operator
    turns it off, ships an explicit warning about what that leaves
    unconfined), and
  * under that default config a worker exec command that tries to read a
    path outside the worktree is blocked / guard-flagged, while an
    ordinary in-worktree command still runs.

No network, no live harness: every file written lands in a temporary
directory.

Run: python3 -m pytest tests/test_exec_guard_default.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"
EXAMPLE_CONFIG = REPO / "config" / "deployment.example.json"

sys.path.insert(0, str(BIN))
_spec = importlib.util.spec_from_file_location("agent_execguard", BIN / "agent.py")
agent = importlib.util.module_from_spec(_spec)
sys.modules["agent_execguard"] = agent
_spec.loader.exec_module(agent)


def example_guard_config() -> dict:
    """The exec-guard section of the committed example config."""
    with open(EXAMPLE_CONFIG, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["workers"]["exec_guard"]


class ExampleConfigTests(unittest.TestCase):
    """The example config never ships the guard silently disabled."""

    def test_example_config_does_not_ship_the_guard_disabled_without_warning(self):
        cfg = example_guard_config()
        if cfg.get("enabled"):
            self.assertTrue(cfg["enabled"])
            return
        about = str(cfg.get("_about", ""))
        self.assertTrue(
            about,
            "the example config disables the exec guard without an "
            "explanatory _about: the residual risk (worker exec stays "
            "unconfined) must be stated in the shipped config")
        self.assertIn("unconfined", about.lower())

    def test_example_config_documents_the_guard_limitations(self):
        about = str(example_guard_config().get("_about", "")).lower()
        self.assertIn("not a sandbox", about)
        self.assertIn("does not block", about)


class GuardedWorkerTests(unittest.TestCase):
    """Under the example (default) config the guard blocks outside reads."""

    def setUp(self):
        self.guard = example_guard_config()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workdir = os.path.join(self.tmp.name, "worktree")
        os.makedirs(self.workdir)
        # A file that lives OUTSIDE the worktree: reading it is the
        # escape the guard must refuse.
        self.outside = os.path.join(self.tmp.name, "outside-secret.txt")
        with open(self.outside, "w", encoding="utf-8") as fh:
            fh.write("TOP-SECRET-VALUE\n")
        self.worker = agent.Worker(self.workdir, 30, exec_guard=self.guard)

    def test_absolute_path_outside_the_worktree_is_blocked(self):
        result = self.worker.run_tool(
            "exec", {"command": f"cat {self.outside}"})
        self.assertIn("[exec-guard] BLOCKED", result)
        self.assertNotIn("TOP-SECRET-VALUE", result)
        self.assertIn("exit=126", result)

    def test_relative_escape_outside_the_worktree_is_blocked(self):
        result = self.worker.run_tool("exec", {"command": "cat ../outside-secret.txt"})
        self.assertIn("[exec-guard] BLOCKED", result)
        self.assertNotIn("TOP-SECRET-VALUE", result)

    def test_home_directory_read_is_blocked(self):
        result = self.worker.run_tool("exec", {"command": "cat ~/.ssh/id_rsa"})
        self.assertIn("[exec-guard] BLOCKED", result)

    def test_command_inside_the_worktree_still_runs(self):
        result = self.worker.run_tool("exec", {"command": "echo inside-ok"})
        self.assertIn("exit=0", result)
        self.assertIn("inside-ok", result)
        self.assertNotIn("[exec-guard] BLOCKED", result)

    def test_guard_off_is_the_pre_guard_behaviour(self):
        # No exec_guard section at all (an install without a deployment
        # config): the command runs, exactly as before the guard existed.
        worker = agent.Worker(self.workdir, 30)
        result = worker.run_tool("exec", {"command": f"cat {self.outside}"})
        self.assertNotIn("[exec-guard] BLOCKED", result)
        self.assertIn("TOP-SECRET-VALUE", result)

    def test_env_scrub_drops_credential_shaped_variables(self):
        os.environ["PLAN_CODER_TEST_API_KEY"] = "sk-not-a-real-key"
        self.addCleanup(os.environ.pop, "PLAN_CODER_TEST_API_KEY", None)
        result = self.worker.run_tool(
            "exec", {"command": "printenv PLAN_CODER_TEST_API_KEY"})
        self.assertNotIn("sk-not-a-real-key", result)


class GuardFunctionTests(unittest.TestCase):
    """The guard helpers themselves, without the worker around them."""

    def test_check_is_off_without_a_config_section(self):
        self.assertIsNone(agent.exec_guard_check("cat /etc/passwd", "/tmp", None))
        self.assertIsNone(
            agent.exec_guard_check("cat /etc/passwd", "/tmp", {"enabled": False}))

    def test_check_blocks_outside_paths_and_allows_inside_ones(self):
        cfg = {"enabled": True}
        self.assertIsNotNone(
            agent.exec_guard_check("cat /etc/passwd", "/tmp", cfg))
        self.assertIsNotNone(
            agent.exec_guard_check("cat ../secret", "/tmp", cfg))
        self.assertIsNone(
            agent.exec_guard_check("cat README.md", "/tmp", cfg))
        self.assertIsNone(
            agent.exec_guard_check("python3 -m pytest tests/ -q", "/tmp", cfg))

    def test_allow_paths_extends_the_exempt_list(self):
        cfg = {"enabled": True, "allow_paths": ["/opt/data"]}
        self.assertIsNone(
            agent.exec_guard_check("cat /opt/data/file.txt", "/tmp", cfg))
        self.assertIsNotNone(
            agent.exec_guard_check("cat /opt/other/file.txt", "/tmp", cfg))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
