"""Compatibility exports for the neutral official URL policy."""

from src.official_url_policy import (
    classify_promotable_official_url,
    is_official_discovery_url,
    is_promotable_official_url,
    is_promotable_publisher_url,
    validate_official_url_for_link_type,
)

__all__ = [
    "classify_promotable_official_url",
    "is_official_discovery_url",
    "is_promotable_official_url",
    "is_promotable_publisher_url",
    "validate_official_url_for_link_type",
]
