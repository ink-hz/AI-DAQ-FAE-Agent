"""事实矩阵 schema:字段字典、range 值解析、同义词索引。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

VALUE_TYPES = {"text", "range"}
STATUSES = {"doc_extracted", "fae_verified", "conflict", "needs_normalization"}
TABLE_TYPES = {
    "product_spec",
    "stream_capability",
    "sdk_platform_support",
    "firmware_operation",
    "mechanical",
    "certification",
    "unknown",
}

_UNIT_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0}
RANGE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mm|cm|m)?\s*[-~–—]\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)?(\+)?",
    re.IGNORECASE,
)


def normalize_label(text: str) -> str:
    return re.sub(r"[\s:：]+", "", text).casefold()


@dataclass(frozen=True)
class FieldDef:
    id: str
    name_zh: str
    name_en: str
    synonyms: tuple[str, ...]
    value_type: str = "text"
    l0_assertable: bool = False
    l0_question: str | None = None
    l0_token_pattern: str | None = None
    section_capture: tuple[str, ...] = ()   # 按节聚合:命中这些 H2 标题时整节作为一行证据
    allowed_table_types: tuple[str, ...] = ("product_spec",)


@dataclass(frozen=True)
class RangeValue:
    min: float
    max: float
    unit: str = "m"
    open_ended: bool = False


@dataclass(frozen=True)
class VideoProfile:
    width: int
    height: int
    formats: tuple[str, ...]
    fps: tuple[int, ...]
    qualifier: str = ""


@dataclass(frozen=True)
class ProfileSet:
    coverage: str
    profiles: tuple[VideoProfile, ...]
    note: str = ""


def parse_range(text: str) -> RangeValue | None:
    m = RANGE_PATTERN.search(text)
    if not m:
        return None
    lo, lo_unit, hi, hi_unit, plus = m.groups()
    if not (lo_unit or hi_unit):
        return None
    factor_lo = _UNIT_TO_M[(lo_unit or hi_unit).lower()]
    factor_hi = _UNIT_TO_M[(hi_unit or lo_unit).lower()]
    return RangeValue(
        min=round(float(lo) * factor_lo, 6),
        max=round(float(hi) * factor_hi, 6),
        unit="m",
        open_ended=bool(plus),
    )


def load_field_dictionary(path: Path) -> dict[str, FieldDef]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fields: dict[str, FieldDef] = {}
    for raw in data["fields"]:
        fid = raw["id"]
        if fid in fields:
            raise ValueError(f"duplicate field id: {fid}")
        vt = raw.get("value_type", "text")
        if vt not in VALUE_TYPES:
            raise ValueError(f"field {fid}: unknown value_type {vt!r}")
        allowed_table_types = tuple(
            raw.get("allowed_table_types", ("product_spec",)) or ()
        )
        unknown_table_types = set(allowed_table_types) - TABLE_TYPES
        if unknown_table_types:
            raise ValueError(
                f"field {fid}: unknown allowed_table_type "
                f"{sorted(unknown_table_types)!r}"
            )
        fields[fid] = FieldDef(
            id=fid,
            name_zh=raw["name_zh"],
            name_en=raw.get("name_en", ""),
            synonyms=tuple(raw.get("synonyms", ()) or ()),
            value_type=vt,
            l0_assertable=bool(raw.get("l0_assertable", False)),
            l0_question=raw.get("l0_question"),
            l0_token_pattern=raw.get("l0_token_pattern"),
            section_capture=tuple(raw.get("section_capture", ()) or ()),
            allowed_table_types=allowed_table_types,
        )
    return fields


def synonym_index(fields: dict[str, FieldDef]) -> dict[str, str]:
    index: dict[str, str] = {}
    for f in fields.values():
        for label in (f.name_zh, f.name_en, *f.synonyms):
            if not label:
                continue
            key = normalize_label(label)
            if key in index and index[key] != f.id:
                raise ValueError(
                    f"synonym collision: {label!r} -> {index[key]} and {f.id}"
                )
            index[key] = f.id
    return index
