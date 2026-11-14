"""Clean counterpart for R5 (no-subprocess-shell-true).

The subprocess call passes an argument list and leaves the shell disabled.
Expected: zero findings.
"""

import subprocess


def run_tool(argv: list[str]) -> str:
    """Run a tool without a shell."""
    completed = subprocess.run(argv, capture_output=True, text=True, check=True)
    return completed.stdout
