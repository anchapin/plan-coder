"""JiT test for plan-coder PR #49 (issue #29): an unattributed file must not
be given a cause the record does not support.

Authority (independent of the code under test): issue #29 section 4 (Part A,
Phase 0) asks for, per sampled unattributed file, "the apparent cause
(exec-edit, generated file, rename, supervisor/shepherd edit after the run,
unknown) -- labelled by evidence, not guessed in bulk", and scores the
measurement against the bar "unattributed causes explainable >= 16/20".

The honest label for a file the record says nothing about is ``unknown``.
A positive cause asserted from *absence* (the PR says ADDED and no worker
edit names it) is a guess, not evidence, and counting it as "explainable"
inflates the very figure the PASS/Kill/Park verdict rests on.

The module is loaded by path with importlib, the convention used by
``tests/test_headroom_phase0.py`` and ``tests/test_ast_grep_phase0_scan.py``
(``bin/provenance_phase0.py`` is not an importable package module).  No
network, no wall clock, no unseeded randomness: the only external boundary
(GitHub's PR file list) is stubbed.

Run: python3 -m pytest tests/test_provenance_phase0_jittest.py -q
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "provenance_phase0.py"

#: The cause classes issue #29 section 4 names for an unattributed file:
#: an exec edit, a generated file, a rename, a supervisor/shepherd edit
#: after the run, and ``unknown``.  A label outside this set is a cause
#: the issue does not define.
ISSUE_CAUSE_CLASSES = {
    "worker_shell_edit",          # exec-edit
    "generated_artifact",          # generated file
    "renamed_file",               # rename
    "supervisor_post_run_edit",    # supervisor/shepherd edit after the run
    "unknown",                    # the honest label
}


def load_module():
    """Load bin/provenance_phase0.py by path (dataclass-safe)."""
    spec = importlib.util.spec_from_file_location("provenance_phase0", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the dataclass decorator can resolve
    # cls.__module__ through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_transcript(directory: Path, run_id: str) -> None:
    """A one-step transcript whose edit_file call names ``a.py``."""
    path = directory / f"{run_id}.jsonl"
    step = {
        "step": 1,
        "provider": "stub",
        "model": "stub",
        "assistant_text": "now write the module",
        "tool_calls": [{
            "name": "edit_file",
            "args": json.dumps({"path": "a.py", "old_text": "x",
                                "new_text": "y"}),
        }],
        "tool_results": ["ok"],
        "tool_result_kinds": ["text"],
    }
    path.write_text(json.dumps(step) + "\n", encoding="utf-8")


def write_ledger(directory: Path, run_id: str) -> Path:
    """A one-row worker ledger with one ``pr_opened`` outcome."""
    path = directory / "worker_ledger.jsonl"
    row = {
        "ts": "2026-01-01T00:00:00Z",
        "repo": "example/example",
        "issue": 1,
        "branch": "worker/1",
        "run_id": run_id,
        "tier": "balanced",
        "providers_used": [],
        "tokens_in": 1,
        "tokens_out": 1,
        "steps": 1,
        "outcome": "pr_opened",
        "pr_url": "https://github.com/example/example/pull/7",
        "gate_triggers": [],
        "grade": None,
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


@unittest.skipUnless(SCRIPT.exists(), "bin/provenance_phase0.py not present")
class UnattributedCauseMustBeEvidence(unittest.TestCase):
    """A cause label must follow from evidence, not from absence."""

    def test_a_file_the_record_says_nothing_about_is_unknown(self):
        module = load_module()
        # A ``RENAMED`` change type is *not* in this list: the PR metadata
        # itself is positive evidence of a rename.
        for change_type in ("ADDED", "MODIFIED", "REMOVED"):
            with self.subTest(change_type=change_type):
                label, evidence = module.classify_unattributed(
                    {"file": "x.md", "change_type": change_type}, [], "")
                self.assertIn(
                    label, ISSUE_CAUSE_CLASSES,
                    "the label must be one of the cause classes issue #29 "
                    f"section 4 names, got {label!r} ({evidence!r})")
                self.assertEqual(
                    label, "unknown",
                    "no edit/write call, no shell write command, no rename "
                    "and no supervisor note support any cause here, so the "
                    "honest label is 'unknown' (issue #29 section 4: "
                    "'labelled by evidence, not guessed in bulk'); got "
                    f"{label!r} with evidence {evidence!r}")

    def test_a_cause_the_record_does_support_is_still_reported(self):
        """The fix must not collapse every label into ``unknown``."""
        module = load_module()

        # A generated artifact: the path shape alone is the evidence.
        label, _ = module.classify_unattributed(
            {"file": "package-lock.json", "change_type": "MODIFIED"}, [], "")
        self.assertEqual(label, "generated_artifact")

        # A rename: the PR metadata is the evidence.
        label, _ = module.classify_unattributed(
            {"file": "new.py", "change_type": "RENAMED",
             "previous_path": "old.py"}, [], "")
        self.assertEqual(label, "renamed_file")

        # A supervisor/shepherd edit after the run: the ledger note is the
        # evidence.
        label, _ = module.classify_unattributed(
            {"file": "x.md", "change_type": "ADDED"}, [],
            note="supervisor salvaged the branch after the run")
        self.assertEqual(label, "supervisor_post_run_edit")

    def test_the_explainable_count_excludes_guessed_causes(self):
        """End to end: a guessed cause must not be counted as explainable.

        The real entry point (``main``) is driven over a synthetic ledger
        and transcript; the only stub is the GitHub PR file list (the one
        true external boundary).
        """
        module = load_module()
        run_id = "run-1"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcripts = tmp_path / "transcripts"
            transcripts.mkdir()
            write_transcript(transcripts, run_id)
            ledger = write_ledger(tmp_path, run_id)

            # The PR changed ``a.py`` (attributed) and added ``b.md``
            # (unattributed, and the record says nothing about it).
            module.gh_pr_files = lambda repo, pr, cache_dir=None: {
                "files": [
                    {"path": "a.py", "changeType": "MODIFIED",
                     "previousPath": None, "additions": 1, "deletions": 1},
                    {"path": "b.md", "changeType": "ADDED",
                     "previousPath": None, "additions": 1, "deletions": 0},
                ],
                "error": None,
            }

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = module.main([
                    "--ledger", str(ledger),
                    "--transcripts-dir", str(transcripts),
                    "--limit", "10",
                    "--no-cache",
                    "--format", "json",
                ])
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())

        metrics = payload["metrics"]
        subsample = payload["unattributed_subsample"]
        self.assertEqual(metrics["subsample_size"], 1)
        self.assertEqual(
            subsample[0]["label"], "unknown",
            "the sampled unattributed file has no supporting evidence, so "
            f"it must be labelled 'unknown', got {subsample[0]['label']!r}")
        self.assertEqual(
            metrics["explainable"], 0,
            "only a cause the record supports counts as explainable "
            "(issue #29 section 4 bar: >= 16/20 explainable); a guessed "
            "cause must not be counted")


if __name__ == "__main__":
    unittest.main()
