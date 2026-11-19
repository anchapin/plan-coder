"""Tests for bin/structural_discovery_phase0.py (issue #79, Phase 0).

The Phase-0 classifier decides the report's tables and the Kill/Proceed
verdict, so it is pinned here with synthetic fixtures: no live
transcript of the supervisor is read, no network is used, no wall clock
is consulted.  The end-to-end test writes a synthetic transcript
directory and checks that the classifier is deterministic and that the
verdict is computed from the tables.

Run: python3 -m pytest tests/test_structural_discovery_phase0.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"

_spec = importlib.util.spec_from_file_location(
    "structural_discovery_phase0", BIN / "structural_discovery_phase0.py")
sdp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdp)


def step(step_no, calls, results=None):
    """One transcript step with the given tool calls."""
    results = results or ["ok"] * len(calls)
    return {
        "step": step_no,
        "tool_calls": [
            {"name": name, "args": json.dumps(args)} for name, args in calls
        ],
        "tool_results": results,
        "tool_result_kinds": ["ok"] * len(calls),
    }


def started(run_id="demo-1-1700000000"):
    return {"event": "started", "run_id": run_id, "tier": "cheap"}


class ExecClassificationTests(unittest.TestCase):
    """The exec rules of the taxonomy."""

    def test_grep_is_structural_search(self):
        klass, subject = sdp.classify_exec(
            "grep -rn 'def foo' src/ | head -20", False)
        self.assertEqual(klass, "struct_search")
        self.assertIn("def foo", subject)

    def test_find_is_structural_search(self):
        klass, _ = sdp.classify_exec("find . -name '*.py' | head", False)
        self.assertEqual(klass, "struct_search")

    def test_pytest_is_verify(self):
        klass, _ = sdp.classify_exec("python3 -m pytest tests/ -q", False)
        self.assertEqual(klass, "verify")

    def test_git_status_is_vcs(self):
        klass, _ = sdp.classify_exec("git status --short", False)
        self.assertEqual(klass, "vcs")

    def test_gh_is_vcs(self):
        klass, _ = sdp.classify_exec("gh pr view 12", False)
        self.assertEqual(klass, "vcs")

    def test_ls_before_first_edit_is_structural_search(self):
        klass, _ = sdp.classify_exec("ls -la src/", False)
        self.assertEqual(klass, "struct_search")

    def test_ls_after_first_edit_is_not_structural_search(self):
        klass, _ = sdp.classify_exec("ls -la src/", True)
        self.assertEqual(klass, "other_exec")

    def test_ls_in_front_of_a_test_run_is_verify(self):
        klass, _ = sdp.classify_exec("ls test/ && pytest -q", False)
        self.assertEqual(klass, "verify")

    def test_install_is_other_exec(self):
        klass, _ = sdp.classify_exec("pip install requests", False)
        self.assertEqual(klass, "other_exec")

    def test_cd_prefix_is_ignored(self):
        klass, _ = sdp.classify_exec(
            "cd ~/workspace/fixtrees/x && grep -rn TODO .", False)
        self.assertEqual(klass, "struct_search")

    def test_heredoc_body_is_not_classified(self):
        klass, _ = sdp.classify_exec(
            "python3 - <<'EOF'\nprint('git status')\nEOF", False)
        self.assertEqual(klass, "other_exec")

    def test_cat_of_a_file_is_a_read(self):
        klass, target = sdp.classify_exec("cat src/app.py 2>/dev/null", False)
        self.assertEqual(klass, "read_exec")
        self.assertEqual(target, "src/app.py")


class RunClassificationTests(unittest.TestCase):
    """The per-run rules: one class per tool call, and the
    pre-first-edit window."""

    def test_every_call_gets_exactly_one_class(self):
        events = [
            started(),
            step(1, [("exec", {"command": "grep -rn x ."})]),
            step(2, [("read_file", {"path": "src/app.py"})]),
            step(3, [("edit_file", {"path": "src/app.py",
                                    "old_text": "a",
                                    "new_text": "b"})]),
        ]
        result = sdp.classify_run(events)
        self.assertEqual(result["tool_calls"], 3)
        self.assertEqual(result["counts"].get("struct_search"), 1)
        self.assertEqual(result["counts"].get("task_read"), 1)
        self.assertEqual(result["counts"].get("edit"), 1)

    def test_first_read_of_an_unedited_file_is_struct_read(self):
        events = [
            started(),
            step(1, [("read_file", {"path": "src/helper.py"})]),
        ]
        result = sdp.classify_run(events)
        self.assertEqual(result["counts"].get("struct_read"), 1)

    def test_reread_of_the_same_file_is_task_read(self):
        events = [
            started(),
            step(1, [("read_file", {"path": "src/helper.py"})]),
            step(2, [("read_file", {"path": "src/helper.py"})]),
        ]
        result = sdp.classify_run(events)
        self.assertEqual(result["counts"].get("struct_read"), 1)
        self.assertEqual(result["counts"].get("task_read"), 1)

    def test_steering_document_read_is_task_read(self):
        events = [
            started(),
            step(1, [("read_file", {"path": "AGENTS.md"})]),
        ]
        result = sdp.classify_run(events)
        self.assertEqual(result["counts"].get("struct_read"), 0)
        self.assertEqual(result["counts"].get("task_read"), 1)

    def test_file_edited_later_in_the_run_is_task_read(self):
        events = [
            started(),
            step(1, [("read_file", {"path": "src/app.py"})]),
            step(2, [("write_file", {"path": "src/app.py",
                                     "content": "x"})]),
        ]
        result = sdp.classify_run(events)
        self.assertEqual(result["counts"].get("struct_read"), 0)
        self.assertEqual(result["counts"].get("task_read"), 1)

    def test_list_dir_before_first_edit_is_struct_search(self):
        events = [
            started(),
            step(1, [("list_dir", {"path": "src"})]),
            step(2, [("write_file", {"path": "src/app.py",
                                     "content": "x"})]),
            step(3, [("list_dir", {"path": "src"})]),
        ]
        result = sdp.classify_run(events)
        self.assertEqual(result["counts"].get("struct_search"), 1)
        self.assertEqual(result["counts"].get("task_read"), 1)

    def test_pre_first_edit_window_counts_only_earlier_calls(self):
        events = [
            started(),
            step(1, [("exec", {"command": "grep -rn x ."})]),
            step(2, [("exec", {"command": "grep -rn y ."})]),
            step(3, [("write_file", {"path": "a.py", "content": "x"})]),
            step(4, [("exec", {"command": "grep -rn z ."})]),
        ]
        result = sdp.classify_run(events)
        self.assertEqual(result["pre_calls"], 2)
        self.assertEqual(result["pre_struct_calls"], 2)
        self.assertEqual(result["pre_struct_share"], 1.0)
        self.assertEqual(result["first_edit_step"], 3)
        self.assertEqual(result["steps_to_first_edit"], 2)

    def test_run_without_an_edit_has_a_full_pre_edit_window(self):
        events = [
            started(),
            step(1, [("exec", {"command": "grep -rn x ."})]),
        ]
        result = sdp.classify_run(events)
        self.assertIsNone(result["first_edit_step"])
        self.assertEqual(result["pre_calls"], 1)


class VerdictTests(unittest.TestCase):
    """The issue #79 section 5 gate."""

    def _aggregate(self, shares, pre_shares):
        runs = [{"struct_share": s, "pre_struct_share": p,
                 "ceiling_perfect": 0.0, "ceiling_realistic": 0.0,
                 "struct_char_share": 0.0, "steps_to_first_edit": 1,
                 "tool_calls": 10, "counts": {}}
                for s, p in zip(shares, pre_shares)]
        return sdp.aggregate_runs(runs)

    def test_low_discovery_meets_the_kill_bar(self):
        aggregate = self._aggregate([0.02, 0.03, 0.05, 0.04],
                                    [0.02, 0.03, 0.05, 0.04])
        verdict = sdp.verdict_of(aggregate)
        self.assertTrue(verdict["kill_bar_met"])
        self.assertEqual(verdict["result"], "KILL")

    def test_high_median_does_not_meet_the_kill_bar(self):
        aggregate = self._aggregate([0.45, 0.5, 0.4], [0.6, 0.7, 0.5])
        verdict = sdp.verdict_of(aggregate)
        self.assertFalse(verdict["kill_bar_met"])
        self.assertEqual(verdict["result"], "PROCEED")

    def test_high_p90_does_not_meet_the_kill_bar(self):
        aggregate = self._aggregate([0.02, 0.02, 0.02, 0.95],
                                    [0.02, 0.02, 0.02, 0.95])
        verdict = sdp.verdict_of(aggregate)
        self.assertFalse(verdict["kill_bar_met"])

    def test_pre_edit_window_must_show_the_same_pattern(self):
        aggregate = self._aggregate([0.05, 0.05, 0.05, 0.05],
                                    [0.05, 0.05, 0.05, 0.80])
        verdict = sdp.verdict_of(aggregate)
        self.assertFalse(verdict["kill_bar_met"])


class HelperTests(unittest.TestCase):
    """Small helpers the report depends on."""

    def test_percentile_is_nearest_rank(self):
        values = [float(i) for i in range(1, 101)]
        self.assertEqual(sdp._pct(values, 0.90), 90.0)

    def test_repo_is_parsed_from_the_run_id(self):
        self.assertEqual(sdp.repo_from_run_id("portkit-v2-281-1795068119"),
                         "portkit-v2")
        self.assertEqual(
            sdp.repo_from_run_id("home-energy-twin-goldfish-2-1794554328"),
            "home-energy-twin")

    def test_outcome_classes(self):
        self.assertEqual(sdp.outcome_class("pr_opened"), "pr_opened")
        self.assertEqual(sdp.outcome_class("failed:empty-diff"),
                         "empty_diff")
        self.assertEqual(sdp.outcome_class("failed:bad-diff"), "failed")
        self.assertEqual(sdp.outcome_class(None), "unknown")

    def test_ceiling_cuts(self):
        run = {"tool_calls": 100,
               "counts": {"struct_search": 20, "struct_read": 10}}
        perfect, realistic = sdp.ceiling_shares(run)
        self.assertAlmostEqual(perfect, 0.25)
        self.assertAlmostEqual(realistic, 0.125)

    def test_steering_check_finds_documents_and_entry_points(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "demo-repo")
            os.makedirs(root)
            with open(os.path.join(root, "AGENTS.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("# Demo\n\n## Entry points\n\nrun bin/demo.py\n")
            row = sdp.steering_check("demo-repo", tmp)
            self.assertTrue(row["docs"]["AGENTS.md"])
            self.assertFalse(row["docs"]["CONTRACT.md"])
            self.assertTrue(row["names_entry_points"])

    def test_steering_check_without_documents(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "bare-repo"))
            row = sdp.steering_check("bare-repo", tmp)
            self.assertFalse(row["names_entry_points"])
            self.assertFalse(any(row["docs"].values()))


class EndToEndTests(unittest.TestCase):
    """The whole script over a synthetic transcript directory."""

    def _write_transcript(self, directory, name, events):
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(event) + "\n" for event in events)
        return path

    def test_report_is_deterministic_and_carries_the_tables(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            transcripts = os.path.join(tmp, "transcripts")
            os.makedirs(transcripts)
            self._write_transcript(transcripts, "demo-1-1700000000.jsonl", [
                started("demo-1-1700000000"),
                step(1, [("exec", {"command": "grep -rn foo src/"})]),
                step(2, [("read_file", {"path": "src/app.py"})]),
                step(3, [("write_file", {"path": "src/other.py",
                                         "content": "x"})]),
                step(4, [("exec", {"command": "pytest -q"})]),
                step(5, [("exec", {"command": "git status"})]),
            ])
            ledger = os.path.join(tmp, "ledger.jsonl")
            with open(ledger, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "run_id": "demo-1-1700000000", "repo": "demo",
                    "issue": 1, "outcome": "pr_opened"}) + "\n")
            first = sdp.build_report(transcripts, "test", 60, 5, ledger,
                                     tmp, set())
            second = sdp.build_report(transcripts, "test", 60, 5, ledger,
                                      tmp, set())
            self.assertEqual(json.dumps(first, sort_keys=True, default=str),
                             json.dumps(second, sort_keys=True,
                                        default=str))
            self.assertEqual(first["corpus_size"], 1)
            self.assertEqual(first["runs"][0]["repo"], "demo")
            self.assertEqual(first["runs"][0]["outcome_class"], "pr_opened")
            self.assertEqual(first["runs"][0]["counts"]["struct_search"], 1)
            self.assertEqual(first["runs"][0]["counts"]["struct_read"], 1)
            self.assertEqual(first["runs"][0]["counts"]["verify"], 1)
            self.assertEqual(first["runs"][0]["counts"]["vcs"], 1)
            rendered = sdp.render_markdown(first)
            self.assertIn("## Verdict", rendered)
            self.assertIn("## Classifier rules", rendered)
            self.assertIn("## Per-run table", rendered)
            self.assertIn("## Steering-document check", rendered)
            self.assertIn("PROCEED", rendered)

    def test_short_runs_are_excluded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            transcripts = os.path.join(tmp, "transcripts")
            os.makedirs(transcripts)
            self._write_transcript(transcripts, "short-1-1700000001.jsonl", [
                started("short-1-1700000001"),
                step(1, [("exec", {"command": "ls"})]),
            ])
            report = sdp.build_report(transcripts, "test", 60, 5,
                                      os.path.join(tmp, "none.jsonl"), tmp,
                                      set())
            self.assertEqual(report["corpus_size"], 0)
            self.assertEqual(len(report["excluded"]), 1)


if __name__ == "__main__":
    unittest.main()
