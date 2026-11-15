"""Tests for bin/prompt_check.py — the prompt self-test runner (issue #55, Phase 0).

The runner gives a prompt template the same treatment bin/ code gets:
static checks plus behavioural specs, one deterministic score, no LLM.

Covered here:
  - the live prompt (prompts/pr-shepherd.md) passes every check
  - the deliberate-regression fixture copy scores lower, and the failing
    specs name the clause that was removed
  - the runner never writes to the prompt it scores
  - each static check actually fires when its condition is violated
  - model-judgement specs are excluded from the score, not faked

Run: python3 -m pytest tests/test_prompt_check.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "bin" / "prompt_check.py"
PROMPT = REPO / "prompts" / "pr-shepherd.md"
SPECS = REPO / "tests" / "fixtures" / "prompt_specs" / "pr-shepherd.json"
REGRESSION = REPO / "tests" / "fixtures" / "prompt_specs" / "pr-shepherd-regression.json"
FIXTURE_COPY = (REPO / "tests" / "fixtures" / "prompts" /
                 "pr-shepherd-known-failure-clause-removed.md")


def _load_module():
    spec = importlib.util.spec_from_file_location("bin_prompt_check", RUNNER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bin_prompt_check"] = module
    spec.loader.exec_module(module)
    return module


prompt_check = _load_module()


def run_runner(*args) -> tuple[int, dict]:
    """Run the runner with --json; return (exit code, parsed output)."""
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--json", *args],
        capture_output=True, text=True, cwd=str(REPO), check=False)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:  # pragma: no cover - defensive
        raise AssertionError("runner produced no JSON:\n%s\n%s"
                             % (proc.stdout, proc.stderr))
    return proc.returncode, payload


class LivePromptTest(unittest.TestCase):
    def test_live_prompt_scores_100_and_exits_zero(self):
        code, out = run_runner()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["score"], 100.0)
        self.assertTrue(out["ok"])
        for check in out["static"]:
            self.assertTrue(check["passed"],
                            "static check failed: %s (%s)"
                            % (check["name"], check["detail"]))
        for spec in out["specs"]:
            self.assertTrue(spec["passed"],
                            "spec failed: %s (%s)" % (spec["id"], spec["detail"]))

    def test_static_checks_are_the_four_required_ones(self):
        _, out = run_runner()
        names = {c["name"] for c in out["static"]}
        self.assertEqual(names, {"required_sections", "tokens_resolved",
                                 "bin_paths_exist", "length_budget"})

    def test_spec_count_within_phase0_budget(self):
        _, out = run_runner()
        self.assertGreaterEqual(out["spec_counts"]["deterministic"], 5)
        self.assertLessEqual(out["spec_counts"]["deterministic"], 10)

    def test_model_judgement_spec_is_excluded_not_faked(self):
        _, out = run_runner()
        excluded = [s["id"] for s in out["excluded_specs"]]
        self.assertIn("diff_scope_judgement", excluded)
        scored_ids = {s["id"] for s in out["specs"]}
        self.assertNotIn("diff_scope_judgement", scored_ids)

    def test_specs_file_is_json_and_names_the_prompt(self):
        doc = json.loads(SPECS.read_text(encoding="utf-8"))
        self.assertEqual(doc["prompt"], "prompts/pr-shepherd.md")
        self.assertTrue(5 <= len(doc["specs"]) <= 10)


class DeliberateRegressionTest(unittest.TestCase):
    """The keep/revert demonstration, on a COPY of the prompt."""

    def setUp(self):
        self.recipe = json.loads(REGRESSION.read_text(encoding="utf-8"))
        self.base_code, self.base = run_runner()
        self.mut_code, self.mut = run_runner(
            "--prompt", str(FIXTURE_COPY))

    def test_fixture_copy_is_the_live_prompt_minus_exactly_one_clause(self):
        live = PROMPT.read_text(encoding="utf-8")
        expected = prompt_check.remove_clause(
            live, self.recipe["removed_clause"])
        self.assertEqual(expected, FIXTURE_COPY.read_text(encoding="utf-8"))

    def test_fixture_copy_scores_lower_than_the_live_prompt(self):
        self.assertEqual(self.base_code, 0)
        self.assertEqual(self.mut_code, 1)
        self.assertLess(self.mut["score"], self.base["score"])
        self.assertEqual(self.base["score"],
                         self.recipe["expected_base_score"])

    def test_failing_specs_name_the_removed_clause(self):
        failed = [s for s in self.mut["specs"] if not s["passed"]]
        failed_ids = {s["id"] for s in failed}
        self.assertTrue(set(self.recipe["expected_failing_specs"])
                        <= failed_ids, failed_ids)
        for spec in failed:
            self.assertIn(self.recipe["removed_clause"], spec["detail"])
            self.assertEqual(spec["clause"], self.recipe["removed_clause"])

    def test_revert_recovers_the_score(self):
        """Reverting the fixture copy (i.e. scoring the live prompt) recovers."""
        reverted = prompt_check.run(str(PROMPT), str(SPECS), str(REPO))
        self.assertEqual(reverted["score"], self.base["score"])
        self.assertEqual(reverted["score"], 100.0)

    def test_runner_never_writes_to_the_prompt_it_scores(self):
        before = PROMPT.read_bytes()
        run_runner("--remove-clause", "known_failure_exemption")
        self.assertEqual(PROMPT.read_bytes(), before)


class StaticCheckFiresTest(unittest.TestCase):
    """Each static check must fail when its condition is violated."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prompt-check-test-")
        self.live = PROMPT.read_text(encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text: str) -> str:
        path = os.path.join(self.tmp, "prompt.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _static(self, text: str) -> dict:
        result = prompt_check.static_checks(text, str(REPO))
        return {c["name"]: c for c in result}

    def test_stale_bin_path_is_caught(self):
        """The 2026-11-08 incident: a prompt naming a renamed bin/ path."""
        broken = self.live.replace(
            "python3 {{skill.dir}}/bin/state_write.py commit",
            "python3 {{skill.dir}}/bin/state_write_atomic.py commit")
        self.assertNotEqual(broken, self.live)
        checks = self._static(broken)
        self.assertFalse(checks["bin_paths_exist"]["passed"])
        self.assertIn("state_write_atomic.py",
                      checks["bin_paths_exist"]["detail"])

    def test_unresolved_token_is_caught(self):
        broken = self.live.replace("{{state.dir}}", "{{state.nope}}")
        checks = self._static(broken)
        self.assertFalse(checks["tokens_resolved"]["passed"])
        self.assertIn("state.nope", checks["tokens_resolved"]["detail"])

    def test_missing_required_section_is_caught(self):
        broken = self.live.replace("NEVER merge", "maybe merge")
        checks = self._static(broken)
        self.assertFalse(checks["required_sections"]["passed"])

    def test_length_budget_is_caught(self):
        broken = self.live + ("\nfiller. " * 5000)
        checks = self._static(broken)
        self.assertFalse(checks["length_budget"]["passed"])


class RuleEngineTest(unittest.TestCase):
    """Direct unit checks of the deterministic rule engine."""

    def setUp(self):
        self.clauses = prompt_check.present_clauses(
            PROMPT.read_text(encoding="utf-8"))

    def test_green_worker_pr_merges(self):
        decision, _, _ = prompt_check.decide({
            "state": "open", "mergeable": "MERGEABLE",
            "merge_state_status": "CLEAN", "head_branch": "worker/x",
            "ci": "green"}, self.clauses)
        self.assertEqual(decision, "merge")

    def test_workflow_touch_is_a_hard_stop(self):
        decision, clause, _ = prompt_check.decide({
            "state": "open", "mergeable": "MERGEABLE",
            "merge_state_status": "CLEAN", "head_branch": "worker/x",
            "touches_workflows": True, "ci": "green"}, self.clauses)
        self.assertEqual(decision, "flag")
        self.assertEqual(clause, "hard_stop_workflows")

    def test_missing_clause_makes_the_case_undecidable(self):
        clauses = self.clauses - {"known_failure_exemption"}
        decision, clause, _ = prompt_check.decide({
            "state": "open", "mergeable": "MERGEABLE",
            "merge_state_status": "UNSTABLE", "head_branch": "worker/x",
            "ci": "green",
            "ci_failures": [{"scope": "pre_existing_tracked"}]},
            clauses)
        self.assertEqual(decision, "hold_unstated")
        self.assertEqual(clause, "known_failure_exemption")


if __name__ == "__main__":
    unittest.main()
