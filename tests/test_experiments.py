"""Tests for bin/experiments.py (issue #132, docs/METRICS.md Phase 2).

The experiment registry is the loop's record of what was tried and what
came of it (docs/METRICS.md section 6).  What these tests pin:

  * the CLI creates the registry, appends a row, lists the rows and
    validates a registry against the section 6 schema;
  * a row that breaks the schema is rejected, not silently stored, and
    a broken registry is reported, not repaired;
  * the verdicts are the section 6 vocabulary, and "pending" is
    normalized to the explicit "in-progress" state;
  * the seeded example registry (examples/experiments.example.jsonl)
    holds the two experiments the issue names -- Context Mode (#53) as
    the retroactive first entry and one p90-tail intervention -- both
    in an explicit in-progress state;
  * the EXISTING dashboard reader (bin/dashboard.py, Panel 5) reads the
    seeded registry unchanged -- this branch does not touch
    bin/dashboard.py;
  * the harness-tune prompt is NOT changed by this branch: the prompt
    change is a proposal document under docs/, ready for review.

Run: python3 -m pytest tests/test_experiments.py -q
"""
from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "experiments.py"
DASHBOARD = REPO / "bin" / "dashboard.py"
EXAMPLE_REGISTRY = REPO / "examples" / "experiments.example.jsonl"
PROMPT = REPO / "prompts" / "harness-tune.md"
PROPOSAL = (REPO / "docs" / "proposals"
            / "harness-tune-experiment-registry.md")
FORMAT_DOC = REPO / "docs" / "EXPERIMENTS.md"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_experiments():
    return load_module(SCRIPT, "plan_coder_experiments")


def load_dashboard():
    return load_module(DASHBOARD, "plan_coder_dashboard_experiments")


def run_tool(module, argv):
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


ADD_ARGS = [
    "--id", "010",
    "--hypothesis", "the brief template clarification cuts failed:brief-* runs",
    "--change", "prompts/backlog-supervisor.md - template clarification (PR #1)",
    "--window-start", "2026-11-14",
    "--primary-metric", "failed:brief-* runs per week",
    "--guardrail", "PR-opened rate",
    "--guardrail", "tokens_out per merged PR (median)",
]


@unittest.skipUnless(SCRIPT.exists(), "bin/experiments.py not present")
class ExperimentsCliTest(unittest.TestCase):
    """init / add / list / close / validate."""

    def setUp(self):
        self.module = load_experiments()
        self.tmp = Path(tempfile.mkdtemp(prefix="experiments-test-"))
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.registry = self.state / "experiments.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_creates_the_registry_in_the_state_dir(self):
        code, out, err = run_tool(self.module,
                                  ["init", "--state-dir", str(self.state)])
        self.assertEqual(code, 0, err)
        self.assertTrue(self.registry.exists())
        self.assertIn("created", out)
        # init is idempotent: it does not truncate an existing registry
        code, out, err = run_tool(self.module,
                                  ["init", "--state-dir", str(self.state)])
        self.assertEqual(code, 0, err)
        self.assertIn("already exists", out)
        self.assertEqual(self.registry.read_text(), "")

    def test_init_writes_nothing_outside_the_state_dir(self):
        before = sorted(p.name for p in self.tmp.iterdir())
        run_tool(self.module, ["init", "--state-dir", str(self.state)])
        after = sorted(p.name for p in self.tmp.iterdir())
        self.assertEqual(before, after)
        self.assertEqual(
            sorted(p.name for p in self.state.iterdir()),
            ["experiments.jsonl"])

    def test_add_appends_a_row_that_validates(self):
        run_tool(self.module, ["init", "--state-dir", str(self.state)])
        code, out, err = run_tool(self.module,
                                  ["add", "--state-dir", str(self.state),
                                   *ADD_ARGS])
        self.assertEqual(code, 0, err)
        rows = read_jsonl(self.registry)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for field in ("id", "date", "hypothesis", "change", "window",
                      "primary_metric", "guardrails", "verdict",
                      "verdict_evidence"):
            self.assertIn(field, row)
        self.assertEqual(row["id"], "010")
        self.assertEqual(row["verdict"], "in-progress")
        self.assertEqual(row["guardrails"],
                         ["PR-opened rate",
                          "tokens_out per merged PR (median)"])
        self.assertEqual(row["window"]["start"], "2026-11-14")
        self.assertEqual(row["window"]["min_sample"], 20)
        self.assertEqual(row["window"]["min_weeks"], 2)
        self.assertIn("RECORDED", out)
        code, out, err = run_tool(self.module,
                                  ["validate", "--state-dir", str(self.state)])
        self.assertEqual(code, 0, err)
        self.assertIn("VALID", out)

    def test_add_rejects_a_duplicate_id(self):
        run_tool(self.module, ["init", "--state-dir", str(self.state)])
        run_tool(self.module, ["add", "--state-dir", str(self.state),
                               *ADD_ARGS])
        code, _out, err = run_tool(self.module,
                                   ["add", "--state-dir", str(self.state),
                                    *ADD_ARGS])
        self.assertEqual(code, 1)
        self.assertIn("duplicate id", err)
        self.assertEqual(len(read_jsonl(self.registry)), 1)

    def test_add_rejects_a_row_without_guardrails(self):
        run_tool(self.module, ["init", "--state-dir", str(self.state)])
        args = [a for a in ADD_ARGS]
        index = args.index("--guardrail")
        args = args[:index]
        code, _out, err = run_tool(self.module,
                                   ["add", "--state-dir", str(self.state),
                                    *args])
        self.assertEqual(code, 1)
        self.assertIn("guardrails", err)
        self.assertEqual(read_jsonl(self.registry), [])

    def test_pending_is_normalized_to_the_in_progress_state(self):
        run_tool(self.module, ["init", "--state-dir", str(self.state)])
        code, _out, err = run_tool(self.module,
                                   ["add", "--state-dir", str(self.state),
                                    *ADD_ARGS, "--verdict", "pending"])
        self.assertEqual(code, 0, err)
        self.assertEqual(read_jsonl(self.registry)[0]["verdict"],
                         "in-progress")

    def test_close_records_a_verdict_and_its_evidence(self):
        run_tool(self.module, ["init", "--state-dir", str(self.state)])
        run_tool(self.module, ["add", "--state-dir", str(self.state),
                               *ADD_ARGS])
        code, out, err = run_tool(self.module,
                                  ["close", "--state-dir", str(self.state),
                                   "--id", "010", "--verdict", "keep",
                                   "--verdict-evidence",
                                   "failed:brief-* 14 -> 9 (n=31 vs n=28)"])
        self.assertEqual(code, 0, err)
        row = read_jsonl(self.registry)[0]
        self.assertEqual(row["verdict"], "keep")
        self.assertIn("14 -> 9", row["verdict_evidence"])
        self.assertIn("verdict_date", row)
        self.assertIn("CLOSED", out)
        # the closed row still validates
        code, _out, err = run_tool(self.module,
                                   ["validate", "--state-dir",
                                    str(self.state)])
        self.assertEqual(code, 0, err)

    def test_close_can_correct_a_retroactive_window_start(self):
        run_tool(self.module, ["init", "--state-dir", str(self.state)])
        run_tool(self.module, ["add", "--state-dir", str(self.state),
                               *ADD_ARGS])
        code, _out, err = run_tool(self.module,
                                   ["close", "--state-dir", str(self.state),
                                    "--id", "010", "--verdict", "inconclusive",
                                    "--verdict-evidence", "n=6 runs, too few",
                                    "--window-start", "2026-11-01"])
        self.assertEqual(code, 0, err)
        row = read_jsonl(self.registry)[0]
        self.assertEqual(row["window"]["start"], "2026-11-01")
        self.assertEqual(row["verdict"], "inconclusive")

    def test_close_refuses_an_unknown_id(self):
        run_tool(self.module, ["init", "--state-dir", str(self.state)])
        code, _out, err = run_tool(self.module,
                                   ["close", "--state-dir", str(self.state),
                                    "--id", "999", "--verdict", "keep",
                                    "--verdict-evidence", "n/a"])
        self.assertEqual(code, 2)
        self.assertIn("no experiment with id", err)

    def test_list_prints_the_rows_and_json(self):
        run_tool(self.module, ["init", "--state-dir", str(self.state)])
        run_tool(self.module, ["add", "--state-dir", str(self.state),
                               *ADD_ARGS])
        code, out, err = run_tool(self.module,
                                  ["list", "--state-dir", str(self.state)])
        self.assertEqual(code, 0, err)
        self.assertIn("010", out)
        self.assertIn("in-progress", out)
        code, out, err = run_tool(self.module,
                                  ["list", "--state-dir", str(self.state),
                                   "--json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["experiments"][0]["id"], "010")
        self.assertNotIn("__line__", payload["experiments"][0])

    def test_validate_reports_a_broken_registry(self):
        broken = self.registry
        broken.write_text(
            json.dumps({"id": "001"}) + "\n"
            + json.dumps({"id": "002", "date": "not-a-date",
                          "hypothesis": "h", "change": "c",
                          "window": {"start": "2026-11-14"},
                          "primary_metric": "m", "guardrails": [],
                          "verdict": "maybe",
                          "verdict_evidence": ""}) + "\n"
            + "{not json}\n",
            encoding="utf-8")
        code, out, err = run_tool(self.module,
                                  ["validate", "--state-dir", str(self.state)])
        self.assertEqual(code, 1)
        self.assertIn("INVALID", out)
        self.assertIn("date", out)
        self.assertIn("verdict", out)
        code, out, err = run_tool(self.module,
                                  ["validate", "--state-dir", str(self.state),
                                   "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["valid"])
        fields = {problem["field"] for problem in payload["errors"]}
        self.assertIn("date", fields)
        self.assertIn("hypothesis", fields)
        self.assertIn("change", fields)
        self.assertIn("window", fields)
        self.assertIn("primary_metric", fields)
        self.assertIn("guardrails", fields)
        self.assertIn("verdict", fields)
        self.assertIn("verdict_evidence", fields)
        self.assertIn("window.min_sample", fields)
        self.assertIn("row", fields)

    def test_validate_flags_a_duplicate_id(self):
        run_tool(self.module, ["init", "--state-dir", str(self.state)])
        run_tool(self.module, ["add", "--state-dir", str(self.state),
                               *ADD_ARGS])
        with open(self.registry, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(read_jsonl(self.registry)[0]) + "\n")
        code, out, _err = run_tool(self.module,
                                   ["validate", "--state-dir", str(self.state)])
        self.assertEqual(code, 1)
        self.assertIn("duplicate id", out)

    def test_missing_registry_is_an_error_not_a_crash(self):
        code, _out, err = run_tool(self.module,
                                   ["validate", "--state-dir", str(self.state)])
        self.assertEqual(code, 2)
        self.assertIn("registry not found", err)
        code, _out, err = run_tool(self.module,
                                   ["add", "--state-dir", str(self.state),
                                    *ADD_ARGS])
        self.assertEqual(code, 2)
        self.assertIn("registry not found", err)


@unittest.skipUnless(EXAMPLE_REGISTRY.exists(),
                     "examples/experiments.example.jsonl not present")
class SeededRegistryTest(unittest.TestCase):
    """The seeded example registry -- the two experiments the issue names."""

    def setUp(self):
        self.module = load_experiments()
        self.rows = read_jsonl(EXAMPLE_REGISTRY)

    def test_the_seeded_registry_validates(self):
        code, out, err = run_tool(self.module,
                                  ["validate", "--registry",
                                   str(EXAMPLE_REGISTRY)])
        self.assertEqual(code, 0, err)
        self.assertIn("VALID", out)

    def test_the_seeded_registry_holds_the_two_named_experiments(self):
        self.assertEqual(len(self.rows), 2)
        first, second = self.rows
        # 1 -- Context Mode (#53), the retroactive first entry
        self.assertEqual(first["id"], "001")
        self.assertIn("Context Mode", first["name"])
        self.assertIn("#53", first["change"])
        self.assertIn("10%", first["hypothesis"])
        self.assertEqual(first["primary_metric"],
                         "tokens_out per merged PR (median)")
        self.assertIn("PR-opened rate", first["guardrails"])
        self.assertIn("grade median", first["guardrails"])
        self.assertEqual(first["verdict"], "in-progress")
        # 2 -- one p90-tail intervention, explicitly in progress
        self.assertEqual(second["id"], "002")
        self.assertIn("p90", second["primary_metric"])
        self.assertEqual(second["verdict"], "in-progress")
        self.assertNotEqual(first["primary_metric"], second["primary_metric"])

    def test_the_seeded_registry_is_written_by_the_tool(self):
        """The example file's rows have the tool's own shape."""
        for row in self.rows:
            self.assertEqual(sorted(row.keys()),
                             sorted(["id", "date", "name", "hypothesis",
                                     "change", "window", "primary_metric",
                                     "guardrails", "verdict",
                                     "verdict_evidence"]))
            self.assertEqual(sorted(row["window"].keys()),
                             ["min_sample", "min_weeks", "start"])


@unittest.skipUnless(DASHBOARD.exists() and EXAMPLE_REGISTRY.exists(),
                     "bin/dashboard.py or the example registry not present")
class DashboardCompatibilityTest(unittest.TestCase):
    """The existing dashboard reader reads the seeded registry unchanged."""

    def setUp(self):
        self.dashboard = load_dashboard()
        self.tmp = Path(tempfile.mkdtemp(prefix="experiments-dash-"))
        shutil.copy(EXAMPLE_REGISTRY, self.tmp / "experiments.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_experiments_reads_the_seeded_registry(self):
        panel = self.dashboard.build_experiments(str(self.tmp))
        self.assertTrue(panel["present"])
        self.assertEqual(panel["count"], 2)
        names = [entry["name"] for entry in panel["entries"]]
        self.assertIn("Context Mode (#53)", names)
        self.assertIn("p90 tail - exploratory read cap", names)
        for entry in panel["entries"]:
            # ts falls back to date, name falls back to id, change is read
            self.assertEqual(entry["ts"], "2026-11-14")
            self.assertTrue(entry["change"])
        first = panel["entries"][0]
        self.assertEqual(first["name"], "Context Mode (#53)")
        self.assertIn("#53", first["change"])

    def test_a_registry_without_names_falls_back_to_the_id(self):
        rows = read_jsonl(EXAMPLE_REGISTRY)
        for row in rows:
            row.pop("name", None)
        with open(self.tmp / "experiments.jsonl", "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        panel = self.dashboard.build_experiments(str(self.tmp))
        self.assertEqual([entry["name"] for entry in panel["entries"]],
                         ["001", "002"])


class ProposalTest(unittest.TestCase):
    """The prompt change is a proposal document, not an applied change."""

    @unittest.skipUnless(PROPOSAL.exists(), "the proposal is not present")
    def test_the_proposal_carries_a_ready_to_apply_diff(self):
        text = PROPOSAL.read_text(encoding="utf-8")
        self.assertIn("```diff", text)
        self.assertIn("--- a/prompts/harness-tune.md", text)
        self.assertIn("+++ b/prompts/harness-tune.md", text)
        self.assertIn("bin/experiments.py", text)
        # the monthly one-page review rule is part of the proposal
        self.assertIn("Monthly metrics review", text)

    @unittest.skipUnless(PROPOSAL.exists(), "the proposal is not present")
    def test_the_proposal_keeps_the_prompt_tokens_as_templates(self):
        text = PROPOSAL.read_text(encoding="utf-8")
        self.assertIn("{{skill.dir}}/bin/experiments.py", text)
        self.assertIn("{{state.dir}}/experiments.jsonl", text)

    @unittest.skipUnless(PROMPT.exists(), "prompts/harness-tune.md missing")
    def test_the_live_prompt_is_not_changed(self):
        text = PROMPT.read_text(encoding="utf-8")
        self.assertNotIn("bin/experiments.py", text)
        self.assertNotIn("experiments.jsonl", text)

    @unittest.skipUnless(FORMAT_DOC.exists(), "docs/EXPERIMENTS.md missing")
    def test_the_format_is_documented(self):
        text = FORMAT_DOC.read_text(encoding="utf-8")
        for field in ("id", "date", "hypothesis", "change", "window",
                      "primary_metric", "guardrails", "verdict",
                      "verdict_evidence"):
            self.assertIn(field, text)


if __name__ == "__main__":
    unittest.main()
