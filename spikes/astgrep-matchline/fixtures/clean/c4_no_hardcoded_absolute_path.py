"""Clean counterpart for R4 (no-hardcoded-absolute-path).

Paths come from the environment, never from a machine-specific literal.
Expected: zero findings.
"""

import os

DATASET_ROOT = os.environ.get("MATCHLINE_DATASET_ROOT", "datasets/matchline")
