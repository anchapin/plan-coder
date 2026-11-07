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
    "workers": {"max_steps": 60, "provider_order": ["minimax", "zai"],
                "max_tokens_out": 0, "cycle_tokens_out_budget": 0},
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

    def test_render_has_trace_grader_wiring(self):
        out = self.tmp / "out-trace"
        r = self.run_bin("render_prompts.py", "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sup = (out / "backlog-supervisor.rendered.md").read_text()
        self.assertIn("ledger_check.py", sup)
        self.assertIn("grade_run.py", sup)
        self.assertIn("failover.py", sup)
        self.assertIn("FLAG FOR ", sup)
        self.assertIn("--max-tokens-out 0", sup)
        self.assertIn("stopped:token-cap", sup)
        self.assertNotIn("{{", sup)


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


if __name__ == "__main__":
    unittest.main()
