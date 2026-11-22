"""Tests for bin/questions_census_phase0.py (issue #147 Part F2, Phase 0).

Phase 0 is measurement only: the census script counts shepherd flags /
Alex-facing questions over one window and prints the evidence.  These
tests check the *script's own* honesty:

  * the window really is the last N weeks of the ledger snapshot;
  * the flag / terminal / repeat definitions are applied as documented
    (a flag after a terminal disposition is NOT a repeat; a flag with no
    intervening terminal IS);
  * the ack numbers are reported as PROXIES, and neither proxy is
    silently merged into a single clean figure;
  * the Kill/Proceed read follows mechanically from the numbers;
  * the script runs end to end on a synthetic snapshot and emits valid
    JSON;
  * the report in docs/ carries the same tables and an explicit verdict.

The tests build their own small snapshots in a temporary directory, so
they do not depend on any live input snapshot being present.

Run: python3 -m pytest tests/test_questions_census_phase0.py -q
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "questions_census_phase0.py"
REPORT = REPO / "docs" / "andon-f2-questions-phase0-report.md"


def _load():
    spec = importlib.util.spec_from_file_location(
        "questions_census_phase0", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["questions_census_phase0"] = module
    spec.loader.exec_module(module)
    return module


census = _load()


def _row(ts, repo, pr, action, outcome="", note=""):
    return {"ts": ts, "repo": repo, "pr": pr, "pr_url": "", "action": action,
            "outcome": outcome, "ci": "", "merge_state": "", "detail": note}


def _write(path: Path, rows: list[dict]) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(row) + "\n" for row in rows)
    return str(path)


class WindowTests(unittest.TestCase):
    """The window is the last N weeks present in the snapshot."""

    def test_window_ends_at_the_newest_ledger_timestamp(self):
        rows = [
            {"ts": census.parse_ts("2026-11-01T00:00:00Z")},
            {"ts": census.parse_ts("2026-11-20T00:00:00Z")},
        ]
        start, end = census.window_of(rows, 4)
        self.assertEqual(end, census.parse_ts("2026-11-20T00:00:00Z"))
        self.assertEqual(start, census.parse_ts("2026-10-23T00:00:00Z"))

    def test_window_is_four_weeks_by_default(self):
        self.assertEqual(census.DEFAULT_WEEKS, 4)

    def test_timestamps_parse_with_and_without_a_zone(self):
        self.assertIsNotNone(census.parse_ts("2026-11-09T04:59:56Z"))
        self.assertIsNotNone(census.parse_ts("2026-11-17T18:46:15-05:00"))
        self.assertIsNone(census.parse_ts(""))
        self.assertIsNone(census.parse_ts("not-a-timestamp"))


class VocabularyTests(unittest.TestCase):
    """The census prints the ledger's own action vocabulary."""

    def test_every_action_is_printed_with_its_row_count(self):
        rows = [
            {"ts": census.parse_ts("2026-11-10T00:00:00Z"), "repo": "r/x",
             "slug": "x", "pr": 1, "action": "needs_alex", "outcome": "",
             "detail": "", "line": 1},
            {"ts": census.parse_ts("2026-11-10T01:00:00Z"), "repo": "r/x",
             "slug": "x", "pr": 1, "action": "needs_alex", "outcome": "",
             "detail": "", "line": 2},
            {"ts": census.parse_ts("2026-11-10T02:00:00Z"), "repo": "r/x",
             "slug": "x", "pr": 1, "action": "merged", "outcome": "",
             "detail": "", "line": 3},
        ]
        vocabulary = census.action_vocabulary(rows)
        self.assertEqual([v["action"] for v in vocabulary],
                         ["needs_alex", "merged"])
        self.assertEqual([v["rows"] for v in vocabulary], [2, 1])
        self.assertEqual(vocabulary[0]["role"], "flag")
        self.assertEqual(vocabulary[1]["role"], "terminal")

    def test_the_flag_and_terminal_vocabularies_are_the_documented_ones(self):
        self.assertEqual(census.FLAG_ACTIONS, ("needs_alex", "needs_operator"))
        self.assertEqual(census.TERMINAL_ACTIONS,
                         ("merged", "merged_by_shepherd", "closed_unmerged"))

    def test_the_issue_wording_maps_onto_the_ledgers_own_spellings(self):
        mapping = census.TERMINAL_MAPPING
        self.assertEqual(set(mapping.values()), {"merge", "closed",
                                                 "merged_by_shepherd"})
        for action in census.TERMINAL_ACTIONS:
            self.assertIn(action, mapping)


class RepeatDefinitionTests(unittest.TestCase):
    """A repeat is a flag with no intervening terminal disposition."""

    def _classify(self, rows):
        prepared = []
        for index, row in enumerate(rows):
            prepared.append({
                "ts": census.parse_ts(row["ts"]),
                "repo": row["repo"],
                "slug": census.repo_slug(row["repo"]),
                "pr": row["pr"],
                "action": row["action"],
                "outcome": row["outcome"],
                "detail": "",
                "line": index + 1,
            })
        prepared.sort(key=lambda r: (r["ts"], r["line"]))
        return census.classify_flags(prepared)["flags"]

    def test_a_second_flag_with_no_terminal_in_between_is_a_repeat(self):
        flags = self._classify([
            _row("2026-11-10T00:00:00Z", "r/x", 1, "needs_alex", "ask"),
            _row("2026-11-10T01:00:00Z", "r/x", 1, "needs_alex", "ask"),
        ])
        self.assertEqual(len(flags), 2)
        self.assertFalse(flags[0]["repeat"])
        self.assertTrue(flags[1]["repeat"])

    def test_a_flag_after_a_terminal_disposition_is_not_a_repeat(self):
        flags = self._classify([
            _row("2026-11-10T00:00:00Z", "r/x", 1, "needs_alex", "ask"),
            _row("2026-11-10T01:00:00Z", "r/x", 1, "merged", "merged"),
            _row("2026-11-10T02:00:00Z", "r/x", 1, "needs_alex", "ask"),
        ])
        self.assertEqual([f["repeat"] for f in flags], [False, False])

    def test_a_flag_on_another_pr_is_not_a_repeat(self):
        flags = self._classify([
            _row("2026-11-10T00:00:00Z", "r/x", 1, "needs_alex", "ask"),
            _row("2026-11-10T01:00:00Z", "r/x", 2, "needs_alex", "ask"),
        ])
        self.assertEqual([f["repeat"] for f in flags], [False, False])

    def test_repeat_text_is_split_into_same_and_different_outcome(self):
        flags = self._classify([
            _row("2026-11-10T00:00:00Z", "r/x", 1, "needs_alex", "ask A"),
            _row("2026-11-10T01:00:00Z", "r/x", 1, "needs_alex", "ask A"),
            _row("2026-11-10T02:00:00Z", "r/x", 1, "needs_alex", "ask B"),
        ])
        self.assertEqual([f["same_text_as_open"] for f in flags],
                         [False, True, False])

    def test_only_flag_actions_produce_flags(self):
        flags = self._classify([
            _row("2026-11-10T00:00:00Z", "r/x", 1, "rerun"),
            _row("2026-11-10T01:00:00Z", "r/x", 1, "merge_conflicts"),
            _row("2026-11-10T02:00:00Z", "r/x", 1, "needs_operator", "op"),
        ])
        self.assertEqual([f["action"] for f in flags], ["needs_operator"])


class AckProxyTests(unittest.TestCase):
    """The ack number is a proxy, and the proxies stay separate."""

    def _payload(self, tmpdir, ledger_rows, acted_rows):
        ledger_path = _write(Path(tmpdir) / "ledger.jsonl", ledger_rows)
        acted_path = _write(Path(tmpdir) / "acted.jsonl", acted_rows)
        payload = census.build_census(census.load_ledger(ledger_path),
                                       census.load_acted_on(acted_path))
        return payload

    def test_proxy_a_counts_a_terminal_disposition_after_the_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = self._payload(tmpdir, [
                _row("2026-11-10T00:00:00Z", "r/x", 1, "needs_alex", "ask"),
                _row("2026-11-10T05:00:00Z", "r/x", 1, "merged", "merged"),
                _row("2026-11-10T06:00:00Z", "r/y", 2, "needs_alex", "ask"),
            ], [])
            proxy_a = payload["ack_proxy_a_terminal"]
            self.assertEqual(proxy_a["flags_with_later_terminal"], 1)
            self.assertEqual(proxy_a["flags_without_later_terminal"], 1)
            self.assertEqual(proxy_a["prs_resolved"], 1)
            self.assertEqual(proxy_a["prs_still_open"], 1)
            self.assertEqual(proxy_a["pr_level_pct"], 50.0)

    def test_a_terminal_before_the_flag_does_not_count_as_an_ack(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = self._payload(tmpdir, [
                _row("2026-11-10T00:00:00Z", "r/x", 1, "merged", "merged"),
                _row("2026-11-10T01:00:00Z", "r/x", 1, "needs_alex", "ask"),
            ], [])
            proxy_a = payload["ack_proxy_a_terminal"]
            self.assertEqual(proxy_a["flags_with_later_terminal"], 0)
            self.assertEqual(proxy_a["prs_still_open"], 1)

    def test_proxy_b_matches_an_acted_on_entry_that_names_the_pr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = self._payload(tmpdir, [
                _row("2026-11-10T00:00:00Z", "anchapin/matchline", 1410,
                     "needs_alex", "ask"),
                _row("2026-11-12T00:00:00Z", "anchapin/matchline", 1411,
                     "checked"),
            ], [
                {"ts": "2026-11-11T00:00:00-05:00", "loop": "pr-shepherd",
                 "item": "matchline#1410 merge tap", "outcome": "acted",
                 "note": ""},
            ])
            proxy_b = payload["ack_proxy_b_acted_on"]
            self.assertEqual(proxy_b["entries_matching_a_flagged_pr"], 1)
            self.assertEqual(proxy_b["flagged_prs_named_by_an_acted_on_entry"],
                             1)

    def test_proxy_b_ignores_an_entry_that_predates_the_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = self._payload(tmpdir, [
                _row("2026-11-10T00:00:00Z", "anchapin/matchline", 1410,
                     "needs_alex", "ask"),
                _row("2026-11-12T00:00:00Z", "anchapin/matchline", 1411,
                     "checked"),
            ], [
                {"ts": "2026-11-09T00:00:00-05:00", "loop": "pr-shepherd",
                 "item": "matchline#1410 merge tap", "outcome": "acted",
                 "note": ""},
            ])
            self.assertEqual(
                payload["ack_proxy_b_acted_on"]["entries_matching_a_flagged_pr"],
                0)

    def test_proxy_b_does_not_invent_a_match_from_an_unrelated_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = self._payload(tmpdir, [
                _row("2026-11-10T00:00:00Z", "anchapin/matchline", 1410,
                     "needs_alex", "ask"),
                _row("2026-11-12T00:00:00Z", "anchapin/matchline", 1411,
                     "checked"),
            ], [
                {"ts": "2026-11-11T00:00:00-05:00", "loop": "weekly",
                 "item": "Merge all merge-ready stale PRs", "outcome": "acted",
                 "note": ""},
            ])
            self.assertEqual(
                payload["ack_proxy_b_acted_on"]["entries_matching_a_flagged_pr"],
                0)

    def test_the_two_proxies_are_reported_separately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = self._payload(tmpdir, [
                _row("2026-11-10T00:00:00Z", "anchapin/matchline", 1410,
                     "needs_alex", "ask"),
                _row("2026-11-10T05:00:00Z", "anchapin/matchline", 1410,
                     "merged", "merged"),
            ], [
                {"ts": "2026-11-11T00:00:00-05:00", "loop": "pr-shepherd",
                 "item": "matchline#1410 merge tap", "outcome": "acted",
                 "note": ""},
            ])
            self.assertIn("ack_proxy_a_terminal", payload)
            self.assertIn("ack_proxy_b_acted_on", payload)
            self.assertIn("union", payload)
            self.assertEqual(payload["union"]["flagged_prs"], 1)

    def test_the_ack_note_says_the_data_is_partial(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = self._payload(tmpdir, [
                _row("2026-11-10T00:00:00Z", "r/x", 1, "needs_alex", "ask"),
            ], [])
            self.assertIn("partial", payload["definitions"]["ack_note"])


class DecisionTests(unittest.TestCase):
    """The Kill/Proceed read follows mechanically from the numbers."""

    def _payload(self, repeat_rate, terminal_rate, median_hours):
        return {
            "repeats": {"rate_pct": repeat_rate},
            "ack_proxy_a_terminal": {
                "pr_level_pct": terminal_rate,
                "hours_first_flag_to_terminal": {"median": median_hours},
            },
        }

    def test_frequent_repeats_proceed(self):
        decision = census.read_decision(self._payload(93.8, 96.2, 5.0))
        self.assertTrue(decision.startswith("PROCEED F2"), decision)

    def test_rare_repeats_and_prompt_terminations_kill(self):
        decision = census.read_decision(self._payload(2.0, 99.0, 3.0))
        self.assertTrue(decision.startswith("KILL F2"), decision)

    def test_rare_repeats_but_slow_terminations_proceed(self):
        decision = census.read_decision(self._payload(2.0, 99.0, 48.0))
        self.assertTrue(decision.startswith("PROCEED F2"), decision)

    def test_the_decision_states_the_numbers_it_used(self):
        decision = census.read_decision(self._payload(93.8, 96.2, 5.0))
        self.assertIn("93.8", decision)
        self.assertIn("96.2", decision)


class ScriptTests(unittest.TestCase):
    """The script runs end to end and writes nothing."""

    def test_the_script_runs_on_a_snapshot_and_prints_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = _write(Path(tmpdir) / "ledger.jsonl", [
                _row("2026-11-10T00:00:00Z", "anchapin/matchline", 1410,
                     "needs_alex", "ask"),
                _row("2026-11-10T01:00:00Z", "anchapin/matchline", 1410,
                     "needs_alex", "ask"),
                _row("2026-11-10T05:00:00Z", "anchapin/matchline", 1410,
                     "merged", "merged"),
            ])
            acted = _write(Path(tmpdir) / "acted.jsonl", [
                {"ts": "2026-11-11T00:00:00-05:00", "loop": "pr-shepherd",
                 "item": "matchline#1410 merge tap", "outcome": "acted",
                 "note": ""},
            ])
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--ledger", ledger,
                 "--acted-on", acted],
                capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Table 1", result.stdout)
            self.assertIn("Table 3", result.stdout)
            self.assertIn("Verdict", result.stdout)
            # a repeat with no intervening terminal disposition is not rare,
            # so the read must be Proceed.
            self.assertIn("PROCEED F2", result.stdout)

    def test_the_json_payload_is_valid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = _write(Path(tmpdir) / "ledger.jsonl", [
                _row("2026-11-10T00:00:00Z", "anchapin/matchline", 1410,
                     "needs_alex", "ask"),
            ])
            acted = _write(Path(tmpdir) / "acted.jsonl", [])
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--ledger", ledger,
                 "--acted-on", acted, "--json"],
                capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["flags"]["total"], 1)
            self.assertIn("decision", payload)

    def test_a_missing_input_exits_non_zero(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--ledger", "/nonexistent.jsonl",
             "--acted-on", "/nonexistent.jsonl"],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read input", result.stderr)

    def test_the_script_writes_nothing_next_to_its_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = _write(Path(tmpdir) / "ledger.jsonl", [
                _row("2026-11-10T00:00:00Z", "anchapin/matchline", 1410,
                     "needs_alex", "ask"),
            ])
            acted = _write(Path(tmpdir) / "acted.jsonl", [])
            before = sorted(p.name for p in Path(tmpdir).iterdir())
            subprocess.run(
                [sys.executable, str(SCRIPT), "--ledger", ledger,
                 "--acted-on", acted],
                capture_output=True, text=True, check=False)
            after = sorted(p.name for p in Path(tmpdir).iterdir())
            self.assertEqual(before, after)


class ReportTests(unittest.TestCase):
    """The report carries the tables, the proxy definitions and a verdict."""

    @classmethod
    def setUpClass(cls):
        cls.text = REPORT.read_text(encoding="utf-8")

    def test_the_report_exists(self):
        self.assertTrue(REPORT.exists(), str(REPORT))

    def test_the_report_leads_with_the_raw_tables(self):
        first_table = self.text.index("Table 1")
        verdict = self.text.index("## Verdict")
        self.assertLess(first_table, verdict)
        self.assertLess(self.text.index("Table 2"), self.text.index("Table 3"))

    def test_the_report_carries_every_census_table(self):
        for number in range(1, 9):
            self.assertIn(f"Table {number}", self.text)

    def test_the_report_states_the_ack_proxy_definitions(self):
        self.assertIn("proxy A", self.text)
        self.assertIn("proxy B", self.text)
        self.assertIn("partial", self.text)

    def test_the_report_states_an_explicit_verdict(self):
        self.assertRegex(self.text, r"(PROCEED F2|KILL F2)")

    def test_the_report_ties_the_verdict_to_the_kill_criterion(self):
        self.assertIn("kill criterion", self.text.lower())

    def test_the_reported_repeat_rate_is_the_arithmetic_of_the_reported_rows(self):
        match = re.search(
            r"\*\*(\d+)\*\* of \*\*(\d+)\*\* flag rows \(\*\*([\d.]+)%\*\*\)",
            self.text)
        self.assertIsNotNone(match, "repeat headline not found in the report")
        repeats, flags, rate = (int(match.group(1)), int(match.group(2)),
                                 float(match.group(3)))
        self.assertAlmostEqual(rate, round(100.0 * repeats / flags, 1),
                               places=1)


if __name__ == "__main__":
    unittest.main()
