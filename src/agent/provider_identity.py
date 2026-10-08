"""Provider response identity normalization.

Configured request models are deliberately excluded from this module.  An
actual provider model exists only when the response itself supplies one.
"""
from __future__ import annotations


def normalize_provider_model(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    model = value.strip()
    return model or None
