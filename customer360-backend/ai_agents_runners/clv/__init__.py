"""Customer lifetime value (CLV) training, scoring, tiering and projection.

See docs/action-plans/PLAN-CLV-REGRESSION-AGENT.md for the design decisions.
"""

from .settings import ClvSettings, TierBand

__all__ = ["ClvSettings", "TierBand"]
