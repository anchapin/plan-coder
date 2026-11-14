"""Clean counterpart for R1 (no-sys-path-hack).

The module is imported normally — no import-path mutation of any kind.
Expected: zero findings.
"""

import json


def load_payload(path: str) -> dict:
    """Read a JSON payload from a caller-supplied path."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
