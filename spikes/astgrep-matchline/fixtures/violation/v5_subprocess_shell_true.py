"""Seeded violation for R5 (no-subprocess-shell-true).

Expected: `no-subprocess-shell-true` fires exactly once in this file.
"""

import subprocess


def run_untrusted(command: str) -> str:
    """Run a command through a shell."""
    completed = subprocess.run(command, shell=True, capture_output=True, text=True)
    return completed.stdout
