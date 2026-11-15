"""Tests for the dashboard v2 panels (issue #60, Phase 1).

`bin/dashboard.py` grew a Pulse header (Panel 0), a Trends panel
(Panel 3, the 7d/30d/90d small multiples over the Phase 0 rollup), a
Funnel & dwell panel (Panel 4) and an experiment-registry stub
(Panel 5).  These tests drive the real entry point (`main()`) against
fixture state dirs inside this repository -- never the operator's real
state dir -- and check the properties the issue asks for:

  * Panels 1-2 keep the CLI's numbers (the aggregation owners are
    unchanged);
  * the new panels' numbers are the rollup's numbers (recomputed here
    from `metrics_rollup.jsonl`, independently of the dashboard);
  * a missing rollup degrades to the "Phase 0 rollup not found" note
    instead of crashing;
  * an absent experiment registry renders the "no experiments yet"
    note;
  * nothing in the state dir is written.

Run: python3 -m pytest tests/test_dashboard_v2.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import statistics
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "dashboard.py"
TRACE_SUMMARY = REPO / "bin" / "trace_summary.py"
SHEPHERD_LEDGER = REPO / "bin" / "shepherd_ledger.py"
FIXTURE = REPO / "tests" / "fixtures" / "dashboard_v2_state"
NO_ROLLUP = REPO / "tests" / "fixtures" / "dashboard_state"
EXAMPLES = REPO / "examples"
EXAMPLE_CONFIG = REPO / "config" / "deployment.example.json"
AS_OF = "2026-11-15"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_dashboard():
    return load_module(SCRIPT, "plan_coder_dashboard_v2")


def run_dashboard(module, argv):
    """Run main() and return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue()


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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


def snapshot_of(module, state_dir=FIXTURE, extra=()):
    code, out, _err = run_dashboard(module, [
        "--state-dir", str(state_dir), "--config", str(EXAMPLE_CONFIG),
        "--as-of", AS_OF, "--json", *extra])
    assert code == 0, f"dashboard exited {code}"
    return json.loads(out)


@unittest.skipUnless(SCRIPT.exists(), "bin/dashboard.py not present")
class DashboardV2PulseTest(unittest.TestCase):
    """Panel 0 -- the pulse header."""

    def test_pulse_panel_renders(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(FIXTURE), "--config", str(EXAMPLE_CONFIG),
            "--as-of", AS_OF])
        self.assertEqual(code, 0)
        self.assertIn("Panel 0 &mdash; Pulse", out)
        self.assertIn("Workers in flight", out)
        self.assertIn("Plan-window burn", out)
        self.assertIn("Needs-Alex queue", out)
        # the fixture's in-flight worker shows up with its elapsed time
        self.assertIn("repo-a-4", out)

    def test_pulse_last_cycle_times_come_from_the_ledgers(self):
        module = load_dashboard()
        pulse = snapshot_of(module)["pulse"]
        worker_rows = read_jsonl(FIXTURE / "worker_ledger.jsonl")
        shepherd_rows = read_jsonl(FIXTURE / "shepherd_ledger.jsonl")
        self.assertEqual(pulse["supervisor"]["last_worker_run"],
                         max(row["ts"] for row in worker_rows))
        self.assertEqual(pulse["supervisor"]["last_cycle"],
                         max(pulse["supervisor"]["last_worker_run"],
                             pulse["supervisor"]["last_trigger"]))
        self.assertEqual(pulse["shepherd"]["last_cycle"],
                         max(row["ts"] for row in shepherd_rows))
        self.assertEqual(pulse["supervisor"]["last_trigger"],
                         "2026-11-12T10:05:00Z")
        self.assertIsNotNone(pulse["supervisor"]["last_cycle_age_hours"])
        self.assertIsNotNone(pulse["shepherd"]["last_cycle_age_hours"])

    def test_pulse_in_flight_workers_carry_elapsed_time(self):
        module = load_dashboard()
        pulse = snapshot_of(module)["pulse"]
        self.assertEqual(pulse["workers"]["in_flight"], 1)
        row = pulse["workers"]["rows"][0]
        self.assertEqual(row["run_id"], "repo-a-4")
        self.assertEqual(row["status"], "working")
        self.assertIsNotNone(row["elapsed_hours"])
        self.assertGreater(row["elapsed_hours"], 0.0)

    def test_pulse_plan_window_burn(self):
        module = load_dashboard()
        burn = snapshot_of(module)["pulse"]["burn"]
        # the plan window is the supervisor's cycle (2h by default)
        self.assertEqual(burn["window_hours"], 2.0)
        self.assertTrue(burn["window_start"].endswith(":00:00Z"))
        self.assertIsInstance(burn["tokens_out"], int)
        self.assertGreaterEqual(burn["tokens_out"], 0)
        self.assertEqual(burn["cycle_budget"], 0)
        self.assertFalse(burn["budget_enabled"])

    def test_pulse_needs_alex_count_and_oldest_age(self):
        module = load_dashboard()
        needs = snapshot_of(module)["pulse"]["needs"]
        # PR 107 was flagged and never merged; PR 101 was flagged and
        # merged afterwards, so it is not waiting any more.
        self.assertEqual(needs["count"], 1)
        self.assertEqual(needs["queue"][0]["pr"], 107)
        self.assertIsNotNone(needs["oldest_age_hours"])
        self.assertGreater(needs["oldest_age_hours"], 0.0)


@unittest.skipUnless(SCRIPT.exists(), "bin/dashboard.py not present")
class DashboardV2TrendsTest(unittest.TestCase):
    """Panel 3 -- the 7d / 30d / 90d small multiples."""

    def _rollup(self) -> list[dict]:
        return read_jsonl(FIXTURE / "metrics_rollup.jsonl")

    def _expected_window(self, rows, start_day, end_day):
        """The window's numbers, recomputed from the rollup alone."""
        prs = [row for row in rows if row.get("type") == "pr"]
        daily = [row for row in rows if row.get("type") == "daily"
                 and row.get("scope") == "day_total"]
        opened = [row for row in prs
                  if start_day <= str(row.get("opened_day") or "") <= end_day]
        merged = [row for row in prs
                  if start_day <= str(row.get("merged_day") or "") <= end_day]
        cohort = [row for row in opened if row.get("merged")]
        days = [row for row in daily
                if start_day <= str(row.get("day") or "") <= end_day]
        tokens = sorted(row["tokens_out"] for row in merged
                        if row.get("tokens_out") is not None)
        opened_tokens = sum(row.get("tokens_out") or 0 for row in opened)
        wasted = sum(row.get("tokens_out") or 0 for row in opened
                     if not row.get("merged"))
        return {
            "opened_prs": len(opened),
            "merged_prs": len(merged),
            "merge_rate": (round(len(cohort) / len(opened), 3)
                           if opened else None),
            "days_with_data": len(days),
            "prs_per_day": (round(len(opened) / len(days), 2)
                            if days else None),
            "tokens_per_merged_pr_median": (statistics.median(tokens)
                                             if tokens else None),
            "tokens_per_merged_pr_p90": (tokens[min(len(tokens) - 1,
                                                      int(0.9 * len(tokens)))]
                                         if tokens else None),
            "wasted_token_share": (round(wasted / opened_tokens, 3)
                                   if opened_tokens else None),
        }

    def test_trends_panel_renders(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(FIXTURE), "--config", str(EXAMPLE_CONFIG),
            "--as-of", AS_OF])
        self.assertEqual(code, 0)
        self.assertIn("Panel 3 &mdash; Trends", out)
        for metric in ("PRs/day", "tokens per merged PR (median)",
                       "tokens per merged PR (p90)", "merge rate",
                       "wasted-token share", "slot occupancy",
                       "failure classes", "grade quartiles"):
            self.assertIn(metric, out)
        for size in ("7", "30", "90"):
            self.assertIn(f"Last {size} days", out)

    def test_trends_windows_end_on_as_of(self):
        module = load_dashboard()
        trends = snapshot_of(module)["trends"]
        self.assertEqual(trends["window_end"], AS_OF)
        self.assertEqual([w["days"] for w in trends["windows"]], [7, 30, 90])
        self.assertEqual(trends["windows"][0]["start_day"], "2026-11-09")
        self.assertEqual(trends["windows"][2]["start_day"], "2026-08-18")

    def test_trends_numbers_match_the_rollup(self):
        module = load_dashboard()
        trends = snapshot_of(module)["trends"]
        rows = self._rollup()
        for window in trends["windows"]:
            expected = self._expected_window(rows, window["start_day"],
                                             window["end_day"])
            for key, value in expected.items():
                self.assertEqual(window[key], value,
                                 f"{key} of the {window['days']}d window")

    def test_trends_experiment_annotations(self):
        module = load_dashboard()
        trends = snapshot_of(module)["trends"]
        seven = trends["windows"][0]
        names = [entry["name"] for entry in seven["experiments"]]
        self.assertIn("tier-ladder", names)
        self.assertIn("empty-diff-gate", names)
        # the annotations show up on the page too
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(FIXTURE), "--config", str(EXAMPLE_CONFIG),
            "--as-of", AS_OF])
        self.assertEqual(code, 0)
        self.assertIn("experiment-registry changes", out)
        self.assertIn("empty-diff-gate", out)

    def test_trends_slot_occupancy_uses_the_configured_slots(self):
        module = load_dashboard()
        trends = snapshot_of(module)["trends"]
        for window in trends["windows"]:
            self.assertEqual(window["max_slots"], 6)
            self.assertIsNotNone(window["slot_occupancy"])


@unittest.skipUnless(SCRIPT.exists(), "bin/dashboard.py not present")
class DashboardV2FunnelTest(unittest.TestCase):
    """Panel 4 -- the stage funnel, the dwell and the Needs-Alex queue."""

    def _pr_rows(self) -> list[dict]:
        return [row for row in read_jsonl(FIXTURE / "metrics_rollup.jsonl")
                if row.get("type") == "pr"]

    def test_funnel_panel_renders(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(FIXTURE), "--config", str(EXAMPLE_CONFIG),
            "--as-of", AS_OF])
        self.assertEqual(code, 0)
        self.assertIn("Panel 4 &mdash; Funnel &amp; dwell", out)
        self.assertIn("conversion", out)
        self.assertIn("median dwell", out)
        self.assertIn("Needs-Alex queue", out)

    def test_funnel_counts_match_the_rollup(self):
        module = load_dashboard()
        funnel = snapshot_of(module)["funnel"]
        opened = [row for row in self._pr_rows() if row.get("opened")]
        stages = {stage["name"]: stage for stage in funnel["stages"]}
        self.assertEqual(stages["PR opened"]["count"], len(opened))
        self.assertEqual(
            stages["shepherd seen"]["count"],
            sum(1 for row in opened if row.get("shepherd_actions")))
        self.assertEqual(
            stages["flagged for the operator"]["count"],
            sum(1 for row in opened
                if "needs_alex" in (row.get("flags") or [])
                or "needs_operator" in (row.get("flags") or [])))
        self.assertEqual(
            stages["merged"]["count"],
            sum(1 for row in opened if row.get("merged")))
        for stage in funnel["stages"]:
            expected = (round(stage["count"] / len(opened), 3)
                        if opened else None)
            self.assertEqual(stage["conversion"], expected)

    def test_funnel_dwell_matches_the_rollup(self):
        module = load_dashboard()
        funnel = snapshot_of(module)["funnel"]
        opened = [row for row in self._pr_rows() if row.get("opened")]
        stages = {stage["name"]: stage for stage in funnel["stages"]}
        dwell_merge = sorted(row["dwell_open_to_merge_hours"]
                             for row in opened
                             if row.get("dwell_open_to_merge_hours")
                             is not None)
        self.assertEqual(stages["merged"]["dwell_hours"],
                         statistics.median(dwell_merge))
        dwell_action = sorted(row["dwell_open_to_first_action_hours"]
                              for row in opened
                              if row.get("dwell_open_to_first_action_hours")
                              is not None)
        self.assertEqual(stages["shepherd seen"]["dwell_hours"],
                         statistics.median(dwell_action))

    def test_needs_alex_queue_has_a_per_pr_age(self):
        module = load_dashboard()
        funnel = snapshot_of(module)["funnel"]
        self.assertEqual(funnel["needs_count"], 1)
        entry = funnel["queue"][0]
        self.assertEqual(entry["pr"], 107)
        self.assertEqual(entry["repo"], "repo-a")
        self.assertEqual(entry["flag_action"], "needs_alex")
        self.assertIsNotNone(entry["age_hours"])
        self.assertGreater(entry["age_hours"], 0.0)


@unittest.skipUnless(SCRIPT.exists(), "bin/dashboard.py not present")
class DashboardV2ExperimentsTest(unittest.TestCase):
    """Panel 5 -- the experiment registry stub."""

    def test_registry_renders(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(FIXTURE), "--config", str(EXAMPLE_CONFIG),
            "--as-of", AS_OF])
        self.assertEqual(code, 0)
        self.assertIn("Panel 5 &mdash; Experiment registry", out)
        self.assertIn("tier-ladder", out)
        self.assertIn("empty-diff-gate", out)
        experiments = snapshot_of(module)["experiments"]
        self.assertTrue(experiments["present"])
        self.assertEqual(experiments["count"], 2)

    def test_absent_registry_renders_the_note(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(NO_ROLLUP), "--config", str(EXAMPLE_CONFIG),
            "--as-of", AS_OF])
        self.assertEqual(code, 0)
        self.assertIn("no experiments yet", out)


@unittest.skipUnless(SCRIPT.exists(), "bin/dashboard.py not present")
class DashboardV2DegradationTest(unittest.TestCase):
    """A missing rollup degrades to a note -- it never crashes."""

    def test_missing_rollup_renders_the_note(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(NO_ROLLUP), "--config", str(EXAMPLE_CONFIG),
            "--as-of", AS_OF])
        self.assertEqual(code, 0)
        self.assertIn("Phase 0 rollup not found", out)
        self.assertIn("Panel 3 &mdash; Trends", out)
        self.assertIn("Panel 4 &mdash; Funnel &amp; dwell", out)

    def test_missing_rollup_still_renders_json(self):
        module = load_dashboard()
        snapshot = snapshot_of(module, state_dir=NO_ROLLUP)
        self.assertFalse(snapshot["trends"]["present"])
        self.assertFalse(snapshot["funnel"]["present"])
        # Panels 1 and 2 keep working without the rollup
        self.assertIn("worker", snapshot["pr_metrics"])

    def test_missing_state_dir_never_crashes(self):
        module = load_dashboard()
        code, out, _err = run_dashboard(module, [
            "--state-dir", str(REPO / "tests" / "fixtures" / "nope"),
            "--config", str(EXAMPLE_CONFIG)])
        self.assertEqual(code, 0)
        self.assertIn("Phase 0 rollup not found", out)
        self.assertIn("no rows yet", out)


@unittest.skipUnless(SCRIPT.exists(), "bin/dashboard.py not present")
class DashboardV2ReadOnlyTest(unittest.TestCase):
    """The new panels do not write to the state dir either."""

    def test_state_dir_is_untouched(self):
        module = load_dashboard()
        before = dir_fingerprint(FIXTURE)
        code, _out, _err = run_dashboard(module, [
            "--state-dir", str(FIXTURE), "--config", str(EXAMPLE_CONFIG),
            "--as-of", AS_OF])
        self.assertEqual(code, 0)
        self.assertEqual(dir_fingerprint(FIXTURE), before)


@unittest.skipUnless(SCRIPT.exists(), "bin/dashboard.py not present")
class DashboardV2SemanticsTest(unittest.TestCase):
    """Panels 1-2 keep the CLI's numbers; the page renders the examples."""

    def test_panels_1_and_2_still_match_the_clis(self):
        module = load_dashboard()
        trace = load_module(TRACE_SUMMARY, "plan_coder_trace_summary_v2")
        shepherd = load_module(SHEPHERD_LEDGER,
                               "plan_coder_shepherd_ledger_v2")
        worker_rows = read_jsonl(FIXTURE / "worker_ledger.jsonl")
        expected_worker = trace.summarize(worker_rows)["overall"]
        expected_shepherd = shepherd.summarize(
            str(FIXTURE / "shepherd_ledger.jsonl"))
        metrics = snapshot_of(module)["pr_metrics"]
        self.assertEqual(metrics["worker"]["runs"], expected_worker["runs"])
        self.assertEqual(metrics["worker"]["pr_opened"],
                         expected_worker["pr_opened"])
        self.assertEqual(metrics["worker"]["pr_rate"],
                         expected_worker["pr_rate"])
        self.assertEqual(metrics["worker"]["tokens_out_total"],
                         expected_worker["tokens_out_total"])
        self.assertEqual(metrics["shepherd"]["merges"],
                         expected_shepherd["merges"])
        self.assertEqual(metrics["shepherd"]["unstable_merges"],
                         expected_shepherd["unstable_merges"])
        self.assertEqual(metrics["shepherd"]["by_action"],
                         expected_shepherd["by_action"])

    def test_renders_against_the_examples_fixtures(self):
        """The examples/ ledger renders (the dashboard never crashes)."""
        module = load_dashboard()
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            shutil.copy(EXAMPLES / "worker_ledger.example.jsonl",
                        state / "worker_ledger.jsonl")
            shutil.copy(EXAMPLES / "workers.example.json",
                        state / "workers.json")
            code, out, _err = run_dashboard(module, [
                "--state-dir", str(state), "--config", str(EXAMPLE_CONFIG)])
            self.assertEqual(code, 0)
            self.assertIn("Panel 0 &mdash; Pulse", out)
            self.assertIn("Panel 2 &mdash; PR metrics", out)
            self.assertIn("Phase 0 rollup not found", out)


if __name__ == "__main__":
    unittest.main()
