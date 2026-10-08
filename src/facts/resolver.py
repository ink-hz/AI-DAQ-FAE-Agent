"""目录驱动的型号实体归一化 (R2):解析结果只能来自产品目录,禁止发明型号。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.data.product_loader import load_product_catalog


def normalize_alias(text: str) -> str:
    return re.sub(r"[\s_\-\.]+", "", text).casefold()


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    display_name: str
    series: str = ""
    family: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolveResult:
    status: str                       # resolved | ambiguous | unrecognized
    model_ids: tuple[str, ...] = ()
    entity_kind: str = "model"       # model | series
    entity_id: str = ""


def derived_aliases(model_id: str, display_name: str) -> set[str]:
    keys = {normalize_alias(model_id), normalize_alias(display_name)}
    parts = model_id.split("_")
    if len(parts) > 1:
        tail = normalize_alias("".join(parts[1:]))
        if len(tail) >= 2:
            keys.add(tail)
    return {k for k in keys if k}


class ModelResolver:
    def __init__(self, entries: list[CatalogEntry], ambiguous_aliases: dict):
        self.entries = {e.id: e for e in entries}
        index: dict[str, set[str]] = {}
        for e in entries:
            keys = derived_aliases(e.id, e.display_name)
            keys |= {normalize_alias(a) for a in e.aliases}
            for k in keys:
                index.setdefault(k, set()).add(e.id)
        self._ambiguous = {
            normalize_alias(str(k)): tuple(v) for k, v in ambiguous_aliases.items()
        }
        # Numeric product suffixes such as ``338`` are governed ambiguity
        # aliases.  When every candidate belongs to the same declared product
        # family, the normal user spelling with that family prefix must retain
        # the exact same ambiguity instead of falling through to unrecognized.
        # Textual family aliases (Femto/DaBai) stay curated and are not expanded.
        for raw_alias, candidate_ids in ambiguous_aliases.items():
            alias = str(raw_alias).strip()
            if not re.fullmatch(r"[0-9]+[A-Za-z]*", alias):
                continue
            candidates = [self.entries.get(str(model_id)) for model_id in candidate_ids]
            families = {
                entry.family.strip()
                for entry in candidates
                if entry is not None and entry.family.strip()
            }
            if len(candidates) != len(candidate_ids) or None in candidates:
                continue
            if len(families) != 1:
                continue
            family = next(iter(families))
            prefixed_alias = normalize_alias(f"{family} {alias}")
            if prefixed_alias in index:
                continue
            self._ambiguous.setdefault(prefixed_alias, tuple(candidate_ids))
        self._index = index
        series_index: dict[str, set[str]] = {}
        series_names: dict[str, str] = {}
        for entry in entries:
            series = entry.series.strip()
            if not series:
                continue
            key = normalize_alias(series)
            series_index.setdefault(key, set()).add(entry.id)
            series_names[key] = series
        self._series_index = {
            key: tuple(sorted(model_ids))
            for key, model_ids in series_index.items()
        }
        self._series_names = series_names

    @staticmethod
    def _series_key(text: str) -> str:
        stripped = re.sub(r"(系列|series|family)", "", text, flags=re.IGNORECASE)
        return normalize_alias(stripped)

    def resolve(self, text: str) -> ResolveResult:
        key = normalize_alias(text)
        if not key:
            return ResolveResult("unrecognized")
        if key in self._ambiguous:
            return ResolveResult("ambiguous", self._ambiguous[key])
        hits = self._index.get(key, set())
        if len(hits) == 1:
            model_id = next(iter(hits))
            return ResolveResult(
                "resolved", (model_id,), entity_kind="model", entity_id=model_id,
            )
        if len(hits) > 1:
            return ResolveResult("ambiguous", tuple(sorted(hits)))
        series_key = self._series_key(text)
        series_models = self._series_index.get(series_key, ())
        if len(series_models) > 1:
            return ResolveResult(
                "resolved",
                series_models,
                entity_kind="series",
                entity_id=self._series_names[series_key],
            )
        return ResolveResult("unrecognized")


def load_catalog(path: Path, knowledge_dir: Path) -> ModelResolver:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries: dict[str, CatalogEntry] = {}
    for raw in data.get("models", []):
        entries[raw["id"]] = CatalogEntry(
            id=raw["id"],
            display_name=raw.get("display_name", raw["id"].replace("_", " ")),
            series=str(raw.get("series", "")),
            family=str(raw.get("family", "")),
            aliases=tuple(str(a) for a in raw.get("aliases", ()) or ()),
        )
    for model_id in load_product_catalog(knowledge_dir):
        if model_id not in entries:
            entries[model_id] = CatalogEntry(
                id=model_id, display_name=model_id.replace("_", " ")
            )
    return ModelResolver(list(entries.values()), data.get("ambiguous_aliases", {}))
