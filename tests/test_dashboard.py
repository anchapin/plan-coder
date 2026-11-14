"""Regression test for bin/dashboard.py (issue #5, dashboard MVP).

The dashboard is a read-only view over the harness's state dir.  These
tests drive the real entry point (`main()`) against fixture state dirs
inside this repository (`tests/fixtures/`) -- never the operator's real
state dir -- and check the properties the ROADMAP "Dashboard design"
section demands:

  * both panels render (live view + PR metrics);
  * missing state files render as empty panels with a "no rows yet"
    note instead of crashing;
  * nothing in the state dir is written;
  * the numbers match the CLIs the design names as the aggregation
    owners (`bin/trace_summary.py`, `bin/shepherd_ledger.py summary`).

Run: python3 -m pytest tests/test_dashboard.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "dashboard.py"
TRACE_SUMMARY = REPO / "bin" / "trace_summary.py"
SHEPHERD_LEDGER = REPO / "bin" / "shepherd_ledger.py"
FIXTURE = REPO / "tests" / "fixtures" / "dashboard_state"
LIVE = REPO / "tests" / "fixtures" / "dashboard_live"
MISSING = REPO / "tests" / "fixtures" / "no-such-state-dir"
EXAMPLE_CONFIG = REPO / "config" / "deployment.example.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_dashboard():
    return load_module(SCRIPT, "plan_coder_dashboard")


def run_dashboard(module, argv):
    """Run main() and return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue()


def dir_fingerprint(directory: Path) -> dict:
    """Names + content hashes + mtimes of every file under `directory`."""
    fingerprint = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            fingerprint[str(path.relative_to(directory))] = (
                digest, stat.st_size, stat.st_mtime_ns)
    return fingerprint


@unittest.skipUnless(SCRIPT.exists(), "bin/dashboard.py not present")
class DashboardRenderTest(unittest.TestCase):
    """The page renders both panels and never crashes on missing state."""

    def test_renders_both_panels(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(FIXTURE), "--config", str(EXAMPLE_CONFIG)])
        self.assertEqual(code, 0)
        self.assertIn("Panel 1 &mdash; Live view", out)
        self.assertIn("Panel 2 &mdash; PR metrics", out)
        # live view: workers and shepherd PRs from the fixtures
        self.assertIn("repo-a", out)
        self.assertIn("Workers in flight", out)
        self.assertIn("Shepherd PRs", out)
        self.assertIn("456", out)
        # PR metrics: the ledger rows and the shepherd actions
        self.assertIn("PR rate", out)
        self.assertIn("failed:empty-diff", out)
        self.assertIn("empty-diff", out)
        self.assertIn("merged_by_shepherd", out)
        self.assertIn("Token burn vs cycle budget", out)

    def test_missing_state_files_render_empty_panels(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(MISSING), "--config", str(EXAMPLE_CONFIG)])
        self.assertEqual(code, 0)
        self.assertIn("no rows yet", out)
        self.assertIn("Panel 1 &mdash; Live view", out)
        self.assertIn("Panel 2 &mdash; PR metrics", out)

    def test_partial_state_dir_renders_no_rows_note(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(LIVE), "--config", str(EXAMPLE_CONFIG)])
        self.assertEqual(code, 0)
        self.assertIn("no rows yet", out)

    def test_in_flight_workers_per_repo(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(LIVE), "--config", str(EXAMPLE_CONFIG),
            "--json"])
        self.assertEqual(code, 0)
        snapshot = json.loads(out)
        workers = snapshot["live_view"]["workers"]
        self.assertEqual(workers["in_flight"], 2)
        self.assertEqual(workers["in_flight_by_repo"],
                         {"repo-a": 1, "repo-b": 1})
        self.assertEqual(workers["tokens_in_flight"], 165000)
        self.assertEqual(workers["by_status"]["done"], 1)
        shepherd = snapshot["live_view"]["shepherd"]
        self.assertEqual(shepherd["tracked_prs"], 2)
        self.assertEqual([pr["pr"] for pr in shepherd["tracked"]],
                         ["101", "102"])


@unittest.skipUnless(SCRIPT.exists(), "bin/dashboard.py not present")
class DashboardReadOnlyTest(unittest.TestCase):
    """The dashboard never writes to the state dir."""

    def test_state_dir_is_untouched(self):
        module = load_dashboard()
        before = dir_fingerprint(FIXTURE)
        code, _out, _err = run_dashboard(module, [
            "--state-dir", str(FIXTURE), "--config", str(EXAMPLE_CONFIG)])
        self.assertEqual(code, 0)
        self.assertEqual(dir_fingerprint(FIXTURE), before)

    def test_refuses_to_write_inside_the_state_dir(self):
        module = load_dashboard()
        code, _out, err = run_dashboard(module, [
            "--state-dir", str(FIXTURE), "--config", str(EXAMPLE_CONFIG),
            "--out", str(FIXTURE / "dashboard.html")])
        self.assertEqual(code, 2)
        self.assertIn("read-only", err)
        self.assertFalse((FIXTURE / "dashboard.html").exists())


@unittest.skipUnless(SCRIPT.exists(), "bin/dashboard.py not present")
class DashboardSemanticsTest(unittest.TestCase):
    """The numbers match the CLIs the design names as the owners."""

    def _snapshot(self, module):
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(FIXTURE), "--config", str(EXAMPLE_CONFIG),
            "--json"])
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_pr_rate_matches_trace_summary(self):
        module = load_dashboard()
        trace = load_module(TRACE_SUMMARY, "plan_coder_trace_summary")
        rows = []
        with open(FIXTURE / "worker_ledger.jsonl", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        expected = trace.summarize(rows)["overall"]
        worker = self._snapshot(module)["pr_metrics"]["worker"]
        self.assertEqual(worker["runs"], expected["runs"])
        self.assertEqual(worker["pr_opened"], expected["pr_opened"])
        self.assertEqual(worker["pr_rate"], expected["pr_rate"])
        self.assertEqual(worker["tokens_out_total"],
                         expected["tokens_out_total"])
        self.assertEqual(worker["tokens_out_median"],
                         expected["tokens_out_median"])

    def test_shepherd_numbers_match_shepherd_ledger_summary(self):
        module = load_dashboard()
        shepherd = load_module(SHEPHERD_LEDGER, "plan_coder_shepherd_ledger")
        expected = shepherd.summarize(str(FIXTURE / "shepherd_ledger.jsonl"))
        metrics = self._snapshot(module)["pr_metrics"]["shepherd"]
        self.assertEqual(metrics["merges"], expected["merges"])
        self.assertEqual(metrics["unstable_merges"], expected["unstable_merges"])
        self.assertEqual(metrics["fix_attempt_distinct_prs"],
                         expected["fix_attempt_distinct_prs"])
        self.assertEqual(metrics["needs_operator_distinct_prs"],
                         expected["needs_operator_distinct_prs"])
        self.assertEqual(metrics["by_action"], expected["by_action"])

    def test_failure_classes_and_burn(self):
        module = load_dashboard()
        metrics = self._snapshot(module)["pr_metrics"]
        self.assertEqual(metrics["failure_classes"], {"empty-diff": 1})
        self.assertEqual(metrics["outcomes"],
                         {"failed:empty-diff": 1, "pr_opened": 1})
        burn = metrics["burn"]
        self.assertEqual(burn["max_cycle_tokens_out"], 40211)
        self.assertEqual(burn["cycle_budget"], 0)
        self.assertFalse(burn["budget_enabled"])


if __name__ == "__main__":
    unittest.main()
