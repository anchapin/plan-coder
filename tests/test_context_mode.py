"""Tests for the Context Mode build (plan-coder issue #52).

The build: raw tool output is stored in a per-run store on disk, the
context receives a capped summary plus a handle, and `ctx_read` puts the
raw output back into the context on demand (docs/spikes/headroom-phase1.md,
Probe C -- Context Mode pattern, PASS with caveats).

Pinned here: the store, the cap, the truncation notice (no silent loss),
the ctx_read round-trip, the preserved tool-result shape (an exec result
still starts with "exit=0", so bin/grade_run.py keeps detecting test
evidence and the tool-kind taxonomy keeps working), the explicit on/off
switch, and the worker-prompt instruction that closes the gap the spike
left open (a worker that never retrieves works with a lossy context).

No network, no live harness: the provider is a local mock server and
every file written lands in a temporary directory.

Run: python3 -m pytest tests/test_context_mode.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"

sys.path.insert(0, str(BIN))
_spec = importlib.util.spec_from_file_location("agent_ctxmode", BIN / "agent.py")
agent = importlib.util.module_from_spec(_spec)
sys.modules["agent_ctxmode"] = agent
_spec.loader.exec_module(agent)

_grade_spec = importlib.util.spec_from_file_location(
    "grade_run_ctxmode", BIN / "grade_run.py")
grade_run = importlib.util.module_from_spec(_grade_spec)
sys.modules["grade_run_ctxmode"] = grade_run
_grade_spec.loader.exec_module(grade_run)


def make_store(tmp: str, name: str = "run-1"):
    """A ContextStore in a throwaway directory."""
    return agent.ContextStore(str(Path(tmp) / "ctx" / name))


def make_worker(tmp: str, store=None):
    return agent.Worker(str(tmp), 30, ctx_store=store)


class StoreTests(unittest.TestCase):
    """The store keeps the raw output on disk and hands back a handle."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pc-ctx-store-")

    def test_store_writes_the_raw_output_and_returns_a_handle(self):
        store = make_store(self.tmp)
        handle = store.put("hello\nworld\n")
        self.assertTrue(handle.startswith("ctx:"), handle)
        path = Path(store.directory) / (handle.removeprefix("ctx:") + ".txt")
        self.assertTrue(path.exists(), "the stored output must be on disk")
        self.assertEqual(path.read_text(encoding="utf-8"), "hello\nworld\n")

    def test_store_round_trips_the_exact_text(self):
        store = make_store(self.tmp)
        body = "\n".join(f"line {i}" for i in range(1, 400))
        handle = store.put(body)
        self.assertEqual(store.get(handle), body)

    def test_store_is_content_addressed(self):
        store = make_store(self.tmp)
        first = store.put("same output")
        second = store.put("same output")
        other = store.put("other output")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_store_refuses_a_handle_that_is_not_a_handle(self):
        store = make_store(self.tmp)
        self.assertIsNone(store.get("../../etc/passwd"))
        self.assertIsNone(store.get("ctx:ZZZZ"))
        self.assertIsNone(store.get(""))

    def test_store_caps_its_own_copy_and_records_the_loss(self):
        store = agent.ContextStore(str(Path(self.tmp) / "ctx"), max_chars=10)
        handle = store.put("x" * 100)
        self.assertEqual(len(store.get(handle)), 10)
        self.assertEqual(store.meta[handle]["dropped_chars"], 90)
        self.assertEqual(store.meta[handle]["total_chars"], 100)


class SummaryTests(unittest.TestCase):
    """The summary states what was dropped -- nothing is dropped silently."""

    def test_summary_states_the_dropped_line_and_char_counts(self):
        body = "\n".join(f"line {i}" for i in range(1, 21))
        kept = "\n".join(f"line {i}" for i in range(1, 7))
        summary = agent.summarize_stored_output(body, "ctx:0123456789ab")
        self.assertIn(f"[ctx] stored output: 20 lines, {len(body)} chars",
                      summary)
        self.assertIn("TRUNCATED for context", summary)
        self.assertIn("kept 6 lines", summary)
        self.assertIn(f"dropped 14 lines / {len(body) - len(kept)} chars",
                      summary)
        self.assertIn('ctx_read(handle="ctx:0123456789ab"', summary)

    def test_summary_keeps_only_the_capped_head(self):
        body = "\n".join(f"line {i}" for i in range(1, 21))
        summary = agent.summarize_stored_output(body, "ctx:0123456789ab")
        self.assertIn("line 1", summary)
        self.assertIn("line 6", summary)
        self.assertNotIn("line 7\n", summary + "\n")

    def test_summary_says_complete_when_nothing_was_dropped(self):
        summary = agent.summarize_stored_output("one line\n", "ctx:0123456789ab")
        self.assertIn("[ctx] complete: nothing dropped", summary)
        self.assertNotIn("TRUNCATED", summary)

    def test_summary_reports_a_capped_stored_copy(self):
        body = "y" * 50
        meta = {"total_chars": 50, "stored_chars": 10, "dropped_chars": 40}
        summary = agent.summarize_stored_output(body, "ctx:0123456789ab",
                                                store_meta=meta)
        self.assertIn("the stored copy itself was capped", summary)
        self.assertIn("40 chars of this output were never stored", summary)


class ExecShapeTests(unittest.TestCase):
    """The tool-result shape survives: 'exit=0' stays the first line."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pc-ctx-exec-")
        self.store = make_store(self.tmp)
        self.worker = make_worker(self.tmp, self.store)

    def test_exec_result_still_starts_with_exit_zero(self):
        result = self.worker.run_tool("exec", {"command": "echo hi"})
        self.assertTrue(result.startswith("exit=0"), result[:40])
        self.assertEqual(agent._tool_result_kind(result), "ok")

    def test_exec_result_keeps_the_nonzero_taxonomy(self):
        result = self.worker.run_tool("exec", {"command": "exit 3"})
        self.assertTrue(result.startswith("exit=3"), result[:40])
        self.assertEqual(agent._tool_result_kind(result), "exec_nonzero")

    def test_exec_result_is_capped_and_names_the_handle(self):
        result = self.worker.run_tool(
            "exec", {"command": "printf 'line-%s\\n' $(seq 1 200)"})
        self.assertTrue(result.startswith("exit=0"))
        self.assertIn("[ctx] stored output:", result)
        self.assertIn("TRUNCATED for context", result)
        self.assertIn("handle ctx:", result)
        # the context only carries the capped head
        self.assertLess(len(result), 1000, len(result))

    def test_read_file_truncation_is_not_silent(self):
        path = Path(self.tmp) / "big.txt"
        path.write_text("\n".join(f"line {i}" for i in range(1, 300)) + "\n",
                        encoding="utf-8")
        result = self.worker.run_tool(
            "read_file", {"path": "big.txt", "offset": 1, "limit": 300})
        self.assertIn("big.txt (lines 1-299 of 299)", result)
        self.assertIn("[ctx] stored output:", result)
        self.assertIn("TRUNCATED for context", result)
        self.assertIn("dropped 293 lines", result)


class CtxReadTests(unittest.TestCase):
    """ctx_read puts the stored raw output back into the context."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pc-ctx-read-")
        self.store = make_store(self.tmp)
        self.worker = make_worker(self.tmp, self.store)
        self.body = "\n".join(f"line {i} payload" for i in range(1, 201))
        self.handle = self.store.put(self.body)

    def test_ctx_read_round_trip_recovers_every_dropped_line(self):
        result = self.worker.run_tool("exec", {
            "command": "printf 'line-%s payload\\n' $(seq 1 200)"})
        handle = result.split("handle ")[1].split(")")[0]
        full = self.worker.run_tool("ctx_read", {"handle": handle})
        self.assertIn("line-1 payload", full)
        self.assertIn("line-200 payload", full)

    def test_ctx_read_returns_the_stored_text_unchanged(self):
        full = self.worker.run_tool("ctx_read", {"handle": self.handle})
        self.assertIn("line 1 payload", full)
        self.assertIn("line 200 payload", full)
        self.assertIn("200 lines,", full)

    def test_ctx_read_takes_a_line_range(self):
        result = self.worker.run_tool(
            "ctx_read", {"handle": self.handle, "start_line": 10,
                         "end_line": 12})
        self.assertIn("lines 10-12 of 200", result)
        self.assertIn("line 10 payload", result)
        self.assertIn("line 12 payload", result)
        self.assertNotIn("line 13 payload", result)

    def test_ctx_read_takes_a_needle(self):
        result = self.worker.run_tool(
            "ctx_read", {"handle": self.handle, "needle": "line 42 "})
        self.assertIn("line 42 payload", result)
        self.assertNotIn("line 41 payload", result)

    def test_ctx_read_reports_an_unknown_handle(self):
        result = self.worker.run_tool("ctx_read", {"handle": "ctx:deadbeefcafe"})
        self.assertIn("unknown handle", result)

    def test_ctx_read_caps_its_answer_and_says_what_is_left(self):
        store = make_store(self.tmp, "run-2")
        worker = make_worker(self.tmp, store)
        handle = store.put("z" * (agent.CTX_READ_MAX_CHARS + 500))
        result = worker.run_tool("ctx_read", {"handle": handle})
        self.assertIn("[ctx] TRUNCATED:", result)
        self.assertIn("500 more chars are available", result)

    def test_ctx_read_without_a_store_is_inert(self):
        worker = agent.Worker(self.tmp, 30)
        result = worker.run_tool("ctx_read", {"handle": "ctx:0123456789ab"})
        self.assertIn("stored-output mode is off", result)


class SwitchTests(unittest.TestCase):
    """The on/off switch exists and defaults to off."""

    def test_switch_defaults_to_off(self):
        saved = os.environ.pop("WORKER_STORED_OUTPUT", None)
        try:
            self.assertFalse(agent._stored_output_enabled(None))
            self.assertFalse(agent._stored_output_enabled(False))
        finally:
            if saved is not None:
                os.environ["WORKER_STORED_OUTPUT"] = saved

    def test_env_flag_enables_the_mode(self):
        saved = os.environ.get("WORKER_STORED_OUTPUT")
        os.environ["WORKER_STORED_OUTPUT"] = "1"
        try:
            self.assertTrue(agent._stored_output_enabled(None))
        finally:
            if saved is None:
                os.environ.pop("WORKER_STORED_OUTPUT", None)
            else:
                os.environ["WORKER_STORED_OUTPUT"] = saved

    def test_cli_flag_wins_over_the_env_flag(self):
        saved = os.environ.get("WORKER_STORED_OUTPUT")
        os.environ["WORKER_STORED_OUTPUT"] = "1"
        try:
            self.assertFalse(agent._stored_output_enabled(False))
            self.assertTrue(agent._stored_output_enabled(True))
        finally:
            if saved is None:
                os.environ.pop("WORKER_STORED_OUTPUT", None)
            else:
                os.environ["WORKER_STORED_OUTPUT"] = saved

    def test_store_dir_never_lands_inside_the_worktree(self):
        workdir = tempfile.mkdtemp(prefix="pc-ctx-wt-")
        inside = os.path.join(workdir, "ctx")
        directory = agent._ctx_store_dir(workdir, inside, "run-1")
        self.assertFalse(os.path.realpath(directory).startswith(
            os.path.realpath(workdir) + os.sep), directory)

    def test_store_dir_defaults_next_to_the_transcript(self):
        workdir = tempfile.mkdtemp(prefix="pc-ctx-wt-")
        transcripts = tempfile.mkdtemp(prefix="pc-ctx-tr-")
        directory = agent._ctx_store_dir(workdir, transcripts, "run-42")
        self.assertEqual(directory, os.path.join(transcripts, "ctx", "run-42"))


class PromptTests(unittest.TestCase):
    """The worker is told to retrieve before relying on capped output."""

    def test_system_prompt_names_ctx_read_and_the_rule(self):
        prompt = agent.SYSTEM_PROMPT
        self.assertIn("ctx_read", prompt)
        self.assertIn("[ctx]", prompt)
        self.assertIn("retrieve", prompt.lower())

    def test_ctx_read_tool_is_only_advertised_in_stored_mode(self):
        names = [tool["function"]["name"] for tool in agent.TOOLS]
        self.assertNotIn("ctx_read", names)
        self.assertEqual(agent.CTX_READ_TOOL["function"]["name"], "ctx_read")
        self.assertIn("handle",
                      agent.CTX_READ_TOOL["function"]["parameters"]["required"])


class GradingTests(unittest.TestCase):
    """The grader keeps seeing test evidence in stored-output mode."""

    def test_grade_run_still_detects_an_exit_zero_test_result(self):
        result = agent.Worker(
            tempfile.mkdtemp(prefix="pc-ctx-grade-"), 30,
            ctx_store=make_store(tempfile.mkdtemp(prefix="pc-ctx-grade-")),
        ).run_tool("exec", {"command": "python3 -m pytest --version"})
        self.assertTrue(result.startswith("exit=0"), result[:40])
        events = [{
            "step": 1,
            "tool_calls": [{"name": "exec", "args": "pytest -q"}],
            "tool_results": [result[:800]],
        }]
        graded = grade_run.grade(events)
        self.assertEqual(graded["test_runs"], 1)
        self.assertEqual(graded["test_passed"], 1)
        self.assertIn("exit=0", graded["signals"]["test_evidence"]["note"])


class _MockProvider(BaseHTTPRequestHandler):
    """One tool call, then a plain answer: enough for one full step."""

    requests: list = []
    calls = 0

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(payload)
        type(self).calls += 1
        if type(self).calls == 1:
            message = {
                "role": "assistant",
                "content": "running",
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "exec",
                        "arguments": json.dumps({
                            "command": "printf 'line-%s\\n' $(seq 1 200)"}),
                    },
                }],
            }
        else:
            message = {"role": "assistant", "content": "done"}
        body = json.dumps({
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class EndToEndTests(unittest.TestCase):
    """One real run of the harness against a mock provider, both arms."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pc-ctx-e2e-")
        self.workdir = os.path.join(self.tmp, "wt")
        os.makedirs(self.workdir)
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

    def _run(self, extra_args):
        sys.argv = ["agent.py", "--brief", self.brief,
                    "--workdir", self.workdir,
                    "--provider", "minimax", "--model-tier", "cheap",
                    "--max-steps", "5",
                    "--run-id", "ctx-e2e",
                    "--transcript-dir", self.transcripts] + extra_args
        rc = agent.main()
        self.assertEqual(rc, 0)
        with open(os.path.join(self.transcripts, "ctx-e2e.jsonl"),
                  encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        return events

    def test_stored_output_on_keeps_the_exit_line_and_adds_the_tool(self):
        events = self._run(["--stored-output"])
        started = next(e for e in events if e.get("event") == "started")
        self.assertTrue(started["stored_output"])
        first = _MockProvider.requests[0]
        names = [t["function"]["name"] for t in first["tools"]]
        self.assertIn("ctx_read", names)
        # the tool result the model sees: the exit line first, then [ctx]
        second = _MockProvider.requests[1]
        tool_messages = [m for m in second["messages"]
                         if m.get("role") == "tool"]
        self.assertTrue(tool_messages)
        content = tool_messages[0]["content"]
        self.assertTrue(content.startswith("exit=0"), content[:40])
        self.assertIn("[ctx] stored output:", content)
        self.assertIn("TRUNCATED for context", content)
        report = (Path(self.workdir) / "WORKER_REPORT.md").read_text(
            encoding="utf-8")
        self.assertIn("- stored_output: on", report)

    def test_stored_output_off_is_the_pre_build_behaviour(self):
        events = self._run(["--no-stored-output"])
        started = next(e for e in events if e.get("event") == "started")
        self.assertFalse(started["stored_output"])
        first = _MockProvider.requests[0]
        names = [t["function"]["name"] for t in first["tools"]]
        self.assertNotIn("ctx_read", names)
        second = _MockProvider.requests[1]
        tool_messages = [m for m in second["messages"]
                         if m.get("role") == "tool"]
        content = tool_messages[0]["content"]
        self.assertTrue(content.startswith("exit=0"), content[:40])
        self.assertNotIn("[ctx]", content)
        report = (Path(self.workdir) / "WORKER_REPORT.md").read_text(
            encoding="utf-8")
        self.assertIn("- stored_output: off", report)

    def test_env_flag_enables_the_mode_without_the_cli_flag(self):
        saved = os.environ.get("WORKER_STORED_OUTPUT")
        os.environ["WORKER_STORED_OUTPUT"] = "1"
        try:
            events = self._run([])
        finally:
            if saved is None:
                os.environ.pop("WORKER_STORED_OUTPUT", None)
            else:
                os.environ["WORKER_STORED_OUTPUT"] = saved
        started = next(e for e in events if e.get("event") == "started")
        self.assertTrue(started["stored_output"])
        names = [t["function"]["name"] for t in _MockProvider.requests[0]["tools"]]
        self.assertIn("ctx_read", names)


class CliSurfaceTests(unittest.TestCase):
    """The switch is reachable from the command line."""

    def test_agent_help_lists_the_switch(self):
        proc = subprocess.run(
            [sys.executable, str(BIN / "agent.py"), "--help"],
            capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--stored-output", proc.stdout)
        self.assertIn("--no-stored-output", proc.stdout)


if __name__ == "__main__":
    unittest.main()
