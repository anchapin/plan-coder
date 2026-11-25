"""Exec timeout kills the whole process group (plan-coder issue #172).

The worker exec tool used `subprocess.run(timeout=...)`, which on timeout
kills only the direct shell child.  A backgrounded descendant of the
timed-out command (a watcher, a dev server, a `sleep`) survived the
timeout and kept running with the worker's workdir and environment.

These tests pin the fixed behaviour:

  * a command that backgrounds a long-lived child and then hangs is
    killed as a GROUP: the descendant is gone (or at least dead -- a
    zombie has stopped running) and it never performs the work it was
    scheduled to do;
  * the timeout result string says that the process group was killed;
  * a command that finishes inside the timeout returns exactly the same
    output as before (no group kill, no changed formatting).

Hermetic: every file written lands in a temporary directory, no
network, no real sleep-and-hang beyond the short timeouts used here.

Run: python3 -m pytest tests/test_exec_timeout.py -q
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))
import agent  # noqa: E402


def _proc_state(pid: int) -> str | None:
    """Return the /proc state of pid, or None when it is gone.

    'Z' means the process is a zombie: it is dead and can no longer
    run anything -- which is what "killed" means for our purposes.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    # The comm field may contain spaces/parens: the state is the field
    # right after the closing paren.
    tail = stat.rsplit(")", 1)[-1].split()
    return tail[0] if tail else None


class ExecTimeoutGroupKillTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def worker(self, timeout: int = 30) -> agent.Worker:
        return agent.Worker(self.workdir, exec_timeout=timeout)

    def test_fast_command_output_unchanged(self) -> None:
        """A command that finishes inside the timeout is untouched."""
        result = self.worker()._exec("echo hello-exec-world", 30)
        self.assertEqual(result, "exit=0\nhello-exec-world")

    def test_fast_command_exit_code_and_stderr_unchanged(self) -> None:
        result = self.worker()._exec("echo out; echo err >&2; exit 3", 30)
        self.assertTrue(result.startswith("exit=3\n"), result)
        self.assertIn("out", result)
        self.assertIn("err", result)

    def test_timeout_kills_backgrounded_descendant(self) -> None:
        """A backgrounded descendant must not survive the timeout."""
        marker = Path(self.workdir) / "marker.txt"
        child_pid_file = Path(self.workdir) / "child.pid"
        # The backgrounded subshell sleeps, then touches the marker.
        # The shell itself waits, so the command times out.
        command = (
            f"sleep 3 && touch {marker} & "
            f"echo $! > {child_pid_file}; wait"
        )
        result = self.worker()._exec(command, 1)

        # The descendant's pid was captured before the timeout fired.
        self.assertTrue(child_pid_file.exists(), "child pid not captured")
        child_pid = int(child_pid_file.read_text().strip())

        # Give any surviving descendant the chance to do its work and
        # to be reaped.
        time.sleep(3.0)
        state = _proc_state(child_pid)
        self.assertIn(
            state, (None, "Z"),
            f"descendant {child_pid} survived the timeout (state={state})")
        self.assertFalse(
            marker.exists(),
            "backgrounded descendant survived the timeout and did its work")

        # The timeout result says what happened to the process group.
        self.assertIn("TIMEOUT after 1s", result)
        self.assertIn("process group", result)
        self.assertIn("killed", result)

    def test_timeout_result_mentions_killed_process_group(self) -> None:
        result = self.worker()._exec("sleep 5", 1)
        self.assertIn("TIMEOUT after 1s", result)
        self.assertIn("process group", result)
        self.assertIn("killed", result)


if __name__ == "__main__":
    unittest.main()
