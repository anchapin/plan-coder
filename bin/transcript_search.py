#!/usr/bin/env python3
"""FTS5 transcript search for plan-coder (issue #183, Phase 1).

Phase 0 (2026-11-26) measured a file-level SQLite FTS5 index over the
worker transcripts at a median 124.4x time-to-evidence saving over grep,
with 5 of 10 real diagnosis questions surfacing a relevant run in the
top 5 that grep missed entirely.  It also found the failure mode this
module fixes: 3 of 10 queries were relevance misses because file-level
BM25 drowns a rare single run's distinctive terms in the vocabulary of
a whole 40+ message transcript.

Phase 1 therefore indexes PER MESSAGE: one FTS5 row per transcript line
(one JSONL record), so BM25 length normalisation works on the message
that actually carries the terms, not on the run that contains it.  On
top of that, boilerplate-heavy runs (shepherd / supervisor / tune cron
vocabulary, the "recall blindness" pattern mined from Hermes #19434)
are DEMOTED in ranking -- a flat score penalty, never an exclusion --
so a repetitive automation-flavoured run cannot starve a worker run
out of the top results merely by repeating the query terms.

Design constraints, mirroring the other bin/ tools:

  * Standard library only.
  * Read-only over the live transcripts: build opens transcript files
    for reading only, and search opens the index with mode=ro.
  * The index lives OUTSIDE the repo, in the deployment config's state
    dir (``transcript_search.sqlite3`` next to the ledgers), never in a
    worktree and never committed.  It is written to a temporary file
    beside its destination and moved into place only on success, so a
    failed build never leaves a half-written index behind.
  * Paths come from the deployment config, with the same search order
    as bin/trace_summary.py: $PLAN_CODER_CONFIG, then
    config/deployment.local.json next to the repo root, then
    ~/.plan-coder/deployment.json.  --transcripts-dir / --index
    override for tests and one-off probes.
  * Returned content is capped: each indexed message is truncated to
    MAX_MESSAGE_CHARS, each snippet to MAX_SNIPPET_CHARS, the result
    list to MAX_LIMIT rows and to MAX_PER_RUN rows per run, so one
    archived tool result can never blow up a caller's context (the
    Hermes 74K-char lesson).

Usage:
    python3 bin/transcript_search.py build [--transcripts-dir DIR]
                                           [--index PATH] [--json]
    python3 bin/transcript_search.py search TERM [TERM ...]
                                           [--index PATH] [--limit N]
                                           [--per-run N] [--no-demote]
                                           [--raw] [--json]

Plain search terms are AND-ed (each term is quoted, so transcript
vocabulary like ``phase_profile`` or ``WORKER_REPORT`` is safe).
--raw passes the query to FTS5 MATCH verbatim for operators who want
full FTS5 syntax (OR, NOT, phrases, column filters).
Exit 0 on success; exit 2 when the transcripts dir or index is missing.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from urllib.parse import quote

# --- caps (returned / indexed content is bounded, always) -------------------

# One indexed message: a single transcript line can carry a huge tool
# result; past this many characters the tail is almost never the part a
# diagnosis query needs, and it inflates the index.
MAX_MESSAGE_CHARS = 20_000
# One returned snippet: enough to judge relevance, never a data dump.
MAX_SNIPPET_CHARS = 400
DEFAULT_LIMIT = 10
MAX_LIMIT = 100
DEFAULT_PER_RUN = 3
MAX_PER_RUN = 20

# --- boilerplate demotion ----------------------------------------------------

# Flat penalty added to a demoted row's BM25 score (FTS5 BM25 is
# negative, more negative = better, so adding a positive penalty pushes
# the row down without ever removing it).
DEMOTION_PENALTY = 5.0
# A run is boilerplate-heavy when at least this fraction of its messages
# carry cron/automation vocabulary (and at least BOILERPLATE_MIN_HITS
# messages do), or when its run_id names an automation run outright.
BOILERPLATE_FRACTION = 0.25
BOILERPLATE_MIN_HITS = 2
BOILERPLATE_MARKERS = (
    "backlog-supervisor",
    "pr-shepherd",
    "harness-tune",
    "shepherd cycle",
    "supervisor cycle",
    "shepherd.json",
    "cron run",
)
BOILERPLATE_RUN_TOKENS = ("shepherd", "supervisor", "harness-tune", "cron")

_RUN_ID_RE = re.compile(r"^(.+)-(\d+)-(\d{6,})$")
_RUN_ID_VARIANT_RE = re.compile(r"^(?:[a-z]+\d+-)?(.+)-(\d+)-(?:on|off)-(\d{6,})$")
_TERM_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")


class TranscriptSearchError(Exception):
    """The index could not be built or searched."""


# --- deployment config (same search order as bin/trace_summary.py) ----------


def _load_deployment_config() -> dict:
    candidates = []
    env_path = os.environ.get("PLAN_CODER_CONFIG")
    if env_path:
        candidates.append(env_path)
    repo_root = os.path.dirname(
        os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
    )
    candidates.append(os.path.join(repo_root, "config", "deployment.local.json"))
    candidates.append(os.path.expanduser("~/.plan-coder/deployment.json"))
    for path in candidates:
        data = None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:  # noqa: BLE001 - config is a convenience
            data = None
        if isinstance(data, dict):
            return data
    return {}


def transcripts_dir_from_config(cfg: dict) -> str:
    """The transcripts dir a config names (state.transcripts_dir, else
    <state dir>/transcripts, else the standalone default)."""
    state = cfg.get("state") or {}
    configured = state.get("transcripts_dir")
    if configured:
        return os.path.expanduser(str(configured))
    state_dir = os.path.expanduser(str(state.get("dir") or "~/.plan-coder/state"))
    return os.path.join(state_dir, "transcripts")


def index_path_from_config(cfg: dict) -> str:
    """Where the index lives for a config: the state dir (the parent of
    the transcripts dir when that is how the config is laid out), so the
    index sits with the ledgers and outside every repo/worktree."""
    state = cfg.get("state") or {}
    if state.get("dir"):
        return os.path.join(
            os.path.expanduser(str(state["dir"])), "transcript_search.sqlite3"
        )
    transcripts = transcripts_dir_from_config(cfg)
    return os.path.join(
        os.path.dirname(transcripts.rstrip(os.sep)) or ".", "transcript_search.sqlite3"
    )


def default_transcripts_dir() -> str:
    return transcripts_dir_from_config(_load_deployment_config())


def default_index_path() -> str:
    return index_path_from_config(_load_deployment_config())


# --- run-id context ----------------------------------------------------------


def parse_run_id(run_id: str) -> tuple[str, int | None]:
    """(repo, issue) derivable from a run_id, else (run_id, None).

    Worker run_ids are ``<repo>-<issue>-<timestamp>`` where the repo
    name itself may contain hyphens (``home-energy-twin-12-1795...``),
    so the parse anchors on the LAST two numeric groups.  A/B variants
    (``ab79-matchline-1383-off-1795...``) anchor on the on/off marker.
    Anything else is underivable and reported as-is with no issue.
    """
    match = _RUN_ID_VARIANT_RE.match(run_id) or _RUN_ID_RE.match(run_id)
    if match is None:
        return run_id, None
    return match.group(1), int(match.group(2))


# --- message extraction ------------------------------------------------------


def _collect_strings(value, out: list[str]) -> None:
    if isinstance(value, str):
        if value:
            out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_strings(item, out)


def extract_message(line: str) -> dict | None:
    """One transcript line -> one indexable message, or None for a blank
    line.  A line that is not a JSON object is indexed as raw text (the
    truncated transcripts occasionally carry one); a JSON object is
    flattened to its string values, which is where the searchable
    vocabulary lives (assistant text, tool call args, tool results)."""
    text = line.strip()
    if not text:
        return None
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        record = None
    if not isinstance(record, dict):
        return {"ts": "", "step": None, "kind": "", "content": text}
    parts: list[str] = []
    _collect_strings(record, parts)
    kind = str(record.get("event") or "")
    if not kind and record.get("step") is not None:
        kind = "step"
    step = record.get("step")
    return {
        "ts": str(record.get("ts") or ""),
        "step": step if isinstance(step, int) else None,
        "kind": kind,
        "content": " ".join(parts),
    }


def is_boilerplate_text(content: str) -> bool:
    lowered = content.lower()
    return any(marker in lowered for marker in BOILERPLATE_MARKERS)


def is_boilerplate_run(run_id: str, contents: list[str]) -> bool:
    """Whether a run is boilerplate-heavy (demoted, never excluded):
    its run_id names an automation run, or at least BOILERPLATE_FRACTION
    of its messages (and at least BOILERPLATE_MIN_HITS of them) carry
    shepherd/supervisor/cron vocabulary."""
    lowered_id = run_id.lower()
    if any(token in lowered_id for token in BOILERPLATE_RUN_TOKENS):
        return True
    if not contents:
        return False
    hits = sum(1 for content in contents if is_boilerplate_text(content))
    return hits >= BOILERPLATE_MIN_HITS and hits / len(contents) >= BOILERPLATE_FRACTION


# --- building the index ------------------------------------------------------

_SCHEMA = (
    (
        "CREATE VIRTUAL TABLE messages USING fts5("
        " run_id UNINDEXED, repo UNINDEXED, issue UNINDEXED,"
        " msg_index UNINDEXED, ts UNINDEXED, step UNINDEXED,"
        " kind UNINDEXED, demoted UNINDEXED, content)"
    ),
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)",
)


def iter_transcript_files(transcripts_dir: str) -> list[str]:
    """The top-level *.jsonl transcript files, sorted.  Subdirectories
    (e.g. the codemap/ artefact dir Phase 0 found) are not transcripts
    and are never descended into."""
    return sorted(
        os.path.join(transcripts_dir, name)
        for name in os.listdir(transcripts_dir)
        if name.endswith(".jsonl")
        and os.path.isfile(os.path.join(transcripts_dir, name))
    )


def build_index(transcripts_dir: str, db_path: str) -> dict:
    """Build the per-message FTS5 index of transcripts_dir at db_path
    and return its stats.  Transcripts are opened read-only; the index
    is written to a temp file beside db_path and moved into place only
    when the build succeeds."""
    if not os.path.isdir(transcripts_dir):
        raise TranscriptSearchError(f"transcripts dir not found: {transcripts_dir}")
    started = time.monotonic()
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    tmp = f"{db_path}.tmp-{os.getpid()}"
    conn = sqlite3.connect(tmp)
    built = False
    stats = {
        "transcripts_dir": transcripts_dir,
        "index": db_path,
        "files": 0,
        "messages": 0,
        "skipped_lines": 0,
        "demoted_runs": 0,
        "runs": 0,
    }
    try:
        for statement in _SCHEMA:
            conn.execute(statement)
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        for path in iter_transcript_files(transcripts_dir):
            run_id = os.path.splitext(os.path.basename(path))[0]
            repo, issue = parse_run_id(run_id)
            messages = []
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    message = extract_message(line)
                    if message is None:
                        stats["skipped_lines"] += 1
                        continue
                    messages.append(message)
            if not messages:
                continue
            demoted = is_boilerplate_run(run_id, [m["content"] for m in messages])
            stats["files"] += 1
            stats["runs"] += 1
            stats["demoted_runs"] += 1 if demoted else 0
            stats["messages"] += len(messages)
            conn.executemany(
                "INSERT INTO messages (run_id, repo, issue, msg_index,"
                " ts, step, kind, demoted, content)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        repo,
                        issue,
                        idx,
                        m["ts"],
                        m["step"],
                        m["kind"],
                        1 if demoted else 0,
                        m["content"][:MAX_MESSAGE_CHARS],
                    )
                    for idx, m in enumerate(messages)
                ],
            )
        stats["built_at"] = datetime.now(UTC).isoformat()
        stats["build_seconds"] = round(time.monotonic() - started, 3)
        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            [(key, str(value)) for key, value in sorted(stats.items())],
        )
        conn.commit()
        built = True
    finally:
        conn.close()
        if not built:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
    os.replace(tmp, db_path)
    return stats


# --- searching the index -----------------------------------------------------


def to_match_query(query: str) -> str:
    """A plain operator query -> a safe FTS5 MATCH expression: every
    term quoted, terms AND-ed.  Raises TranscriptSearchError when the
    query carries no searchable term."""
    terms = _TERM_RE.findall(query or "")
    if not terms:
        raise TranscriptSearchError(f"no searchable term in query: {query!r}")
    return " AND ".join(f'"{term}"' for term in terms)


def cap_snippet(snippet: str) -> str:
    """A snippet capped at MAX_SNIPPET_CHARS, with the cut marked."""
    snippet = " ".join((snippet or "").split())
    if len(snippet) <= MAX_SNIPPET_CHARS:
        return snippet
    suffix = " …[capped]"
    return snippet[: MAX_SNIPPET_CHARS - len(suffix)].rstrip() + suffix


def search_index(
    db_path: str,
    query: str,
    limit: int = DEFAULT_LIMIT,
    per_run: int = DEFAULT_PER_RUN,
    demote: bool = True,
    raw: bool = False,
) -> list[dict]:
    """Search a built index.  Returns at most ``limit`` per-message
    results (at most ``per_run`` from any one run), best first, each
    with run_id, repo, issue, msg_index, ts, step, kind, demoted,
    score and a capped snippet.  With demote on (the default), a
    boilerplate-heavy run's rows carry DEMOTION_PENALTY on their BM25
    score; with it off the ranking is raw BM25 -- the demoted rows are
    present either way, demotion never excludes."""
    if not os.path.isfile(db_path):
        raise TranscriptSearchError(
            f"index not found: {db_path} (run `transcript_search.py build` first)"
        )
    limit = max(1, min(int(limit), MAX_LIMIT))
    per_run = max(1, min(int(per_run), MAX_PER_RUN))
    match = query if raw else to_match_query(query)
    penalty = DEMOTION_PENALTY if demote else 0.0
    uri = "file:" + quote(os.path.abspath(db_path)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT run_id, repo, issue, msg_index, ts, step, kind,"
            " demoted, bm25(messages) + CASE WHEN demoted = 1"
            " THEN ? ELSE 0 END AS score,"
            " snippet(messages, 8, '[', ']', ' … ', 32) AS snip"
            " FROM messages WHERE messages MATCH ?"
            " ORDER BY score ASC LIMIT ?",
            (penalty, match, max(limit * per_run * 4, 100)),
        ).fetchall()
    except sqlite3.Error as exc:
        raise TranscriptSearchError(f"search failed: {exc}") from exc
    finally:
        conn.close()
    results: list[dict] = []
    per_run_seen: dict[str, int] = {}
    for run_id, repo, issue, msg_index, ts, step, kind, demoted, score, snip in rows:
        if per_run_seen.get(run_id, 0) >= per_run:
            continue
        per_run_seen[run_id] = per_run_seen.get(run_id, 0) + 1
        results.append(
            {
                "run_id": run_id,
                "repo": repo,
                "issue": issue,
                "msg_index": msg_index,
                "ts": ts,
                "step": step,
                "kind": kind,
                "demoted": bool(demoted),
                "score": round(float(score), 4),
                "snippet": cap_snippet(snip),
            }
        )
        if len(results) >= limit:
            break
    return results


# --- command line ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-message FTS5 search over plan-coder transcripts "
        "(issue #183, Phase 1)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build_p = sub.add_parser("build", help="build (or rebuild) the index")
    build_p.add_argument(
        "--transcripts-dir",
        default=None,
        help="transcripts dir (default: deployment config)",
    )
    build_p.add_argument(
        "--index",
        default=None,
        help="index file to write (default: deployment config state dir)",
    )
    build_p.add_argument(
        "--json", action="store_true", help="print the build stats as JSON"
    )

    search_p = sub.add_parser("search", help="search a built index")
    search_p.add_argument("terms", nargs="+", help="search terms (AND-ed unless --raw)")
    search_p.add_argument(
        "--index",
        default=None,
        help="index file to read (default: deployment config state dir)",
    )
    search_p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"maximum results (default {DEFAULT_LIMIT})",
    )
    search_p.add_argument(
        "--per-run",
        type=int,
        default=DEFAULT_PER_RUN,
        help=f"maximum results from one run (default {DEFAULT_PER_RUN})",
    )
    search_p.add_argument(
        "--no-demote",
        action="store_true",
        help="rank by raw BM25, without the boilerplate demotion penalty",
    )
    search_p.add_argument(
        "--raw", action="store_true", help="pass the query to FTS5 MATCH verbatim"
    )
    search_p.add_argument(
        "--json", action="store_true", help="print the results as JSON"
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            stats = build_index(
                args.transcripts_dir or default_transcripts_dir(),
                args.index or default_index_path(),
            )
            if args.json:
                print(json.dumps(stats, sort_keys=True))
            else:
                print(f"index: {stats['index']}")
                print(f"transcripts: {stats['transcripts_dir']}")
                print(
                    f"files: {stats['files']}  runs: {stats['runs']}  "
                    f"messages: {stats['messages']}  "
                    f"skipped lines: {stats['skipped_lines']}  "
                    f"demoted runs: {stats['demoted_runs']}"
                )
                print(f"build seconds: {stats['build_seconds']}")
            return 0
        results = search_index(
            args.index or default_index_path(),
            " ".join(args.terms),
            limit=args.limit,
            per_run=args.per_run,
            demote=not args.no_demote,
            raw=args.raw,
        )
    except TranscriptSearchError as exc:
        print(f"transcript_search: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(results, indent=1))
    else:
        if not results:
            print("no results")
        for result in results:
            context = result["repo"]
            if result["issue"] is not None:
                context += f"#{result['issue']}"
            demoted = " [demoted: boilerplate-heavy run]" if result["demoted"] else ""
            print(
                f"{result['run_id']}  ({context})  "
                f"msg {result['msg_index']}  score {result['score']}{demoted}"
            )
            print(f"  {result['snippet']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
