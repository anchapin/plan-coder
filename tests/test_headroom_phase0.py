"""Tests for bin/headroom_phase0.py (issue #22, Phase 0).

The Phase-0 script measures the tool-output share of worker transcripts
and decides Kill vs Phase 1.  Both the measurement and the gate decide
what the report says, so they are pinned here with synthetic fixtures:
no transcript of the live corpus is read and nothing is written outside
the temporary directory.

Run: python3 -m pytest tests/test_headroom_phase0.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"

_spec = importlib.util.spec_from_file_location(
    "headroom_phase0", BIN / "headroom_phase0.py")
headroom = importlib.util.module_from_spec(_spec)
# dataclasses resolve their module through sys.modules, so register first.
sys.modules["headroom_phase0"] = headroom
_spec.loader.exec_module(headroom)


def write_transcript(path, run_id, steps, events=("started", "finished")):
    """Write a synthetic transcript jsonl and return its path."""
    with open(path, "w", encoding="utf-8") as handle:
        if "started" in events:
            handle.write(json.dumps({
                "event": "started", "run_id": run_id, "tier": "cheap"}) + "\n")
        for step in steps:
            handle.write(json.dumps(step) + "\n")
        if "finished" in events:
            handle.write(json.dumps({
                "event": "finished", "steps": len(steps),
                "total_in": sum(s.get("tokens_in", 0) for s in steps),
                "total_out": sum(s.get("tokens_out", 0) for s in steps)}) + "\n")
    return str(path)


def make_step(index, tokens_in, tokens_out, calls, results, kinds=None,
              ts=None):
    """Build one synthetic step record."""
    step = {
        "step": index,
        "provider": "test/model",
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "assistant_text": "",
        "tool_calls": [{"name": name, "args": "{}"} for name in calls],
        "tool_results": list(results),
    }
    if kinds is not None:
        step["tool_result_kinds"] = list(kinds)
    if ts is not None:
        step["ts"] = ts
    return step


class ToolKindTests(unittest.TestCase):
    """Tool names map onto the four measured kinds."""

    def test_known_tools(self):
        self.assertEqual(headroom.tool_kind("exec"), "exec")
        self.assertEqual(headroom.tool_kind("Bash"), "exec")
        self.assertEqual(headroom.tool_kind("read_file"), "read_file")
        self.assertEqual(headroom.tool_kind("list_dir"), "list_dir")

    def test_everything_else_is_other(self):
        self.assertEqual(headroom.tool_kind("edit_file"), "other")
        self.assertEqual(headroom.tool_kind("write_file"), "other")
        self.assertEqual(headroom.tool_kind(""), "other")


class RunIdTests(unittest.TestCase):
    """Repo / issue / flavour extraction from a supervisor run id."""

    def test_known_repo(self):
        self.assertEqual(headroom.parse_run_id("fluxion-4744-1794427226"),
                         ("fluxion", 4744, None))

    def test_flavour(self):
        self.assertEqual(
            headroom.parse_run_id("matchline-goldfish-1324-1794202010"),
            ("matchline", 1324, "goldfish"))

    def test_unknown_repo(self):
        self.assertIsNone(headroom.parse_run_id("run-20261112-175239"))


class PercentileTests(unittest.TestCase):
    """Nearest-rank percentiles."""

    def test_median_and_percentiles(self):
        values = [float(v) for v in range(1, 11)]
        self.assertEqual(headroom.percentile(values, 90), 9.0)
        self.assertEqual(headroom.percentile(values, 99), 10.0)
        self.assertEqual(headroom.median(values), 5.5)

    def test_empty(self):
        self.assertIsNone(headroom.percentile([], 90))
        self.assertIsNone(headroom.median([]))


class MeasurementTests(unittest.TestCase):
    """Per-run measurement of the issue #22 section 3 fields."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_measurement_fields(self):
        path = os.path.join(self.tmp.name, "fluxion-1-1700000000.jsonl")
        write_transcript(path, "fluxion-1-1700000000", [
            make_step(1, 1000, 10, ["exec"], ["x" * 400], ["ok"]),
            make_step(2, 2000, 20, ["read_file", "list_dir"],
                      ["y" * 200, "z" * 100], ["ok", "ok"]),
        ])
        run = headroom.load_run(path)
        row = headroom.measure_run(run)
        self.assertEqual(row["steps"], 2)
        self.assertEqual(row["final_tokens_in"], 2000)
        self.assertEqual(row["tokens_out"], 30)
        self.assertEqual(row["end_to_end_tokens_in"], 3000)
        self.assertEqual(row["chars_exec"], 400)
        self.assertEqual(row["chars_read_file"], 200)
        self.assertEqual(row["chars_list_dir"], 100)
        self.assertEqual(row["chars_other"], 0)
        self.assertEqual(row["tool_chars_total"], 700)
        self.assertAlmostEqual(row["tool_tokens"], 175.0)
        # 175 tool tokens out of a 2000 token final context.
        self.assertAlmostEqual(row["share_final_pct"], 8.75)
        self.assertEqual(row["trunc_hits"], 0)
        self.assertEqual(row["top_tool"], "exec")

    def test_truncation_markers_are_counted(self):
        path = os.path.join(self.tmp.name, "fluxion-2-1700000001.jsonl")
        write_transcript(path, "fluxion-2-1700000001", [
            make_step(1, 1000, 10, ["exec"],
                      ["out ...[truncated] more ...[truncated]"], ["ok"]),
        ])
        row = headroom.measure_run(headroom.load_run(path))
        self.assertEqual(row["trunc_hits"], 2)

    def test_event_records_are_not_steps(self):
        path = os.path.join(self.tmp.name, "fluxion-3-1700000002.jsonl")
        write_transcript(path, "fluxion-3-1700000002", [
            make_step(1, 1000, 10, ["exec"], ["a" * 100], ["ok"]),
        ])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "event": "aborted", "step": 2, "error": "model call failed"}) + "\n")
        row = headroom.measure_run(headroom.load_run(path))
        self.assertEqual(row["steps"], 1)
        self.assertEqual(row["final_tokens_in"], 1000)

    def test_missing_typed_kinds_is_reported_not_inferred(self):
        path = os.path.join(self.tmp.name, "fluxion-4-1700000003.jsonl")
        write_transcript(path, "fluxion-4-1700000003", [
            make_step(1, 1000, 10, ["exec"], ["a" * 100]),
        ])
        row = headroom.measure_run(headroom.load_run(path))
        self.assertEqual(row["typed_steps"], 0)
        self.assertEqual(row["tool_chars_total"], 100)

    def test_ceiling_saving_is_bounded_by_the_prompt(self):
        path = os.path.join(self.tmp.name, "fluxion-5-1700000004.jsonl")
        write_transcript(path, "fluxion-5-1700000004", [
            make_step(1, 1000, 10, ["exec"], ["a" * 4000], ["ok"]),
            make_step(2, 1000, 10, ["exec"], ["b" * 4000], ["ok"]),
        ])
        row = headroom.measure_run(headroom.load_run(path))
        # Step 1 sees no tool output yet; step 2 sees 1000 tool tokens,
        # so 50% compression can save at most 500 tokens there.
        self.assertAlmostEqual(row["saving_50_tokens"], 500.0)
        self.assertAlmostEqual(row["saving_80_tokens"], 800.0)
        self.assertAlmostEqual(row["saving_50_pct"], 500.0 / 2000 * 100)


class WindowTests(unittest.TestCase):
    """The 14-day window is applied on file mtime."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, name, mtime):
        path = os.path.join(self.tmp.name, name)
        write_transcript(path, "fluxion-1-1700000000", [
            make_step(1, 1000, 10, ["exec"], ["a" * 40], ["ok"])])
        stamp = datetime.fromtimestamp(mtime, timezone.utc).timestamp()
        os.utime(path, (stamp, stamp))
        return headroom.load_run(path)

    def test_only_recent_files_are_selected(self):
        now = datetime(2026, 11, 14, tzinfo=timezone.utc)
        old = self._run("fluxion-1-1700000000.jsonl", (now - timedelta(days=30)).timestamp())
        new = self._run("fluxion-2-1700000001.jsonl", (now - timedelta(days=2)).timestamp())
        selected, start, end = headroom.select_window(
            [old, new], 14, as_of=now)
        self.assertEqual([run.file for run in selected], [new.file])
        self.assertEqual(end, now)
        self.assertEqual(start, now - timedelta(days=14))

    def test_window_end_defaults_to_newest_mtime(self):
        now = datetime(2026, 11, 14, tzinfo=timezone.utc)
        old = self._run("fluxion-1-1700000000.jsonl", (now - timedelta(days=30)).timestamp())
        new = self._run("fluxion-2-1700000001.jsonl", (now - timedelta(days=2)).timestamp())
        selected, _start, end = headroom.select_window([old, new], 14)
        self.assertEqual(end, new.mtime)


def row(repo="fluxion", share=5.0, tool_tokens=1000.0, e2e=10000,
        final=10000, steps=10, trunc=0, typed=10, ts=10, run_id="fluxion-1-1"):
    """Build a synthetic measurement row."""
    return {
        "file": run_id + ".jsonl", "run_id": run_id, "repo": repo,
        "issue": 1, "flavour": None, "mtime": "2026-11-13T00:00:00+00:00",
        "steps": steps, "reported_steps": steps, "final_tokens_in": final,
        "end_to_end_tokens_in": e2e, "tokens_out": 100,
        "total_in_event": e2e, "total_out_event": 100,
        "chars_exec": tool_tokens * 4, "chars_read_file": 0,
        "chars_list_dir": 0, "chars_other": 0,
        "tool_chars_total": tool_tokens * 4, "tool_tokens": tool_tokens,
        "share_final_pct": share, "trunc_hits": trunc, "top_tool": "exec",
        "typed_steps": typed, "ts_steps": ts,
        "saving_50_tokens": 100.0, "saving_80_tokens": 160.0,
        "saving_50_pct": 1.0, "saving_80_pct": 1.6,
    }


def build(rows, days=14):
    """Run build_report over synthetic rows."""
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    return headroom.build_report(
        rows=rows, window_start=now - timedelta(days=days), window_end=now,
        corpus_files=len(rows), corpus_steps=sum(r["steps"] for r in rows),
        verification_note="", days=days)


class GateTests(unittest.TestCase):
    """The Kill / Phase-1-eligible gate."""

    def test_kill_when_median_below_ten_percent(self):
        rows = [row(share=2.0, run_id=f"fluxion-{i}-1") for i in range(10)]
        payload = build(rows)
        self.assertEqual(payload["verdict"]["verdict"], "KILL")
        self.assertAlmostEqual(payload["verdict"]["median_share_pct"], 2.0)

    def test_eligible_when_median_at_or_above_ten_percent(self):
        rows = [row(share=12.0, run_id=f"fluxion-{i}-1") for i in range(10)]
        payload = build(rows)
        self.assertEqual(payload["verdict"]["verdict"], "PHASE-1-ELIGIBLE")

    def test_tail_population_keeps_a_low_median_eligible(self):
        rows = [row(share=1.0, run_id=f"fluxion-{i}-1") for i in range(89)]
        rows.extend(row(share=25.0, repo="matchline",
                        run_id=f"matchline-{i}-1") for i in range(11))
        payload = build(rows)
        self.assertEqual(payload["verdict"]["verdict"], "PHASE-1-ELIGIBLE")
        self.assertAlmostEqual(payload["verdict"]["median_share_pct"], 1.0)
        self.assertGreaterEqual(payload["verdict"]["p90_share_pct"], 20.0)
        self.assertEqual(payload["verdict"]["tail_runs"], 11)
        self.assertIn(("matchline", 11), payload["verdict"]["tail_repos"])

    def test_kill_when_median_low_and_no_tail(self):
        rows = [row(share=1.0, run_id=f"fluxion-{i}-1") for i in range(10)]
        payload = build(rows)
        self.assertEqual(payload["verdict"]["verdict"], "KILL")
        self.assertEqual(payload["verdict"]["tail_runs"], 0)

    def test_missing_fields_are_counted(self):
        rows = [row(typed=0, ts=0, steps=0, final=0, share=None,
                    run_id="run-20261112-1")]
        payload = build(rows)
        quality = payload["data_quality"]
        self.assertEqual(quality["runs_without_typed_kinds"], 1)
        self.assertEqual(quality["runs_without_any_ts"], 1)
        self.assertEqual(quality["runs_without_steps"], 1)
        self.assertEqual(len(payload["incomplete_rows"]), 1)


class EndToEndTests(unittest.TestCase):
    """The script runs over a corpus and writes report + data files."""

    def test_script_writes_report_and_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "transcripts")
            os.makedirs(corpus)
            write_transcript(
                os.path.join(corpus, "fluxion-1-1700000000.jsonl"),
                "fluxion-1-1700000000",
                [make_step(1, 1000, 10, ["exec"], ["a" * 400], ["ok"]),
                 make_step(2, 2000, 20, ["read_file"], ["b" * 200], ["ok"])])
            out = os.path.join(tmp, "docs", "spikes", "headroom-phase0.md")
            code = headroom.main([
                "--transcripts-dir", corpus, "--out", out, "--days", "14"])
            self.assertEqual(code, 0)
            report = Path(out).read_text(encoding="utf-8")
            self.assertIn("## Gate verdict", report)
            self.assertIn("Verdict:", report)
            self.assertIn("## Table 1 -- per-run raw table", report)
            self.assertIn("fluxion-1-1700000000", report)
            csv_path = Path(out).with_name("headroom-phase0-runs.csv")
            json_path = Path(out).with_name("headroom-phase0-runs.json")
            self.assertTrue(csv_path.exists())
            self.assertTrue(json_path.exists())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["overall"]["runs"], 1)

    def test_missing_corpus_exits_two(self):
        self.assertEqual(
            headroom.main(["--transcripts-dir", "/nonexistent/corpus"]), 2)


if __name__ == "__main__":
    unittest.main()
