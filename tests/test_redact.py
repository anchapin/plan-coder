"""Tests for the redaction pass on persisted harness output (issue #171).

The #169 security audit found two holes: the secret-shape list behind
``bin/agent.py:_redact`` did not cover the AKIA access-key shape nor the
provider env-key value shapes (z.ai, MiniMax, DashScope, OpenRouter),
and WORKER_REPORT.md was written with no redaction pass at all.

Pinned here:

* every covered shape is masked by ``_redact`` (the shape list lives in
  one place in bin/agent.py, each entry naming its issuer);
* every covered shape is masked in the two persisted artifacts a run
  produces -- the transcript log and WORKER_REPORT.md -- so neither file
  holds a seeded value;
* a deliberately unmatched control string survives untouched: the pass
  must not mangle ordinary output.

Every secret below is synthetic. No real key material is in this file.

Run: python3 -m pytest tests/test_redact.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
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
_spec = importlib.util.spec_from_file_location("agent_redact", BIN / "agent.py")
agent = importlib.util.module_from_spec(_spec)
sys.modules["agent_redact"] = agent
_spec.loader.exec_module(agent)


# --- synthetic fake secrets (no real key material) ------------------------
FAKE_AWS_ACCESS_KEY = "AKIA" + "TESTKEY000000001"  # AKIA + 16 chars
FAKE_ZAI_KEY = ("0f1e2d3c4b5a6978" + "."
                + "8f7e6d5c4b3a29180f1e2d3c4b5a6978")
FAKE_MINIMAX_KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
                    + ".eyJzdWIiOiJmYWtlLW1pbmltYXgifQ"
                    + ".ZmFrZS1zaWduYXR1cmUtZmFrZS1zaWc")
FAKE_DASHSCOPE_KEY = "sk-" + "0123456789abcdef0123456789abcdef"
FAKE_OPENROUTER_KEY = "sk-or-v1-" + "0123456789abcdef" * 4
FAKE_GITHUB_PAT = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"
FAKE_FINE_GRAINED_PAT = "github_pat_" + "11abcdefghijklmnopqrstuvwxyz"

# Every shape the audit's payload set covers, in the shape this
# harness's config and transcripts carry it.
SHAPES = (
    ("aws_access_key", FAKE_AWS_ACCESS_KEY),
    ("zai_key_value", FAKE_ZAI_KEY),
    ("minimax_key_value", FAKE_MINIMAX_KEY),
    ("dashscope_key_value", FAKE_DASHSCOPE_KEY),
    ("openrouter_key_value", FAKE_OPENROUTER_KEY),
    ("github_pat", FAKE_GITHUB_PAT),
    ("github_fine_grained_pat", FAKE_FINE_GRAINED_PAT),
    ("zai_env_assignment", "ZAI_API_KEY=" + FAKE_ZAI_KEY),
    ("minimax_env_assignment", "MINIMAX_API_KEY=" + FAKE_MINIMAX_KEY),
    ("dashscope_env_assignment", "DASHSCOPE_API_KEY=" + FAKE_DASHSCOPE_KEY),
    ("openrouter_env_assignment",
     "OPENROUTER_API_KEY=" + FAKE_OPENROUTER_KEY),
)

# A control string that matches no shape: ordinary worker output.
CONTROL = ("Nothing secret here: 3 tests passed, 0 failures, and the "
           + "summary is at https://example.com/docs/plan-coder.")


class ShapeListTests(unittest.TestCase):
    """The shape list lives in one place and names its issuers."""

    def test_every_shape_is_listed_with_an_issuer(self):
        self.assertTrue(agent._SECRET_SHAPES)
        for pattern, issuer in agent._SECRET_SHAPES:
            self.assertIsInstance(pattern, str)
            self.assertTrue(issuer.strip(), f"{pattern} has no issuer")

    def test_the_aws_shape_covers_akia_keys(self):
        masked = agent._redact(f"key {FAKE_AWS_ACCESS_KEY} here")
        self.assertNotIn(FAKE_AWS_ACCESS_KEY, masked)
        self.assertIn("***", masked)


class RedactTests(unittest.TestCase):
    """Every covered shape is masked; ordinary output is left alone."""

    def test_every_covered_shape_is_masked(self):
        for name, seed in SHAPES:
            with self.subTest(shape=name):
                masked = agent._redact(f"before {seed} after")
                self.assertNotIn(seed, masked)
                self.assertIn("***", masked)

    def test_control_string_is_left_untouched(self):
        self.assertEqual(agent._redact(CONTROL), CONTROL)

    def test_near_miss_strings_are_left_untouched(self):
        for control in ("AKIA", "AKIA12345", "sk-short", "GITHUB_API_KEY unset",
                        "the prefix AKIAAAAAAAAAAAAAAAA is not a key"):
            with self.subTest(control=control):
                self.assertEqual(agent._redact(control), control)


class _MockProvider(BaseHTTPRequestHandler):
    """One tool call that echoes every seeded shape, then a summary
    that repeats them: enough to land a secret in both persisted
    artifacts of one run."""

    requests: ClassVar[list] = []
    calls = 0

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(payload)
        type(self).calls += 1
        if type(self).calls == 1:
            command = "printf '%s\\n' " + " ".join(
                shlex.quote(seed) for _name, seed in SHAPES)
            message = {
                "role": "assistant",
                "content": "reading the environment",
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "exec",
                        "arguments": json.dumps({"command": command}),
                    },
                }],
            }
        else:
            message = {
                "role": "assistant",
                "content": "done: " + " ".join(seed for _name, seed in SHAPES),
            }
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


class PersistedOutputTests(unittest.TestCase):
    """One real run: neither the transcript nor the report keeps a seed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pc-redact-e2e-")
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

    def test_transcript_and_report_keep_no_seeded_shape(self):
        sys.argv = ["agent.py", "--brief", self.brief,
                    "--workdir", self.workdir,
                    "--provider", "minimax", "--model-tier", "cheap",
                    "--max-steps", "5",
                    "--run-id", "redact-e2e",
                    "--transcript-dir", self.transcripts]
        rc = agent.main()
        self.assertEqual(rc, 0)

        report = Path(self.workdir, "WORKER_REPORT.md").read_text(
            encoding="utf-8")
        transcript = Path(self.transcripts, "redact-e2e.jsonl").read_text(
            encoding="utf-8")
        for name, seed in SHAPES:
            with self.subTest(shape=name, artifact="report"):
                self.assertNotIn(seed, report)
            with self.subTest(shape=name, artifact="transcript"):
                self.assertNotIn(seed, transcript)
        # The redaction must not break the transcript: it stays one JSON
        # object per line, because bin/grade_run.py parses it.
        for line in transcript.splitlines():
            if line.strip():
                self.assertIsInstance(json.loads(line), dict)

    def test_the_report_still_carries_the_ordinary_summary(self):
        sys.argv = ["agent.py", "--brief", self.brief,
                    "--workdir", self.workdir,
                    "--provider", "minimax", "--model-tier", "cheap",
                    "--max-steps", "5",
                    "--run-id", "redact-e2e-2",
                    "--transcript-dir", self.transcripts]
        rc = agent.main()
        self.assertEqual(rc, 0)
        report = Path(self.workdir, "WORKER_REPORT.md").read_text(
            encoding="utf-8")
        self.assertIn("# Worker report", report)
        self.assertIn("## Machine manifest", report)
        self.assertIn("***", report)


if __name__ == "__main__":
    unittest.main()
