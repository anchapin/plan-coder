"""Worked example for the optional named-deliverable contract
(plan-coder issue #56).

A brief may carry one optional line:

    DELIVERABLE: docs/perf-notes.md

naming a repo-relative path the run must produce.  The collection-time
grader (`bin/grade_run.py --brief ... --workdir ...`) checks that path
against the worktree's final state:

  * the path is missing from the worktree  -> the run is flagged
    `failed:missing-deliverable` (a mechanical finding, not a score
    change);
  * the path is present                    -> the run collects
    normally, exactly as before;
  * the brief names no deliverable         -> no check runs at all
    (backwards compatible).

Run: python3 -m pytest tests/test_deliverable_check.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))

import grade_run  # noqa: E402  (path set above)

BRIEF_WITH = """# Brief: something (issue #56)

## SCOPE — why, and what is allowed

- Goal: write the perf notes.

## DELIVERABLE

`DELIVERABLE: docs/perf-notes.md`

## Acceptance criteria

1. The notes exist.
"""

BRIEF_WITHOUT = """# Brief: something (issue #56)

## SCOPE — why, and what is allowed

- Goal: fix the parser.

## Acceptance criteria

1. The parser works.
"""


def _transcript(path: Path) -> Path:
    """A minimal transcript the grader can read."""
    events = [
        {"event": "started", "run_id": "repo-56-1"},
        {"step": 1, "tool_calls": [{"name": "write_file",
                                    "args": "{\"path\": \"a.py\"}"}],
         "tool_results": ["exit=0"], "tokens_in": 100, "tokens_out": 10},
        {"event": "finished", "steps": 1},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n",
                    encoding="utf-8")
    return path


class TestParseDeliverable(unittest.TestCase):
    """The brief-line parser: one optional line, backwards compatible."""

    def test_no_line_names_nothing(self):
        self.assertIsNone(grade_run.parse_deliverable(BRIEF_WITHOUT))

    def test_line_names_the_path(self):
        self.assertEqual(
            grade_run.parse_deliverable(BRIEF_WITH), "docs/perf-notes.md")

    def test_none_is_not_a_deliverable(self):
        self.assertIsNone(
            grade_run.parse_deliverable("DELIVERABLE: none\n"))
        self.assertIsNone(
            grade_run.parse_deliverable("DELIVERABLE: N/A\n"))

    def test_path_prefix_and_backticks_are_tolerated(self):
        self.assertEqual(
            grade_run.parse_deliverable("DELIVERABLE: `docs/a.md`\n"),
            "docs/a.md")
        self.assertEqual(
            grade_run.parse_deliverable("DELIVERABLE: path=docs/b.md\n"),
            "docs/b.md")


class TestDeliverableCheck(unittest.TestCase):
    """The check itself: missing is caught, present collects normally."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-deliverable-"))
        self.workdir = self.tmp / "worktree"
        (self.workdir / "docs").mkdir(parents=True)
        (self.workdir / "a.py").write_text("x = 1\n", encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _brief(self, text: str) -> Path:
        brief = self.tmp / "BRIEF.md"
        brief.write_text(text, encoding="utf-8")
        return brief

    def test_missing_deliverable_is_flagged(self):
        result = grade_run.check_deliverable(
            self._brief(BRIEF_WITH), str(self.workdir))
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["path"], "docs/perf-notes.md")
        self.assertIn("absent", result["detail"])

    def test_present_deliverable_is_collected_normally(self):
        (self.workdir / "docs" / "perf-notes.md").write_text(
            "notes\n", encoding="utf-8")
        result = grade_run.check_deliverable(
            self._brief(BRIEF_WITH), str(self.workdir))
        self.assertEqual(result["status"], "present")
        self.assertEqual(result["path"], "docs/perf-notes.md")

    def test_brief_without_the_line_is_not_checked(self):
        result = grade_run.check_deliverable(
            self._brief(BRIEF_WITHOUT), str(self.workdir))
        self.assertEqual(result["status"], "not_named")
        self.assertIsNone(result["path"])

    def test_unreadable_brief_is_unknown_not_a_finding(self):
        result = grade_run.check_deliverable(
            str(self.tmp / "nope.md"), str(self.workdir))
        self.assertEqual(result["status"], "unknown")

    def test_absolute_or_escaping_path_is_not_checked(self):
        result = grade_run.check_deliverable(
            self._brief("DELIVERABLE: /etc/passwd\n"), str(self.workdir))
        self.assertEqual(result["status"], "unknown")
        result = grade_run.check_deliverable(
            self._brief("DELIVERABLE: ../outside.md\n"), str(self.workdir))
        self.assertEqual(result["status"], "unknown")


class TestGradeRunCli(unittest.TestCase):
    """End-to-end through the CLI the supervisor runs at collection."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-deliverable-cli-"))
        self.workdir = self.tmp / "worktree"
        (self.workdir / "docs").mkdir(parents=True)
        (self.workdir / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.transcript = _transcript(self.tmp / "repo-56-1.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, brief_text: str | None) -> dict:
        argv = [sys.executable, str(REPO / "bin" / "grade_run.py"),
                "--transcript", str(self.transcript),
                "--workdir", str(self.workdir), "--json"]
        if brief_text is not None:
            brief = self.tmp / "BRIEF.md"
            brief.write_text(brief_text, encoding="utf-8")
            argv += ["--brief", str(brief)]
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=60, check=False)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return json.loads(out.stdout)

    def test_missing_deliverable_is_classified(self):
        result = self._run(BRIEF_WITH)
        self.assertTrue(result["missing_deliverable"])
        self.assertEqual(result["deliverable"]["status"], "missing")
        self.assertEqual(result["suggested_outcome"],
                         "failed:missing-deliverable")

    def test_present_deliverable_collects_normally(self):
        (self.workdir / "docs" / "perf-notes.md").write_text(
            "notes\n", encoding="utf-8")
        result = self._run(BRIEF_WITH)
        self.assertFalse(result["missing_deliverable"])
        self.assertEqual(result["deliverable"]["status"], "present")
        self.assertIsNone(result["suggested_outcome"])

    def test_brief_without_the_line_is_backwards_compatible(self):
        without = self._run(BRIEF_WITHOUT)
        no_brief = self._run(None)
        self.assertEqual(without["deliverable"]["status"], "not_named")
        self.assertFalse(without["missing_deliverable"])
        # same run, same score: the optional contract changes nothing
        self.assertEqual(without["score"], no_brief["score"])
        self.assertEqual(without["verdict"], no_brief["verdict"])

    def test_missing_deliverable_does_not_move_the_score(self):
        without = self._run(BRIEF_WITHOUT)
        missing = self._run(BRIEF_WITH)
        self.assertEqual(missing["score"], without["score"])
        self.assertEqual(missing["verdict"], without["verdict"])


class TestPromptContract(unittest.TestCase):
    """The brief contract lives in the prompt template, not in a
    rendered cron body."""

    def test_supervisor_prompt_carries_the_deliverable_contract(self):
        body = (REPO / "prompts" / "backlog-supervisor.md").read_text(
            encoding="utf-8")
        self.assertIn("DELIVERABLE:", body)
        self.assertIn("failed:missing-deliverable", body)

    def test_rendered_prompt_has_no_unresolved_tokens(self):
        tmp = Path(tempfile.mkdtemp(prefix="pc-render-"))
        try:
            r = subprocess.run(
                [sys.executable, str(REPO / "bin" / "render_prompts.py"),
                 "--config", str(REPO / "config" / "deployment.example.json"),
                 "--out", str(tmp)],
                capture_output=True, text=True, timeout=120, check=False,
                env={**os.environ, "PLAN_CODER_CONFIG": ""})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sup = (tmp / "backlog-supervisor.rendered.md").read_text(
                encoding="utf-8")
            self.assertNotIn("{{", sup)
            self.assertIn("DELIVERABLE:", sup)
            self.assertIn("failed:missing-deliverable", sup)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
