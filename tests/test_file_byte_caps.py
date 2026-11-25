"""Tests for the worker file-tool byte caps (plan-coder issue #175).

The file tools are confined to the workdir by Worker._safe, but a single
call had no size limit: read_file could pull a whole multi-megabyte file
into one tool result, and write_file could put one there.  The caps bound
one call:

  * READ: the content of one call is truncated at FILE_READ_MAX_BYTES and
    the notice names the cap, so the worker can page with offset/limit.
  * WRITE: the write is REFUSED before a byte is written -- no partial
    file, and an existing file is left exactly as it was.
  * EDIT: an edit whose result would exceed the cap is refused and the
    file keeps its old content.

Pinned here: the cap message names the cap size, the refusal leaves no
partial file, an existing file survives an over-cap write/edit untouched,
and a normal-sized read/write/edit behaves exactly as before.

No network, no live harness: every file written lands in a temporary
directory.

Run: python3 -m pytest tests/test_file_byte_caps.py -q
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"

sys.path.insert(0, str(BIN))
_spec = importlib.util.spec_from_file_location("agent_bytecaps", BIN / "agent.py")
agent = importlib.util.module_from_spec(_spec)
sys.modules["agent_bytecaps"] = agent
_spec.loader.exec_module(agent)


class CapOverride:
    """Temporarily set a module-level cap constant."""

    def __init__(self, name: str, value: int):
        self.name = name
        self.value = value
        self.old = None

    def __enter__(self):
        self.old = getattr(agent, self.name)
        setattr(agent, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(agent, self.name, self.old)
        return False


class CapDefaultsTests(unittest.TestCase):
    """The shipped defaults are generous enough for real source files."""

    def test_the_caps_are_above_ordinary_source_file_sizes(self):
        self.assertGreaterEqual(agent.FILE_READ_MAX_BYTES, 100000)
        self.assertGreaterEqual(agent.FILE_WRITE_MAX_BYTES, 100000)

    def test_the_caps_are_named_module_level_constants(self):
        self.assertIsInstance(agent.FILE_READ_MAX_BYTES, int)
        self.assertIsInstance(agent.FILE_WRITE_MAX_BYTES, int)

    def test_the_caps_are_overridable_by_environment_variable(self):
        """The env override follows the WORKER_CTX_* constant style."""
        code = (
            "import sys; sys.path.insert(0, %r); import agent; "
            "print(agent.FILE_READ_MAX_BYTES, agent.FILE_WRITE_MAX_BYTES)"
            % str(BIN)
        )
        env = dict(os.environ)
        env["WORKER_FILE_READ_MAX_BYTES"] = "12345"
        env["WORKER_FILE_WRITE_MAX_BYTES"] = "54321"
        out = subprocess.run([sys.executable, "-c", code], env=env,
                             capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "12345 54321")


class ReadCapTests(unittest.TestCase):
    """An over-cap read is truncated, and the notice names the cap."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pc-bytecaps-read-")
        self.worker = agent.Worker(self.tmp, 30)
        self.path = Path(self.tmp) / "big.txt"
        self.path.write_text("\n".join(f"line {i} " + "x" * 200
                                       for i in range(1, 41)) + "\n",
                             encoding="utf-8")

    def test_an_over_cap_read_is_truncated_and_names_the_cap(self):
        with CapOverride("FILE_READ_MAX_BYTES", 1000):
            result = self.worker.run_tool(
                "read_file", {"path": "big.txt", "offset": 1, "limit": 100})
        self.assertIn("truncated", result)
        self.assertIn("read cap", result)
        self.assertIn("1000", result)
        # The truncated content is still delivered, not refused.
        self.assertIn("line 1 ", result)

    def test_an_over_cap_read_delivers_at_most_the_cap(self):
        with CapOverride("FILE_READ_MAX_BYTES", 1000):
            result = self.worker.run_tool(
                "read_file", {"path": "big.txt", "offset": 1, "limit": 100})
        body = result.split("\n", 1)[1]
        self.assertLessEqual(len(body.encode("utf-8")), 1000)

    def test_a_normal_read_is_unchanged(self):
        result = self.worker.run_tool(
            "read_file", {"path": "big.txt", "offset": 1, "limit": 3})
        self.assertIn("big.txt (lines 1-3 of 40):", result)
        self.assertIn("line 1 ", result)
        self.assertIn("line 3 ", result)
        self.assertNotIn("truncated", result)
        self.assertNotIn("cap", result)

    def test_a_normal_read_under_a_generous_cap_is_unchanged(self):
        with CapOverride("FILE_READ_MAX_BYTES", 2000000):
            result = self.worker.run_tool(
                "read_file", {"path": "big.txt", "offset": 1, "limit": 40})
        self.assertNotIn("truncated", result)
        self.assertIn("line 40 ", result)

    def test_a_small_file_read_is_unchanged(self):
        path = Path(self.tmp) / "small.py"
        path.write_text("def f():\n    return 1\n", encoding="utf-8")
        result = self.worker.run_tool(
            "read_file", {"path": "small.py", "offset": 1, "limit": 10})
        self.assertIn("small.py (lines 1-2 of 2):", result)
        self.assertIn("def f():", result)
        self.assertNotIn("truncated", result)

    def test_an_over_cap_read_is_truncated_in_context_mode_too(self):
        """Context Mode stores the chunk, so the cap has to hold there too."""
        store_dir = tempfile.mkdtemp(prefix="pc-bytecaps-store-")
        store = agent.ContextStore(store_dir)
        worker = agent.Worker(self.tmp, 30, ctx_store=store)
        with CapOverride("FILE_READ_MAX_BYTES", 1000):
            result = worker.run_tool(
                "read_file", {"path": "big.txt", "offset": 1, "limit": 100})
        self.assertIn("truncated", result)
        self.assertIn("read cap", result)
        self.assertIn("1000", result)
        stored = list(Path(store_dir).glob("*.txt"))
        self.assertEqual(len(stored), 1)
        self.assertLessEqual(stored[0].stat().st_size, 1000)


class WriteCapTests(unittest.TestCase):
    """An over-cap write is refused before anything is written."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pc-bytecaps-write-")
        self.worker = agent.Worker(self.tmp, 30)

    def test_an_over_cap_write_is_refused_and_names_the_cap(self):
        with CapOverride("FILE_WRITE_MAX_BYTES", 1000):
            result = self.worker.run_tool(
                "write_file", {"path": "out.txt", "content": "y" * 5000})
        self.assertIn("refused", result)
        self.assertIn("write cap", result)
        self.assertIn("1000", result)

    def test_an_over_cap_write_leaves_no_partial_file(self):
        with CapOverride("FILE_WRITE_MAX_BYTES", 1000):
            self.worker.run_tool(
                "write_file", {"path": "out.txt", "content": "y" * 5000})
        self.assertFalse((Path(self.tmp) / "out.txt").exists())
        self.assertEqual(os.listdir(self.tmp), [])

    def test_an_over_cap_write_leaves_an_existing_file_untouched(self):
        path = Path(self.tmp) / "out.txt"
        path.write_text("original\n", encoding="utf-8")
        with CapOverride("FILE_WRITE_MAX_BYTES", 1000):
            result = self.worker.run_tool(
                "write_file", {"path": "out.txt", "content": "y" * 5000})
        self.assertIn("refused", result)
        self.assertEqual(path.read_text(encoding="utf-8"), "original\n")

    def test_a_normal_write_is_unchanged(self):
        result = self.worker.run_tool(
            "write_file", {"path": "out.txt", "content": "hello\n"})
        self.assertIn("wrote out.txt", result)
        self.assertEqual((Path(self.tmp) / "out.txt").read_text(
            encoding="utf-8"), "hello\n")

    def test_a_normal_write_under_the_shipped_cap_is_unchanged(self):
        content = "z" * 100000
        result = self.worker.run_tool(
            "write_file", {"path": "big.txt", "content": content})
        self.assertIn("wrote big.txt", result)
        self.assertEqual((Path(self.tmp) / "big.txt").stat().st_size,
                         len(content))


class EditCapTests(unittest.TestCase):
    """An edit whose result would exceed the cap is refused."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pc-bytecaps-edit-")
        self.worker = agent.Worker(self.tmp, 30)
        self.path = Path(self.tmp) / "code.py"
        self.path.write_text("VALUE = 1\n", encoding="utf-8")

    def test_an_over_cap_edit_is_refused_and_names_the_cap(self):
        with CapOverride("FILE_WRITE_MAX_BYTES", 1000):
            result = self.worker.run_tool(
                "edit_file", {"path": "code.py", "old_text": "VALUE = 1",
                              "new_text": "VALUE = " + "9" * 5000})
        self.assertIn("refused", result)
        self.assertIn("write cap", result)
        self.assertIn("1000", result)

    def test_an_over_cap_edit_leaves_the_file_unchanged(self):
        with CapOverride("FILE_WRITE_MAX_BYTES", 1000):
            self.worker.run_tool(
                "edit_file", {"path": "code.py", "old_text": "VALUE = 1",
                              "new_text": "VALUE = " + "9" * 5000})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_a_normal_edit_is_unchanged(self):
        result = self.worker.run_tool(
            "edit_file", {"path": "code.py", "old_text": "VALUE = 1",
                          "new_text": "VALUE = 2"})
        self.assertIn("edited code.py", result)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "VALUE = 2\n")

    def test_an_edit_with_an_unknown_old_text_still_fails_the_old_way(self):
        result = self.worker.run_tool(
            "edit_file", {"path": "code.py", "old_text": "nope",
                          "new_text": "VALUE = 3"})
        self.assertIn("old_text not found", result)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "VALUE = 1\n")


if __name__ == "__main__":
    unittest.main()
