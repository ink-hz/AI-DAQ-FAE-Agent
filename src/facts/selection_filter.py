"""选型硬约束过滤 (D3+D9):候选枚举自矩阵,约束判定归代码。

原则:
- missing != negative:矩阵查不到对应字段的型号进 unfilterable(需补证),不剔除;
- 例外:data_interface 行存在即视为接口完整枚举,缺 token 可剔除(理由记录在案);
- 停产型号默认剔除(include_discontinued=True 显式放行);lifecycle 为
  conflict 状态时不剔除,只在 notes 标注"停产状态存在冲突"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from src.facts.lexicon import INTERFACE_GROUPS
from src.facts.schema import RangeValue
from src.facts.store import FactStore

_UNIT_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0}
_DIST_VALUE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm|m)(?![a-zA-Z])", re.IGNORECASE)
_DIST_CONTEXT_RE = re.compile(r"距离|范围|工作|测量|远|米内|m 内")
_IP_RE = re.compile(r"IP\s*(\d{2})", re.IGNORECASE)

# 约束关键词 -> 接口 token 组(命中任一 token 即满足);单一定义见 lexicon.py
_INTERFACE_GROUPS = INTERFACE_GROUPS


@dataclass(frozen=True)
class ConstraintSpec:
    distances_m: list[float] = dc_field(default_factory=list)
    min_ip: int | None = None
    interface_tokens: list[str] = dc_field(default_factory=list)   # 命中的约束关键词
    unparsed: list[str] = dc_field(default_factory=list)

    @property
    def has_parsed(self) -> bool:
        return bool(self.distances_m or self.min_ip or self.interface_tokens)


@dataclass(frozen=True)
class FilterResult:
    candidates: list[str]
    excluded: list[tuple[str, str]]          # (model, 剔除理由)
    unfilterable: list[str]                  # 约束存在但矩阵证据不足,不可判定
    notes: list[str] = dc_field(default_factory=list)


def parse_constraints(constraints: list[str]) -> ConstraintSpec:
    distances: list[float] = []
    min_ip: int | None = None
    interfaces: list[str] = []
    unparsed: list[str] = []
    for raw in constraints:
        text = str(raw).strip()
        if not text:
            continue
        parsed_any = False
        if _DIST_CONTEXT_RE.search(text) and "精度" not in text:
            for m in _DIST_VALUE_RE.finditer(text):
                distances.append(round(float(m.group(1)) * _UNIT_TO_M[m.group(2).lower()], 6))
                parsed_any = True
        ip_match = _IP_RE.search(text)
        if ip_match:
            required = int(ip_match.group(1))
            min_ip = max(min_ip or 0, required)
            parsed_any = True
        lowered = text.casefold()
        for keyword in _INTERFACE_GROUPS:
            if keyword in lowered:
                if keyword not in interfaces:
                    interfaces.append(keyword)
                parsed_any = True
        if not parsed_any:
            unparsed.append(text)
    return ConstraintSpec(
        distances_m=distances, min_ip=min_ip,
        interface_tokens=interfaces, unparsed=unparsed,
    )


def _row_text(store: FactStore, model: str, field_id: str) -> str:
    result = store.get_spec(model, field_id)
    if result.status != "found":
        return ""
    return f"{result.row.value} {result.row.raw_value}".casefold()


def hard_filter_candidates(
    seed_candidates: list[str],
    constraints: list[str],
    store: FactStore,
    *,
    include_discontinued: bool = False,
    enumeration_cap: int = 8,
) -> FilterResult:
    spec = parse_constraints(constraints)
    enumerated = False
    models = [m for m in seed_candidates if m in store.known_models]
    if not models and spec.has_parsed:
        models = sorted(m for m in store.known_models if store.rows(m))
        enumerated = True

    candidates: list[str] = []
    excluded: list[tuple[str, str]] = []
    unfilterable: list[str] = []
    notes: list[str] = []
    if spec.unparsed:
        notes.append("未能代码化判定的约束(留给模型权衡): " + "; ".join(spec.unparsed))

    for model in models:
        reasons: list[str] = []
        unknown = False

        # 停产过滤(与其他约束无关,永远检查)
        lc = store.get_spec(model, "lifecycle_status")
        if lc.status == "found":
            lc_text = f"{lc.row.value} {lc.row.raw_value}"
            if lc.row.status == "conflict":
                notes.append(f"{model}: 停产状态存在来源冲突,未据此剔除")
            elif "停产" in lc_text and not include_discontinued:
                reasons.append("停产(依据: " + str(lc.row.source.get("file", "")) + ")")

        # 距离覆盖
        if spec.distances_m:
            r = store.get_spec(model, "depth_range_max")
            if r.status == "found" and isinstance(r.row.value, RangeValue):
                rng = r.row.value
                for d in spec.distances_m:
                    covered = d >= rng.min and (rng.open_ended or d <= rng.max)
                    if not covered:
                        reasons.append(f"工作距离 {d}m 超出标称 {r.row.raw_value}")
            else:
                unknown = True

        # IP 防护数值比较
        if spec.min_ip is not None:
            text = _row_text(store, model, "ip_rating")
            if text:
                m = _IP_RE.search(text)
                if m and int(m.group(1)) < spec.min_ip:
                    reasons.append(f"防护 IP{m.group(1)} 低于要求 IP{spec.min_ip}")
                elif not m:
                    unknown = True
            else:
                unknown = True

        # 接口(行存在即完整枚举,缺 token 剔除;行缺失 -> 不可判定)
        for keyword in spec.interface_tokens:
            group = _INTERFACE_GROUPS[keyword]
            text = _row_text(store, model, "data_interface")
            text += " " + _row_text(store, model, "poe_standard")
            if not text.strip():
                unknown = True
                continue
            if not any(tok in text for tok in group):
                reasons.append(f"数据接口未列出 {keyword}(接口行为完整枚举)")

        if reasons:
            excluded.append((model, "; ".join(reasons)))
        elif unknown and spec.has_parsed:
            unfilterable.append(model)
        else:
            candidates.append(model)

    if enumerated:
        notes.append(f"无种子候选,已按矩阵全库枚举并过滤(cap={enumeration_cap})")
        candidates = candidates[:enumeration_cap]
    return FilterResult(
        candidates=candidates, excluded=excluded,
        unfilterable=unfilterable, notes=notes,
    )
