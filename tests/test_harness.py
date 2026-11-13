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
import re
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
    "workers": {"max_steps": 60, "provider_order": ["minimax", "zai"],
                "max_tokens_out": 0, "cycle_tokens_out_budget": 0,
                "goldfish": {"enabled": True, "max_per_cycle": 2,
                             "max_tokens_out": 30000, "max_steps": 15,
                             "tier": "cheap", "max_scope_files": 5},
                "jittest": {"enabled": True, "max_per_cycle": 2,
                            "hypothesis_tier": "balanced",
                            "test_tier": "cheap",
                            "max_tokens_out_hypothesis": 30000,
                            "max_tokens_out_test": 30000,
                            "max_steps": 25, "max_files": 5,
                            "max_diff_lines": 300,
                            "max_three_state_runs": 3}},
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


class TestPlans(TreeCase):
    def test_legacy_provider_is_an_api_plan_and_keeps_resolution(self):
        """A providers-only config is one API plan per old provider."""
        r = self.agent_eval(
            "import agent, json; "
            "plan = agent.PLAN_BY_ID['minimax']; "
            "dispatch = agent.resolve_plan_dispatch('minimax', 'api', "
            "tier='cheap'); "
            "print(json.dumps({"
            "'ids': [p.id for p in agent.PLANS], "
            "'route': plan.routes[0].key, "
            "'models': plan.models, "
            "'resolved_model': dispatch.model, "
            "'locked_to': [r.key for r in plan.locked_to]}))")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["ids"], ["minimax", "zai"])
        self.assertEqual(
            data["route"],
            "api:https://api.minimax.io/v1/chat/completions")
        self.assertEqual(data["models"], {
            "cheap": "MiniMax-M2.5-highspeed",
            "balanced": "MiniMax-M2.7",
            "flagship": "MiniMax-M3",
        })
        self.assertEqual(data["resolved_model"], "MiniMax-M2.5-highspeed")
        self.assertEqual(data["locked_to"], [])

    def test_malformed_plans_are_rejected_with_named_error(self):
        bad = self.tmp / "bad-plans.json"
        bad.write_text(json.dumps({"plans": [{
            "id": "broken",
            "vendor": "example",
            "routes": [],
            "models": {"balanced": "example-model"},
            "locked_to": [],
        }]}))
        r = self.agent_eval(
            "import agent", env=self.env(PLAN_CODER_CONFIG=str(bad)))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DeploymentConfigError", r.stderr)
        self.assertIn("invalid plan 'broken'", r.stderr)
        self.assertIn("routes must be a non-empty list", r.stderr)

    def test_locked_cli_plan_fails_closed_and_stub_records_invocation(self):
        r = self.agent_eval(
            "import agent, plans\n"
            "try:\n"
            "    agent.resolve_plan_dispatch('anthropic-max', "
            "{'kind': 'api', 'endpoint': 'https://api.anthropic.com/v1/messages'})\n"
            "except agent.PlanLockError as exc:\n"
            "    print(type(exc).__name__)\n"
            "    print(exc)\n"
            "d = agent.resolve_plan_dispatch('anthropic-max', "
            "{'kind': 'cli', 'harness': 'claude'})\n"
            "print(d.plan_id, d.route.key, d.model)\n"
            "print(' '.join(plans.cli_invocation(d.route, d.model)))",
            env=self.env(PLAN_CODER_CONFIG=str(
                REPO / "config" / "deployment.example.json")))
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "PlanLockError")
        self.assertIn("anthropic-max", lines[1])
        self.assertIn("allowed routes: cli:claude", lines[1])
        self.assertEqual(lines[2],
                         "anthropic-max cli:claude claude-sonnet-4-5")
        self.assertEqual(lines[3], "claude --model claude-sonnet-4-5")

        # The only CLI implementation in this slice is a credential-free
        # stub.  It records what a runner would invoke; it never runs it.
        record = self.tmp / "cli-invocation.json"
        stub = self.run_bin(
            "plan_cli_stub.py", "--harness", "claude",
            "--model", "claude-sonnet-4-5", "--record", str(record))
        self.assertEqual(stub.returncode, 0, stub.stdout + stub.stderr)
        recorded = json.loads(record.read_text())
        self.assertTrue(recorded["stub"])
        self.assertEqual(recorded["argv"],
                         ["claude", "--model", "claude-sonnet-4-5"])

    def test_example_config_expresses_api_and_locked_cli_plans(self):
        r = self.agent_eval(
            "import agent, json; print(json.dumps([{"
            "'id': p.id, 'vendor': p.vendor, "
            "'routes': [x.key for x in p.routes], "
            "'locked_to': [x.key for x in p.locked_to]} "
            "for p in agent.PLANS])); "
            "print(agent.resolve_dispatch('minimax', 'api', tier='cheap').model); "
            "print(agent.resolve_dispatch('zai', 'api', tier='flagship').model)",
            env=self.env(PLAN_CODER_CONFIG=str(
                REPO / "config" / "deployment.example.json")))
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        data = json.loads(lines[0])
        self.assertEqual(lines[1:], ["MiniMax-M2.5-highspeed", "glm-5.3"])
        self.assertEqual([plan["id"] for plan in data],
                         ["minimax", "zai", "anthropic-max"])
        self.assertEqual(data[0]["routes"], [
            "api:https://api.minimax.io/v1/chat/completions"])
        self.assertEqual(data[1]["routes"], [
            "api:https://api.z.ai/api/coding/paas/v4/chat/completions"])
        self.assertEqual(data[2]["routes"], [
            "cli:claude", "api:https://api.anthropic.com/v1/messages"])
        self.assertEqual(data[2]["locked_to"], ["cli:claude"])


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

    def test_render_has_trace_grader_wiring(self):
        out = self.tmp / "out-trace"
        r = self.run_bin("render_prompts.py", "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sup = (out / "backlog-supervisor.rendered.md").read_text()
        self.assertIn("ledger_check.py", sup)
        self.assertIn("grade_run.py", sup)
        self.assertIn("failover.py", sup)
        self.assertIn("goldfish.py", sup)
        self.assertIn("goldfish_ledger.jsonl", sup)
        self.assertIn("FLAG FOR ", sup)
        self.assertIn("--max-tokens-out 0", sup)
        self.assertIn("stopped:token-cap", sup)
        self.assertNotIn("{{", sup)

    def test_render_has_jittest_wiring(self):
        out = self.tmp / "out-jittest"
        r = self.run_bin("render_prompts.py", "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sup = (out / "backlog-supervisor.rendered.md").read_text()
        self.assertIn("jittest.py check", sup)
        self.assertIn("jittest.py packet", sup)
        self.assertIn("jittest.py launch", sup)
        self.assertIn("jittest.py parse-hypotheses", sup)
        self.assertIn("jittest.py three-state", sup)
        self.assertIn("jittest_ledger.jsonl", sup)
        self.assertIn("jittest:catch", sup)
        self.assertNotIn("{{", sup)
        shep = (out / "pr-shepherd.rendered.md").read_text()
        self.assertIn("jittest:catch", shep)
        self.assertIn("NOT merge-eligible", shep)
        self.assertNotIn("{{", shep)

    def test_render_has_spike_ledger_tune_wiring(self):
        out = self.tmp / "out-tune-ledgers"
        r = self.run_bin("render_prompts.py", "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tune = (out / "harness-tune.rendered.md").read_text()
        # Both spike ledgers are in the tune corpus.
        self.assertIn("goldfish_ledger.jsonl", tune)
        self.assertIn("jittest_ledger.jsonl", tune)
        # Goldfish window metrics: acceptance, overhead, unavailable,
        # brief_changed + downstream join.
        self.assertIn("findings acceptance", tune)
        self.assertIn("propose_fix", tune)
        self.assertIn("unavailable rate", tune)
        self.assertIn("brief_changed", tune)
        self.assertIn("downstream join", tune)
        # JiTTest window metrics: latest-row collapse, label
        # adjudication, disposition true-positive rate, Stage A kill
        # rate, survived / catch_flagged counts.
        self.assertIn("latest row per repo + PR", tune)
        self.assertIn("label_adjudication", tune)
        self.assertIn("disputed", tune)
        self.assertIn("true-positive", tune)
        self.assertIn("Stage A kill rate", tune)
        self.assertIn("catch_flagged", tune)
        self.assertIn("coverage_incomplete", tune)
        self.assertNotIn("{{", tune)


SYNTH_LEDGER = [
    {"ts": "2026-11-06T10:00:00Z", "repo": "alpha", "issue": 1,
     "run_id": "alpha-1-a", "tier": "cheap", "tokens_in": 100000,
     "tokens_out": 10000, "steps": "20/60", "outcome": "pr_opened",
     "pr_url": "https://example.invalid/1", "gate_triggers": "empty_diff=0"},
    {"ts": "2026-11-06T11:00:00Z", "repo": "alpha", "issue": 2,
     "run_id": "alpha-2-a", "tier": "balanced", "tokens_in": 200000,
     "tokens_out": 40000, "steps": "60/60", "outcome": "failed:bad-diff",
     "pr_url": "", "gate_triggers": "empty_diff=1, midrun_no_edit=1"},
    {"ts": "2026-11-06T12:00:00Z", "repo": "alpha", "issue": 2,
     "run_id": "alpha-2-b", "tier": "balanced", "tokens_in": 150000,
     "tokens_out": 30000, "steps": "60/60", "outcome": "failed:worker-error",
     "pr_url": "", "gate_triggers": "empty_diff=0"},
    {"ts": "2026-11-07T09:00:00Z", "repo": "beta", "issue": 7,
     "run_id": "beta-7-a", "tier": "flagship", "tokens_in": 0,
     "tokens_out": 0, "steps": "unknown", "outcome": "split_suggested",
     "pr_url": "", "gate_triggers": ""},
]


class TestTraceSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-trace-"))
        self.ledger = self.tmp / "worker_ledger.jsonl"
        self.ledger.write_text("\n".join(
            json.dumps(r) for r in SYNTH_LEDGER) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_summary(self, *argv):
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "trace_summary.py"),
             "--ledger", str(self.ledger), *argv],
            capture_output=True, text=True, timeout=60)

    def test_json_totals_reconcile(self):
        r = self.run_summary("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["rows"], 4)
        self.assertEqual(data["overall"]["pr_opened"], 1)
        self.assertAlmostEqual(data["overall"]["pr_rate"], 0.25)
        # Zero-token split row is counted but excluded from the medians.
        self.assertEqual(data["overall"]["tokens_out_total"], 80000)
        self.assertEqual(data["overall"]["tokens_out_median"], 30000)
        self.assertEqual(data["overall"]["hit_cap"], 2)
        self.assertEqual(data["overall"]["gates_nonzero"],
                         {"empty_diff": 1, "midrun_no_edit": 1})
        self.assertEqual(data["by_repo"]["alpha"]["runs"], 3)
        self.assertEqual(data["by_tier"]["flagship"]["runs"], 1)
        self.assertEqual(data["top_burn"][0]["repo"], "alpha")
        self.assertEqual(data["top_burn"][0]["issue"], 2)
        self.assertEqual(len(data["repeat_failures"]), 1)
        self.assertEqual(data["repeat_failures"][0]["issue"], 2)
        self.assertEqual(data["repeat_failures"][0]["failed"], 2)

    def test_since_filter_and_human_output(self):
        r = self.run_summary("--since", "2026-11-07")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 runs, 0 PRs opened", r.stdout)
        r = self.run_summary()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("4 runs, 1 PRs opened (25% PR rate)", r.stdout)
        self.assertIn("repeat failure: alpha#2", r.stdout)

    def test_missing_ledger_exits_2(self):
        r = subprocess.run(
            [sys.executable, str(REPO / "bin" / "trace_summary.py"),
             "--ledger", str(self.tmp / "nope.jsonl")],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)


class TestLedgerCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-check-"))
        self.ledger = self.tmp / "worker_ledger.jsonl"
        self.ledger.write_text("\n".join(
            json.dumps(r) for r in SYNTH_LEDGER) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_check(self, *argv):
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "ledger_check.py"), *argv,
             "--ledger", str(self.ledger)],
            capture_output=True, text=True, timeout=60)

    def test_blocked_two_failures_no_pr(self):
        r = self.run_check("alpha", "2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("BLOCKED", r.stdout)

    def test_allowed_after_pr_opened(self):
        r = self.run_check("alpha", "1")
        self.assertEqual(r.returncode, 0)
        self.assertIn("OK:", r.stdout)

    def test_allowed_one_failure(self):
        r = self.run_check("alpha", "2", "--min-failures", "3")
        self.assertEqual(r.returncode, 0)

    def test_allowed_unknown_issue(self):
        r = self.run_check("beta", "99")
        self.assertEqual(r.returncode, 0)

    def test_json_shape(self):
        r = self.run_check("alpha", "2", "--json")
        self.assertEqual(r.returncode, 1)
        data = json.loads(r.stdout)
        self.assertEqual(
            (data["attempts"], data["failed"], data["pr_opened"],
             data["blocked"]), (2, 2, False, True))


FAILOVER_LEDGER = [
    {"ts": "2026-11-06T10:00:00Z", "repo": "beta", "issue": 7,
     "run_id": "beta-7-a", "tier": "cheap", "tokens_in": 100000,
     "tokens_out": 10000, "steps": "60/60", "outcome": "failed:bad-diff",
     "pr_url": "", "gate_triggers": "empty_diff=1", "grade": 30},
    {"ts": "2026-11-06T11:00:00Z", "repo": "beta", "issue": 7,
     "run_id": "beta-7-b", "tier": "balanced", "tokens_in": 100000,
     "tokens_out": 10000, "steps": "45/60",
     "outcome": "failed:worker-error", "pr_url": "",
     "gate_triggers": "", "grade": 40},
    {"ts": "2026-11-06T12:00:00Z", "repo": "beta", "issue": 7,
     "run_id": "beta-7-c", "tier": "flagship", "tokens_in": 100000,
     "tokens_out": 10000, "steps": "60/60", "outcome": "escalated",
     "pr_url": "", "gate_triggers": "", "grade": 20},
    {"ts": "2026-11-06T10:00:00Z", "repo": "beta", "issue": 8,
     "run_id": "beta-8-a", "tier": "balanced", "tokens_in": 100000,
     "tokens_out": 10000, "steps": "60/60", "outcome": "failed:empty-diff",
     "pr_url": "", "gate_triggers": "empty_diff=1", "grade": 10},
    {"ts": "2026-11-06T10:00:00Z", "repo": "beta", "issue": 9,
     "run_id": "beta-9-a", "tier": "balanced", "tokens_in": 100000,
     "tokens_out": 200000, "steps": "30/60",
     "outcome": "stopped:token-cap", "pr_url": "",
     "gate_triggers": "", "grade": None},
    {"ts": "2026-11-06T10:00:00Z", "repo": "beta", "issue": 10,
     "run_id": "beta-10-a", "tier": "cheap", "tokens_in": 100000,
     "tokens_out": 10000, "steps": "55/60", "outcome": "stale",
     "pr_url": "", "gate_triggers": "", "grade": 30},
]


class TestFailover(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-failover-"))
        self.ledger = self.tmp / "worker_ledger.jsonl"
        rows = SYNTH_LEDGER + FAILOVER_LEDGER
        self.ledger.write_text("\n".join(
            json.dumps(r) for r in rows) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_failover(self, *argv):
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "failover.py"), *argv,
             "--ledger", str(self.ledger)],
            capture_output=True, text=True, timeout=60)

    def test_failover_after_two_failures_bumps_tier(self):
        r = self.run_failover("alpha", "2", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data["action"], "failover")
        self.assertEqual(data["next_tier"], "flagship")
        self.assertEqual(data["graded_failures"], 2)
        self.assertEqual(len(data["reflections"]), 2)
        self.assertIn("REFLECTION alpha#2", data["reflections"][0]["text"])

    def test_flag_for_operator_after_three_failures(self):
        r = self.run_failover("beta", "7")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FLAG FOR OPERATOR", r.stdout)

    def test_one_failure_dispatches_with_reflection(self):
        r = self.run_failover("beta", "8", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data["action"], "dispatch_with_reflection")
        self.assertIn("no-output", data["reflections"][0]["category"])

    def test_token_cap_is_reflection_not_failure(self):
        r = self.run_failover("beta", "9", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data["graded_failures"], 0)
        self.assertEqual(data["action"], "dispatch")
        self.assertEqual(len(data["reflections"]), 1)
        self.assertIn("budget-exhaustion",
                      data["reflections"][0]["category"])

    def test_low_grade_row_counts_as_graded_failure(self):
        r = self.run_failover("beta", "10", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["graded_failures"], 1)
        self.assertEqual(data["action"], "dispatch_with_reflection")

    def test_pr_opened_disables_failover(self):
        r = self.run_failover("alpha", "1", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["action"], "none_pr_opened")

    def test_unknown_issue_dispatches(self):
        r = self.run_failover("beta", "99", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["action"], "dispatch")
        self.assertEqual(data["next_tier"], "balanced")

    def test_missing_ledger_exits_2(self):
        r = subprocess.run(
            [sys.executable, str(REPO / "bin" / "failover.py"),
             "beta", "1", "--ledger", str(self.tmp / "nope.jsonl")],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)


class TestGoldfish(TreeCase):
    def run_goldfish(self, *argv, env=None):
        return self.run_bin("goldfish.py", *argv, env=env)

    def test_check_flagship_triggers(self):
        r = self.run_goldfish("check", "--repo", "alpha", "--issue", "5",
                              "--tier", "flagship", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["triggered"])
        self.assertEqual(data["trigger"], "flagship")

    def test_check_balanced_small_scope_no_trigger(self):
        r = self.run_goldfish("check", "--repo", "alpha", "--issue", "5",
                              "--tier", "balanced", "--scope-files", "2",
                              "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertFalse(data["triggered"])

    def test_check_failover_and_scope_and_criteria_triggers(self):
        r = self.run_goldfish("check", "--repo", "alpha", "--issue", "5",
                              "--tier", "balanced",
                              "--failover-action", "failover", "--json")
        self.assertEqual(json.loads(r.stdout)["trigger"], "failover")
        r = self.run_goldfish("check", "--repo", "alpha", "--issue", "5",
                              "--tier", "balanced", "--scope-files", "9",
                              "--json")
        self.assertEqual(json.loads(r.stdout)["trigger"], "scope")
        r = self.run_goldfish("check", "--repo", "alpha", "--issue", "5",
                              "--tier", "balanced", "--has-criteria", "no",
                              "--json")
        self.assertEqual(json.loads(r.stdout)["trigger"], "no-criteria")
        r = self.run_goldfish(
            "check", "--repo", "alpha", "--issue", "5", "--tier",
            "balanced", "--scope-paths", ".github/workflows/ci.yml,src/a.py",
            "--json")
        self.assertEqual(json.loads(r.stdout)["trigger"], "hard-stop")

    def test_check_disabled_config_exits_2(self):
        disabled = self.tmp / "goldfish-off.json"
        disabled.write_text(json.dumps(
            {"workers": {"goldfish": {"enabled": False}}}))
        r = self.run_goldfish(
            "check", "--repo", "alpha", "--issue", "5", "--tier",
            "flagship", env=self.env(PLAN_CODER_CONFIG=str(disabled)))
        self.assertEqual(r.returncode, 2)
        self.assertIn("disabled", r.stderr)

    def _packet_inputs(self, brief_text="Brief body."):
        base = self.tmp / "gf-base"
        base.mkdir(exist_ok=True)
        (base / "real.py").write_text("x = 1\n")
        issue = self.tmp / "gf-issue.md"
        issue.write_text("Issue title\n\nDo the thing.\n")
        brief = self.tmp / "gf-brief.md"
        brief.write_text(brief_text)
        return base, issue, brief

    def test_packet_builds_and_filters_reading_list(self):
        base, issue, brief = self._packet_inputs()
        out = self.tmp / "gf-packet.md"
        r = self.run_goldfish(
            "packet", "--repo", "alpha", "--issue", "5",
            "--issue-file", str(issue), "--brief-file", str(brief),
            "--base-dir", str(base), "--reading", "real.py,missing.py",
            "--out", str(out), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        manifest = json.loads(r.stdout)
        self.assertEqual(manifest["reading_list"], ["real.py"])
        self.assertEqual(manifest["excluded_reading"], ["missing.py"])
        text = out.read_text()
        self.assertIn("goldfish-manifest:", text)
        self.assertIn("Do the thing.", text)
        self.assertIn("goldfish-findings", text)

    def test_packet_rejects_supervisor_context_markers(self):
        base, issue, brief = self._packet_inputs(
            brief_text="## Prior attempts\n\nSee worker_ledger.jsonl.\n")
        out = self.tmp / "gf-packet-bad.md"
        r = self.run_goldfish(
            "packet", "--repo", "alpha", "--issue", "5",
            "--issue-file", str(issue), "--brief-file", str(brief),
            "--base-dir", str(base), "--out", str(out))
        self.assertEqual(r.returncode, 3)
        self.assertIn("PACKET REJECTED", r.stderr)
        self.assertFalse(out.exists())

    def test_parse_discards_evidenceless_and_invalid_findings(self):
        report = self.tmp / "gf-report.md"
        findings = [
            {"severity": "Blocker", "lens": "premise",
             "evidence": "src/a.py:12", "finding": "File does not exist",
             "suggested_edit": "Fix the path"},
            {"severity": "Major", "lens": "completeness",
             "evidence": "", "finding": "Vague worry",
             "suggested_edit": "none"},
            {"severity": "Huge", "lens": "premise",
             "evidence": "src/a.py:1", "finding": "Bad severity",
             "suggested_edit": "none"},
        ]
        report.write_text(
            "# Report\n\n```goldfish-findings\n"
            + json.dumps(findings) + "\n```\n")
        r = self.run_goldfish("parse", "--report", str(report), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["block_found"])
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(data["findings"][0]["severity"], "Blocker")
        self.assertEqual(data["discarded_no_evidence"], 1)
        self.assertEqual(data["invalid"], 1)

    def test_parse_minor_severity_aliases_to_nit(self):
        # Phase 0b calibration: reviewers improvise "Minor" for the
        # lowest severity; the parser accepts it as Nit.
        report = self.tmp / "gf-report-minor.md"
        findings = [
            {"severity": "Minor", "lens": "premise",
             "evidence": "src/a.py:3", "finding": "Wording is off",
             "suggested_edit": "none"},
        ]
        report.write_text(
            "# Report\n\n```goldfish-findings\n"
            + json.dumps(findings) + "\n```\n")
        r = self.run_goldfish("parse", "--report", str(report), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(data["findings"][0]["severity"], "Nit")
        self.assertEqual(data["invalid"], 0)

    def test_parse_without_block_is_empty(self):
        report = self.tmp / "gf-report2.md"
        report.write_text("# Report\n\nNo findings block here.\n")
        r = self.run_goldfish("parse", "--report", str(report), "--json")
        data = json.loads(r.stdout)
        self.assertFalse(data["block_found"])
        self.assertEqual(data["findings"], [])

    def test_log_appends_goldfish_ledger_row(self):
        ledger = self.tmp / "goldfish_ledger.jsonl"
        r = self.run_goldfish(
            "log", "--repo", "alpha", "--issue", "5", "--trigger",
            "flagship", "--tokens-out", "12345", "--steps", "9/15",
            "--findings", "3", "--blockers", "1", "--propose-fix", "2",
            "--contest", "1", "--escalate", "0", "--decline", "0",
            "--discarded", "1", "--brief-changed", "yes", "--outcome",
            "dispatched_revised", "--ledger", str(ledger), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        row = json.loads(ledger.read_text().strip())
        self.assertEqual(row["repo"], "alpha")
        self.assertEqual(row["trigger"], "flagship")
        self.assertEqual(row["tier"], "cheap")
        self.assertTrue(row["brief_changed"])
        self.assertEqual(row["dispositions"]["propose_fix"], 2)
        self.assertEqual(row["dispositions"]["discarded_no_evidence"], 1)
        self.assertEqual(row["outcome"], "dispatched_revised")


JIT_DIFF_PY = """diff --git a/src/widget.py b/src/widget.py
--- a/src/widget.py
+++ b/src/widget.py
@@ -1,3 +1,4 @@
 def compute_total(items):
     total = sum(items)
-    return total
+    return total + 1
+# trailing comment
"""

JIT_DIFF_PY_TRUNCATED = """diff --git a/src/widget.py b/src/widget.py
--- a/src/widget.py
+++ b/src/widget.py
@@ -1,3 +1,4 @@
 def compute_total(items):
     total = sum(items)
-    return total
+    return total + 1
"""

JIT_DIFF_FORMAT = """diff --git a/data/points.parquet b/data/points.parquet
--- a/data/points.parquet
+++ b/data/points.parquet
@@ -1,1 +1,1 @@
-old-bytes
+new-bytes
"""

JIT_DIFF_PHYSICS = """diff --git a/src/physics/model.py b/src/physics/model.py
--- a/src/physics/model.py
+++ b/src/physics/model.py
@@ -1,1 +1,1 @@
-old
+new
"""


class TestJittest(TreeCase):
    def run_jittest(self, *argv, env=None):
        return self.run_bin("jittest.py", *argv, env=env)

    # ---------------------------------------------------------- check

    def test_check_flagship_and_small_no_trigger(self):
        r = self.run_jittest("check", "--repo", "alpha", "--issue",
                             "5", "--pr", "9", "--tier", "flagship",
                             "--diff-files", "2", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["triggered"])
        self.assertEqual(data["trigger"], "flagship")
        r = self.run_jittest("check", "--repo", "alpha", "--issue",
                             "5", "--pr", "9", "--tier", "balanced",
                             "--diff-files", "2", "--diff-lines",
                             "40", "--json")
        self.assertFalse(json.loads(r.stdout)["triggered"])

    def test_check_trigger_variants(self):
        def trigger(**kw):
            argv = ["check", "--repo", "alpha", "--issue", "5",
                    "--pr", "9", "--tier", "balanced"]
            for key, value in kw.items():
                argv += ["--" + key.replace("_", "-"), str(value)]
            argv.append("--json")
            r = self.run_jittest(*argv)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)
        self.assertEqual(trigger(diff_paths=".github/workflows/ci.yml"
                                 )["trigger"], "hard-stop")
        self.assertEqual(trigger(failover_action="failover"
                                 )["trigger"], "failover")
        self.assertEqual(trigger(diff_files=6)["trigger"], "size")
        self.assertEqual(trigger(diff_lines=301)["trigger"], "size")
        self.assertEqual(trigger(grade_band="bottom"
                                 )["trigger"], "lucky-green")

    def test_check_exemptions_and_cycle_cap(self):
        def check(**kw):
            argv = ["check", "--repo", "alpha", "--issue", "5",
                    "--pr", "9", "--tier", "flagship"]
            for key, value in kw.items():
                argv += ["--" + key.replace("_", "-"), str(value)]
            argv.append("--json")
            return json.loads(self.run_jittest(*argv).stdout)
        data = check(diff_paths="docs/guide.md,README.md")
        self.assertFalse(data["triggered"])
        self.assertEqual(data["exempt"], "docs-only")
        data = check(diff_paths="tests/test_a.py,src/b.test.ts")
        self.assertEqual(data["exempt"], "test-only")
        data = check(has_regression_test="yes")
        self.assertEqual(data["exempt"], "has-regression-test")
        data = check(cycle_checks_used=2)
        self.assertFalse(data["triggered"])
        self.assertEqual(data["exempt"], "cycle-cap")

    def test_check_disabled_config_exits_2(self):
        disabled = self.tmp / "jittest-off.json"
        disabled.write_text(json.dumps(
            {"workers": {"jittest": {"enabled": False}}}))
        r = self.run_jittest(
            "check", "--repo", "alpha", "--issue", "5", "--pr", "9",
            "--tier", "flagship",
            env=self.env(PLAN_CODER_CONFIG=str(disabled)))
        self.assertEqual(r.returncode, 2)
        self.assertIn("disabled", r.stderr)

    # --------------------------------------------------------- packet

    def _packet_inputs(self, diff_text=JIT_DIFF_PY):
        issue = self.tmp / "jit-issue.md"
        issue.write_text("Fix the total\n\nThe total must equal "
                         "the sum of the items.\n")
        pr = self.tmp / "jit-pr.md"
        pr.write_text("Implements the fix.\n")
        diff = self.tmp / "jit.diff"
        diff.write_text(diff_text)
        return issue, pr, diff

    def test_packet_builds_full_diff_and_coverage_seed(self):
        issue, pr, diff = self._packet_inputs()
        out = self.tmp / "jit-packet.md"
        r = self.run_jittest(
            "packet", "--repo", "alpha", "--issue", "5", "--pr",
            "9", "--issue-file", str(issue), "--pr-file", str(pr),
            "--diff-file", str(diff), "--out", str(out), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        manifest = json.loads(r.stdout)
        self.assertFalse(manifest["diff_truncated"])
        funcs = [cf["function"]
                 for cf in manifest["changed_functions"]]
        self.assertIn("compute_total", funcs)
        text = out.read_text()
        self.assertIn("jittest-manifest:", text)
        self.assertIn("+    return total + 1", text)
        self.assertIn("COVERAGE:", text)
        self.assertIn("FIRST-TOOL-CALL RULE", text)

    def test_packet_rejects_truncated_diff(self):
        issue, pr, diff = self._packet_inputs(JIT_DIFF_PY_TRUNCATED)
        out = self.tmp / "jit-packet-trunc.md"
        r = self.run_jittest(
            "packet", "--repo", "alpha", "--issue", "5", "--pr",
            "9", "--issue-file", str(issue), "--pr-file", str(pr),
            "--diff-file", str(diff), "--out", str(out))
        self.assertEqual(r.returncode, 3)
        self.assertIn("PACKET REJECTED", r.stderr)
        self.assertFalse(out.exists())

    def test_packet_includes_contract_for_format_diff(self):
        base = self.tmp / "jit-fmt-base"
        (base / "data").mkdir(parents=True)
        (base / "points-schema.md").write_text(
            "POINTS FORMAT CONTRACT: columns are x, y, width, "
            "height in COCO order.\n")
        issue, pr, diff = self._packet_inputs(JIT_DIFF_FORMAT)
        out = self.tmp / "jit-packet-fmt.md"
        r = self.run_jittest(
            "packet", "--repo", "alpha", "--issue", "5", "--pr",
            "9", "--issue-file", str(issue), "--pr-file", str(pr),
            "--diff-file", str(diff), "--base-dir", str(base),
            "--out", str(out), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        manifest = json.loads(r.stdout)
        self.assertEqual(manifest["format_files"],
                         ["data/points.parquet"])
        self.assertTrue(any("points-schema" in p
                            for p in manifest["contract_included"]))
        text = out.read_text()
        self.assertIn("External contract material", text)
        self.assertIn("COCO order", text)

    # ---------------------------------------------- parse-hypotheses

    def _hyp_workdir(self):
        wt = self.tmp / "jit-hyp-wt"
        (wt / "src").mkdir(parents=True, exist_ok=True)
        (wt / "src" / "widget.py").write_text(
            "def compute_total(items):\n"
            "    total = sum(items)\n"
            "    return total + 1\n")
        report = self.tmp / "jit-HYPOTHESES.md"
        report.write_text(
            "# Hypotheses — alpha PR #9\n\n"
            "## H1\n"
            "CLAIM: compute_total adds one to every total\n"
            "CLASS: facet\n"
            'EVIDENCE: "return total + 1" — src/widget.py:3\n'
            "FALSIFIED_BY: compute_total([1, 2]) observes 4, "
            "not 3\n"
            "CONFIDENCE: high\n"
            "COVERAGE: compute_total: covered\n\n"
            "## H2\n"
            "CLAIM: an unquotable worry\n"
            "CLASS: edge\n"
            "CONFIDENCE: low\n\n"
            "## H3\n"
            "CLAIM: a fabricated quote claim\n"
            "CLASS: seam\n"
            'EVIDENCE: "this line does not exist" — '
            "src/widget.py:1\n"
            "FALSIFIED_BY: nothing\n"
            "CONFIDENCE: low\n\n"
            "RANKED: H1, H3, H2\n")
        return wt, report

    def test_parse_hypotheses_evidence_gate_and_coverage(self):
        wt, report = self._hyp_workdir()
        diff = self.tmp / "jit-hyp.diff"
        diff.write_text(JIT_DIFF_PY)
        r = self.run_jittest(
            "parse-hypotheses", "--report", str(report),
            "--workdir", str(wt), "--diff-file", str(diff),
            "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["n_hypotheses"], 1)
        self.assertEqual(data["hypotheses"][0]["id"], "H1")
        self.assertEqual(data["discarded_no_evidence"], 1)
        self.assertEqual(data["discarded_unverifiable"], 1)
        self.assertTrue(data["coverage_complete"])
        self.assertEqual(data["coverage"][0]["status"], "covered")

    def test_parse_hypotheses_flags_unaccounted_function(self):
        wt, report = self._hyp_workdir()
        r = self.run_jittest(
            "parse-hypotheses", "--report", str(report),
            "--workdir", str(wt),
            "--changed-functions", "compute_total,missing_func",
            "--json")
        data = json.loads(r.stdout)
        self.assertFalse(data["coverage_complete"])
        self.assertIn("missing_func",
                      data["unaccounted_functions"])

    # ---------------------------------------------------- three-state

    def _three_state_dirs(self, name, diff_test, verify_test=None,
                          diff_state=None, verify_state=None):
        dw = self.tmp / f"jit-{name}-diff"
        vw = self.tmp / f"jit-{name}-verify"
        dw.mkdir(parents=True, exist_ok=True)
        vw.mkdir(parents=True, exist_ok=True)
        (dw / "test_probe.py").write_text(diff_test)
        if verify_test is not None:
            (vw / "test_probe.py").write_text(verify_test)
        if diff_state is not None:
            (dw / "state.txt").write_text(diff_state)
        if verify_state is not None:
            (vw / "state.txt").write_text(verify_state)
        return dw, vw

    def _run_three_state(self, dw, vw):
        out = Path(str(dw) + "-threestate.json")
        r = self.run_jittest(
            "three-state", "--test-path", "test_probe.py",
            "--diff-workdir", str(dw), "--verify-workdir", str(vw),
            "--out", str(out), "--json", "--cmd", sys.executable,
            "test_probe.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_three_state_catch(self):
        probe = ("import sys\n"
                 "sys.exit(1 if open('state.txt').read().strip()"
                 " == 'buggy' else 0)\n")
        dw, vw = self._three_state_dirs("catch", probe,
                                        diff_state="buggy\n",
                                        verify_state="fixed\n")
        data = self._run_three_state(dw, vw)
        self.assertEqual(data["classification"], "catch")
        self.assertTrue(data["three_state_pass"])
        self.assertEqual(data["executions"], 3)
        self.assertTrue(data["copied_to_verify"])

    def test_three_state_fail_on_both_and_pass_on_both(self):
        dw, vw = self._three_state_dirs(
            "failboth", "import sys\nsys.exit(1)\n",
            verify_test="import sys\nsys.exit(1)\n")
        self.assertEqual(self._run_three_state(dw, vw)
                         ["classification"], "fail_on_both")
        dw, vw = self._three_state_dirs(
            "passboth", "import sys\nsys.exit(0)\n",
            verify_test="import sys\nsys.exit(0)\n")
        self.assertEqual(self._run_three_state(dw, vw)
                         ["classification"], "pass_on_both")

    def test_three_state_feature_test_pattern(self):
        probe = ("import sys\n"
                 "sys.exit(0 if open('state.txt').read().strip()"
                 " == 'buggy' else 1)\n")
        dw, vw = self._three_state_dirs("feature", probe,
                                        diff_state="buggy\n",
                                        verify_state="fixed\n")
        self.assertEqual(self._run_three_state(dw, vw)
                         ["classification"], "feature_test")

    def test_three_state_flake_on_rerun(self):
        counter = ("import os, sys\n"
                   "n = int(open('count.txt').read()) "
                   "if os.path.exists('count.txt') else 0\n"
                   "open('count.txt', 'w').write(str(n + 1))\n"
                   "sys.exit(1 if n == 0 else 0)\n")
        dw, vw = self._three_state_dirs(
            "flake", counter,
            verify_test="import sys\nsys.exit(0)\n")
        self.assertEqual(self._run_three_state(dw, vw)
                         ["classification"], "flake")

    # --------------------------------------------------------- assess

    def _assess(self, test_text, three_state=None, judge=None,
                extra_argv=()):
        test_file = self.tmp / "jit-assess-test.py"
        test_file.write_text(test_text)
        argv = ["assess", "--test-file", str(test_file)]
        if three_state is not None:
            ts_file = self.tmp / "jit-assess-ts.json"
            ts_file.write_text(json.dumps(three_state))
            argv += ["--three-state-file", str(ts_file)]
        if judge is not None:
            judge_file = self.tmp / "jit-assess-judge.md"
            judge_file.write_text(judge)
            argv += ["--judge-file", str(judge_file)]
        argv += list(extra_argv) + ["--json"]
        r = self.run_jittest(*argv)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    _CATCH_TS = {"classification": "catch",
                 "buggy_run1": {"exit": 1,
                                "output": "1 failed: assert 4 == 3"},
                 "buggy_run2": {"exit": 1,
                                "output": "1 failed: assert 4 == 3"},
                 "verify_run": {"exit": 0, "output": "1 passed"}}

    def test_assess_env_failure_killed_stage_a(self):
        ts = {"classification": "catch",
              "buggy_run1": {"exit": 1, "output":
                             "ModuleNotFoundError: No module "
                             "named 'nope'"},
              "verify_run": {"exit": 1, "output":
                             "ModuleNotFoundError"}}
        data = self._assess("def test_x():\n    assert True\n",
                            three_state=ts)
        self.assertEqual(data["outcome"], "killed_stage_a")
        rules = [k["rule"] for k in data["stage_a_kills"]]
        self.assertIn("env_failure", rules)

    def test_assess_exact_float_and_nondeterminism_kills(self):
        diff = self.tmp / "jit-physics.diff"
        diff.write_text(JIT_DIFF_PHYSICS)
        data = self._assess(
            "import time\n"
            "def test_model():\n"
            "    assert energy_out == 1.23456789\n"
            "    now = time.time()\n",
            three_state=self._CATCH_TS,
            extra_argv=("--diff-file", str(diff)))
        rules = [k["rule"] for k in data["stage_a_kills"]]
        self.assertIn("exact_float_simulation_output", rules)
        self.assertIn("nondeterministic_by_construction", rules)
        self.assertEqual(data["outcome"], "killed_stage_a")

    def test_assess_judge_evidenceless_discarded(self):
        judge = ("```jittest-judge\n"
                 + json.dumps({"verdict": "catch", "evidence": ""})
                 + "\n```\n")
        data = self._assess(
            "def test_total():\n"
            "    assert compute_total([1, 2]) == 3\n",
            three_state=self._CATCH_TS, judge=judge)
        self.assertEqual(data["judge"]["discarded_no_evidence"], 1)
        self.assertEqual(data["outcome"], "pending_judge")

    def test_assess_surviving_catch(self):
        issue = self.tmp / "jit-assess-issue.md"
        issue.write_text("The total must equal the sum of the "
                         "items.\n")
        judge = ("```jittest-judge\n"
                 + json.dumps({
                     "verdict": "catch",
                     "evidence": "The total must equal the sum "
                                 "of the items."})
                 + "\n```\n")
        data = self._assess(
            "def test_total():\n"
            "    assert compute_total([1, 2]) == 3\n",
            three_state=self._CATCH_TS, judge=judge,
            extra_argv=("--issue-file", str(issue)))
        self.assertEqual(data["outcome"], "surviving_catch")
        self.assertEqual(data["stage_a_kills"], [])

    # -------------------------------------------------------- harvest

    def test_harvest_finds_tests_regardless_of_report_name(self):
        wt = self.tmp / "jit-harvest-wt"
        (wt / "tests").mkdir(parents=True)
        (wt / "src").mkdir()
        (wt / "tests" / "test_new_thing.py").write_text("x = 1\n")
        (wt / "src" / "widget.py").write_text("x = 1\n")
        (wt / "WORKER_REPORT.md").write_text("# done\n")
        (wt / "HYPOTHESES.md").write_text("# Hypotheses\n")
        subprocess.run(["git", "init", "-q", str(wt)], check=True)
        r = self.run_jittest("harvest", "--workdir", str(wt),
                             "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertIn("tests/test_new_thing.py", data["tests"])
        self.assertNotIn("src/widget.py", data["tests"])
        self.assertEqual(data["hypotheses_file"], "HYPOTHESES.md")
        self.assertIn("WORKER_REPORT.md", data["reports"])
        plain = self.tmp / "jit-harvest-plain"
        plain.mkdir()
        (plain / "test_loose.py").write_text("x = 1\n")
        r = self.run_jittest("harvest", "--workdir", str(plain),
                             "--json")
        data = json.loads(r.stdout)
        self.assertIn("test_loose.py", data["tests"])

    # --------------------------------------------------------- launch

    def _dummy_agent(self, name, body):
        script = self.tmp / name
        script.write_text(body)
        return script

    def test_launch_hard_cap_kills_over_cap_run(self):
        dummy = self._dummy_agent(
            "jit-dummy-over.py",
            "import json, os, sys, time\n"
            "argv = sys.argv\n"
            "tdir = argv[argv.index('--transcript-dir') + 1]\n"
            "rid = argv[argv.index('--run-id') + 1]\n"
            "os.makedirs(tdir, exist_ok=True)\n"
            "with open(os.path.join(tdir, rid + '.jsonl'), 'w') "
            "as fh:\n"
            "    fh.write(json.dumps({'tokens_out': 5000})"
            " + '\\n')\n"
            "time.sleep(60)\n")
        tdir = self.tmp / "jit-launch-transcripts"
        r = self.run_jittest(
            "launch", "--brief", "b.md", "--workdir", str(self.tmp),
            "--tier", "cheap", "--cap", "1000", "--run-id",
            "jit-over", "--transcript-dir", str(tdir),
            "--agent", str(dummy), "--json")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["hard_cap_killed"])
        self.assertEqual(data["tokens_out"], 5000)

    def test_launch_under_cap_finishes(self):
        dummy = self._dummy_agent(
            "jit-dummy-ok.py",
            "import json, os, sys\n"
            "argv = sys.argv\n"
            "tdir = argv[argv.index('--transcript-dir') + 1]\n"
            "rid = argv[argv.index('--run-id') + 1]\n"
            "os.makedirs(tdir, exist_ok=True)\n"
            "with open(os.path.join(tdir, rid + '.jsonl'), 'w') "
            "as fh:\n"
            "    fh.write(json.dumps({'tokens_out': 10})"
            " + '\\n')\n")
        tdir = self.tmp / "jit-launch-transcripts2"
        r = self.run_jittest(
            "launch", "--brief", "b.md", "--workdir", str(self.tmp),
            "--tier", "balanced", "--cap", "30000", "--run-id",
            "jit-ok", "--transcript-dir", str(tdir),
            "--agent", str(dummy), "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertFalse(data["hard_cap_killed"])
        self.assertEqual(data["tokens_out"], 10)

    # ------------------------------------------------------------ log

    def test_log_appends_jittest_ledger_row_with_adjudication(self):
        ledger = self.tmp / "jittest_ledger.jsonl"
        r = self.run_jittest(
            "log", "--repo", "alpha", "--issue", "5", "--pr", "9",
            "--trigger", "flagship", "--outcome", "catch_flagged",
            "--hypotheses-produced", "5", "--hypotheses-kept", "4",
            "--discarded-no-evidence", "1",
            "--coverage-complete", "yes", "--tests-generated", "1",
            "--three-state-passed", "1",
            "--three-state-class", "catch",
            "--killed-stage-a", "env_failure=1",
            "--judge-catch", "1", "--survived", "1",
            "--tokens-out", "54321",
            "--label-ground-truth", "clean",
            "--label-adjudicated-by", "operator",
            "--label-note", "survivor was a real defect; label "
            "adjudicated before scoring",
            "--ledger", str(ledger), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        row = json.loads(ledger.read_text().strip())
        self.assertEqual(row["repo"], "alpha")
        self.assertEqual(row["pr"], 9)
        self.assertEqual(row["trigger"], "flagship")
        self.assertEqual(row["hypotheses"]["kept"], 4)
        self.assertTrue(row["hypotheses"]["coverage_complete"])
        self.assertEqual(row["killed_stage_a"],
                         {"env_failure": 1})
        self.assertEqual(row["judge"]["catch"], 1)
        self.assertEqual(row["survived"], 1)
        self.assertEqual(row["operator_disposition"], "pending")
        self.assertEqual(row["label_adjudication"]
                         ["ground_truth"], "clean")
        self.assertEqual(row["label_adjudication"]
                         ["adjudicated_by"], "operator")


def _synth_transcript(path: Path, steps: list, finished: dict):
    with open(path, "w", encoding="utf-8") as fh:
        for ev in steps:
            fh.write(json.dumps(ev) + "\n")
        fh.write(json.dumps({"event": "finished", **finished}) + "\n")


class TestGradeRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-grade-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_grade(self, *argv):
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "grade_run.py"), *argv],
            capture_output=True, text=True, timeout=60)

    def _step(self, n, calls, results=None, provider="minimax"):
        return {"step": n, "provider": provider, "model": "m",
                "duration_ms": 100, "tokens_in": 1000, "tokens_out": 100,
                "assistant_text": "",
                "tool_calls": calls,
                "tool_results": results or [""] * len(calls),
                "tool_result_kinds": ["ok"] * len(calls)}

    def test_good_run_passes(self):
        tr = self.tmp / "good.jsonl"
        _synth_transcript(tr, [
            self._step(1, [{"name": "read_file", "args": "{}"}]),
            self._step(2, [{"name": "read_file", "args": "{}"}]),
            self._step(3, [{"name": "edit_file", "args": "{}"}]),
            self._step(5, [{"name": "exec",
                            "args": '{"command": "pytest -q"}'}],
                       ["exit=0\n3 passed"]),
        ], {"steps": 10, "duration_ms": 1, "total_in": 1, "total_out": 1,
            "files_changed": ["a.py"], "commands_run": 1,
            "gate_triggers": 0, "midrun_gate_triggers": 0})
        r = self.run_grade("--transcript", str(tr), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["score"], 95)
        self.assertEqual(data["verdict"], "pass")
        self.assertEqual(data["signals"]["first_edit"]["value"], 3)
        self.assertEqual(data["signals"]["claim_vs_diff"]["value"], "unknown")

    def test_no_edit_hit_cap_reviews(self):
        tr = self.tmp / "bad.jsonl"
        steps = [self._step(n, [{"name": "read_file", "args": "{}"}])
                 for n in range(1, 61)]
        steps.append({"step": 60, "event": "gate_trigger",
                      "gate": "empty_diff", "round": 1, "max_rounds": 2})
        _synth_transcript(tr, steps,
                          {"steps": 60, "duration_ms": 1, "total_in": 1,
                           "total_out": 1, "files_changed": [],
                           "commands_run": 0, "gate_triggers": 1,
                           "midrun_gate_triggers": 0})
        r = self.run_grade("--transcript", str(tr), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["verdict"], "review")
        self.assertLess(data["score"], 50)
        self.assertEqual(data["signals"]["first_edit"]["points"], 0)
        self.assertEqual(data["signals"]["empty_diff_gate"]["points"], 0)

    def test_missing_transcript_exits_2(self):
        r = self.run_grade("--transcript", str(self.tmp / "nope.jsonl"))
        self.assertEqual(r.returncode, 2)

    def _git_repo_with_report(self, claimed, manifest, name="wt"):
        repo = self.tmp / name
        repo.mkdir()
        env = dict(os.environ,
                   GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example",
                   GIT_COMMITTER_NAME="Test",
                   GIT_COMMITTER_EMAIL="t@example")
        subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True,
                       env=env)
        (repo / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo,
                       check=True, env=env)
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, check=True,
                              env=env).stdout.strip()
        (repo / "a.py").write_text("x = 2\n")
        (repo / "b.py").write_text("y = 1\n")
        report = repo / "WORKER_REPORT.md"
        report.write_text(
            "# Worker report\n\n"
            f"- Files changed: {claimed}\n\n"
            "## Machine manifest (harness-generated — verify, do not trust prose above)\n\n"
            f"- base_commit: {base}\n"
            f"- head_commit: {base}\n"
            f"- files_changed_git: {manifest}\n"
            "- gate_triggers: empty_diff=0, midrun_no_edit=0\n")
        return repo, report

    def test_claim_vs_diff_match_and_mismatch(self):
        tr = self.tmp / "t.jsonl"
        _synth_transcript(tr, [
            self._step(1, [{"name": "edit_file", "args": "{}"}]),
        ], {"steps": 2, "duration_ms": 1, "total_in": 1, "total_out": 1,
            "files_changed": ["a.py"], "commands_run": 0,
            "gate_triggers": 0, "midrun_gate_triggers": 0})
        repo, report = self._git_repo_with_report("a.py, b.py", "a.py, b.py")
        r = self.run_grade("--transcript", str(tr), "--report", str(report),
                           "--workdir", str(repo), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["signals"]["claim_vs_diff"]["value"], "match")
        self.assertEqual(data["signals"]["claim_vs_diff"]["points"], 10)
        repo2, report2 = self._git_repo_with_report(
            "a.py, c.py", "a.py, b.py", name="wt2")
        r = self.run_grade("--transcript", str(tr), "--report", str(report2),
                           "--workdir", str(repo2), "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["signals"]["claim_vs_diff"]["value"],
                         "mismatch")
        self.assertEqual(data["signals"]["claim_vs_diff"]["points"], 0)


# Fake-provider driver for the token-out guard: a localhost HTTP server
# answers every chat call with a tool-calling completion (600 tokens out
# per step), so --max-tokens-out 1000 must trip on step 2 while an
# uncapped run reaches the step cap instead. No network, no credentials.
TOKEN_CAP_DRIVER = '''
import json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

mode = sys.argv[1]
bin_dir = sys.argv[2]
workdir = sys.argv[3]
tdir = sys.argv[4]

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        body = json.dumps({"choices": [{"message": {
            "role": "assistant", "content": "working",
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "exec",
                "arguments": json.dumps({"command": "echo hi"})}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 600}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
sys.path.insert(0, bin_dir)
import agent
agent.PROVIDERS["minimax"]["chat_url"] = (
    f"http://127.0.0.1:{srv.server_address[1]}/chat")
brief = os.path.join(workdir, "BRIEF.md")
open(brief, "w").write("Do the thing.\\n")
argv = ["agent.py", "--brief", brief, "--workdir", workdir,
        "--provider", "minimax", "--model-tier", "cheap",
        "--run-id", "cap-test", "--transcript-dir", tdir]
if mode == "cap":
    argv += ["--max-steps", "10", "--max-tokens-out", "1000"]
else:
    argv += ["--max-steps", "3"]
sys.argv = argv
rc = agent.main()
report = open(os.path.join(workdir, "WORKER_REPORT.md")).read()
transcript = open(os.path.join(tdir, "cap-test.jsonl")).read()
finished = json.loads(transcript.strip().splitlines()[-1])
if mode == "cap":
    assert "[stopped: token budget reached]" in report, report
    assert finished.get("token_cap") is True, finished
    assert finished.get("total_out", 0) >= 1000, finished
else:
    assert "[stopped: max steps reached]" in report, report
    assert "[stopped: token budget reached]" not in report, report
    assert finished.get("token_cap") is False, finished
print("driver ok:", mode)
sys.exit(0)
'''


class TestAgentTokenCap(TreeCase):
    def test_token_cap_trips_and_uncapped_reaches_step_cap(self):
        env = dict(os.environ,
                   GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example",
                   GIT_COMMITTER_NAME="Test",
                   GIT_COMMITTER_EMAIL="t@example")
        for mode in ("cap", "nocap"):
            wt = self.tmp / f"wt-{mode}"
            wt.mkdir()
            subprocess.run(["git", "init", "-q", "."], cwd=wt, check=True,
                           env=env)
            (wt / "a.py").write_text("x = 1\n")
            subprocess.run(["git", "add", "."], cwd=wt, check=True, env=env)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=wt,
                           check=True, env=env)
            tdir = self.tmp / f"tr-{mode}"
            driver = self.tmp / f"driver-{mode}.py"
            driver.write_text(TOKEN_CAP_DRIVER)
            r = subprocess.run(
                [sys.executable, str(driver), mode,
                 str(self.root / "bin"), str(wt), str(tdir)],
                capture_output=True, text=True,
                env=self.env(MINIMAX_API_KEY="test-key-123"), timeout=120)
            self.assertEqual(r.returncode, 0,
                             f"mode={mode}\nstdout={r.stdout}\nstderr={r.stderr}")
            self.assertIn(f"driver ok: {mode}", r.stdout)


class TestStateWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-state-"))
        self.dest = self.tmp / "state.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sw(self, *argv):
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "state_write.py"), *argv],
            capture_output=True, text=True, timeout=60)

    def test_commit_replaces_and_backs_up(self):
        self.dest.write_text('{"a": 1}\n')
        staging = self.tmp / "state.json.new"
        staging.write_text('{"a": 2, "b": [1, 2]}\n')
        r = self.run_sw("commit", str(staging), str(self.dest), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.dest.read_text()),
                         {"a": 2, "b": [1, 2]})
        backups = list(self.tmp.glob("state.json.statebak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), {"a": 1})

    def test_commit_refuses_truncated_staging_and_keeps_dest(self):
        self.dest.write_text('{"a": 1}\n')
        staging = self.tmp / "state.json.new"
        staging.write_text('{"a": 2, "b": [1,')  # truncated mid-write
        r = self.run_sw("commit", str(staging), str(self.dest))
        self.assertEqual(r.returncode, 1)
        self.assertIn("REFUSED", r.stderr)
        self.assertEqual(json.loads(self.dest.read_text()), {"a": 1})
        self.assertEqual(list(self.tmp.glob("state.json.statebak-*")), [])

    def test_commit_refuses_staging_equals_dest(self):
        self.dest.write_text('{"a": 1}\n')
        r = self.run_sw("commit", str(self.dest), str(self.dest))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(self.dest.read_text()), {"a": 1})

    def test_commit_new_file_no_backup(self):
        staging = self.tmp / "fresh.json.new"
        staging.write_text('[1, 2, 3]\n')
        dest = self.tmp / "fresh.json"
        r = self.run_sw("commit", str(staging), str(dest))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(dest.read_text()), [1, 2, 3])

    def test_validate_ok_and_invalid(self):
        self.dest.write_text('{"x": true}\n')
        r = self.run_sw("validate", str(self.dest), "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual((data["valid"], data["type"], data["entries"]),
                         (True, "object", 1))
        self.dest.write_text('not json')
        r = self.run_sw("validate", str(self.dest))
        self.assertEqual(r.returncode, 1)


class TestShepherdLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-shepherd-"))
        self.ledger = self.tmp / "shepherd_ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sl(self, *argv):
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "shepherd_ledger.py"),
             *argv, "--ledger", str(self.ledger)],
            capture_output=True, text=True, timeout=60)

    def test_append_and_summary_counts(self):
        r = self.run_sl("append", "--repo", "alpha", "--pr", "12",
                        "--action", "rerun", "--outcome", "flaky")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_sl("append", "--repo", "alpha", "--pr", "12",
                        "--action", "merged_by_shepherd",
                        "--outcome", "merged", "--merge-state", "UNSTABLE")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_sl("append", "--repo", "beta", "--pr", "3",
                        "--action", "needs_operator", "--outcome", "physics")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [json.loads(x) for x in
                self.ledger.read_text().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all("ts" in row for row in rows))
        r = self.run_sl("summary", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        # --ledger was appended after --json by run_sl; argparse still parses
        data = json.loads(r.stdout)
        self.assertEqual(data["rows"], 3)
        self.assertEqual(data["merges"], 1)
        self.assertEqual(data["unstable_merges"], 1)
        self.assertEqual(data["needs_operator_distinct_prs"], 1)

    def test_summary_missing_ledger_exits_2(self):
        r = self.run_sl("summary")
        self.assertEqual(r.returncode, 2)


class TestProofReceipt(unittest.TestCase):
    """The proof-packet receipt: presence/gaps per PR + coverage."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-receipt-"))
        self.ledger = self.tmp / "receipts.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_rc(self, *argv):
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "receipt.py"), *argv],
            capture_output=True, text=True, timeout=60)

    def test_check_reports_present_gaps_and_absent(self):
        r = self.run_rc("check", "--required", "a,b", "--present", "a,b",
                        "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["proof"], "present")

        r = self.run_rc("check", "--required", "a,b", "--present", "a",
                        "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["proof"], "gaps")
        self.assertEqual(data["gap_lines"], ["proof gap: b"])

        r = self.run_rc("check", "--required", "a", "--present", "",
                        "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["proof"], "absent")
        self.assertEqual(data["gap_lines"], ["proof gap: a"])

    def test_check_marks_not_available_with_reason(self):
        r = self.run_rc("check", "--required", "before screenshot",
                        "--present", "",
                        "--not-available", "before screenshot (no display)",
                        "--json")
        data = json.loads(r.stdout)
        # an explicitly recorded unavailable item is a known gap, not a
        # silent absence — the receipt names it and the reason
        self.assertEqual(data["proof"], "gaps")
        self.assertEqual(
            data["gap_lines"],
            ["proof gap: before screenshot (not available: no display)"])

    def test_record_and_summary_report_coverage_over_window(self):
        r = self.run_rc("record", "--repo", "you/alpha", "--pr", "1",
                        "--url", "https://example/pr/1", "--type", "visual",
                        "--required", "before screenshot,after screenshot",
                        "--present", "before screenshot,after screenshot",
                        "--ledger", str(self.ledger))
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_rc("record", "--repo", "you/beta", "--pr", "2",
                        "--type", "numeric", "--required", "before numbers",
                        "--present", "", "--not-available",
                        "before numbers (no baseline run)",
                        "--ledger", str(self.ledger))
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [json.loads(x) for x in self.ledger.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["proof"] for r in rows], ["present", "gaps"])
        self.assertTrue(all("ts" in r for r in rows))

        r = self.run_rc("summary", "--ledger", str(self.ledger), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["prs"], 2)
        self.assertEqual(data["by_proof"]["present"], 1)
        self.assertEqual(data["by_proof"]["gaps"], 1)
        self.assertEqual(data["required_prs"], 2)
        self.assertEqual(data["coverage_pct"], 50.0)
        self.assertEqual(data["gap_prs"][0]["gaps"], ["before numbers"])

        # window: a --since far in the future empties the window
        r = self.run_rc("summary", "--ledger", str(self.ledger),
                        "--since", "2999-01-01", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["prs"], 0)
        self.assertIsNone(data["coverage_pct"])

    def test_summary_missing_ledger_exits_2(self):
        r = self.run_rc("summary", "--ledger", str(self.ledger))
        self.assertEqual(r.returncode, 2)


class TestProofPromptAndExamples(unittest.TestCase):
    """The supervisor prompt assembles the Proof section; examples show it."""

    def prompt(self):
        return (REPO / "prompts" / "backlog-supervisor.md").read_text()

    def test_prompt_has_step7_proof_assembly(self):
        text = self.prompt()
        self.assertIn("7-proof.", text)
        self.assertIn("## Proof", text)
        self.assertIn("proof gap:", text)
        self.assertIn("bin/receipt.py record", text)
        self.assertIn("bin/receipt.py check", text)
        # the brief must carry a PROOF: expectation
        self.assertIn("PROOF:", text)

    def test_rendered_prompt_keeps_proof_instructions(self):
        tmp = Path(tempfile.mkdtemp(prefix="pc-proof-render-"))
        try:
            config = tmp / "deployment.json"
            config.write_text(json.dumps(FULL_CONFIG))
            out = tmp / "out"
            r = subprocess.run(
                [sys.executable, str(REPO / "bin" / "render_prompts.py"),
                 "--config", str(config), "--out", str(out)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sup = (out / "backlog-supervisor.rendered.md").read_text()
            self.assertNotIn("{{", sup)
            self.assertIn("7-proof.", sup)
            self.assertIn("proof gap:", sup)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_examples_show_proof_for_visual_and_numeric_repos(self):
        brief = (REPO / "examples" / "BRIEF.example.md").read_text()
        self.assertIn("PROOF:", brief)
        self.assertIn("type=numeric", brief)
        brief_visual = (REPO / "examples" / "BRIEF.visual.example.md").read_text()
        self.assertIn("PROOF:", brief_visual)
        self.assertIn("type=visual", brief_visual)
        visual = (REPO / "examples" / "PR_BODY.visual.example.md").read_text()
        numeric = (REPO / "examples" / "PR_BODY.numeric.example.md").read_text()
        for body in (visual, numeric):
            self.assertIn("## Proof", body)
            self.assertIn("Provider/tier:", body)
            self.assertIn("diffstat", body)
        self.assertIn("before", visual)
        self.assertIn("after", visual)
        self.assertIn("| case | before | after |", numeric)

    def test_example_briefs_declare_the_type_the_pr_body_shows(self):
        pairs = (("BRIEF.visual.example.md", "PR_BODY.visual.example.md"),
                 ("BRIEF.example.md", "PR_BODY.numeric.example.md"))
        for brief_name, body_name in pairs:
            brief = (REPO / "examples" / brief_name).read_text()
            body = (REPO / "examples" / body_name).read_text()
            kind = re.search(r"PROOF: type=(\w+)", brief).group(1)
            self.assertIn(kind, brief)
            # the PR body's proof material matches the declared type
            if kind == "visual":
                self.assertIn("Before/after", body)
            elif kind == "numeric":
                self.assertIn("Headline numbers", body)

    def test_example_with_missing_proof_states_the_gap(self):
        gap = (REPO / "examples" / "PR_BODY.gap.example.md").read_text()
        self.assertIn("## Proof", gap)
        self.assertIn("proof gap:", gap)
        # the gap brief requires the numbers the gap example could not produce
        brief = (REPO / "examples" / "BRIEF.gap.example.md").read_text()
        self.assertIn("PROOF:", brief)
        self.assertIn("drop_5m", brief)
        # the gap example's line is the one receipt.py itself produces
        r = subprocess.run(
            [sys.executable, str(REPO / "bin" / "receipt.py"), "check",
             "--required", "before numbers for the case `drop_5m`",
             "--present", "",
             "--not-available",
             "before numbers for the case `drop_5m` (no baseline run was "
             "captured in the worktree)",
             "--json"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        gap_line = json.loads(r.stdout)["gap_lines"][0]
        self.assertIn(gap_line, gap)


if __name__ == "__main__":
    unittest.main()
