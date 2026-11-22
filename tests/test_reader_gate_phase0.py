"""Tests for bin/reader_gate_phase0.py (issue #152 Phase 0).

Phase 0 is measurement only: the script classifies and scores, it
changes nothing.  These tests pin down the *definitions* the census
depends on and the gate arithmetic taken from the issue:

  * Part A -- a premise is accurate / incomplete / corrupted according
    to what the RE-READ of its cited sources shows, and the tier comes
    from the ledger row the premise joins to;
  * Part A gate -- PROCEED at divergence >= 5% on acted-on premises or
    on any corrupted premise reaching a merge decision, KILL below 2%
    with zero corrupted-to-decision, HOLD in between;
  * Part B -- every dispatch lands in exactly one documented class, and
    the shared-evidence gate only fires on a measurable, compute-matched
    win over the nearest single-agent comparator;
  * the script runs end to end on the committed fixtures and the report
    in docs/ carries the same tables and verdicts.

The fixtures are committed under tests/fixtures/reader_gate/; the real
operator state dir is outside this checkout and is never read here.

Run: python3 -m pytest tests/test_reader_gate_phase0.py -q
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "reader_gate_phase0.py"
REPORT = REPO / "docs" / "reader-gate-phase0-report.md"
FIXTURES = REPO / "tests" / "fixtures" / "reader_gate"


def _load():
    spec = importlib.util.spec_from_file_location(
        "reader_gate_phase0", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["reader_gate_phase0"] = module
    spec.loader.exec_module(module)
    return module


census = _load()


def _write_jsonl(path: Path, rows: list[dict]) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(row) + "\n" for row in rows)
    return str(path)


def _ledger_row(run_id="run-1", tier="balanced", outcome="pr_opened",
                 tokens_out=1000, repo="anchapin/plan-coder"):
    return {"ts": "2026-11-10T09:00:00Z", "repo": repo, "issue": 1,
            "run_id": run_id, "tier": tier, "outcome": outcome,
            "tokens_in": tokens_out, "tokens_out": tokens_out,
            "tokens_total": tokens_out * 2}


def _premise(premise_id="p-1", summary_id="run-1", path="a.md",
             quote="hello", contradicts=None, acted_on=None, decision=None):
    source = {"path": path, "quote": quote}
    if contradicts:
        source["contradicts"] = contradicts
    row = {"premise_id": premise_id, "summary_id": summary_id,
           "claim": "a claim", "sources": [source]}
    if acted_on is not None:
        row["acted_on"] = acted_on
    if decision:
        row["decision"] = decision
    return row


class SourceScoringTests(unittest.TestCase):
    """One cited source is re-read and scored on its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        (Path(self.root) / "a.md").write_text(
            "The ledger is append-only.\n", encoding="utf-8")
        (Path(self.root) / "b.md").write_text(
            "The legacy path rewrote the ledger.\n", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def test_present_quote_is_supported(self):
        result = census.score_source({"path": "a.md", "quote": "append-only"},
                                      self.root)
        self.assertEqual(result["status"], "supported")

    def test_quote_match_ignores_case_and_whitespace(self):
        result = census.score_source({"path": "a.md", "quote": "APPEND-ONLY"},
                                      self.root)
        self.assertEqual(result["status"], "supported")

    def test_absent_quote_is_unsupported_not_corrupted(self):
        result = census.score_source({"path": "a.md", "quote": "rewritten"},
                                      self.root)
        self.assertEqual(result["status"], "unsupported")

    def test_contradiction_marker_is_detected(self):
        result = census.score_source(
            {"path": "b.md", "quote": "append-only",
             "contradicts": ["rewrote the ledger"]}, self.root)
        self.assertEqual(result["status"], "contradicted")

    def test_missing_file_is_unreadable(self):
        result = census.score_source({"path": "nope.md", "quote": "x"},
                                      self.root)
        self.assertEqual(result["status"], "unreadable")

    def test_citation_without_a_quote_is_unanchored(self):
        result = census.score_source({"path": "a.md"}, self.root)
        self.assertEqual(result["status"], "unanchored")

    def test_token_cost_is_chars_over_four(self):
        result = census.score_source({"path": "a.md", "quote": "append-only"},
                                      self.root)
        self.assertEqual(result["tokens"],
                         census.token_estimate(result["chars"]))
        self.assertGreater(result["tokens"], 0)


class PremiseScoringTests(unittest.TestCase):
    """The weakest cited source decides the premise verdict."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        (Path(self.root) / "a.md").write_text(
            "The ledger is append-only.\n", encoding="utf-8")
        (Path(self.root) / "b.md").write_text(
            "The legacy path rewrote the ledger.\n", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def test_all_supported_is_accurate(self):
        result = census.score_premise(
            _premise(path="a.md", quote="append-only"), self.root)
        self.assertEqual(result["verdict"], census.VERDICT_ACCURATE)

    def test_one_unsupported_source_makes_the_premise_incomplete(self):
        premise = _premise(path="a.md", quote="append-only")
        premise["sources"].append({"path": "b.md", "quote": "append-only"})
        result = census.score_premise(premise, self.root)
        self.assertEqual(result["verdict"], census.VERDICT_INCOMPLETE)

    def test_contradiction_makes_the_premise_corrupted(self):
        premise = _premise(path="b.md", quote="append-only",
                           contradicts=["rewrote the ledger"])
        result = census.score_premise(premise, self.root)
        self.assertEqual(result["verdict"], census.VERDICT_CORRUPTED)

    def test_contradiction_outranks_an_unsupported_source(self):
        premise = _premise(path="b.md", quote="append-only",
                           contradicts=["rewrote the ledger"])
        premise["sources"].append({"path": "a.md", "quote": "nothing here"})
        result = census.score_premise(premise, self.root)
        self.assertEqual(result["verdict"], census.VERDICT_CORRUPTED)

    def test_premise_without_any_citation_is_incomplete(self):
        premise = _premise()
        premise["sources"] = []
        result = census.score_premise(premise, self.root)
        self.assertEqual(result["verdict"], census.VERDICT_INCOMPLETE)

    def test_premise_tokens_sum_the_rereads(self):
        premise = _premise(path="a.md", quote="append-only")
        premise["sources"].append({"path": "b.md", "quote": "rewrote"})
        result = census.score_premise(premise, self.root)
        self.assertEqual(result["tokens"],
                         sum(s["tokens"] for s in result["sources"]))


class ActedOnTests(unittest.TestCase):
    """Acted-on and merge-decision come from the joined ledger row."""

    def test_ledger_outcome_decides_acted_on(self):
        ledger = [{"run_id": "run-1", "tier": "balanced",
                   "outcome": "pr_opened", "tokens_out": 10, "tokens_in": 10,
                   "tokens_total": 20, "repo": "r", "issue": 1,
                   "pr_url": ""}]
        self.assertTrue(census.premise_is_acted_on({}, ledger[0]))

    def test_failed_run_is_not_acted_on(self):
        ledger = [{"run_id": "run-1", "tier": "balanced",
                   "outcome": "failed_no_pr", "tokens_out": 10,
                   "tokens_in": 10, "tokens_total": 20, "repo": "r",
                   "issue": 1, "pr_url": ""}]
        self.assertFalse(census.premise_is_acted_on({}, ledger[0]))

    def test_explicit_flag_wins_over_the_ledger(self):
        ledger = [{"run_id": "run-1", "tier": "balanced",
                   "outcome": "failed_no_pr", "tokens_out": 10,
                   "tokens_in": 10, "tokens_total": 20, "repo": "r",
                   "issue": 1, "pr_url": ""}]
        self.assertTrue(census.premise_is_acted_on({"acted_on": True},
                                                    ledger[0]))

    def test_unjoined_premise_without_a_flag_is_not_acted_on(self):
        self.assertFalse(census.premise_is_acted_on({}, None))

    def test_merge_outcome_is_a_merge_decision(self):
        ledger = [{"run_id": "run-1", "tier": "balanced", "outcome": "merged",
                   "tokens_out": 10, "tokens_in": 10, "tokens_total": 20,
                   "repo": "r", "issue": 1, "pr_url": ""}]
        self.assertTrue(census.premise_reached_decision({}, ledger[0]))

    def test_explicit_decision_field_is_a_merge_decision(self):
        self.assertTrue(census.premise_reached_decision(
            {"decision": "merge"}, None))


class PartAGateTests(unittest.TestCase):
    """The Part A gate arithmetic, straight from issue #152."""

    def test_proceed_on_divergence_at_or_above_five_percent(self):
        self.assertEqual(census.part_a_verdict(5.0, 0), "PROCEED")
        self.assertEqual(census.part_a_verdict(9.68, 0), "PROCEED")

    def test_proceed_on_any_corrupted_premise_reaching_a_decision(self):
        self.assertEqual(census.part_a_verdict(0.0, 1), "PROCEED")

    def test_kill_below_two_percent_with_no_corrupted_to_decision(self):
        self.assertEqual(census.part_a_verdict(1.99, 0), "KILL")
        self.assertEqual(census.part_a_verdict(0.0, 0), "KILL")

    def test_hold_between_two_and_five_percent(self):
        self.assertEqual(census.part_a_verdict(2.0, 0), "HOLD")
        self.assertEqual(census.part_a_verdict(4.99, 0), "HOLD")

    def test_thresholds_are_the_issue_numbers(self):
        self.assertEqual(census.DIVERGENCE_PROCEED, 5.0)
        self.assertEqual(census.DIVERGENCE_KILL, 2.0)


class PartACensusTests(unittest.TestCase):
    """The census: tier attribution, divergence rate, token cost."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        (Path(self.root) / "a.md").write_text(
            "The ledger is append-only.\n", encoding="utf-8")
        (Path(self.root) / "b.md").write_text(
            "The legacy path rewrote the ledger.\n", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def _census(self, premises, ledger=None):
        ledger_rows = ledger if ledger is not None else [
            {"run_id": "run-1", "tier": "cheap", "outcome": "pr_opened",
             "tokens_out": 1000, "tokens_in": 1000, "tokens_total": 2000,
             "repo": "r", "issue": 1, "pr_url": ""},
            {"run_id": "run-2", "tier": "flagship", "outcome": "merged",
             "tokens_out": 500, "tokens_in": 500, "tokens_total": 1000,
             "repo": "r", "issue": 2, "pr_url": ""},
        ]
        return census.census_part_a(ledger_rows, premises, self.root)

    def test_tier_comes_from_the_joined_ledger_row(self):
        result = self._census([_premise(summary_id="run-1")])
        self.assertEqual(result["by_tier"]["cheap"]["acted_on"], 1)
        self.assertEqual(result["overall"]["unmatched"], 0)

    def test_unjoined_premise_is_reported_not_hidden(self):
        result = self._census([_premise(summary_id="run-missing")])
        self.assertEqual(result["overall"]["unmatched"], 1)
        self.assertEqual(result["overall"]["acted_on"], 0)

    def test_divergence_rate_is_incomplete_plus_corrupted(self):
        premises = [
            _premise("p1", "run-1", quote="append-only"),
            _premise("p2", "run-1", quote="nothing here"),
            _premise("p3", "run-2", quote="append-only",
                     contradicts=["rewrote the ledger"], path="b.md"),
        ]
        result = self._census(premises)
        self.assertEqual(result["overall"]["accurate"], 1)
        self.assertEqual(result["overall"]["incomplete"], 1)
        self.assertEqual(result["overall"]["corrupted"], 1)
        self.assertEqual(result["overall"]["divergence_rate"], 66.67)

    def test_corrupted_premise_reaching_a_merge_decision_is_counted(self):
        premises = [
            _premise("p1", "run-2", quote="append-only",
                     contradicts=["rewrote the ledger"], path="b.md"),
        ]
        result = self._census(premises)
        self.assertEqual(result["overall"]["corrupted_to_decision"], 1)
        self.assertEqual(result["gate"]["verdict"], "PROCEED")

    def test_reread_tokens_are_reported(self):
        result = self._census([_premise(summary_id="run-1")])
        self.assertGreater(result["overall"]["reread_tokens"], 0)
        self.assertAlmostEqual(
            result["overall"]["reread_chars"] / census.CHARS_PER_TOKEN,
            result["overall"]["reread_tokens"], delta=1.0)


class PartBClassifierTests(unittest.TestCase):
    """Every dispatch lands in exactly one documented class."""

    def _row(self, **kwargs):
        row = _ledger_row()
        row.update(kwargs)
        return {"run_id": row["run_id"], "repo": row["repo"],
                "issue": row["issue"], "tier": row["tier"],
                "outcome": row["outcome"], "tokens_in": row["tokens_in"],
                "tokens_out": row["tokens_out"],
                "tokens_total": row["tokens_total"], "pr_url": "",
                "raw": row}

    def test_shared_evidence_class(self):
        klass, flags = census.classify_dispatch(
            self._row(agent_count=3, shared_evidence=True))
        self.assertEqual(klass, census.CLS_SHARED)
        self.assertTrue(flags["multi_agent"])

    def test_external_verification_class(self):
        klass, _ = census.classify_dispatch(
            self._row(external_source="upstream docs"))
        self.assertEqual(klass, census.CLS_EXTERNAL)

    def test_model_diversity_class(self):
        klass, _ = census.classify_dispatch(
            self._row(providers_used="openai/gpt,zai/glm"))
        self.assertEqual(klass, census.CLS_DIVERSITY)

    def test_partitioned_evidence_is_the_default(self):
        klass, flags = census.classify_dispatch(self._row())
        self.assertEqual(klass, census.CLS_PARTITIONED)
        self.assertFalse(flags["multi_agent"])

    def test_shared_evidence_wins_over_external_verification(self):
        klass, _ = census.classify_dispatch(
            self._row(shared_evidence=True, external_source="docs"))
        self.assertEqual(klass, census.CLS_SHARED)

    def test_external_verification_wins_over_model_diversity(self):
        klass, _ = census.classify_dispatch(
            self._row(external_source="docs", providers_used="a/x,b/y"))
        self.assertEqual(klass, census.CLS_EXTERNAL)


class PartBOutcomeTests(unittest.TestCase):
    """Outcome classes and the compute-matched gate."""

    def test_outcome_classes(self):
        self.assertEqual(census.outcome_class("pr_opened"), "success")
        self.assertEqual(census.outcome_class("merged"), "success")
        self.assertEqual(census.outcome_class("failed_no_pr"), "failure")
        self.assertEqual(census.outcome_class("whatever"), "other")

    def test_gate_is_proceed_on_a_compute_matched_win(self):
        shared = {"dispatches": 4, "successes": 4, "failures": 0,
                  "ungraded": 0, "success_rate": 100.0,
                  "mean_tokens_out": 100.0, "total_tokens_out": 400}
        comparator = {"dispatches": 4, "successes": 2, "failures": 2,
                      "ungraded": 0, "success_rate": 50.0,
                      "mean_tokens_out": 150.0, "total_tokens_out": 600}
        self.assertEqual(census.part_b_verdict(shared, comparator),
                         "PROCEED")

    def test_gate_is_kill_when_more_compute_is_needed_for_the_same_rate(self):
        shared = {"dispatches": 4, "successes": 4, "failures": 0,
                  "ungraded": 0, "success_rate": 100.0,
                  "mean_tokens_out": 200.0, "total_tokens_out": 800}
        comparator = {"dispatches": 4, "successes": 2, "failures": 2,
                      "ungraded": 0, "success_rate": 50.0,
                      "mean_tokens_out": 150.0, "total_tokens_out": 600}
        self.assertEqual(census.part_b_verdict(shared, comparator), "KILL")

    def test_gate_is_kill_on_an_equal_success_rate(self):
        shared = {"dispatches": 2, "successes": 1, "failures": 1,
                  "ungraded": 0, "success_rate": 50.0,
                  "mean_tokens_out": 100.0, "total_tokens_out": 200}
        comparator = {"dispatches": 2, "successes": 1, "failures": 1,
                      "ungraded": 0, "success_rate": 50.0,
                      "mean_tokens_out": 100.0, "total_tokens_out": 200}
        self.assertEqual(census.part_b_verdict(shared, comparator), "KILL")

    def test_gate_is_hold_without_a_class_or_a_comparator(self):
        empty = {"dispatches": 0, "successes": 0, "failures": 0,
                 "ungraded": 0, "success_rate": 0.0,
                 "mean_tokens_out": 0.0, "total_tokens_out": 0}
        shared = {"dispatches": 2, "successes": 2, "failures": 0,
                  "ungraded": 0, "success_rate": 100.0,
                  "mean_tokens_out": 100.0, "total_tokens_out": 200}
        self.assertEqual(census.part_b_verdict(shared, empty), "HOLD")
        self.assertEqual(census.part_b_verdict(empty, shared), "HOLD")


class PartBCensusTests(unittest.TestCase):
    """The census picks the nearest single-agent comparator."""

    def test_comparator_is_the_single_agent_dispatches_of_the_same_repo(self):
        rows = [
            {"run_id": "s1", "repo": "r1", "issue": 1, "tier": "balanced",
             "outcome": "failed_no_pr", "tokens_in": 0, "tokens_out": 100,
             "tokens_total": 0, "pr_url": "", "raw": {"run_id": "s1"}},
            {"run_id": "s2", "repo": "r2", "issue": 2, "tier": "balanced",
             "outcome": "pr_opened", "tokens_in": 0, "tokens_out": 100,
             "tokens_total": 0, "pr_url": "", "raw": {"run_id": "s2"}},
            {"run_id": "m1", "repo": "r1", "issue": 3, "tier": "balanced",
             "outcome": "merged", "tokens_in": 0, "tokens_out": 90,
             "tokens_total": 0, "pr_url": "",
             "raw": {"run_id": "m1", "agent_count": 2,
                     "shared_evidence": True}},
        ]
        result = census.census_part_b(rows)
        comparison = result["comparison"]
        self.assertEqual(comparison["comparator_basis"], "same_repo")
        self.assertEqual(comparison["comparator"]["dispatches"], 1)
        self.assertEqual(result["gate"]["verdict"], "PROCEED")

    def test_multi_agent_partitioned_dispatches_are_not_comparators(self):
        rows = [
            {"run_id": "m1", "repo": "r1", "issue": 3, "tier": "balanced",
             "outcome": "merged", "tokens_in": 0, "tokens_out": 90,
             "tokens_total": 0, "pr_url": "",
             "raw": {"run_id": "m1", "agent_count": 2}},
            {"run_id": "m2", "repo": "r1", "issue": 4, "tier": "balanced",
             "outcome": "merged", "tokens_in": 0, "tokens_out": 90,
             "tokens_total": 0, "pr_url": "",
             "raw": {"run_id": "m2", "agent_count": 2,
                     "shared_evidence": True}},
        ]
        result = census.census_part_b(rows)
        self.assertEqual(result["comparison"]["comparator"]["dispatches"], 0)
        self.assertEqual(result["gate"]["verdict"], "HOLD")


class FixtureCensusTests(unittest.TestCase):
    """The committed fixtures produce the tables and verdicts."""

    def test_part_a_runs_on_the_committed_fixture(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "reader",
             "--ledger", str(FIXTURES / "worker_ledger.jsonl"),
             "--premises", str(FIXTURES / "premises.jsonl"),
             "--sources-root", str(FIXTURES)],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn("Table A1", out)
        self.assertIn("cheap", out)
        self.assertIn("balanced", out)
        self.assertIn("flagship", out)
        self.assertIn("verdict:", out)
        self.assertIn("re-read tokens", out)

    def test_part_a_json_output_is_valid(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "reader",
             "--ledger", str(FIXTURES / "worker_ledger.jsonl"),
             "--premises", str(FIXTURES / "premises.jsonl"),
             "--sources-root", str(FIXTURES), "--json"],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("gate", payload)
        self.assertIn(payload["gate"]["verdict"],
                      ("PROCEED", "KILL", "HOLD"))
        self.assertEqual(payload["overall"]["acted_on"], 31)
        self.assertEqual(payload["overall"]["corrupted"], 1)
        self.assertEqual(payload["overall"]["corrupted_to_decision"], 1)

    def test_part_b_runs_on_the_committed_fixture(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "multiagent",
             "--ledger", str(FIXTURES / "multiagent_ledger.jsonl"),
             "--json"],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["dispatches"], 27)
        classes = {name: row["dispatches"]
                   for name, row in payload["by_class"].items()}
        self.assertEqual(classes[census.CLS_SHARED], 6)
        self.assertEqual(classes[census.CLS_EXTERNAL], 4)
        self.assertEqual(classes[census.CLS_DIVERSITY], 4)
        self.assertEqual(payload["comparison"]["comparator_basis"],
                         "same_repo")
        self.assertIn(payload["gate"]["verdict"],
                      ("PROCEED", "KILL", "HOLD"))


class CliTests(unittest.TestCase):
    """The tool takes its inputs as arguments; nothing is hard-coded."""

    def test_help_lists_both_subcommands(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                                capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reader", result.stdout)
        self.assertIn("multiagent", result.stdout)

    def test_reader_requires_a_ledger_argument(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "reader", "--premises", "x.jsonl"],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--ledger", result.stderr)

    def test_multiagent_requires_a_ledger_argument(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "multiagent"],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--ledger", result.stderr)

    def test_missing_ledger_exits_nonzero(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "multiagent",
             "--ledger", str(FIXTURES / "does_not_exist.jsonl")],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 1)

    def test_the_source_never_hard_codes_the_operator_state_dir(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("hidden_files", source)
        self.assertNotIn("token-plan-coding-manager-setup", source)


class ReportTests(unittest.TestCase):
    """The report carries the census tables and the two verdicts."""

    def setUp(self):
        self.text = REPORT.read_text(encoding="utf-8")

    def test_report_exists_and_names_both_parts(self):
        self.assertIn("Part A", self.text)
        self.assertIn("Part B", self.text)

    def test_report_carries_the_tables(self):
        self.assertIn("Table A1", self.text)
        self.assertIn("Table A2", self.text)
        self.assertIn("Table A3", self.text)
        self.assertIn("Table B1", self.text)
        self.assertIn("Table B2", self.text)

    def test_report_carries_the_two_gate_verdicts(self):
        self.assertIn("Part A gate", self.text)
        self.assertIn("Part B gate", self.text)
        self.assertRegex(self.text, r"Part A gate.*(PROCEED|KILL|HOLD)")
        self.assertRegex(self.text, r"Part B gate.*(PROCEED|KILL|HOLD)")

    def test_report_states_the_data_availability(self):
        self.assertIn("fixture", self.text.lower())
        self.assertIn("--ledger", self.text)
        self.assertIn("--premises", self.text)

    def test_report_tables_match_the_fixture_census(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "reader",
             "--ledger", str(FIXTURES / "worker_ledger.jsonl"),
             "--premises", str(FIXTURES / "premises.jsonl"),
             "--sources-root", str(FIXTURES)],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            if line.startswith(("| cheap |", "| balanced |", "| flagship |")):
                self.assertIn(line.strip(), self.text)

    def test_report_states_that_no_prompt_or_gate_was_changed(self):
        lowered = self.text.lower()
        self.assertIn("measurement only", lowered)
        self.assertIn("phase 1", lowered)


if __name__ == "__main__":
    unittest.main()
