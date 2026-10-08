"""型号来源覆盖清单：描述资料状态，不把资料缺失解释成产品不支持。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

SOURCE_STATES = {
    "source_current",
    "source_sparse",
    "source_stale",
    "source_conflict",
}
OFFICIAL_LISTING_STATES = {"listed", "not_listed", "unknown"}
ROSTER_ENTITY_KINDS = {"product", "series", "tool", "sdk_entity"}
ROSTER_STATUSES = {"mapped", "pending", "informational"}
SOURCE_KINDS = {
    "product_page",
    "sdk_support",
    "ros2_support",
    "versioned_static",
    "local_review",
}
FRESHNESS_DAYS = {
    "product_page": 90,
    "sdk_support": 30,
    "ros2_support": 30,
}


class ModelCoverageError(ValueError):
    """覆盖清单结构或引用不合法。"""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ModelCoverageError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class ModelSourceCoverage:
    model_id: str
    product_kind: str
    source_state: str
    official_listing: str
    canonical_url: str | None
    source_scope: tuple[str, ...]
    verified_at: date
    source_kind: str
    content_hash: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class OfficialRosterEntry:
    official_name: str
    entity_kind: str
    status: str
    model_ids: tuple[str, ...]
    canonical_url: str
    reason: str | None = None


@dataclass(frozen=True)
class SourceFreshnessFinding:
    model_id: str
    code: str
    verified_at: date
    age_days: int
    freshness_days: int


@dataclass(frozen=True)
class ModelCoverageManifest:
    path: Path
    version: int
    roster_verified_at: date
    roster_sources: tuple[dict[str, str], ...]
    roster_entries: tuple[OfficialRosterEntry, ...]
    models: dict[str, ModelSourceCoverage]

    @property
    def unmapped_official_products(self) -> tuple[OfficialRosterEntry, ...]:
        return tuple(
            entry
            for entry in self.roster_entries
            if entry.entity_kind == "product" and entry.status == "pending"
        )

    def summary(self) -> dict[str, int]:
        counts = {state: 0 for state in sorted(SOURCE_STATES)}
        for model in self.models.values():
            counts[model.source_state] += 1
        return {
            "source_current": counts["source_current"],
            "source_sparse": counts["source_sparse"],
            "source_stale": counts["source_stale"],
            "source_conflict": counts["source_conflict"],
            "unmapped_official_products": len(self.unmapped_official_products),
        }


def _parse_date(value: object, *, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        raise ModelCoverageError(f"{field}: verified_at is required")
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ModelCoverageError(f"{field}: invalid date {value!r}") from exc


def _tuple_of_strings(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ModelCoverageError(f"{field}: expected a list")
    items = tuple(str(item).strip() for item in value if str(item).strip())
    if len(items) != len(value):
        raise ModelCoverageError(f"{field}: contains an empty value")
    return items


def _required_text(raw: dict[str, object], name: str, *, field: str) -> str:
    value = str(raw.get(name, "")).strip()
    if not value:
        raise ModelCoverageError(f"{field}: {name} is required")
    return value


def _load_roster(
    raw: object,
    *,
    known_models: set[str],
) -> tuple[date, tuple[dict[str, str], ...], tuple[OfficialRosterEntry, ...]]:
    if not isinstance(raw, dict):
        raise ModelCoverageError("official_roster must be a mapping")
    verified_at = _parse_date(raw.get("verified_at"), field="official_roster")
    source_rows = raw.get("sources")
    if not isinstance(source_rows, list) or not source_rows:
        raise ModelCoverageError("official_roster: sources must not be empty")
    sources: list[dict[str, str]] = []
    for index, item in enumerate(source_rows):
        if not isinstance(item, dict):
            raise ModelCoverageError(f"official_roster.sources[{index}] must be a mapping")
        url = _required_text(item, "url", field=f"official_roster.sources[{index}]")
        source_kind = _required_text(
            item, "source_kind", field=f"official_roster.sources[{index}]"
        )
        if source_kind not in SOURCE_KINDS:
            raise ModelCoverageError(
                f"official_roster.sources[{index}]: unknown source_kind {source_kind!r}"
            )
        sources.append({"url": url, "source_kind": source_kind})

    entry_rows = raw.get("entries")
    if not isinstance(entry_rows, list):
        raise ModelCoverageError("official_roster: entries must be a list")
    entries: list[OfficialRosterEntry] = []
    seen_names: set[str] = set()
    for index, item in enumerate(entry_rows):
        if not isinstance(item, dict):
            raise ModelCoverageError(f"official_roster.entries[{index}] must be a mapping")
        field = f"official_roster.entries[{index}]"
        name = _required_text(item, "official_name", field=field)
        if name in seen_names:
            raise ModelCoverageError(f"duplicate official roster entry: {name}")
        seen_names.add(name)
        entity_kind = _required_text(item, "entity_kind", field=field)
        if entity_kind not in ROSTER_ENTITY_KINDS:
            raise ModelCoverageError(f"{name}: unknown entity_kind {entity_kind!r}")
        status = _required_text(item, "status", field=field)
        if status not in ROSTER_STATUSES:
            raise ModelCoverageError(f"{name}: unknown roster status {status!r}")
        model_ids = _tuple_of_strings(item.get("model_ids", []), field=f"{name}.model_ids")
        unknown = sorted(set(model_ids) - known_models)
        if unknown:
            raise ModelCoverageError(f"{name}: unknown model_ids {unknown}")
        reason = str(item.get("reason", "")).strip() or None
        if status == "mapped" and not model_ids:
            raise ModelCoverageError(f"{name}: mapped entry requires model_ids")
        if status == "pending" and (model_ids or not reason):
            raise ModelCoverageError(
                f"{name}: pending entry requires empty model_ids and a reason"
            )
        if entity_kind == "product" and status == "informational":
            raise ModelCoverageError(
                f"{name}: product must be mapped or explicitly pending"
            )
        entries.append(
            OfficialRosterEntry(
                official_name=name,
                entity_kind=entity_kind,
                status=status,
                model_ids=model_ids,
                canonical_url=_required_text(item, "canonical_url", field=field),
                reason=reason,
            )
        )
    return verified_at, tuple(sources), tuple(entries)


def _load_models(
    raw: object,
    *,
    known_models: set[str],
) -> dict[str, ModelSourceCoverage]:
    if not isinstance(raw, dict):
        raise ModelCoverageError("models must be a mapping")
    manifest_models = {str(model_id) for model_id in raw}
    missing = sorted(known_models - manifest_models)
    if missing:
        raise ModelCoverageError(f"missing model coverage: {', '.join(missing)}")
    unknown = sorted(manifest_models - known_models)
    if unknown:
        raise ModelCoverageError(f"unknown local models: {', '.join(unknown)}")

    models: dict[str, ModelSourceCoverage] = {}
    for model_id, item in raw.items():
        if not isinstance(item, dict):
            raise ModelCoverageError(f"{model_id}: coverage must be a mapping")
        model_id = str(model_id)
        source_state = _required_text(item, "source_state", field=model_id)
        if source_state not in SOURCE_STATES:
            raise ModelCoverageError(
                f"{model_id}: unknown source_state {source_state!r}"
            )
        official_listing = _required_text(item, "official_listing", field=model_id)
        if official_listing not in OFFICIAL_LISTING_STATES:
            raise ModelCoverageError(
                f"{model_id}: unknown official_listing {official_listing!r}"
            )
        source_scope = _tuple_of_strings(
            item.get("source_scope"), field=f"{model_id}.source_scope"
        )
        if source_state in {"source_current", "source_sparse"} and not source_scope:
            raise ModelCoverageError(f"{model_id}: source_scope must not be empty")
        canonical_url = str(item.get("canonical_url", "")).strip() or None
        if source_state == "source_current" and canonical_url is None:
            raise ModelCoverageError(f"{model_id}: canonical_url is required")
        verified_at = _parse_date(item.get("verified_at"), field=model_id)
        source_kind = _required_text(item, "source_kind", field=model_id)
        if source_kind not in SOURCE_KINDS:
            raise ModelCoverageError(
                f"{model_id}: unknown source_kind {source_kind!r}"
            )
        content_hash = str(item.get("content_hash", "")).strip() or None
        if source_kind == "versioned_static" and not content_hash:
            raise ModelCoverageError(
                f"{model_id}: versioned_static requires content_hash"
            )
        reason = str(item.get("reason", "")).strip() or None
        if source_state == "source_conflict" and not reason:
            raise ModelCoverageError(f"{model_id}: source_conflict requires reason")
        models[model_id] = ModelSourceCoverage(
            model_id=model_id,
            product_kind=_required_text(item, "product_kind", field=model_id),
            source_state=source_state,
            official_listing=official_listing,
            canonical_url=canonical_url,
            source_scope=source_scope,
            verified_at=verified_at,
            source_kind=source_kind,
            content_hash=content_hash,
            reason=reason,
        )
    return models


def load_model_source_coverage(
    path: Path,
    known_models: Iterable[str],
) -> ModelCoverageManifest:
    """严格加载覆盖清单；缺文件或不完整都属于配置错误。"""
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except FileNotFoundError as exc:
        raise ModelCoverageError(f"model source coverage is missing: {path}") from exc
    except yaml.YAMLError as exc:
        raise ModelCoverageError(f"invalid model source coverage YAML: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ModelCoverageError(f"model source coverage must be a mapping: {path}")
    if raw.get("version") != 1:
        raise ModelCoverageError(f"unsupported model source coverage version: {raw.get('version')!r}")
    known = {str(model_id) for model_id in known_models}
    roster_date, roster_sources, roster_entries = _load_roster(
        raw.get("official_roster"), known_models=known
    )
    models = _load_models(raw.get("models"), known_models=known)
    return ModelCoverageManifest(
        path=path,
        version=1,
        roster_verified_at=roster_date,
        roster_sources=roster_sources,
        roster_entries=roster_entries,
        models=models,
    )


def audit_model_source_coverage(
    manifest: ModelCoverageManifest,
    *,
    as_of: date | None = None,
) -> tuple[SourceFreshnessFinding, ...]:
    """报告已过新鲜期的 current 来源；不修改事实或覆盖状态。"""
    today = as_of or date.today()
    findings: list[SourceFreshnessFinding] = []
    for model in manifest.models.values():
        if model.source_state != "source_current":
            continue
        freshness_days = FRESHNESS_DAYS.get(model.source_kind)
        if freshness_days is None:
            continue
        age_days = (today - model.verified_at).days
        if age_days > freshness_days:
            findings.append(
                SourceFreshnessFinding(
                    model_id=model.model_id,
                    code="source_freshness_expired",
                    verified_at=model.verified_at,
                    age_days=age_days,
                    freshness_days=freshness_days,
                )
            )
    return tuple(sorted(findings, key=lambda item: item.model_id))
