"""Catalog-grounded requirements for series-scoped answer evidence."""
from __future__ import annotations

import re

from src.agent.product_entities import resolve_product_entities


def _question_field_ids(question: str, fields: dict) -> list[str]:
    """Map a question to governed fact fields using dictionary token patterns."""
    matched = []
    for field_id, field_def in fields.items():
        pattern = getattr(field_def, "l0_token_pattern", None)
        if not getattr(field_def, "l0_assertable", False) or not pattern:
            continue
        if re.search(pattern, question or "", flags=re.IGNORECASE):
            matched.append(str(field_id))
    return sorted(set(matched))


def derive_series_evidence_requirements(
    schema,
    *,
    question: str = "",
    fields: dict | None = None,
    product_catalog: dict[str, dict],
    resolver,
) -> list[dict]:
    """Return governed series entities present in the schema.

    Curated ambiguity such as bare ``335`` remains a candidate set, not a product
    series requirement. Only resolver results backed by catalog ``series`` fields
    become series-scoped evidence requirements.
    """
    raw_products = [
        str(value).strip()
        for value in (getattr(schema, "products", None) or [])
        if str(value).strip()
    ]
    if not raw_products:
        return []
    resolution = resolve_product_entities(
        raw_products,
        product_catalog,
        resolver=resolver,
    )
    entity_by_raw = {entity.raw: entity for entity in resolution.entities}
    requirements: list[dict] = []
    seen: set[str] = set()
    evidence_kind = (
        "membership"
        if getattr(schema, "intent", "") == "catalog_overview"
        else "fact"
    )
    field_ids = (
        []
        if evidence_kind == "membership"
        else _question_field_ids(question, fields or {})
    )
    for raw in raw_products:
        entity = entity_by_raw.get(raw)
        if entity is None or entity.kind != "series":
            continue
        resolved = resolver.resolve(raw)
        if (
            resolved.status != "resolved"
            or resolved.entity_kind != "series"
            or not resolved.entity_id
            or resolved.entity_id in seen
        ):
            continue
        seen.add(resolved.entity_id)
        requirements.append({
            "series_id": resolved.entity_id,
            "evidence_kind": evidence_kind,
            "field_ids": field_ids,
            "model_ids": list(resolved.model_ids),
        })
    return requirements
