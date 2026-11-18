"""Tests for the native code map (plan-coder issue #80, section 2).

The build: bin/codemap.py indexes the worktree into a SQLite database at
run start -- Python definitions via the `ast` module, every other language
via a short documented list of regular expressions -- and bin/agent.py
advertises a `code_query` tool for it, gated on $WORKER_CODE_MAP=1.

Pinned here:

  * the indexer: ast-based Python symbols, pattern-based symbols for other
    languages, the excluded directories (.git, node_modules, venvs,
    build/dist), the name-based occurrence edges, and the "too common to
    map" rule for a name that occurs in more than 50 files;
  * the store: outside the worktree, keyed by the worktree realpath plus
    the HEAD sha;
  * the gate: the tool is absent from the run's tool list when the gate is
    off and present when it is on, tested at the same seam the ctx_read
    tool uses (the tool list the run actually sends to the model), and a
    failed or timed-out build leaves the tool absent and logs a
    `codemap_build_failed` event.

No network, no live harness: the provider is a local mock server and every
file written lands in a temporary directory.

Run: python3 -m pytest tests/test_codemap.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"

sys.path.insert(0, str(BIN))
_spec = importlib.util.spec_from_file_location("agent_codemap", BIN / "agent.py")
agent = importlib.util.module_from_spec(_spec)
sys.modules["agent_codemap"] = agent
_spec.loader.exec_module(agent)

import codemap  # noqa: E402

PY_SOURCE = '''"""A module."""

LIMIT = 10


class Widget:
    """A widget."""

    def render(self):
        return helper()


def helper():
    return LIMIT
'''

JS_SOURCE = """export function buildApp(config) {
  return new App(config);
}

export class App {
  constructor(config) {
    this.config = config;
  }
}
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build_map(root: Path, tmp: Path, name: str = "index.sqlite3"):
    """Build an index of root and return the read-only view of it."""
    db_path = Path(tmp) / name
    codemap.build_index(str(root), str(db_path))
    return codemap.CodeMap(str(db_path))


class IndexerTests(unittest.TestCase):
    """The indexer: what it finds, and what it refuses to look at."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-codemap-idx-"))
        self.root = self.tmp / "wt"
        self.root.mkdir()

    def test_python_definitions_come_from_the_ast(self):
        write(self.root / "pkg" / "mod.py", PY_SOURCE)
        cmap = build_map(self.root, self.tmp)
        answer = cmap.query("def", "helper")
        self.assertIn("pkg/mod.py:13", answer)
        self.assertIn("function helper", answer)
        answer = cmap.query("def", "Widget")
        self.assertIn("pkg/mod.py:6", answer)
        self.assertIn("class Widget", answer)
        answer = cmap.query("def", "render")
        self.assertIn("pkg/mod.py:9", answer)
        self.assertIn("method render", answer)
        answer = cmap.query("def", "LIMIT")
        self.assertIn("pkg/mod.py:3", answer)
        self.assertIn("variable LIMIT", answer)

    def test_other_languages_come_from_the_documented_patterns(self):
        write(self.root / "app.js", JS_SOURCE)
        cmap = build_map(self.root, self.tmp)
        self.assertIn("function buildApp", cmap.query("def", "buildApp"))
        self.assertIn("class App", cmap.query("def", "App"))

    def test_excluded_directories_are_never_indexed(self):
        write(self.root / "keep.py", PY_SOURCE)
        for excluded in (".git", "node_modules", ".venv", "venv", "build",
                         "dist"):
            write(self.root / excluded / "hidden.py",
                  "def hidden_symbol():\n    pass\n")
        cmap = build_map(self.root, self.tmp)
        self.assertIn("function helper", cmap.query("def", "helper"))
        self.assertIn("nothing in the index",
                      cmap.query("def", "hidden_symbol"))

    def test_occurrence_edges_are_name_based(self):
        write(self.root / "lib.py", PY_SOURCE)
        write(self.root / "use.py", "from lib import helper\n\n"
                                    "def go():\n    return helper()\n")
        cmap = build_map(self.root, self.tmp)
        refs = cmap.query("refs", "helper")
        self.assertIn("lib.py:13", refs)
        self.assertIn("use.py:4", refs)
        callers = cmap.query("callers", "helper")
        self.assertIn("use.py:4", callers)
        # the definition line itself is not a call site
        self.assertNotIn("lib.py:13", callers)

    def test_a_name_in_more_than_fifty_files_is_too_common_to_map(self):
        for i in range(51):
            write(self.root / f"mod{i}.py",
                  f"def common_symbol():\n    return {i}\n")
        cmap = build_map(self.root, self.tmp)
        answer = cmap.query("def", "common_symbol")
        self.assertIn("too common to map", answer)
        self.assertIn("51 files", answer)

    def test_a_name_in_few_files_is_not_too_common(self):
        write(self.root / "one.py", "def rare_symbol():\n    pass\n")
        cmap = build_map(self.root, self.tmp)
        self.assertNotIn("too common to map", cmap.query("def", "rare_symbol"))

    def test_symbols_in_lists_the_symbols_of_a_file(self):
        write(self.root / "pkg" / "mod.py", PY_SOURCE)
        cmap = build_map(self.root, self.tmp)
        answer = cmap.query("symbols_in", "pkg/mod.py")
        self.assertIn("class Widget", answer)
        self.assertIn("function helper", answer)
        self.assertIn("method render", answer)

    def test_the_index_file_is_keyed_by_the_worktree_and_the_head(self):
        other = self.tmp / "other-wt"
        other.mkdir()
        first = codemap.index_filename("/a/worktree", "abc123")
        same = codemap.index_filename("/a/worktree", "abc123")
        other_head = codemap.index_filename("/a/worktree", "def456")
        other_tree = codemap.index_filename("/b/worktree", "abc123")
        self.assertEqual(first, same)
        self.assertNotEqual(first, other_head)
        self.assertNotEqual(first, other_tree)
        self.assertIn("abc123", first)

    def test_the_store_never_lands_inside_the_worktree(self):
        inside = str(self.root / "codemap")
        directory = codemap.index_store_dir(str(self.root), inside, "run-1")
        self.assertFalse(os.path.realpath(directory).startswith(
            os.path.realpath(self.root) + os.sep), directory)

    def test_the_store_defaults_next_to_the_transcript(self):
        transcripts = self.tmp / "transcripts"
        transcripts.mkdir()
        directory = codemap.index_store_dir(str(self.root), str(transcripts),
                                            "run-42")
        self.assertEqual(directory, os.path.join(str(transcripts), "codemap",
                                                "run-42"))


class QueryTests(unittest.TestCase):
    """The answer carries its provenance and its caps."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-codemap-q-"))
        self.root = self.tmp / "wt"
        self.root.mkdir()
        write(self.root / "mod.py", PY_SOURCE)

    def test_the_header_carries_the_head_sha_and_the_build_time(self):
        cmap = build_map(self.root, self.tmp)
        answer = cmap.query("def", "helper")
        self.assertIn("index HEAD=", answer)
        self.assertIn("built in ", answer)
        self.assertIn("as of run start", answer)

    def test_results_are_capped_and_the_cap_says_what_was_left_out(self):
        body = "\n".join("many_symbol()" for _ in range(60))
        write(self.root / "many.py", "def many_symbol():\n    pass\n\n"
                                    + body + "\n")
        cmap = build_map(self.root, self.tmp)
        answer = cmap.query("refs", "many_symbol")
        self.assertIn("TRUNCATED", answer)
        self.assertIn("more row(s)", answer)
        self.assertLessEqual(len(answer.splitlines()),
                             codemap.MAX_ROWS + 10)

    def test_an_unknown_mode_is_rejected(self):
        cmap = build_map(self.root, self.tmp)
        answer = cmap.query("nonsense", "helper")
        self.assertIn("unknown mode", answer)

    def test_an_empty_query_is_rejected(self):
        cmap = build_map(self.root, self.tmp)
        self.assertIn("empty query", cmap.query("def", "  "))


class GateTests(unittest.TestCase):
    """The gate: off by default, and the tool set follows it exactly."""

    def test_the_gate_defaults_to_off(self):
        saved = os.environ.pop("WORKER_CODE_MAP", None)
        try:
            self.assertFalse(codemap.code_map_enabled())
            self.assertFalse(agent.code_map_enabled())
        finally:
            if saved is not None:
                os.environ["WORKER_CODE_MAP"] = saved

    def test_the_env_flag_turns_the_gate_on(self):
        saved = os.environ.get("WORKER_CODE_MAP")
        os.environ["WORKER_CODE_MAP"] = "1"
        try:
            self.assertTrue(codemap.code_map_enabled())
            self.assertTrue(agent.code_map_enabled())
        finally:
            if saved is None:
                os.environ.pop("WORKER_CODE_MAP", None)
            else:
                os.environ["WORKER_CODE_MAP"] = saved

    def test_the_code_query_tool_is_not_in_the_global_tool_list(self):
        names = [tool["function"]["name"] for tool in agent.TOOLS]
        self.assertNotIn("code_query", names)
        self.assertEqual(agent.CODE_QUERY_TOOL["function"]["name"],
                         "code_query")
        self.assertEqual(
            agent.CODE_QUERY_TOOL["function"]["parameters"]["required"],
            ["mode", "query"])


class _MockProvider(BaseHTTPRequestHandler):
    """One plain answer: enough for the run to start and finish."""

    requests: ClassVar[list] = []
    calls = 0

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(payload)
        type(self).calls += 1
        message = {"role": "assistant", "content": "done"}
        body = json.dumps({
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class RunGateTests(unittest.TestCase):
    """The seam: the tool list the run actually sends to the model.

    Same seam the ctx_read tool uses -- the tool list assembled in main()
    -- so an off run keeps exactly the tool set it has today and an on run
    carries one more tool.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pc-codemap-run-")
        self.workdir = os.path.join(self.tmp, "wt")
        os.makedirs(self.workdir)
        write(Path(self.workdir) / "mod.py", PY_SOURCE)
        self.transcripts = os.path.join(self.tmp, "tr")
        os.makedirs(self.transcripts)
        self.brief = os.path.join(self.tmp, "BRIEF.md")
        with open(self.brief, "w", encoding="utf-8") as fh:
            fh.write("Do the thing.\n")
        self.saved_argv = list(sys.argv)
        self.saved_key = os.environ.get("MINIMAX_API_KEY")
        self.saved_url = agent.PROVIDERS["minimax"]["chat_url"]
        os.environ["MINIMAX_API_KEY"] = "test-key-123"
        _MockProvider.requests = []
        _MockProvider.calls = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockProvider)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        agent.PROVIDERS["minimax"]["chat_url"] = (
            f"http://127.0.0.1:{self.server.server_address[1]}/chat")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        sys.argv = self.saved_argv
        if self.saved_key is None:
            os.environ.pop("MINIMAX_API_KEY", None)
        else:
            os.environ["MINIMAX_API_KEY"] = self.saved_key
        agent.PROVIDERS["minimax"]["chat_url"] = self.saved_url

    def _run(self, extra_args=(), code_map=None, timeout=None):
        env_saved = {}
        for name, value in (("WORKER_CODE_MAP", code_map),
                            ("WORKER_CODE_MAP_TIMEOUT", timeout),
                            ("WORKER_EMPTY_DIFF_GATE", "0")):
            env_saved[name] = os.environ.get(name)
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        try:
            sys.argv = ["agent.py", "--brief", self.brief,
                        "--workdir", self.workdir,
                        "--provider", "minimax", "--model-tier", "cheap",
                        "--max-steps", "3",
                        "--run-id", "codemap-e2e",
                        "--transcript-dir", self.transcripts]
            sys.argv.extend(extra_args)
            rc = agent.main()
        finally:
            for name, value in env_saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.assertEqual(rc, 0)
        with open(os.path.join(self.transcripts, "codemap-e2e.jsonl"),
                  encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        return events

    @staticmethod
    def _tool_names():
        return [tool["function"]["name"]
                for tool in _MockProvider.requests[0]["tools"]]

    def test_the_gate_off_run_keeps_exactly_the_current_tools(self):
        self._run(["--stored-output"])
        names = self._tool_names()
        self.assertEqual(names, ["exec", "read_file", "write_file",
                                 "edit_file", "list_dir", "ctx_read"])
        self.assertNotIn("code_query", names)

    def test_the_gate_on_run_carries_the_code_query_tool(self):
        self._run(code_map="1")
        names = self._tool_names()
        self.assertIn("code_query", names)
        self.assertEqual(len(names), 6)

    def test_the_gate_off_run_does_not_mention_the_code_map(self):
        self._run()
        system = _MockProvider.requests[0]["messages"][0]["content"]
        self.assertNotIn("code_query", system)

    def test_the_gate_on_run_mentions_the_code_map_once(self):
        self._run(code_map="1")
        system = _MockProvider.requests[0]["messages"][0]["content"]
        self.assertIn("code_query", system)
        self.assertEqual(system.count("code_query"), 1)

    def test_a_failed_build_leaves_the_tool_absent_and_is_logged(self):
        events = self._run(code_map="1", timeout="0")
        names = self._tool_names()
        self.assertNotIn("code_query", names)
        failures = [e for e in events
                    if e.get("event") == "codemap_build_failed"]
        self.assertTrue(failures, events)
        self.assertIn("error", failures[0])
        started = next(e for e in events if e.get("event") == "started")
        self.assertFalse(started["code_map"])

    def test_the_index_is_stored_outside_the_worktree(self):
        self._run(code_map="1")
        store = os.path.join(self.transcripts, "codemap", "codemap-e2e")
        self.assertTrue(os.path.isdir(store), store)
        for root, _dirs, files in os.walk(store):
            for name in files:
                self.assertFalse(
                    os.path.realpath(os.path.join(root, name)).startswith(
                        os.path.realpath(self.workdir) + os.sep))


if __name__ == "__main__":
    unittest.main()
