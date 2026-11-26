"""Tests for bin/transcript_search.py (plan-coder issue #183, Phase 1).

Pinned here:

  * per-message indexing: one FTS5 row per transcript line, with the
    run's repo/issue context derived from the run_id, blank lines
    skipped, a non-JSON line indexed as raw text, and subdirectories
    of the transcripts dir never descended into;
  * per-message ranking: a short message carrying rare terms outranks
    a long run where the same terms are diluted (the Phase 0 file-level
    BM25 failure mode Phase 1 exists to fix);
  * boilerplate demotion: a shepherd/supervisor-vocabulary-heavy run
    whose messages repeat the query terms ranks BELOW a normal run
    with demotion on, ABOVE it with demotion off, and is never
    excluded either way;
  * content caps: an oversized message is truncated at index time and
    every returned snippet is within MAX_SNIPPET_CHARS;
  * per-run result caps and the config-derived default paths;
  * the CLI end to end (build then search, --json) in temp dirs.

Hermetic: fixture transcripts in temporary directories only, no
network, no real state, no real deployment config (config helpers are
tested against dicts passed in, never the developer's config file).

Run: python3 -m pytest tests/test_transcript_search.py -q
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import transcript_search as ts  # noqa: E402


def write_transcript(directory: Path, run_id: str, records: list) -> Path:
    path = directory / f"{run_id}.jsonl"
    lines = []
    for record in records:
        lines.append(record if isinstance(record, str) else json.dumps(record))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def step_record(step: int, text: str) -> dict:
    return {
        "ts": "2026-11-26T00:00:00+00:00",
        "step": step,
        "assistant_text": text,
        "tool_results": [text],
    }


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-ts183-"))
        self.transcripts = self.tmp / "transcripts"
        self.transcripts.mkdir()
        self.index = self.tmp / "state" / "transcript_search.sqlite3"

    def build(self) -> dict:
        return ts.build_index(str(self.transcripts), str(self.index))


class ParseRunIdTest(unittest.TestCase):
    def test_simple_and_hyphenated_repos(self):
        self.assertEqual(ts.parse_run_id("fluxion-3995-1792982559"), ("fluxion", 3995))
        self.assertEqual(
            ts.parse_run_id("home-energy-twin-12-1795178530"), ("home-energy-twin", 12)
        )
        self.assertEqual(
            ts.parse_run_id("portkit-v2-299-1795178530"), ("portkit-v2", 299)
        )

    def test_ab_variant(self):
        self.assertEqual(
            ts.parse_run_id("ab79-matchline-1383-off-1795229045"), ("matchline", 1383)
        )

    def test_underivable(self):
        self.assertEqual(ts.parse_run_id("some-odd-name"), ("some-odd-name", None))


class ConfigPathTest(unittest.TestCase):
    def test_transcripts_dir_from_config(self):
        cfg = {"state": {"dir": "/x/state", "transcripts_dir": "/x/tr"}}
        self.assertEqual(ts.transcripts_dir_from_config(cfg), "/x/tr")
        cfg = {"state": {"dir": "/x/state"}}
        self.assertEqual(
            ts.transcripts_dir_from_config(cfg), os.path.join("/x/state", "transcripts")
        )

    def test_index_path_lives_in_state_dir(self):
        cfg = {"state": {"dir": "/x/state", "transcripts_dir": "/x/tr"}}
        self.assertEqual(
            ts.index_path_from_config(cfg),
            os.path.join("/x/state", "transcript_search.sqlite3"),
        )


class IndexingTest(FixtureCase):
    def test_one_row_per_message_with_context(self):
        write_transcript(
            self.transcripts,
            "planar-nexus-2604-1111111111",
            [
                {
                    "ts": "2026-11-26T00:00:00+00:00",
                    "event": "started",
                    "run_id": "planar-nexus-2604-1111111111",
                },
                step_record(1, "mesh quarantine timeout investigation"),
                "",  # blank line: skipped
                "a raw non-json line about mesh failure",
            ],
        )
        stats = self.build()
        self.assertEqual(stats["files"], 1)
        self.assertEqual(stats["messages"], 3)
        self.assertEqual(stats["skipped_lines"], 1)
        conn = sqlite3.connect(str(self.index))
        try:
            rows = conn.execute(
                "SELECT run_id, repo, issue, msg_index, kind FROM messages"
                " ORDER BY msg_index"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][1:3], ("planar-nexus", 2604))
        self.assertEqual(rows[0][4], "started")
        self.assertEqual(rows[1][4], "step")
        results = ts.search_index(str(self.index), "mesh failure")
        self.assertTrue(
            any(r["run_id"] == "planar-nexus-2604-1111111111" for r in results)
        )

    def test_subdirectories_are_not_transcripts(self):
        nested = self.transcripts / "codemap"
        nested.mkdir()
        write_transcript(
            nested, "fluxion-1-1111111111", [step_record(1, "codemap artefact text")]
        )
        write_transcript(
            self.transcripts,
            "fluxion-2-1111111112",
            [step_record(1, "real transcript text")],
        )
        stats = self.build()
        self.assertEqual(stats["files"], 1)
        self.assertEqual(ts.search_index(str(self.index), "codemap"), [])

    def test_missing_transcripts_dir_raises(self):
        with self.assertRaises(ts.TranscriptSearchError):
            ts.build_index(str(self.tmp / "nope"), str(self.index))

    def test_search_missing_index_raises(self):
        with self.assertRaises(ts.TranscriptSearchError):
            ts.search_index(str(self.index), "anything")


class RankingTest(FixtureCase):
    def test_short_rare_message_outranks_diluted_long_run(self):
        # The rare run: one short message carrying the distinctive terms.
        write_transcript(
            self.transcripts,
            "fluxion-3995-1792982559",
            [
                step_record(42, "infra restart killed the run, WORKER_REPORT missing"),
            ],
        )
        # The diluted run: many long messages, the same terms once each,
        # buried in unrelated vocabulary (file-level BM25's failure mode).
        filler = " ".join(f"fillerword{i}" for i in range(200))
        write_transcript(
            self.transcripts,
            "fluxion-4000-1792982600",
            [
                step_record(i, f"{filler} infra WORKER_REPORT note {i}")
                for i in range(30)
            ],
        )
        self.build()
        results = ts.search_index(str(self.index), "infra WORKER_REPORT", limit=5)
        self.assertTrue(results)
        self.assertEqual(results[0]["run_id"], "fluxion-3995-1792982559")
        self.assertEqual(results[0]["repo"], "fluxion")
        self.assertEqual(results[0]["issue"], 3995)

    def test_per_run_cap(self):
        write_transcript(
            self.transcripts,
            "fluxion-1-1111111111",
            [step_record(i, f"commonterm evidence number {i}") for i in range(10)],
        )
        self.build()
        results = ts.search_index(str(self.index), "commonterm", limit=10, per_run=2)
        self.assertEqual(len(results), 2)


class DemotionTest(FixtureCase):
    def setUp(self):
        super().setUp()
        # Boilerplate-heavy: every message is shepherd-cycle vocabulary
        # AND repeats the query term heavily -- raw BM25 loves it.
        boiler = (
            "pr-shepherd shepherd cycle: checking shepherd.json, "
            "rerunning ci, merging green prs. " + "quixotictoken " * 12
        )
        write_transcript(
            self.transcripts,
            "plan-coder-77-1111111111",
            [step_record(i, boiler) for i in range(4)],
        )
        # Normal worker run: the term once, in real work content.
        write_transcript(
            self.transcripts,
            "matchline-55-1111111112",
            [
                step_record(1, "the quixotictoken appears in the parser output"),
                step_record(2, "unrelated follow-up work on geometry"),
            ],
        )
        self.stats = self.build()

    def test_boilerplate_run_is_flagged_demoted(self):
        self.assertEqual(self.stats["demoted_runs"], 1)
        conn = sqlite3.connect(str(self.index))
        try:
            row = conn.execute(
                "SELECT DISTINCT demoted FROM messages WHERE run_id = ?",
                ("plan-coder-77-1111111111",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], 1)

    def test_demotion_reorders_but_never_excludes(self):
        raw = ts.search_index(str(self.index), "quixotictoken", limit=5, demote=False)
        demoted = ts.search_index(
            str(self.index), "quixotictoken", limit=5, demote=True
        )
        self.assertEqual(raw[0]["run_id"], "plan-coder-77-1111111111")
        self.assertEqual(demoted[0]["run_id"], "matchline-55-1111111112")
        # Demoted, not excluded: the boilerplate run is still returned.
        self.assertIn("plan-coder-77-1111111111", [r["run_id"] for r in demoted])
        hit = next(r for r in demoted if r["run_id"] == "plan-coder-77-1111111111")
        self.assertTrue(hit["demoted"])


class ContentCapTest(FixtureCase):
    def test_message_and_snippet_caps(self):
        big = "capword " + ("x" * 60_000)
        write_transcript(
            self.transcripts, "fluxion-9-1111111111", [step_record(1, big)]
        )
        self.build()
        conn = sqlite3.connect(str(self.index))
        try:
            stored = conn.execute("SELECT length(content) FROM messages").fetchone()[0]
        finally:
            conn.close()
        self.assertLessEqual(stored, ts.MAX_MESSAGE_CHARS)
        results = ts.search_index(str(self.index), "capword")
        self.assertTrue(results)
        self.assertLessEqual(len(results[0]["snippet"]), ts.MAX_SNIPPET_CHARS)


class CliTest(FixtureCase):
    def test_build_then_search_json(self):
        write_transcript(
            self.transcripts,
            "portkit-v2-287-1111111111",
            [
                step_record(1, "residue vanilla quark classes explained"),
            ],
        )
        out = io.StringIO()
        with redirect_stdout(out):
            rc = ts.main(
                [
                    "build",
                    "--transcripts-dir",
                    str(self.transcripts),
                    "--index",
                    str(self.index),
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["messages"], 1)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = ts.main(
                [
                    "search",
                    "residue",
                    "vanilla",
                    "quark",
                    "--index",
                    str(self.index),
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        results = json.loads(out.getvalue())
        self.assertEqual(results[0]["run_id"], "portkit-v2-287-1111111111")
        self.assertEqual(results[0]["repo"], "portkit-v2")
        self.assertEqual(results[0]["issue"], 287)
        self.assertIn("residue", results[0]["snippet"])

    def test_search_without_index_exits_2(self):
        self.assertEqual(ts.main(["search", "anything", "--index", str(self.index)]), 2)


if __name__ == "__main__":
    unittest.main()
