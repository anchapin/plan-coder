#!/usr/bin/env python3
"""Smoke tests for the generic host adapters (issue #74, Phase 0).

The generic adapters under `adapters/generic/` are thin wrappers: they
resolve the manager command and the rendered prompt from the environment
and run the manager command.  These tests are stub-driven -- the "manager"
is a shell stub that records the arguments it was called with and exits
with a chosen status -- so nothing here touches a scheduler, a network,
a provider or the operator's state dir.

The properties pinned down here:

  * each wrapper runs the configured manager command;
  * the rendered prompt is passed as the final argument when one is
    configured, and omitted when the prompt variable is set to an empty
    value (CLI mode);
  * a failing manager command makes the wrapper fail with the same status;
  * missing configuration fails loudly (exit 2) with a message naming the
    variable that is missing;
  * the adapter files carry no operator-specific values and no secret
    shapes.

Run: python3 -m pytest tests/test_generic_adapters.py -q
"""
from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADAPTER_DIR = REPO / "adapters" / "generic"
SUPERVISOR = ADAPTER_DIR / "supervisor.sh"
SHEPHERD = ADAPTER_DIR / "shepherd.sh"

# Secret shapes the publish gate (bin/sync_public.py) refuses to publish.
SECRET_SHAPES = (
    r"sk-[A-Za-z0-9_-]{10,}",
    r"ghp_[A-Za-z0-9]{10,}",
    r"github_pat_[A-Za-z0-9]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"BEGIN [A-Z ]*PRIVATE KEY",
)

# Operator-specific values that must never appear in an adapter: a home
# directory outside the repo, a host-specific absolute path.
OPERATOR_PATH_SHAPES = (
    r"/(?:home|Users|opt|root)/[A-Za-z0-9_.-]+",
)


def clean_env() -> dict:
    """An environment with no plan-coder configuration leaking in."""
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PLAN_CODER_"):
            del env[key]
    return env


class GenericAdapterSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("TMPDIR", "/tmp")) / (
            "plan-coder-adapter-test-%d" % os.getpid())
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.args_log = self.tmp / "args.log"
        self.stub = self.tmp / "stub.sh"
        self.stub.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$@\" >> \"$STUB_LOG\"\n"
            "exit ${STUB_EXIT:-0}\n",
            encoding="utf-8")
        self.stub.chmod(0o755)
        self.prompt = self.tmp / "backlog-supervisor.rendered.md"
        self.prompt.write_text("# rendered prompt\n", encoding="utf-8")

    def tearDown(self):
        for path in (self.args_log, self.stub, self.prompt):
            if path.exists():
                path.unlink()
        if self.tmp.exists():
            self.tmp.rmdir()

    # -- helpers ------------------------------------------------------
    def run_adapter(self, script: Path, env_overrides: dict):
        env = clean_env()
        env["STUB_LOG"] = str(self.args_log)
        env.setdefault("STUB_EXIT", "0")
        env.update(env_overrides)
        proc = subprocess.run(["bash", str(script)], env=env, check=False,
                              capture_output=True, text=True, cwd=str(self.tmp))
        return proc

    def recorded_args(self) -> list:
        if not self.args_log.exists():
            return []
        text = self.args_log.read_text(encoding="utf-8")
        return [line for line in text.splitlines() if line]

    # -- the wrappers invoke the configured command -------------------
    def test_supervisor_runs_the_configured_manager_command(self):
        proc = self.run_adapter(
            SUPERVISOR,
            {"PLAN_CODER_MANAGER_CMD": f"{self.stub} status --json",
             "PLAN_CODER_SUPERVISOR_PROMPT": str(self.prompt)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded_args(),
                         ["status", "--json", str(self.prompt)])

    def test_shepherd_runs_the_configured_manager_command(self):
        proc = self.run_adapter(
            SHEPHERD,
            {"PLAN_CODER_MANAGER_CMD": f"{self.stub} shepherd-check --json",
             "PLAN_CODER_SHEPHERD_PROMPT": str(self.prompt)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded_args(),
                         ["shepherd-check", "--json", str(self.prompt)])

    def test_cli_mode_passes_no_prompt_argument(self):
        """An empty prompt variable means: run the CLI subcommand only."""
        proc = self.run_adapter(
            SUPERVISOR,
            {"PLAN_CODER_MANAGER_CMD": f"{self.stub} next-actions --json",
             "PLAN_CODER_SUPERVISOR_PROMPT": ""})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded_args(), ["next-actions", "--json"])

    def test_relative_prompt_path_resolves_against_the_repo_root(self):
        proc = self.run_adapter(
            SHEPHERD,
            {"PLAN_CODER_MANAGER_CMD": str(self.stub),
             "PLAN_CODER_SHEPHERD_PROMPT": "local/pr-shepherd.rendered.md"})
        # The rendered file does not exist in the repo -> loud failure.
        self.assertEqual(proc.returncode, 2)
        self.assertIn("prompt file not found", proc.stderr)
        self.assertIn(str(REPO / "local" / "pr-shepherd.rendered.md"),
                      proc.stderr)

    # -- failure is loud and propagated -------------------------------
    def test_manager_failure_is_propagated(self):
        proc = self.run_adapter(
            SUPERVISOR,
            {"PLAN_CODER_MANAGER_CMD": str(self.stub),
             "PLAN_CODER_SUPERVISOR_PROMPT": "",
             "STUB_EXIT": "3"})
        self.assertEqual(proc.returncode, 3)

    def test_missing_manager_command_fails_loudly(self):
        proc = self.run_adapter(SUPERVISOR, {})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("PLAN_CODER_MANAGER_CMD", proc.stderr)

    def test_missing_prompt_file_fails_loudly(self):
        proc = self.run_adapter(
            SHEPHERD,
            {"PLAN_CODER_MANAGER_CMD": str(self.stub),
             "PLAN_CODER_SHEPHERD_PROMPT": str(self.tmp / "absent.md")})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("prompt file not found", proc.stderr)
        self.assertEqual(self.recorded_args(), [])

    # -- hygiene ------------------------------------------------------
    def test_adapters_are_syntactically_valid_bash(self):
        for script in (SUPERVISOR, SHEPHERD):
            proc = subprocess.run(["bash", "-n", str(script)], check=False,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_adapters_carry_no_secret_shapes(self):
        for script in (SUPERVISOR, SHEPHERD):
            text = script.read_text(encoding="utf-8")
            for pattern in SECRET_SHAPES:
                self.assertIsNone(re.search(pattern, text),
                                  f"{script.name}: secret shape {pattern}")

    def test_adapters_carry_no_operator_specific_paths(self):
        for script in (SUPERVISOR, SHEPHERD):
            text = script.read_text(encoding="utf-8")
            for pattern in OPERATOR_PATH_SHAPES:
                self.assertIsNone(re.search(pattern, text),
                                  f"{script.name}: operator path {pattern}")


if __name__ == "__main__":
    unittest.main()
