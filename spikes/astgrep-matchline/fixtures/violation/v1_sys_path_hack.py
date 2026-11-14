"""Seeded violation for R1 (no-sys-path-hack).

Expected: `no-sys-path-hack` fires on every sys.path mutation below.
"""

import sys

sys.path.insert(0, "/opt/vendor-libs")
sys.path.append("/opt/other-libs")
sys.path.extend(["/opt/third-libs"])
