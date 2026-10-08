"""Validate reviewed official-web facts into an offline patch proposal.

This module deliberately has no network or filesystem write path.  Callers must
first reduce an official page to an allowlisted, model-bound field candidate;
the returned rows still require a reviewed repository patch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from src.facts.store import FactStore
from src.official_url_policy import is_promotable_publisher_url

PRODUCT_SURFACE = "product_fact"
COMMERCIAL_FIELDS = {
    "availability",
    "inventory",
    "lifecycle_status",
    "price",
    "purchase_status",
    "stock",
}
RAW_WEB_KEYS = {"body", "html", "page_html", "raw_html", "script"}
SDK_ONLY_FIELDS = {"sdk_wrapper_support"}
@dataclass(frozen=True)
class PatchFinding:
    candidate: object
    reason: str


@dataclass(frozen=True)
class OfficialFactPatch:
    accepted: tuple[dict[str, object], ...]
    rejected: tuple[PatchFinding, ...]
    conflicts: tuple[PatchFinding, ...]
    unchanged: tuple[tuple[str, str], ...]


def build_official_fact_patch(
    candidates: list[object] | tuple[object, ...],
    *,
    store: FactStore,
) -> OfficialFactPatch:
    """Return an immutable, reviewable patch proposal for structured facts."""

    accepted: list[dict[str, object]] = []
    rejected: list[PatchFinding] = []
    conflicts: list[PatchFinding] = []
    unchanged: list[tuple[str, str]] = []

    for candidate in candidates:
        reason = _rejection_reason(candidate, store=store)
        if reason:
            rejected.append(PatchFinding(candidate, reason))
            continue

        assert isinstance(candidate, dict)
        model = str(candidate["model"])
        field = str(candidate["field"])
        existing = store.get_spec(model, field)
        if existing.status == "found" and existing.row is not None:
            if existing.row.status in {"fae_verified", "conflict"}:
                conflicts.append(PatchFinding(candidate, "protected_existing_fact"))
                continue
            if not _same_fact(candidate, existing.row):
                conflicts.append(
                    PatchFinding(candidate, "value_change_requires_adjudication")
                )
                continue
            unchanged.append((model, field))
            continue

        source = dict(candidate["source"])
        accepted.append(
            {
                "field": field,
                "raw_value": str(candidate["raw_value"]),
                "value": candidate["value"],
                "qualifier": dict(candidate.get("qualifier") or {}),
                "source": {
                    "source_type": "official_web",
                    "file": str(source["file"]),
                    "section": str(source["section"]),
                    "canonical_url": str(source["canonical_url"]),
                    "verified_at": str(source["verified_at"]),
                },
                "note": "offline official candidate; requires reviewed file patch",
                "status": "doc_extracted",
            }
        )

    return OfficialFactPatch(
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        conflicts=tuple(conflicts),
        unchanged=tuple(unchanged),
    )


def _rejection_reason(candidate: object, *, store: FactStore) -> str | None:
    if not isinstance(candidate, dict):
        return "raw_web_payload_not_allowed"
    if RAW_WEB_KEYS.intersection(candidate):
        return "raw_web_payload_not_allowed"

    field = str(candidate.get("field") or "")
    if field in COMMERCIAL_FIELDS:
        return "commercial_field_not_allowed"
    model = str(candidate.get("model") or "")
    if model not in store.known_models:
        return "unknown_model"
    if field not in store.fields:
        return "unknown_field"
    if candidate.get("surface") != PRODUCT_SURFACE or field in SDK_ONLY_FIELDS:
        return "field_surface_mismatch"

    source = candidate.get("source")
    if not isinstance(source, dict):
        return "invalid_source"
    local_file = str(source.get("file") or "")
    if not _valid_local_source(local_file):
        return "local_source_path_invalid"
    if not str(source.get("section") or "").strip():
        return "invalid_source"
    if not str(source.get("verified_at") or "").strip():
        return "invalid_source"
    if not _is_official_url(str(source.get("canonical_url") or "")):
        return "non_official_source"
    if not str(candidate.get("raw_value") or "").strip():
        return "empty_value"
    if candidate.get("value") in (None, ""):
        return "empty_value"
    qualifier = candidate.get("qualifier", {})
    if not isinstance(qualifier, dict):
        return "invalid_qualifier"
    return None


def _valid_local_source(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _is_official_url(value: str) -> bool:
    return is_promotable_publisher_url(value, publisher="Orbbec")


def _same_fact(candidate: dict[str, object], existing: object) -> bool:
    return (
        getattr(existing, "raw_value", None) == str(candidate.get("raw_value") or "")
        and getattr(existing, "value", None) == candidate.get("value")
        and getattr(existing, "qualifier", None)
        == dict(candidate.get("qualifier") or {})
    )
