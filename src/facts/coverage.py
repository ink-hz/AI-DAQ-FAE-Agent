"""事实字段覆盖契约。

覆盖状态与规格值分离：它只说明一个字段是否完成了声明范围内的源资料审计，
不改变 ``null=missing`` 的事实语义，也不把未发布值推断为不支持。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from src.facts.schema import FieldDef
from src.facts.store import FactStore

COVERAGE_FILENAME = "field_coverage.yaml"
SOURCE_COMPLETE_SCOPE = "published_values_in_local_sources"


@dataclass(frozen=True)
class FieldCoverage:
    field: str
    status: str
    comparison_ready: bool
    scope: str | None = None
    audited_at: str | None = None
    reviewer: str | None = None
    documented_models: tuple[str, ...] | None = None


class FieldCoverageManifest:
    def __init__(self, records: dict[str, FieldCoverage]):
        self._records = dict(records)

    def get(self, field_id: str) -> FieldCoverage:
        return self._records[field_id]

    def items(self):
        return self._records.items()

    @property
    def source_complete_count(self) -> int:
        return sum(r.status == "source_complete" for r in self._records.values())

    def summary(self, field_id: str, store: FactStore) -> dict[str, object]:
        record = self.get(field_id)
        with_value = sum(
            _has_non_conflict_fact(store, model_id, field_id)
            for model_id in store.known_models
        )
        documented = record.documented_models
        missing_documented = []
        if documented is not None:
            missing_documented = [
                model_id for model_id in documented
                if not _has_non_conflict_fact(store, model_id, field_id)
            ]
        return {
            "status": record.status,
            "scope": record.scope,
            "catalog_models": len(store.known_models),
            "models_with_value": with_value,
            "models_without_value": len(store.known_models) - with_value,
            "documented_models": len(documented) if documented is not None else None,
            "missing_documented_models": missing_documented,
            "comparison_ready": record.comparison_ready,
        }


def _has_non_conflict_fact(store: FactStore, model_id: str, field_id: str) -> bool:
    result = store.get_spec(model_id, field_id)
    return result.status == "found" and result.row.status != "conflict"


def audit_field_coverage(
    manifest: FieldCoverageManifest,
    store: FactStore,
    documented_field_models: dict[str, set[str]],
    *,
    all_fields: bool = False,
) -> dict[str, list[dict[str, str]]]:
    """Compare exact Markdown table labels, structured facts, and coverage scope."""
    findings: dict[str, list[dict[str, str]]] = {
        "documented_without_fact": [],
        "fact_without_documented_source": [],
        "source_complete_scope_mismatch": [],
    }
    for field_id, record in sorted(manifest.items()):
        if not all_fields and record.status != "source_complete":
            continue
        documented = documented_field_models.get(field_id, set())
        facts = {
            model_id for model_id in store.known_models
            if _has_non_conflict_fact(store, model_id, field_id)
        }
        for model_id in sorted(documented - facts):
            findings["documented_without_fact"].append({
                "field": field_id,
                "model": model_id,
                "status": record.status,
            })
        for model_id in sorted(facts - documented):
            findings["fact_without_documented_source"].append({
                "field": field_id,
                "model": model_id,
                "status": record.status,
            })
        if (
            record.status == "source_complete"
            and set(record.documented_models or ()) != documented
        ):
            findings["source_complete_scope_mismatch"].append({
                "field": field_id,
                "manifest_models": ",".join(record.documented_models or ()),
                "source_models": ",".join(sorted(documented)),
                "status": record.status,
            })
    return findings


def load_field_coverage(
    path: Path,
    fields: dict[str, FieldDef],
    store: FactStore,
    documented_field_models: dict[str, set[str]] | None = None,
) -> FieldCoverageManifest:
    """Load and validate the complete field partition and audited fact presence."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise ValueError(f"{path}: unsupported field coverage version")

    legacy = [str(value) for value in (data.get("legacy_unreviewed") or [])]
    source_complete = data.get("source_complete") or {}
    if not isinstance(source_complete, dict):
        raise ValueError(f"{path}: source_complete must be a mapping")

    classified = legacy + [str(value) for value in source_complete]
    unknown = sorted(set(classified) - set(fields))
    if unknown:
        raise ValueError(f"{path}: unknown field ids: {unknown}")
    duplicates = sorted({field for field in classified if classified.count(field) > 1})
    if duplicates:
        raise ValueError(f"{path}: fields classified more than once: {duplicates}")
    missing = sorted(set(fields) - set(classified))
    if missing:
        raise ValueError(f"{path}: unclassified fields: {missing}")

    records = {
        field_id: FieldCoverage(
            field=field_id,
            status="legacy_unreviewed",
            comparison_ready=False,
        )
        for field_id in legacy
    }
    for field_id, raw in source_complete.items():
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: source_complete.{field_id} must be a mapping")
        scope = str(raw.get("scope") or "")
        if scope != SOURCE_COMPLETE_SCOPE:
            raise ValueError(
                f"{path}: source_complete.{field_id}: unsupported scope {scope!r}"
            )
        documented = tuple(str(value) for value in (raw.get("documented_models") or []))
        unknown_models = sorted(set(documented) - store.known_models)
        if unknown_models:
            raise ValueError(
                f"{path}: source_complete.{field_id}: unknown models {unknown_models}"
            )
        missing_facts = [
            model_id for model_id in documented
            if not _has_non_conflict_fact(store, model_id, field_id)
        ]
        if missing_facts:
            raise ValueError(
                f"{path}: source_complete.{field_id}: documented models require "
                f"a non-conflict fact: {missing_facts}"
            )
        fact_models = {
            model_id for model_id in store.known_models
            if _has_non_conflict_fact(store, model_id, field_id)
        }
        extra_facts = sorted(fact_models - set(documented))
        if extra_facts:
            raise ValueError(
                f"{path}: source_complete.{field_id}: fact scope mismatch; "
                f"facts outside documented_models: {extra_facts}"
            )
        if documented_field_models is not None:
            source_models = documented_field_models.get(field_id, set())
            if set(documented) != source_models:
                raise ValueError(
                    f"{path}: source_complete.{field_id}: source scope mismatch; "
                    f"manifest={sorted(documented)}, source={sorted(source_models)}"
                )
        records[field_id] = FieldCoverage(
            field=field_id,
            status="source_complete",
            comparison_ready=True,
            scope=scope,
            audited_at=str(raw.get("audited_at") or ""),
            reviewer=str(raw.get("reviewer") or ""),
            documented_models=tuple(sorted(documented)),
        )
    return FieldCoverageManifest(records)
