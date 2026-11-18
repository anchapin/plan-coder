"""Tests for bin/verify_stamp.py (issue #95).

Synthetic-transcript unit tests always run. The Phase 0 regression
fixture (tests/fixtures/verification_phase0_runs.csv — the 100 runs of
the #94 Part A reconstruction) runs only when those runs' transcripts
are findable on this machine; it skips elsewhere (e.g. CI), because
the transcripts are harness state, not repo content.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import unittest
from collections import Counter
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import verify_stamp as vs

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "verification_phase0_runs.csv")


def write_transcript(path, commands, results=(), texts=()):
    lines = []
    for cmd in commands:
        lines.append(json.dumps({
            "tool_calls": [{"name": "exec",
                            "args": json.dumps({"command": cmd})}],
        }) + "\n")
    for res in results:
        lines.append(json.dumps({"tool_results": [res]}) + "\n")
    for text in texts:
        lines.append(json.dumps({"assistant_text": text}) + "\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def classify(self, commands, **kw):
        path = os.path.join(self.tmp, "run.jsonl")
        write_transcript(path, commands, **kw)
        return vs.classify_transcript(path)

    def test_suite_pytest_no_target(self):
        out = self.classify(["python3 -m pytest tests/ -q"])
        self.assertEqual(out["verify_class"], "SUITE")

    def test_targeted_pytest_single_file(self):
        out = self.classify(["python3 -m pytest tests/test_x.py -q"])
        self.assertEqual(out["verify_class"], "TARGETED")

    def test_lint_only_is_targeted(self):
        out = self.classify(["ruff check bin/"])
        self.assertEqual(out["verify_class"], "TARGETED")

    def test_no_verification_is_none(self):
        out = self.classify(["ls src/", "cat README.md"])
        self.assertEqual(out["verify_class"], "NONE")
        self.assertEqual(out["verify_commands"], [])

    def test_segment_anchoring(self):
        # A test command buried after a cd in a chain still counts;
        # a pytest mention inside a longer non-leading segment does not
        # start a segment, so it is not a test segment (Phase 0's
        # segment-anchored rule, not the loose parser's).
        out = self.classify(["cd repo && python3 -m pytest -q"])
        self.assertEqual(out["verify_class"], "SUITE")
        out = self.classify(["echo pytest is great"])
        self.assertEqual(out["verify_class"], "NONE")

    def test_clean_base_detected_via_stash(self):
        out = self.classify(
            ["git stash -u", "python3 -m pytest -q", "git stash pop"])
        self.assertTrue(out["clean_base_detected"])
        self.assertEqual(out["verify_class"], "SUITE")


class StampTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.tdir = os.path.join(self.tmp, "transcripts")
        os.makedirs(self.tdir)

    def test_stamp_row_shape_and_collection_separation(self):
        write_transcript(os.path.join(self.tdir, "repo-1-123.jsonl"),
                         ["python3 -m pytest tests/test_a.py -q"])
        row = vs.build_stamp(
            "repo-1-123", self.tdir, repo="repo", issue=1,
            pr_url="https://example.invalid/pr/1",
            collection_commands=["python3 -m pytest -q"],
            clean_base_verified="not_claimed",
            moment=datetime(2026, 11, 17, tzinfo=UTC))
        self.assertEqual(row["verify_class"], "TARGETED")
        self.assertEqual(row["collection_verify_class"], "SUITE")
        self.assertEqual(row["verify_commands"]["collection"],
                         ["python3 -m pytest -q"])
        self.assertIn("pytest", row["verify_commands"]["worker"][0])
        self.assertEqual(row["clean_base_verified"], "not_claimed")

    def test_missing_transcript_is_unknown(self):
        row = vs.build_stamp("nope-1-1", self.tdir, repo="repo")
        self.assertEqual(row["verify_class"], "UNKNOWN")

    def test_append_and_summary(self):
        ledger = os.path.join(self.tmp, "verification_ledger.jsonl")
        moment = datetime(2026, 11, 17, tzinfo=UTC)
        for i, cls in enumerate(["SUITE", "SUITE", "NONE", "TARGETED"]):
            row = vs.build_stamp(f"missing-{i}", self.tdir, repo="r",
                                 moment=moment)
            row["verify_class"] = cls
            vs.append_stamp(ledger, row)
        out = vs.summarise(ledger, 14, moment=moment)
        self.assertEqual(out["total"], 4)
        self.assertEqual(out["counts"]["SUITE"], 2)
        self.assertEqual(out["share_pct"]["SUITE"], 50.0)
        old = vs.summarise(ledger, 14,
                           moment=datetime(2027, 1, 1,
                                           tzinfo=UTC))
        self.assertEqual(old["total"], 0)


class DisabledGateTests(unittest.TestCase):
    def test_stamp_exits_2_when_disabled(self):
        # The worktree/CI deployment config has no enabled stamp.
        rc = vs.main(["stamp", "--run-id", "x-1-1", "--repo", "r"])
        self.assertEqual(rc, 2)


def _find_transcripts_dir(run_ids):
    candidates = []
    env = os.environ.get("PLAN_CODER_TRANSCRIPTS_DIR")
    if env:
        candidates.append(env)
    try:
        cfg = vs._load_deployment_config() or {}
        td = (cfg.get("state") or {}).get("transcripts_dir")
        if td:
            candidates.append(os.path.expanduser(td))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    candidates.append(os.path.expanduser(
        "~/workspace/goals/token-plan-coding-manager-setup/"
        "hidden_files/transcripts"))
    for cand in candidates:
        if cand and os.path.isdir(cand):
            hits = sum(1 for rid in run_ids
                       if vs.find_transcript(cand, rid))
            if hits >= 90:
                return cand
    return None


class Phase0RegressionFixtureTests(unittest.TestCase):
    def test_reproduces_phase0_counts(self):
        with open(FIXTURE, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 100)
        tdir = _find_transcripts_dir([r["run_id"] for r in rows])
        if tdir is None:
            self.skipTest("Phase 0 transcripts not findable here")
        counts = Counter()
        mismatches = []
        for r in rows:
            path = vs.find_transcript(tdir, r["run_id"])
            if path is None:
                got = "UNKNOWN"
            else:
                got = vs.classify_transcript(path)["verify_class"]
            want = r["class"].removeprefix("A-")
            counts[got] += 1
            if got != want:
                mismatches.append((r["run_id"], want, got))
        self.assertEqual(mismatches, [])
        self.assertEqual(counts, Counter(
            {"SUITE": 58, "TARGETED": 11, "NONE": 31, "E2E": 0}))


if __name__ == "__main__":
    unittest.main()
