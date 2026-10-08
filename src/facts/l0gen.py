"""从 fae_verified × l0_assertable 事实行生成 L0 断言用例。

评测断言与答案证据同源:答错 -> 改一行 facts.yaml -> 工具/答案/评测同步修正。
"""
from __future__ import annotations

import re

from src.facts.schema import RANGE_PATTERN, RangeValue
from src.facts.store import FactStore

DEFAULT_QUESTION = "{model} 的{name}是什么？"


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def build_l0_cases(store: FactStore, display_names: dict[str, str]) -> list[dict]:
    cases: list[dict] = []
    for model_id in sorted(store.known_models):
        for fid, row in sorted(store.rows(model_id).items()):
            fdef = store.fields[fid]
            if row.status != "fae_verified" or not fdef.l0_assertable:
                continue
            if isinstance(row.value, RangeValue):
                m = RANGE_PATTERN.search(row.raw_value)
                tokens = (
                    [m.group(1), m.group(3)] if m
                    else [_fmt(row.value.min), _fmt(row.value.max)]
                )
            elif fdef.l0_token_pattern:
                m = re.search(
                    fdef.l0_token_pattern,
                    f"{row.value} {row.raw_value}",
                    re.IGNORECASE,
                )
                if not m:
                    continue
                tokens = [m.group(0)]
            else:
                continue
            display = display_names.get(model_id, model_id.replace("_", " "))
            question = (fdef.l0_question or DEFAULT_QUESTION).format(
                model=display, name=fdef.name_zh
            )
            cases.append({
                "id": f"l0-fact-{model_id}-{fid}".lower().replace("_", "-"),
                "question": question,
                "expected_outcome": "resolved",
                "must_include_all": tokens,
                "must_not": ["Knowledge/"],
                "deterministic_assertions": {
                    "fact_matrix": {
                        "model": model_id,
                        "field": fid,
                        "expected_raw": row.raw_value,
                    }
                },
                "severity": "blocker",
                "quality_gate": "L0_fact_matrix",
                "tags": ["l0", "fact_matrix", fid],
                # 数值+单位通用校验只适用于 range 型字段;token 型字段
                # (IP65/802.3af/Global Shutter/被动)的 must_include_all 本身
                # 就是字段值断言,叠加 _has_field_value 会全量误报
                # field_missing:no_numeric_unit(20260707 全量跑实证)。
                "requires_field_value": isinstance(row.value, RangeValue),
                "origin": "fact_matrix",
                "notes": row.note or row.source.get("file", ""),
            })
    return cases
