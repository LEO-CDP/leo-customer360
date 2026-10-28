"""AI segment-rule generation: a rule tree, not a campaign plan.

Reuses `_instructions` and `_resolve_provider` from `campaign_planner.base`.
"""

from ai_providers.base import AIProviderError
from segment_planner.rules import (
    SEGMENT_RULES_INSTRUCTIONS,
    GeneratedSegmentRules,
    SegmentRulesBrief,
    generate_segment_rules,
)

__all__ = [
    "AIProviderError",
    "GeneratedSegmentRules",
    "SEGMENT_RULES_INSTRUCTIONS",
    "SegmentRulesBrief",
    "generate_segment_rules",
]
