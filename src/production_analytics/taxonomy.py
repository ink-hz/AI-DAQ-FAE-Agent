from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.facts.resolver import ModelResolver

_EXPECTED_KEYS = {
    "taxonomy_version",
    "intent_capabilities",
    "complexity_levels",
    "resolution_statuses",
    "failure_layers",
    "product_signals",
    "human_value_classes",
    "scenario_categories",
    "technical_objects",
}


class TaxonomyError(ValueError):
    pass


@dataclass(frozen=True)
class AnalyticsTaxonomy:
    taxonomy_version: str
    intent_capabilities: tuple[str, ...]
    complexity_levels: tuple[str, ...]
    resolution_statuses: tuple[str, ...]
    failure_layers: tuple[str, ...]
    product_signals: tuple[str, ...]
    human_value_classes: tuple[str, ...]
    scenario_categories: tuple[str, ...]
    technical_objects: tuple[str, ...]
    source_keys: frozenset[str]


@dataclass(frozen=True)
class NormalizedModelMentions:
    resolved_model_ids: tuple[str, ...]
    ambiguous_mentions: dict[str, tuple[str, ...]]
    unresolved_mentions: tuple[str, ...]


def _string_tuple(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise TaxonomyError(f"{key} must be a non-empty array")
    if any(not isinstance(item, str) or not item for item in value):
        raise TaxonomyError(f"{key} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise TaxonomyError(f"{key} contains duplicates")
    return tuple(value)


def load_taxonomy(path: Path) -> AnalyticsTaxonomy:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != _EXPECTED_KEYS:
        raise TaxonomyError("taxonomy top-level contract mismatch")
    version = payload.get("taxonomy_version")
    if not isinstance(version, str) or not version:
        raise TaxonomyError("taxonomy_version must be a non-empty string")
    return AnalyticsTaxonomy(
        taxonomy_version=version,
        intent_capabilities=_string_tuple(payload, "intent_capabilities"),
        complexity_levels=_string_tuple(payload, "complexity_levels"),
        resolution_statuses=_string_tuple(payload, "resolution_statuses"),
        failure_layers=_string_tuple(payload, "failure_layers"),
        product_signals=_string_tuple(payload, "product_signals"),
        human_value_classes=_string_tuple(payload, "human_value_classes"),
        scenario_categories=_string_tuple(payload, "scenario_categories"),
        technical_objects=_string_tuple(payload, "technical_objects"),
        source_keys=frozenset(payload),
    )


def normalize_model_mentions(
    mentions: list[str], resolver: ModelResolver
) -> NormalizedModelMentions:
    resolved: list[str] = []
    ambiguous: dict[str, tuple[str, ...]] = {}
    unresolved: list[str] = []
    for mention in mentions:
        result = resolver.resolve(mention)
        if result.status == "resolved":
            resolved.extend(model_id for model_id in result.model_ids if model_id not in resolved)
        elif result.status == "ambiguous":
            ambiguous[mention] = result.model_ids
        else:
            unresolved.append(mention)
    return NormalizedModelMentions(tuple(resolved), ambiguous, tuple(unresolved))
