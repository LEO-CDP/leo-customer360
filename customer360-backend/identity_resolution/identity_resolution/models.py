"""Data classes shared across the identity resolution package."""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class IdentityRule:
    """Represents one active matching rule read from ``cdp_profile_attributes``.

    Attributes:
        attribute_code: Column name on ``cdp_raw_profiles_stage`` /
            ``cdp_master_profiles`` this rule applies to
            (``cdp_profile_attributes.attribute_internal_code``).
        match_rule: One of ``exact``, ``fuzzy_trgm``, ``fuzzy_dmetaphone``.
        threshold: Similarity threshold used only by ``fuzzy_trgm``.
        consolidation_rule: Merge precedence to use when updating the master
            profile with this attribute's incoming value.
        consolidation_config: Optional rule parameters such as timestamp_field,
            verified_field, verified_values, or source_priority.
    """

    attribute_code: str
    match_rule: str
    threshold: Optional[float] = None
    consolidation_rule: Optional[str] = None
    consolidation_config: Optional[Dict[str, Any]] = None
