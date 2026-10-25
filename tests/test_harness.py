"""Hermetic regression tests for the plan-coder harness.

Everything runs against a throwaway copy of bin/ + prompts/ in a temp
directory with a synthetic deployment config, so the suite passes on
any clone — including a public one with no deployment.local.json and
no credentials. No network access is used: provider auth is exercised
up to the request-header level only.

Run: python3 -m unittest discover -s tests
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

FULL_CONFIG = {
    "operator": {"name": "Test Op", "flag_label": "NEEDS HUMAN"},
    "state": {"dir": "/tmp/pc-state",
              "transcripts_dir": "/tmp/pc-state/transcripts"},
    "worktrees": {"dir": "/tmp/pc-wt"},
    "repos": [
        {"name": "alpha", "clone": "~/repos/alpha", "slug": "you/alpha",
         "base": "develop", "issues_per_cycle": 2,
         "skip_note": "[later] items", "brief_extra": "Alpha extra rule."},
        {"name": "beta", "clone": "~/repos/beta", "slug": "you/beta",
         "base": "main", "issues_per_cycle": 1},
    ],
    "supervisor": {"max_parallel": 6, "collect_deadline_hours": 2.5,
                   "interval_hours": 2, "quota_context": "Quota text.",
                   "merge_order_example": "", "memory_note": "",
                   "auth_note": ""},
    "workers": {"max_steps": 60, "provider_order": ["minimax", "zai"]},
    "providers": {
        "zai": {"env_key": "ZAI_API_KEY", "credential": "custom.zai"},
        "minimax": {"env_key": "MINIMAX_API_KEY",
                    "credential": "custom.minimax"},
    },
    "credentials": {"helper_dir": "~/.plan-coder/cred"},
    "shepherd": {"interval_minutes": 30, "trigger_cooldown_minutes": 30,
                 "max_fix_attempts": 2, "trigger_note": "Trigger note.",
                 "final_note": ""},
    "merge_policy": {"known_failures": "known drift",
                     "hard_stop_domain": "physics outputs",
                     "directive_quote": "\"move fast\"",
                     "directive_date": "2026-01-01"},
    "machine": {"build_note": "avoid heavy builds"},
    "skill": {"dir": "~/pc-skill"},
    "public_repo": {"slug": "you/plan-coder", "local_clone": "~/plan-coder"},
    "tune": {"ideas_brief_file": "brief.md", "flag_label": "[flag for human]",
             "log_step": "Log it."},
    "delivery": {"chat_id": "chat-123"},
    "crons": {
        "backlog-supervisor": {
            "title": "Sup", "owner": "goal:test",
            "schedule": {"kind": "interval", "every": "2h"},
            "timeout_secs": 10800},
        "pr-shepherd": {
            "title": "Shep", "owner": "goal:test",
            "schedule": {"kind": "interval", "every": "30m"},
            "timeout_secs": 3600},
        "harness-tune": {
            "title": "Tune", "owner": "goal:test",
            "schedule": {"kind": "weekly", "dow": ["Sun"],
                         "time": "13:06:00"},
            "timeout_secs": 86400},
    },
}


class TreeCase(unittest.TestCase):
    """Base: a temp copy of bin/ + prompts/ with FULL_CONFIG installed."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="pc-test-"))
        cls.root = cls.tmp / "repo"
        shutil.copytree(REPO / "bin", cls.root / "bin")
        shutil.copytree(REPO / "prompts", cls.root / "prompts")
        (cls.root / "config").mkdir(parents=True)
        cls.config = cls.root / "config" / "deployment.local.json"
        cls.config.write_text(json.dumps(FULL_CONFIG))
        cls.home = cls.tmp / "home"
        cls.home.mkdir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def env(self, **extra):
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home),
               "PLAN_CODER_CONFIG": str(self.config)}
        env.update(extra)
        return env

    def run_bin(self, script, *argv, env=None, cwd=None):
        return subprocess.run(
            [sys.executable, str(self.root / "bin" / script), *argv],
            capture_output=True, text=True, env=env or self.env(),
            cwd=cwd or str(self.root), timeout=120)

    def agent_eval(self, code, env=None):
        """Run a snippet with bin/ on sys.path and agent imported."""
        full = f"import sys; sys.path.insert(0, {str(self.root / 'bin')!r}); " + code
        return subprocess.run([sys.executable, "-c", full],
                              capture_output=True, text=True,
                              env=env or self.env(), timeout=60)


class TestConfig(TreeCase):
    def test_config_loaded_and_overrides_applied(self):
        r = self.agent_eval(
            "import agent; "
            "print(agent.PROVIDERS['minimax']['credential']); "
            "print(agent._cfg('state', 'transcripts_dir'))")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(),
                         ["custom.minimax", "/tmp/pc-state/transcripts"])

    def test_env_var_config_wins(self):
        other = self.tmp / "other.json"
        cfg = dict(FULL_CONFIG)
        cfg["providers"] = {"minimax": {"credential": "custom.fromenv"}}
        other.write_text(json.dumps(cfg))
        r = self.agent_eval(
            "import agent; print(agent.PROVIDERS['minimax']['credential'])",
            env=self.env(PLAN_CODER_CONFIG=str(other)))
        self.assertEqual(r.stdout.strip(), "custom.fromenv")

    def test_malformed_config_is_not_fatal(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{not json")
        empty_home = self.tmp / "empty-home"
        empty_home.mkdir(exist_ok=True)
        # Point HOME at an empty dir and remove the repo-local config
        # candidate by using a tree without one.
        bare = self.tmp / "bare"
        shutil.copytree(REPO / "bin", bare / "bin")
        r = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(bare / 'bin')!r}); "
             "import agent; print(agent.DEPLOYMENT)"],
            capture_output=True, text=True, timeout=60,
            env={"PATH": os.environ["PATH"], "HOME": str(empty_home),
                 "PLAN_CODER_CONFIG": str(bad)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "{}")


class TestAuth(TreeCase):
    def test_fail_fast_without_any_credential(self):
        r = self.run_bin("quick_check.py", "--provider", "minimax",
                         "--model", "MiniMax-M2.5-highspeed")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no credential available", r.stdout + r.stderr)

    def test_env_key_sets_bearer_header(self):
        r = self.agent_eval(
            "import urllib.request, agent; "
            "req = urllib.request.Request('https://example.invalid', data=b'{}'); "
            "mode = agent._apply_auth(req, agent.PROVIDERS['minimax']); "
            "print(mode); print(req.get_header('Authorization'))",
            env=self.env(MINIMAX_API_KEY="test-key-123"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(),
                         ["env", "Bearer test-key-123"])

    def test_credential_helper_mode(self):
        cred = self.tmp / "cred"
        cred.mkdir(exist_ok=True)
        (cred / "dynamic_credentials.py").write_text(
            "def add_surrogate_to_request(req, credential, **kwargs):\n"
            "    req.add_header('X-Surrogate', credential)\n"
            "    return req\n")
        cfg = dict(FULL_CONFIG)
        cfg["credentials"] = {"helper_dir": str(cred)}
        cfg["providers"] = {"minimax": {"credential": "custom.test"}}
        cfgp = self.tmp / "helper-cfg.json"
        cfgp.write_text(json.dumps(cfg))
        r = self.agent_eval(
            "import urllib.request, agent; "
            "req = urllib.request.Request('https://example.invalid', data=b'{}'); "
            "mode = agent._apply_auth(req, agent.PROVIDERS['minimax']); "
            "print(mode); print(req.get_header('X-surrogate'))",
            env=self.env(PLAN_CODER_CONFIG=str(cfgp)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["helper", "custom.test"])


class TestRendering(TreeCase):
    def test_render_full_config(self):
        out = self.tmp / "out-full"
        r = self.run_bin("render_prompts.py", "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sup = (out / "backlog-supervisor.rendered.md").read_text()
        self.assertIn("2 from alpha, 1 from beta", sup)
        self.assertIn("- alpha — ~/repos/alpha, you/alpha, base develop", sup)
        self.assertIn("Alpha: skip [later] items.", sup)
        self.assertIn("Alpha extra rule.", sup)
        self.assertNotIn("{{", sup)

    def test_render_partial_config_aborts_and_writes_nothing(self):
        partial = self.tmp / "partial.json"
        partial.write_text(json.dumps({"operator": {"name": "X"}}))
        out = self.tmp / "out-partial"
        r = self.run_bin("render_prompts.py", "--config", str(partial),
                         "--out", str(out))
        self.assertEqual(r.returncode, 2)
        self.assertIn("UNRESOLVED TOKENS", r.stdout)
        written = list(out.glob("*.md")) if out.exists() else []
        self.assertEqual(written, [])

    def test_installer_manifest_full(self):
        out = self.tmp / "out-install"
        r = self.run_bin("install_crons.py", "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        man = json.loads((out / "cron-manifest.json").read_text())
        self.assertEqual(sorted(man["crons"]),
                         ["backlog-supervisor", "harness-tune", "pr-shepherd"])
        for cron_id, entry in man["crons"].items():
            body = Path(entry["rendered"]).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(),
                             entry["sha256"], cron_id)
            self.assertIsNotNone(entry["schedule"], cron_id)
            self.assertEqual(entry["owner"], "goal:test", cron_id)
            self.assertEqual(entry["delivery"], [{"chat_id": "chat-123"}],
                             cron_id)


class TestPublishGateMechanism(unittest.TestCase):
    """The publish gate scans `git ls-files --cached --others
    --exclude-standard`; pin the gitignore behavior it relies on."""

    def test_ls_files_excludes_gitignored(self):
        tmp = Path(tempfile.mkdtemp(prefix="pc-git-"))
        try:
            subprocess.run(["git", "init", "-q", "."], cwd=tmp, check=True)
            (tmp / ".gitignore").write_text("secret.local.json\n")
            (tmp / "secret.local.json").write_text("internal values")
            (tmp / "tracked.md").write_text("public")
            r = subprocess.run(
                ["git", "ls-files", "--cached", "--others",
                 "--exclude-standard"],
                cwd=tmp, capture_output=True, text=True, check=True)
            self.assertNotIn("secret.local.json", r.stdout)
            self.assertIn("tracked.md", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCliSurface(TreeCase):
    def test_agent_help(self):
        r = self.run_bin("agent.py", "--help")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
