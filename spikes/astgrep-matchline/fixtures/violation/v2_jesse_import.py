"""Seeded violation for R2 (no-jesse-import).

Expected: `no-jesse-import` fires on both import forms below.
"""

import jesse  # noqa: F401
from jesse import research_helper  # noqa: F401
