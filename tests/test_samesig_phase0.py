"""Tests for bin/samesig_phase0.py (issue #148, Phase 0).

The Phase-0 script replays worker transcripts and measures how often the
*same* failure signature recurs inside one run, what the recurrence cost
and how a repeated signature is shaped (genuine symptom-loop versus benign
noise).  It imports ``failure_signature`` from
``bin/tokeneconomizer_phase0.py`` -- it never re-implements the
normalisation.

Every fixture here is synthetic: transcripts are written into a temporary
directory and removed afterwards.  No transcript of the live corpus is
read and nothing outside the temporary directory is written.

Run: python3 -m pytest tests/test_samesig_phase0.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, BIN / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their module through sys.modules, so register first.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tokeneconomizer = _load("tokeneconomizer_phase0")
samesig = _load("samesig_phase0")


def write_transcript(path, run_id, steps, files_changed=("a.py",),
                     token_cap=False, finished=True):
    """Write a synthetic transcript jsonl and return its path."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "started", "run_id": run_id}) + "\n")
        handle.writelines(json.dumps(step) + "\n" for step in steps)
        if finished:
            handle.write(json.dumps({
                "event": "finished",
                "steps": len(steps),
                "total_in": sum(s.get("tokens_in", 0) for s in steps),
                "total_out": sum(s.get("tokens_out", 0) for s in steps),
                "files_changed": list(files_changed),
                "token_cap": token_cap,
            }) + "\n")
    return str(path)


def exec_step(index, command, result, tokens_in=100, tokens_out=50,
              edits=()):
    """One step with a single exec call (plus optional edit calls)."""
    calls = [{"name": "exec", "args": json.dumps({"command": command})}]
    results = [result]
    for path in edits:
        calls.append({"name": "edit_file",
                      "args": json.dumps({"path": path, "old_string": "x",
                                          "new_string": "y"})})
        results.append("ok")
    return {
        "step": index,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "assistant_text": "",
        "tool_calls": calls,
        "tool_results": results,
    }


def edit_step(index, path, tokens_in=100, tokens_out=50):
    """One step that only edits a file."""
    return {
        "step": index,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "assistant_text": "",
        "tool_calls": [{"name": "edit_file",
                        "args": json.dumps({"path": path, "old_string": "x",
                                            "new_string": "y"})}],
        "tool_results": ["ok"],
    }


FAIL = ("exit=1\n"
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 3, in <module>\n"
        "ModuleNotFoundError: No module named 'missing_pkg'\n")


def noisy_fail():
    """A failure whose text names a transient environment condition."""
    return ("exit=1\n"
            "curl: (7) Failed to connect to example.invalid port 443: "
            "Connection refused\n")


class TempCorpus(unittest.TestCase):
    """Base class giving each test its own temporary transcript corpus."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def corpus(self):
        return samesig.load_corpus_with_outcomes(str(self.dir))

    def measure(self):
        return samesig.measure(self.corpus())


class SignatureReuseTests(unittest.TestCase):
    """The signature function is imported, not re-implemented."""

    def test_failure_signature_is_the_tokeneconomizer_one(self):
        self.assertIs(samesig.failure_signature,
                      tokeneconomizer.failure_signature)

    def test_normalisation_helpers_are_the_tokeneconomizer_ones(self):
        self.assertIs(samesig.normalize_command,
                      tokeneconomizer.normalize_command)
        self.assertIs(samesig.path_tokens, tokeneconomizer.path_tokens)
        self.assertIs(samesig.edit_touches, tokeneconomizer.edit_touches)

    def test_same_failure_text_gives_same_signature(self):
        first = samesig.failure_signature(FAIL)
        second = samesig.failure_signature(FAIL.replace("app.py", "app.py"))
        self.assertIsNotNone(first)
        self.assertEqual(first, second)


class RecurrenceTests(TempCorpus):
    """Q1: how often the same signature occurs 2+ times inside one run."""

    def test_single_occurrence_is_not_a_recurrence(self):
        write_transcript(self.dir / "fluxion-1-1.jsonl", "fluxion-1-1", [
            exec_step(1, "python3 -c pass", FAIL),
            exec_step(2, "python3 -c pass", "exit=0\n"),
        ])
        payload = self.measure()
        self.assertEqual(payload["runs_scanned"], 1)
        self.assertEqual(payload["runs_with_recurrence"], 0)
        self.assertEqual(payload["recurring_signature_groups"], 0)

    def test_two_identical_failures_are_one_recurrence(self):
        write_transcript(self.dir / "fluxion-2-1.jsonl", "fluxion-2-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
        ])
        payload = self.measure()
        self.assertEqual(payload["runs_with_recurrence"], 1)
        self.assertEqual(payload["recurring_signature_groups"], 1)
        run = payload["runs"][0]
        self.assertEqual(run["recurrence_occurrences"], 2)
        self.assertEqual(run["recurrences"][0]["occurrences"], 2)

    def test_three_occurrences_are_one_group_of_three(self):
        write_transcript(self.dir / "fluxion-3-1.jsonl", "fluxion-3-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
            exec_step(3, "python3 app.py", FAIL),
        ])
        payload = self.measure()
        self.assertEqual(payload["recurring_signature_groups"], 1)
        self.assertEqual(payload["runs"][0]["recurrence_occurrences"], 3)
        self.assertEqual(payload["q1"]["occurrence_histogram"], {"3": 1})

    def test_different_failures_are_different_groups(self):
        other = ("exit=1\n"
                 "AssertionError: expected 1 got 2\n")
        write_transcript(self.dir / "fluxion-4-1.jsonl", "fluxion-4-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
            exec_step(3, "pytest tests/", other),
            exec_step(4, "pytest tests/", other),
        ])
        payload = self.measure()
        self.assertEqual(payload["recurring_signature_groups"], 2)
        self.assertEqual(payload["runs"][0]["recurrence_occurrences"], 4)

    def test_a_run_without_failures_has_no_recurrence(self):
        write_transcript(self.dir / "fluxion-5-1.jsonl", "fluxion-5-1", [
            exec_step(1, "python3 app.py", "exit=0\n"),
            exec_step(2, "python3 app.py", "exit=0\n"),
        ])
        payload = self.measure()
        self.assertEqual(payload["runs_with_recurrence"], 0)


class CostAfterSecondTests(TempCorpus):
    """Q2: attempts and tokens spent after the second occurrence."""

    def test_tokens_after_the_second_occurrence_only(self):
        write_transcript(self.dir / "fluxion-6-1.jsonl", "fluxion-6-1", [
            exec_step(1, "python3 app.py", FAIL, tokens_in=10, tokens_out=5),
            exec_step(2, "python3 app.py", FAIL, tokens_in=20, tokens_out=7),
            exec_step(3, "python3 app.py", FAIL, tokens_in=30, tokens_out=11),
            exec_step(4, "python3 app.py", "exit=0\n", tokens_in=40, tokens_out=13),
        ])
        payload = self.measure()
        run = payload["runs"][0]
        # Steps after the second occurrence: step 3 and step 4.
        self.assertEqual(run["tokens_in_after_second"], 30 + 40)
        self.assertEqual(run["tokens_out_after_second"], 11 + 13)
        self.assertEqual(run["tokens_after_second"], 70 + 24)

    def test_attempts_after_the_second_occurrence(self):
        write_transcript(self.dir / "fluxion-7-1.jsonl", "fluxion-7-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
            exec_step(3, "python3 app.py", FAIL),
            exec_step(4, "python3 app.py", "exit=0\n"),
        ])
        payload = self.measure()
        run = payload["runs"][0]
        # Two command calls after the second occurrence (steps 3 and 4);
        # one of them failed.
        self.assertEqual(run["attempts_after_second"], 2)
        self.assertEqual(run["failing_attempts_after_second"], 1)
        # The same signature recurred once more after the second occurrence.
        self.assertEqual(run["same_signature_attempts_after_second"], 1)

    def test_run_that_eventually_succeeded(self):
        write_transcript(self.dir / "fluxion-8-1.jsonl", "fluxion-8-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
            exec_step(3, "python3 app.py", "exit=0\n"),
        ], files_changed=("app.py",))
        payload = self.measure()
        run = payload["runs"][0]
        self.assertEqual(run["outcome"], "edited")
        self.assertTrue(run["succeeded"])
        self.assertEqual(payload["q2"]["succeeded"], 1)

    def test_run_that_did_not_succeed(self):
        write_transcript(self.dir / "fluxion-9-1.jsonl", "fluxion-9-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
        ], files_changed=())
        payload = self.measure()
        run = payload["runs"][0]
        self.assertEqual(run["outcome"], "no_edit")
        self.assertFalse(run["succeeded"])
        self.assertEqual(payload["q2"]["not_succeeded"], 1)

    def test_token_cap_outcome(self):
        write_transcript(self.dir / "fluxion-10-1.jsonl", "fluxion-10-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
        ], files_changed=("app.py",), token_cap=True)
        payload = self.measure()
        self.assertEqual(payload["runs"][0]["outcome"], "token_cap")
        self.assertFalse(payload["runs"][0]["succeeded"])

    def test_missing_finished_event(self):
        write_transcript(self.dir / "fluxion-11-1.jsonl", "fluxion-11-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
        ], finished=False)
        payload = self.measure()
        self.assertEqual(payload["runs"][0]["outcome"], "no_finished_event")


class ShapeTests(TempCorpus):
    """Q3: benign-looking repeats versus genuine symptom-loops."""

    def test_same_command_no_edit_is_a_symptom_loop(self):
        write_transcript(self.dir / "fluxion-12-1.jsonl", "fluxion-12-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
        ])
        payload = self.measure()
        rec = payload["runs"][0]["recurrences"][0]
        self.assertEqual(rec["kind"], samesig.CLS_LOOP)

    def test_edit_between_occurrences_is_a_re_test(self):
        write_transcript(self.dir / "fluxion-13-1.jsonl", "fluxion-13-1", [
            exec_step(1, "python3 app.py", FAIL),
            edit_step(2, "app.py"),
            exec_step(3, "python3 app.py", FAIL),
        ])
        payload = self.measure()
        rec = payload["runs"][0]["recurrences"][0]
        self.assertEqual(rec["kind"], samesig.CLS_PROGRESS)

    def test_environment_noise_is_benign(self):
        write_transcript(self.dir / "fluxion-14-1.jsonl", "fluxion-14-1", [
            exec_step(1, "curl https://example.invalid", noisy_fail()),
            exec_step(2, "curl https://example.invalid", noisy_fail()),
        ])
        payload = self.measure()
        rec = payload["runs"][0]["recurrences"][0]
        self.assertEqual(rec["kind"], samesig.CLS_NOISE)

    def test_different_commands_same_text_is_benign_other(self):
        write_transcript(self.dir / "fluxion-15-1.jsonl", "fluxion-15-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 other.py", FAIL),
        ])
        payload = self.measure()
        rec = payload["runs"][0]["recurrences"][0]
        self.assertEqual(rec["kind"], samesig.CLS_OTHER)

    def test_class_counts_are_reported(self):
        write_transcript(self.dir / "fluxion-16-1.jsonl", "fluxion-16-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
        ])
        write_transcript(self.dir / "fluxion-17-1.jsonl", "fluxion-17-1", [
            exec_step(1, "curl https://example.invalid", noisy_fail()),
            exec_step(2, "curl https://example.invalid", noisy_fail()),
        ])
        payload = self.measure()
        by_class = payload["q3"]["by_class"]
        self.assertEqual(by_class[samesig.CLS_LOOP], 1)
        self.assertEqual(by_class[samesig.CLS_NOISE], 1)
        self.assertEqual(by_class[samesig.CLS_PROGRESS], 0)
        self.assertEqual(by_class[samesig.CLS_OTHER], 0)
        self.assertAlmostEqual(payload["q3"]["loop_pct_of_repeated"], 50.0)
        self.assertAlmostEqual(payload["q3"]["benign_pct_of_repeated"], 50.0)


class CliTests(TempCorpus):
    """The command-line surface."""

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as caught:
            samesig.main(["--help"])
        self.assertEqual(caught.exception.code, 0)

    def test_missing_transcripts_dir_is_an_error(self):
        self.assertEqual(
            samesig.main(["--transcripts", str(self.dir / "nope")]), 2)

    def test_json_mode_emits_the_payload(self):
        write_transcript(self.dir / "fluxion-18-1.jsonl", "fluxion-18-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
        ])
        payload = samesig.measure(samesig.load_corpus_with_outcomes(
            str(self.dir)))
        rendered = json.dumps(payload)
        self.assertEqual(json.loads(rendered)["runs_with_recurrence"], 1)

    def test_render_table_mentions_the_three_questions(self):
        write_transcript(self.dir / "fluxion-19-1.jsonl", "fluxion-19-1", [
            exec_step(1, "python3 app.py", FAIL),
            exec_step(2, "python3 app.py", FAIL),
        ])
        payload = samesig.measure(samesig.load_corpus_with_outcomes(
            str(self.dir)))
        text = samesig.render_table(payload)
        self.assertIn("Q1", text)
        self.assertIn("Q2", text)
        self.assertIn("Q3", text)
        self.assertIn("symptom_loop", text)


if __name__ == "__main__":
    unittest.main()
