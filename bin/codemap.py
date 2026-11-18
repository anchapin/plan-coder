"""Native code map for plan-coder workers (plan-coder issue #80, section 2).

A worker's bill is dominated by re-reading files it has already seen.
This module builds a small, native code map of the worktree once, at run
start, so a worker can ask "where is this defined", "where is this name
used", "what does this file define" without shelling out to grep.

The design constraints come from the issue:

  * Standard library only.  No ctags, no ast-grep, no tree-sitter, no
    CodeGraph.  Python definitions come from the `ast` module; every other
    language comes from the short, documented list of regular expressions
    in LANGUAGE_PATTERNS below.
  * The index is a SQLite database built at worker start and stored OUTSIDE
    the worktree (a store inside the worktree would look like a worker edit
    to the empty-diff gate).  Its file name is keyed by the worktree's
    realpath plus the HEAD sha it was built from, so two worktrees -- or
    one worktree at two commits -- never share an index.
  * Occurrence edges are name-based: a known symbol name that occurs in a
    file is an edge to that file and line.  They are built in ONE
    tokenizing pass per file (never one scan per symbol).  Each file is
    read at most twice: once to extract its definitions, once for the
    occurrence pass.
  * A symbol whose name occurs in more than TOO_COMMON_FILES files is
    reported as "too common to map" instead of dumping hundreds of rows.
  * The build is bounded: it raises CodeMapTimeout once the time budget
    (BUILD_TIMEOUT_SECONDS, 60s by default) is spent.  A failed or
    timed-out build means the caller carries on without the tool, and
    bin/agent.py records a `codemap_build_failed` transcript event.

The gate is $WORKER_CODE_MAP=1 and it is OFF by default: nothing in this
module arms itself, and bin/agent.py only advertises the tool when the
gate is on *and* the index was built.

Run standalone (build cost measurement):
    python3 bin/codemap.py --repo /path/to/repo
    python3 bin/codemap.py --repo /path/to/repo --json
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from urllib.parse import quote

# --- the gate --------------------------------------------------------------

CODE_MAP_ENV = "WORKER_CODE_MAP"
# Where the index lives.  $WORKER_CODE_MAP_DIR overrides the root; the
# default root is a per-run directory next to the run's transcript.
CODE_MAP_DIR_ENV = "WORKER_CODE_MAP_DIR"
# The build's time budget, in seconds.  Past it the build gives up and the
# tool is not advertised at all.
BUILD_TIMEOUT_SECONDS = 60.0
# A name that occurs in more files than this is not mappable: the answer
# would be a wall of noise, so the query says so instead.
TOO_COMMON_FILES = 50
# Result caps (the tool result is a context message, not a data dump).
MAX_ROWS = 40
MAX_CHARS = 4000
# Files bigger than this are not indexed (generated blobs, fixtures).
MAX_FILE_BYTES = 1_000_000

# --- what gets indexed -----------------------------------------------------

# Directories that are never walked into.  This is the ".gitignore-level"
# exclusion list from the issue: version-control metadata, dependency
# trees, virtualenvs and build output.
EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "vendor",
    ".venv", "venv", "env", "site-packages",
    "build", "dist", "target", "out",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".eggs", ".next", ".cache",
})

# Extension -> language.  Only these files are read.
EXT_LANGUAGE = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".rb": "ruby", ".sh": "shell", ".bash": "shell",
    ".php": "php", ".cs": "csharp",
}

# The documented pattern list for the non-Python languages.  Each entry is
# (kind, regex); the regex must expose the symbol name as group 1 and is
# matched against one source line at a time.
_JS_PATTERNS = (
    ("function",
     (r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+"
      r"([A-Za-z_$][\w$]*)")),
    ("function",
     (r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
      r"(?:async\s*)?(?:function\b|\(|<)")),
    ("class",
     (r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+"
      r"([A-Za-z_$][\w$]*)")),
)

LANGUAGE_PATTERNS = {
    "javascript": _JS_PATTERNS,
    "typescript": _JS_PATTERNS,
    "go": (
        ("function", r"^func\s+([A-Za-z_]\w*)\s*\("),
        ("method", r"^func\s+\([^)]*\)\s+([A-Za-z_]\w*)\s*\("),
        ("type", r"^type\s+([A-Za-z_]\w*)\s+"),
    ),
    "rust": (
        ("function",
         (r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+"
          r"([A-Za-z_]\w*)")),
        ("struct", r"^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+([A-Za-z_]\w*)"),
        ("enum", r"^\s*(?:pub(?:\([^)]*\))?\s+)?enum\s+([A-Za-z_]\w*)"),
        ("trait", r"^\s*(?:pub(?:\([^)]*\))?\s+)?trait\s+([A-Za-z_]\w*)"),
    ),
    "java": (
        ("class",
         r"^\s*(?:public\s+|final\s+|abstract\s+)*class\s+([A-Za-z_]\w*)"),
        ("interface", r"^\s*(?:public\s+)?interface\s+([A-Za-z_]\w*)"),
        ("enum", r"^\s*(?:public\s+)?enum\s+([A-Za-z_]\w*)"),
    ),
    "c": (
        ("function",
         r"^[A-Za-z_][\w\s\*&:<>,]*?([A-Za-z_]\w*)\s*\([^;]*\)\s*\{"),
        ("struct", r"^\s*(?:typedef\s+)?struct\s+([A-Za-z_]\w*)"),
    ),
    "cpp": (
        ("function",
         r"^[A-Za-z_][\w\s\*&:<>,]*?([A-Za-z_]\w*)\s*\([^;]*\)\s*\{"),
        ("class", r"^\s*class\s+([A-Za-z_]\w*)"),
        ("struct", r"^\s*(?:typedef\s+)?struct\s+([A-Za-z_]\w*)"),
    ),
    "ruby": (
        ("function", r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)"),
        ("class", r"^\s*class\s+([A-Za-z_]\w*)"),
        ("module", r"^\s*module\s+([A-Za-z_]\w*)"),
    ),
    "shell": (
        ("function", r"^\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\)\s*\{"),
        ("function", r"^\s*function\s+([A-Za-z_]\w*)"),
    ),
    "php": (
        ("function",
         (r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+"
          r"([A-Za-z_]\w*)")),
        ("class",
         (r"^\s*(?:abstract\s+|final\s+)?class\s+([A-Za-z_]\w*)")),
    ),
    "csharp": (
        ("class",
         (r"^\s*(?:public\s+|internal\s+|sealed\s+|abstract\s+|partial\s+)*"
          r"class\s+([A-Za-z_]\w*)")),
        ("method",
         (r"^\s+(?:public\s+|private\s+|protected\s+|internal\s+|static\s+|"
          r"virtual\s+|override\s+|async\s+)*[\w<>\[\],.\s]+?\s+"
          r"([A-Za-z_]\w*)\s*\([^;]*\)\s*\{")),
    ),
}

# A cheap pre-filter per language: a line that holds no trigger word
# cannot hold a definition, so it never reaches the pattern list.  This is
# what keeps the build linear in the size of the tree.
LANGUAGE_TRIGGERS = {
    "javascript": ("function", "class", "const", "let", "var"),
    "typescript": ("function", "class", "const", "let", "var"),
    "go": ("func", "type"),
    "rust": ("fn", "struct", "enum", "trait"),
    "java": ("class", "interface", "enum"),
    "c": ("{", "struct"),
    "cpp": ("{", "struct", "class"),
    "ruby": ("def ", "class ", "module "),
    "shell": ("function", "()"),
    "php": ("function", "class"),
    "csharp": ("class", "{"),
}

# Words a definition pattern may capture but that are never symbols.
_STOPWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "do", "else",
    "case", "match", "with", "try", "new", "delete", "sizeof", "typeof",
    "class", "def", "fn", "func", "function", "struct", "enum", "trait",
    "interface", "type", "module", "public", "private", "protected",
    "static", "const", "let", "var", "void", "int", "char", "bool",
    "float", "double", "long", "short", "unsigned", "signed", "extern",
    "template", "typename", "operator", "this", "self", "super", "null",
    "nil", "true", "false", "and", "or", "not", "in", "is", "as", "await",
    "async", "yield", "lambda", "print", "echo",
})

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

QUERY_MODES = ("def", "callers", "symbols_in", "refs")

CODE_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "code_query",
        "description": (
            "Query the run's code map, an index of the worktree built when "
            "the run started. mode=def: where a name is defined. "
            "mode=callers: where a name occurs (name-based, so a hit is "
            "not proof of a call). mode=symbols_in: what a file defines. "
            "mode=refs: every occurrence of a name. The index is a "
            "snapshot from the start of the run, so confirm anything it "
            "says against the current file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": list(QUERY_MODES),
                    "description": "def | callers | symbols_in | refs",
                },
                "query": {
                    "type": "string",
                    "description": "a symbol name (def, callers, refs) or "
                                   "a file path (symbols_in)",
                },
            },
            "required": ["mode", "query"],
        },
    },
}


class CodeMapError(Exception):
    """The index could not be built."""


class CodeMapTimeout(CodeMapError):
    """The index build ran out of its time budget."""


# --- the gate and the store ------------------------------------------------


def build_timeout() -> float:
    """The build's time budget: $WORKER_CODE_MAP_TIMEOUT overrides 60s.

    Read at call time, not at import time, so an operator (or a test) can
    change it without re-importing the module.
    """
    raw = os.environ.get("WORKER_CODE_MAP_TIMEOUT")
    if raw is None:
        return BUILD_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return BUILD_TIMEOUT_SECONDS


def code_map_enabled() -> bool:
    """The gate: $WORKER_CODE_MAP=1 turns the code map on.  Off by default."""
    raw = os.environ.get(CODE_MAP_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def index_store_dir(workdir: str, transcript_dir: str, run_id: str) -> str:
    """Where this run's code-map index lives.

    Mirrors bin/agent.py's _ctx_store_dir: the default root is a per-run
    directory next to the run's transcript (i.e. outside the worktree),
    $WORKER_CODE_MAP_DIR overrides the root, and a store that would land
    inside the workdir is refused in favour of a directory outside it --
    an index inside the worktree would look like a worker edit to the
    empty-diff gate.
    """
    root = os.path.expanduser(
        os.environ.get(CODE_MAP_DIR_ENV)
        or os.path.join(transcript_dir, "codemap"))
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id) or "run"
    directory = os.path.join(root, safe)
    real = os.path.realpath(directory)
    base = os.path.realpath(workdir)
    if real == base or real.startswith(base + os.sep):
        return tempfile.mkdtemp(prefix="pc-codemap-")
    return directory


def index_filename(worktree: str, head_sha: str) -> str:
    """The index file name: keyed by the worktree realpath and the HEAD sha.

    Two worktrees, or the same worktree at two commits, never share a
    file, so a stale index cannot be picked up by accident.
    """
    real = os.path.realpath(worktree)
    base = os.path.basename(real.rstrip(os.sep)) or "worktree"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", base)[:40].strip("-") or "worktree"
    digest = hashlib.sha256(real.encode("utf-8", "replace")).hexdigest()[:8]
    sha = re.sub(r"[^0-9a-f]+", "", (head_sha or "").lower())[:12] or "nohead"
    return f"{slug}-{digest}-{sha}.sqlite3"


def head_sha(workdir: str) -> str:
    """The HEAD sha of the worktree, or "nohead" when git cannot tell."""
    try:
        proc = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return "nohead"
    if proc.returncode != 0:
        return "nohead"
    return proc.stdout.strip() or "nohead"


# --- extracting symbols ----------------------------------------------------


def _python_symbols(text: str) -> list[tuple[str, str, int]]:
    """(name, kind, line) for every definition in a Python source text.

    The `ast` module is the parser: no regular expressions, no external
    tools.  Module- and class-level assignments are "variable"; anything
    inside a function body is a local and is not indexed.
    """
    tree = ast.parse(text)
    found: list[tuple[str, str, int]] = []

    def visit(node, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                found.append((child.name, "class", child.lineno))
                visit(child, "class")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if scope == "class" else "function"
                found.append((child.name, kind, child.lineno))
                visit(child, "function")
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                if scope in ("module", "class"):
                    targets = (child.targets if isinstance(child, ast.Assign)
                               else [child.target])
                    for target in targets:
                        if isinstance(target, ast.Name):
                            found.append((target.id, "variable",
                                          child.lineno))
                        elif isinstance(target, ast.Attribute):
                            found.append((target.attr, "variable",
                                          child.lineno))
            else:
                visit(child, scope)

    visit(tree, "module")
    return found


def _regex_symbols(text: str, language: str) -> list[tuple[str, str, int]]:
    """(name, kind, line) for every definition matched by the language's
    documented patterns (see LANGUAGE_PATTERNS)."""
    patterns = LANGUAGE_PATTERNS.get(language, ())
    if not patterns:
        return []
    compiled = [(kind, re.compile(pattern)) for kind, pattern in patterns]
    triggers = LANGUAGE_TRIGGERS.get(language, ())
    trigger_re = (re.compile("|".join(re.escape(t) for t in triggers))
                  if triggers else None)
    found: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str, int]] = set()
    for lineno, line in enumerate(text.splitlines(), 1):
        if trigger_re is not None and trigger_re.search(line) is None:
            continue
        for kind, pattern in compiled:
            match = pattern.match(line)
            if match is None:
                continue
            name = match.group(1)
            if name in _STOPWORDS or (name, kind, lineno) in seen:
                continue
            seen.add((name, kind, lineno))
            found.append((name, kind, lineno))
    return found


def _symbols_for(rel_path: str, text: str) -> list[tuple[str, str, int]]:
    """The definitions in one file, or an empty list when it cannot be read."""
    language = EXT_LANGUAGE.get(os.path.splitext(rel_path)[1].lower())
    if language is None:
        return []
    if language == "python":
        try:
            return _python_symbols(text)
        except (SyntaxError, ValueError, RecursionError):
            return []
    return _regex_symbols(text, language)


def _read_text(path: str) -> str | None:
    """The file's text, or None when it cannot be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def iter_source_files(workdir: str, deadline: float | None = None):
    """Every indexable source file under workdir, as (relative path, path).

    The walk never descends into EXCLUDED_DIRS and never follows symlinks.
    """
    for dirpath, dirnames, filenames in os.walk(workdir):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() not in EXT_LANGUAGE:
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield os.path.relpath(full, workdir), full
        _check_deadline(deadline)


def _check_deadline(deadline: float | None) -> None:
    """Stop the build when its time budget is gone."""
    if deadline is not None and time.monotonic() > deadline:
        raise CodeMapTimeout(
            f"code map build exceeded its {BUILD_TIMEOUT_SECONDS:g}s budget")


def _occurrences(text: str, names: set[str]) -> list[tuple[str, int]]:
    """Every occurrence of a known symbol name, in one pass over the text.

    The pass is a single tokenizing scan of the file -- the naive
    alternative, one regex scan per symbol, is what makes a code map
    expensive.  Identifiers inside strings and comments count as
    occurrences: the edges are name-based on purpose, and the tool says so.
    """
    found: list[tuple[str, int]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in _IDENT_RE.finditer(line):
            name = match.group(0)
            if name in names:
                found.append((name, lineno))
    return found


# --- building the index ----------------------------------------------------

_SCHEMA = (
    ("CREATE TABLE IF NOT EXISTS meta ("
     " key TEXT PRIMARY KEY, value TEXT)"),
    ("CREATE TABLE IF NOT EXISTS symbols ("
     " id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,"
     " file TEXT NOT NULL, line INTEGER NOT NULL)"),
    ("CREATE TABLE IF NOT EXISTS edges ("
     " name TEXT NOT NULL, file TEXT NOT NULL, line INTEGER NOT NULL)"),
)

# The indexes are built after the bulk insert: maintaining them row by row
# is the slowest part of the build, and the rows are already in place.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS symbols_by_name ON symbols(name)",
    "CREATE INDEX IF NOT EXISTS symbols_by_file ON symbols(file)",
    "CREATE INDEX IF NOT EXISTS edges_by_name ON edges(name)",
)


def _create_schema(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA:
        conn.execute(statement)
    # The index is a throwaway build artefact: it is written to a
    # temporary file and moved into place only when the build succeeds,
    # so the fastest safe journal settings are the right ones.
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")


def _collect_symbols(conn: sqlite3.Connection, workdir: str,
                     deadline: float | None):
    """Phase one: the definitions.  Returns (names, count, files)."""
    names: set[str] = set()
    count = 0
    files: list[tuple[str, str]] = []
    batch: list[tuple[str, str, str, int]] = []
    for rel, full in iter_source_files(workdir, deadline):
        _check_deadline(deadline)
        text = _read_text(full)
        if text is None:
            continue
        files.append((rel, full))
        for name, kind, line in _symbols_for(rel, text):
            names.add(name)
            count += 1
            batch.append((name, kind, rel, line))
            if len(batch) >= 2000:
                conn.executemany(
                    "INSERT INTO symbols (name, kind, file, line) "
                    "VALUES (?, ?, ?, ?)", batch)
                batch.clear()
    if batch:
        conn.executemany(
            "INSERT INTO symbols (name, kind, file, line) VALUES (?, ?, ?, ?)",
            batch)
    return names, count, files


def _collect_edges(conn: sqlite3.Connection, files, names: set[str],
                   deadline: float | None) -> int:
    """Phase two: the occurrence edges, one tokenizing pass per file."""
    if not names:
        return 0
    count = 0
    batch: list[tuple[str, str, int]] = []
    for rel, full in files:
        _check_deadline(deadline)
        text = _read_text(full)
        if text is None:
            continue
        for name, line in _occurrences(text, names):
            count += 1
            batch.append((name, rel, line))
            if len(batch) >= 5000:
                conn.executemany(
                    "INSERT INTO edges (name, file, line) VALUES (?, ?, ?)",
                    batch)
                batch.clear()
    if batch:
        conn.executemany(
            "INSERT INTO edges (name, file, line) VALUES (?, ?, ?)", batch)
    return count


def build_index(workdir: str, db_path: str,
                deadline: float | None = None) -> dict:
    """Build the SQLite index of workdir at db_path and return its stats.

    The database is written to a temporary file next to db_path and moved
    into place at the end, so a failed build never leaves a half-written
    index behind.
    """
    started = time.monotonic()
    _check_deadline(deadline)
    workdir = os.path.realpath(workdir)
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    tmp = f"{db_path}.tmp-{os.getpid()}"
    conn = sqlite3.connect(tmp)
    built = False
    try:
        _create_schema(conn)
        names, symbol_count, files = _collect_symbols(conn, workdir,
                                                       deadline)
        edge_count = _collect_edges(conn, files, names, deadline)
        for statement in _INDEXES:
            conn.execute(statement)
        stats = {
            "head_sha": head_sha(workdir),
            "built_at": datetime.now(UTC).isoformat(),
            "build_seconds": round(time.monotonic() - started, 3),
            "worktree": workdir,
            "file_count": len(files),
            "symbol_count": symbol_count,
            "edge_count": edge_count,
        }
        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            sorted(stats.items()))
        conn.commit()
        built = True
    finally:
        conn.close()
        if not built:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
    os.replace(tmp, db_path)
    return stats


def build_code_map(workdir: str, transcript_dir: str, run_id: str,
                   timeout: float | None = None) -> "CodeMap":
    """Build this run's index and return the read-only view of it.

    Raises CodeMapError (or CodeMapTimeout) when the build fails; the
    caller then carries on without the tool.
    """
    if timeout is None:
        timeout = build_timeout()
    store = index_store_dir(workdir, transcript_dir, run_id)
    head = head_sha(workdir)
    db_path = os.path.join(store, index_filename(workdir, head))
    build_index(workdir, db_path, deadline=time.monotonic() + timeout)
    return CodeMap(db_path)


# --- querying the index ----------------------------------------------------


class CodeMap:
    """A read-only view over a built code-map index."""

    def __init__(self, db_path: str):
        self.path = db_path
        uri = "file:" + quote(os.path.abspath(db_path)) + "?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)
        self.meta = {
            key: value for key, value in
            self.conn.execute("SELECT key, value FROM meta")
        }

    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self.conn.close()

    def __enter__(self) -> "CodeMap":
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False

    # -- header -----------------------------------------------------------

    def header(self, mode: str, query: str) -> str:
        """The header every answer carries: which index, how old it is."""
        head = self.meta.get("head_sha", "unknown")
        seconds = self.meta.get("build_seconds", "?")
        return (f"[codemap] mode={mode} query={query!r} | "
                f"index HEAD={head} | built in {seconds}s | "
                "as of run start: the index is a snapshot of the worktree "
                "when this run started, so edits made since then are not "
                "in it")

    # -- queries ----------------------------------------------------------

    def _occurrence_files(self, name: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT file) FROM edges WHERE name = ?",
            (name,)).fetchone()
        return int(row[0]) if row else 0

    def _files_for(self, path: str) -> list[str]:
        """The indexed files a symbols_in query names.

        An exact relative path wins; otherwise the basename is matched
        against the end of the path, so "agent.py" finds "bin/agent.py".
        """
        rows = self.conn.execute(
            "SELECT DISTINCT file FROM symbols WHERE file = ?",
            (path,)).fetchall()
        if rows:
            return [row[0] for row in rows]
        base = os.path.basename(path)
        if not base:
            return []
        escaped = base.replace("\\", "\\\\").replace("%", "\\%") \
            .replace("_", "\\_")
        rows = self.conn.execute(
            "SELECT DISTINCT file FROM symbols "
            "WHERE file LIKE ? ESCAPE '\\' ORDER BY file",
            (f"%/{escaped}",)).fetchall()
        return [row[0] for row in rows]

    def _rows(self, mode: str, query: str) -> list[tuple]:
        if mode == "def":
            return self.conn.execute(
                "SELECT file, line, kind, name FROM symbols WHERE name = ? "
                "ORDER BY file, line", (query,)).fetchall()
        if mode == "callers":
            return self.conn.execute(
                "SELECT e.file, e.line FROM edges e WHERE e.name = ? AND "
                "NOT EXISTS (SELECT 1 FROM symbols s WHERE s.name = e.name "
                "AND s.file = e.file AND s.line = e.line) "
                "ORDER BY e.file, e.line", (query,)).fetchall()
        if mode == "symbols_in":
            files = self._files_for(query)
            if not files:
                return []
            placeholders = ",".join("?" * len(files))
            return self.conn.execute(
                "SELECT file, line, kind, name FROM symbols WHERE file IN "
                f"({placeholders}) ORDER BY file, line", files).fetchall()
        return self.conn.execute(
            "SELECT file, line FROM edges WHERE name = ? ORDER BY file, line",
            (query,)).fetchall()

    @staticmethod
    def _format(mode: str, rows: list[tuple]) -> list[str]:
        lines: list[str] = []
        for row in rows:
            if mode in ("def", "symbols_in"):
                file, line, kind, name = row
                lines.append(f"{file}:{line}  {kind} {name}")
            elif mode == "callers":
                file, line = row
                lines.append(f"{file}:{line}  occurrence (name-based)")
            else:
                file, line = row
                lines.append(f"{file}:{line}  reference")
        return lines

    def query(self, mode: str, query: str) -> str:
        """Answer one code_query call.  The result is capped, and the cap
        says what was left out."""
        mode = str(mode or "").strip().lower()
        query = str(query or "").strip()
        if mode not in QUERY_MODES:
            return (f"[codemap] unknown mode {mode!r}; use one of: "
                    f"{', '.join(QUERY_MODES)}")
        if not query:
            return f"[codemap] mode={mode}: empty query"
        header = self.header(mode, query)
        if mode in ("def", "callers", "refs"):
            spread = self._occurrence_files(query)
            if spread > TOO_COMMON_FILES:
                return (f"{header}\n"
                        f"[codemap] {query!r} occurs in {spread} files -- "
                        f"too common to map (threshold: {TOO_COMMON_FILES} "
                        "files). Narrow the query, or use symbols_in on a "
                        "file path.")
        try:
            rows = self._rows(mode, query)
        except sqlite3.Error as exc:
            return f"{header}\n[codemap] query failed: {exc}"
        if not rows:
            if mode == "symbols_in":
                return (f"{header}\n[codemap] no indexed file matches "
                        f"{query!r}")
            return f"{header}\n[codemap] nothing in the index for {query!r}"
        lines = self._format(mode, rows)
        shown = lines[:MAX_ROWS]
        dropped_rows = len(lines) - len(shown)
        body = "\n".join(shown)
        dropped_chars = 0
        if len(body) > MAX_CHARS:
            dropped_chars = len(body) - MAX_CHARS
            body = body[:MAX_CHARS]
        out = [header, body]
        if dropped_rows or dropped_chars:
            out.append(
                f"[codemap] TRUNCATED: {dropped_rows} more row(s) and "
                f"{dropped_chars} more char(s) were not shown (caps: "
                f"{MAX_ROWS} rows / {MAX_CHARS} chars)")
        return "\n".join(out)


# --- command line ----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Build an index of a repository and report the build cost.

    Used for the build-cost measurement (issue #80, section 2.5); the
    harness itself never calls this.
    """
    parser = argparse.ArgumentParser(
        description="Build a code-map index of a repository.")
    parser.add_argument("--repo", required=True,
                        help="repository or worktree to index")
    parser.add_argument("--db", default=None,
                        help="index file to write (default: a temporary file)")
    parser.add_argument("--timeout", type=float, default=None,
                        help="build time budget in seconds "
                             "(default: $WORKER_CODE_MAP_TIMEOUT or 60)")
    parser.add_argument("--json", action="store_true",
                        help="print the stats as JSON")
    args = parser.parse_args(argv)
    timeout = args.timeout if args.timeout is not None else build_timeout()
    db_path = args.db or os.path.join(
        tempfile.mkdtemp(prefix="pc-codemap-"), "index.sqlite3")
    started = time.monotonic()
    stats = build_index(args.repo, db_path,
                        deadline=time.monotonic() + timeout)
    stats["wall_seconds"] = round(time.monotonic() - started, 3)
    stats["db_path"] = db_path
    if args.json:
        print(json.dumps(stats, sort_keys=True))
    else:
        print(f"repo: {stats['worktree']}")
        print(f"index: {db_path}")
        print(f"build seconds: {stats['build_seconds']}")
        print(f"files: {stats['file_count']}")
        print(f"symbols: {stats['symbol_count']}")
        print(f"edges: {stats['edge_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
