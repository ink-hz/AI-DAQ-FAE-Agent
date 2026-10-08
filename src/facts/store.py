"""事实矩阵内存表与查表接口——即升级设计中 fact_lookup 工具的实现。"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from types import MappingProxyType

import yaml

from src.data.product_loader import load_product_catalog
from src.facts.resolver import load_catalog
from src.facts.schema import STATUSES, FieldDef, ProfileSet, RangeValue, VideoProfile

FACTS_FILENAME = "facts.yaml"
_CAPACITY_COUNT_UNITS = {"secondary_units", "total_cameras", "hub_ports"}
_CAPACITY_SOURCE_SCOPES = {"product", "series", "accessory", "legacy_document"}


@dataclass(frozen=True)
class FactRow:
    field: str
    value: object                    # str | RangeValue | None
    raw_value: str
    status: str
    source: dict
    qualifier: dict = dc_field(default_factory=dict)
    note: str = ""
    profile_set: ProfileSet | None = None


@dataclass(frozen=True)
class FactResult:
    status: str                      # found | not_found | field_not_exists | unknown_model
    row: FactRow | None = None


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_count_unit(value: object, context: str) -> str:
    unit = str(value or "")
    if unit not in _CAPACITY_COUNT_UNITS:
        raise ValueError(
            f"{context}: count_unit must be one of "
            f"{sorted(_CAPACITY_COUNT_UNITS)!r}"
        )
    return unit


def _validate_count_range(raw: object, context: str) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{context}: count range must be a mapping")
    minimum = raw.get("minimum")
    maximum = raw.get("maximum")
    if not _is_count(minimum) or not _is_count(maximum):
        raise ValueError(f"{context}: minimum/maximum must be numeric counts")
    if minimum > maximum:
        raise ValueError(f"{context}: minimum exceeds maximum")
    _validate_count_unit(raw.get("count_unit"), context)


def _validate_observed_count(raw: object, context: str) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{context}: observed count must be a mapping")
    if not _is_count(raw.get("value")):
        raise ValueError(f"{context}: value must be a numeric count")
    _validate_count_unit(raw.get("count_unit"), context)


def _validate_capacity_maximum(raw: object, context: str) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{context}: maximum must be a mapping")
    kind = str(raw.get("kind") or "")
    if kind == "fixed_count":
        if not _is_count(raw.get("value")):
            raise ValueError(f"{context}: fixed_count value must be numeric")
        _validate_count_unit(raw.get("count_unit"), context)
        return
    if kind == "no_fixed_limit":
        if "value" in raw or "count_unit" in raw:
            raise ValueError(
                f"{context}: no_fixed_limit forbids value and count_unit"
            )
        limiting_factors = raw.get("limiting_factors")
        if (
            not isinstance(limiting_factors, list)
            or not limiting_factors
            or any(not str(value).strip() for value in limiting_factors)
        ):
            raise ValueError(
                f"{context}: no_fixed_limit requires limiting_factors"
            )
        return
    if kind == "no_data":
        forbidden = {"value", "count_unit", "limiting_factors"}.intersection(raw)
        if forbidden:
            raise ValueError(
                f"{context}: no_data forbids value/count_unit/limiting_factors"
            )
        if not str(raw.get("reason") or "").strip():
            raise ValueError(f"{context}: no_data requires reason")
        return
    raise ValueError(f"{context}: unknown maximum kind {kind!r}")


def _validate_capacity_scope(
    qualifier: object,
    context: str,
    *,
    model_id: str,
    allow_related: bool,
) -> None:
    if not isinstance(qualifier, dict):
        raise ValueError(f"{context}: qualifier must be a mapping")
    source_scope = str(qualifier.get("source_scope") or "")
    if source_scope not in _CAPACITY_SOURCE_SCOPES:
        raise ValueError(
            f"{context}: source_scope must be one of "
            f"{sorted(_CAPACITY_SOURCE_SCOPES)!r}"
        )
    _validate_capacity_maximum(qualifier.get("maximum"), f"{context}.maximum")
    if "typical_count" in qualifier:
        _validate_count_range(
            qualifier["typical_count"], f"{context}.typical_count"
        )
    if "tested_count" in qualifier:
        _validate_observed_count(
            qualifier["tested_count"], f"{context}.tested_count"
        )
    if "derived_total" in qualifier:
        derived = qualifier["derived_total"]
        if not isinstance(derived, dict):
            raise ValueError(f"{context}.derived_total must be a mapping")
        maximum = qualifier["maximum"]
        if (
            maximum.get("kind") != "fixed_count"
            or maximum.get("count_unit") != "secondary_units"
        ):
            raise ValueError(
                f"{context}: hub_ports cannot be converted to a camera total"
            )
        for key in ("value", "primary_units", "secondary_units"):
            if not _is_count(derived.get(key)):
                raise ValueError(
                    f"{context}.derived_total.{key} must be a numeric count"
                )
        if derived.get("count_unit") != "total_cameras":
            raise ValueError(
                f"{context}.derived_total.count_unit must be total_cameras"
            )
        if derived["secondary_units"] != maximum["value"]:
            raise ValueError(
                f"{context}.derived_total secondary_units must match maximum"
            )
        if derived["value"] != derived["primary_units"] + derived["secondary_units"]:
            raise ValueError(
                f"{context}.derived_total value must equal primary plus secondary"
            )
        if not str(derived.get("formula") or "").strip():
            raise ValueError(f"{context}.derived_total requires formula")
    related_scopes = qualifier.get("related_scopes")
    if related_scopes is not None:
        if not allow_related:
            raise ValueError(f"{context}: nested related_scopes are not allowed")
        if not isinstance(related_scopes, list) or not related_scopes:
            raise ValueError(f"{context}.related_scopes must be a non-empty list")
        for index, scope in enumerate(related_scopes):
            _validate_capacity_scope(
                scope,
                f"{context}.related_scopes[{index}]",
                model_id=model_id,
                allow_related=False,
            )


def _validate_multi_camera_sync_capacity(
    qualifier: object,
    context: str,
    *,
    model_id: str,
) -> None:
    _validate_capacity_scope(
        qualifier,
        context,
        model_id=model_id,
        allow_related=True,
    )


def _validate_series_scope(
    qualifier: object,
    context: str,
    *,
    model_id: str,
    series_memberships: Mapping[str, frozenset[str]],
) -> tuple[frozenset[str], frozenset[str]]:
    if not isinstance(qualifier, dict):
        raise ValueError(f"{context}: qualifier must be a mapping")
    if "series_models" in qualifier:
        raise ValueError(
            f"{context}: legacy series_models is not allowed; use "
            "source_applies_to and coverage_gap"
        )
    series_id = str(qualifier.get("series_id") or "").strip()
    if not series_id:
        raise ValueError(f"{context}: series scope requires series_id")
    governed_members = series_memberships.get(series_id)
    if not governed_members:
        raise ValueError(f"{context}: unknown governed series {series_id!r}")

    def member_set(key: str) -> frozenset[str]:
        raw = qualifier.get(key)
        if not isinstance(raw, list):
            raise ValueError(f"{context}: {key} must be a list")
        if any(not isinstance(item, str) or not item.strip() for item in raw):
            raise ValueError(f"{context}: {key} must contain canonical model ids")
        members = frozenset(raw)
        if len(members) != len(raw):
            raise ValueError(f"{context}: duplicate member in {key}")
        unknown = sorted(members - governed_members)
        if unknown:
            raise ValueError(f"{context}: unknown members in {key}: {unknown!r}")
        return members

    source_applies_to = member_set("source_applies_to")
    coverage_gap = member_set("coverage_gap")
    if model_id not in source_applies_to:
        raise ValueError(
            f"{context}: source_applies_to must be containing {model_id}"
        )
    expected_gap = governed_members - source_applies_to
    if coverage_gap != expected_gap:
        raise ValueError(
            f"{context}: coverage_gap must equal the governed_members - "
            "source_applies_to difference"
        )
    return source_applies_to, coverage_gap


def _validate_variant_relation(
    qualifier: object,
    context: str,
    *,
    model_id: str,
    known_models: set[str],
) -> None:
    if not isinstance(qualifier, dict):
        raise ValueError(f"{context}: qualifier must be a mapping")
    required = {
        "relation_kind", "base_model", "variant_model", "differentiator",
    }
    if set(qualifier) != required:
        missing = sorted(required - set(qualifier))
        extra = sorted(set(qualifier) - required)
        raise ValueError(
            f"{context}: variant relation requires exactly {sorted(required)!r}; "
            f"missing={missing!r}, extra={extra!r}"
        )
    if qualifier["relation_kind"] != "direct_variant":
        raise ValueError(f"{context}: relation_kind must be direct_variant")
    base_model = str(qualifier["base_model"] or "").strip()
    variant_model = str(qualifier["variant_model"] or "").strip()
    differentiator = str(qualifier["differentiator"] or "").strip()
    if base_model not in known_models:
        raise ValueError(f"{context}: unknown base_model {base_model!r}")
    if variant_model != model_id:
        raise ValueError(
            f"{context}: variant_model must equal owning model {model_id!r}"
        )
    if base_model == variant_model:
        raise ValueError(f"{context}: base_model and variant_model must differ")
    if not differentiator:
        raise ValueError(f"{context}: differentiator is required")


def _parse_row(
    raw: dict,
    fields: dict[str, FieldDef],
    model_id: str,
    series_memberships: Mapping[str, frozenset[str]],
    known_models: set[str],
) -> FactRow:
    fid = raw.get("field")
    if fid not in fields:
        raise ValueError(f"{model_id}/{FACTS_FILENAME}: unknown field id {fid!r}")
    status = raw.get("status")
    if status not in STATUSES:
        raise ValueError(f"{model_id}/{FACTS_FILENAME}: field {fid}: bad status {status!r}")
    value = raw.get("value")
    raw_qualifier = raw.get("qualifier") or {}
    if not isinstance(raw_qualifier, dict):
        raise ValueError(
            f"{model_id}/{FACTS_FILENAME}: field {fid}: qualifier must be a mapping"
        )
    qualifier = dict(raw_qualifier)
    if fid == "variant_relation":
        _validate_variant_relation(
            qualifier,
            f"{model_id}/{FACTS_FILENAME}: field {fid}",
            model_id=model_id,
            known_models=known_models,
        )
    if fid == "multi_camera_sync_capacity":
        _validate_multi_camera_sync_capacity(
            qualifier,
            f"{model_id}/{FACTS_FILENAME}: field {fid}",
            model_id=model_id,
        )
    if qualifier.get("source_scope") == "series":
        _validate_series_scope(
            qualifier,
            f"{model_id}/{FACTS_FILENAME}: field {fid}",
            model_id=model_id,
            series_memberships=series_memberships,
        )
    if fields[fid].value_type == "range" and isinstance(value, dict):
        rng = RangeValue(
            min=float(value["min"]),
            max=float(value["max"]),
            unit=str(value.get("unit", "m")),
            open_ended=bool(value.get("open_ended", False)),
        )
        if rng.min > rng.max:
            raise ValueError(f"{model_id}/{FACTS_FILENAME}: field {fid}: min > max")
        value = rng
    profile_set = None
    raw_profile_set = raw.get("profile_table")
    if raw_profile_set is not None:
        if fid not in {"rgb_resolution_fps", "depth_resolution_fps"}:
            raise ValueError(
                f"{model_id}/{FACTS_FILENAME}: field {fid}: "
                "profile table is only valid for typed stream fields"
            )
        coverage = str(raw_profile_set.get("coverage") or "")
        if coverage not in {"complete", "partial"}:
            raise ValueError(
                f"{model_id}/{FACTS_FILENAME}: field {fid}: "
                f"bad profile coverage {coverage!r}"
            )
        profiles = []
        for profile in raw_profile_set.get("profiles") or []:
            formats = tuple(str(v).strip() for v in profile.get("formats") or [])
            fps = tuple(int(v) for v in profile.get("fps") or [])
            width = int(profile["width"])
            height = int(profile["height"])
            if (
                width <= 0 or height <= 0
                or not formats or any(not value for value in formats)
                or not fps or any(value <= 0 for value in fps)
            ):
                raise ValueError(
                    f"{model_id}/{FACTS_FILENAME}: field {fid}: "
                    "profile dimensions, formats, and fps must be positive/non-empty"
                )
            profiles.append(VideoProfile(
                width=width,
                height=height,
                formats=formats,
                fps=fps,
                qualifier=str(profile.get("qualifier") or ""),
            ))
        if not profiles:
            raise ValueError(
                f"{model_id}/{FACTS_FILENAME}: field {fid}: empty profile table"
            )
        profile_set = ProfileSet(
            coverage=coverage,
            profiles=tuple(profiles),
            note=str(raw_profile_set.get("note") or ""),
        )
    return FactRow(
        field=fid,
        value=value,
        raw_value=str(raw.get("raw_value", "")),
        status=status,
        source=dict(raw.get("source", {}) or {}),
        qualifier=qualifier,
        note=str(raw.get("note", "")),
        profile_set=profile_set,
    )


class FactStore:
    def __init__(
        self,
        fields,
        facts,
        known_models,
        series_memberships: Mapping[str, frozenset[str]] | None = None,
    ):
        self.fields: dict[str, FieldDef] = fields
        self._facts: dict[str, dict[str, FactRow]] = facts
        self.known_models: set[str] = set(known_models)
        memberships = {
            str(series_id): frozenset(model_ids)
            for series_id, model_ids in (series_memberships or {}).items()
        }
        self._series_memberships: Mapping[str, frozenset[str]] = MappingProxyType(
            memberships
        )

    @property
    def series_memberships(self) -> Mapping[str, frozenset[str]]:
        return self._series_memberships

    def get_spec(self, model_id: str, field_id: str) -> FactResult:
        if field_id not in self.fields:
            return FactResult("field_not_exists")
        if model_id not in self.known_models:
            return FactResult("unknown_model")
        row = self._facts.get(model_id, {}).get(field_id)
        if row is None:
            return FactResult("not_found")
        return FactResult("found", row)

    def compare(self, model_ids, field_ids):
        return {f: {m: self.get_spec(m, f) for m in model_ids} for f in field_ids}

    def assert_support(self, model_id: str, field_id: str, token: str) -> str:
        result = self.get_spec(model_id, field_id)
        if result.status != "found":
            return "unknown"
        haystack = f"{result.row.value} {result.row.raw_value}".casefold()
        return "confirmed" if token.casefold() in haystack else "not_stated"

    def rows(self, model_id: str) -> dict[str, FactRow]:
        return dict(self._facts.get(model_id, {}))

    def validate_series_qualifier(
        self,
        model_id: str,
        qualifier: object,
        *,
        context: str,
    ) -> tuple[frozenset[str], frozenset[str]]:
        return _validate_series_scope(
            qualifier,
            context,
            model_id=model_id,
            series_memberships=self.series_memberships,
        )


def _catalog_series_memberships(
    knowledge_dir: Path,
) -> dict[str, frozenset[str]]:
    catalog_path = knowledge_dir / "_facts" / "catalog.yaml"
    if not catalog_path.is_file():
        return {}
    resolver = load_catalog(catalog_path, knowledge_dir)
    grouped: dict[str, set[str]] = {}
    for entry in resolver.entries.values():
        series_id = entry.series.strip()
        if series_id:
            grouped.setdefault(series_id, set()).add(entry.id)
    return {
        series_id: frozenset(model_ids)
        for series_id, model_ids in grouped.items()
    }


def load_fact_store(
    knowledge_dir: Path,
    fields: dict[str, FieldDef],
    *,
    series_memberships: Mapping[str, frozenset[str]] | None = None,
) -> FactStore:
    facts: dict[str, dict[str, FactRow]] = {}
    known = set(load_product_catalog(knowledge_dir))
    catalog_path = knowledge_dir / "_facts" / "catalog.yaml"
    if series_memberships is not None and catalog_path.is_file():
        raise ValueError(
            "series_memberships fixture override is forbidden when catalog.yaml exists"
        )
    memberships = (
        _catalog_series_memberships(knowledge_dir)
        if series_memberships is None
        else {
            str(series_id): frozenset(model_ids)
            for series_id, model_ids in series_memberships.items()
        }
    )
    for model_id in sorted(known):
        path = knowledge_dir / model_id / FACTS_FILENAME
        if not path.exists():
            continue
        raw_rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        parsed: dict[str, FactRow] = {}
        for raw in raw_rows:
            row = _parse_row(raw, fields, model_id, memberships, known)
            if row.field in parsed:
                raise ValueError(f"{model_id}/{FACTS_FILENAME}: duplicate field {row.field}")
            parsed[row.field] = row
        facts[model_id] = parsed
    return FactStore(fields, facts, known, memberships)
