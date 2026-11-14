"""Clean counterpart for R2 (no-jesse-import).

The production module imports a supported dependency, never the research-only
reproduction module.
Expected: zero findings.
"""

from dataclasses import dataclass


@dataclass
class Feature:
    name: str
    weight: float
