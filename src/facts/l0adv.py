"""L0 加强(对抗)规格事实用例生成:六类变体,程序化判分,held-out 纪律。

与 l0gen.py(顺问形态)互补。六类变体针对 434 三轮暴露的错误形态:
  alias         别名问法(G335L/335网口/XL 混淆面)
  unit          单位换算(m ↔ cm,考数值保真)
  dual_caliber  双口径(工作范围 vs 理想范围,不得混用——断言纪律 §8.1)
  cross_model   跨型号并问(张冠李戴面,两个值都要对)
  discontinued  停产陷阱(新项目推荐问停产型号,须提示停产)
  missing_field 未收录字段(正确答案=明说没有;考 false-assert 红线)

held-out 纪律:本集只出报告、不逐题修;禁止把单题失败直接改成
针对性补丁——失败先归因(证据/上下文/循环/模型),按类修。
"""
from __future__ import annotations

import re
from pathlib import Path

from src.facts.l0gen import _fmt
from src.facts.schema import RANGE_PATTERN, RangeValue
from src.facts.store import FactStore

_TRUSTED = ("fae_verified", "doc_extracted")
# 弃答/负向披露形态词表(20260710 首跑复盘补宽):
# - 缺失披露:"无数据/未提及/未标注"等真实答案高频用词首跑漏收,9 条假失败;
# - 负向断言:"不支持/不适用/不涉及"——接口证据充分的负向结论
#   (USB-only 相机之于 PoE)是正确答案而非误断言;编造具体值仍然无标记可中。
_ABSTAIN_MARKERS = [
    "未收录", "没有", "未提供", "无法确认", "未明确", "暂无", "不能确认",
    "无数据", "未提及", "未标注", "无该字段", "无相关",
    "不支持", "不适用", "不涉及", "不采用",
]

# missing_field 前提校验(20260710):"矩阵无行"≠"知识库无此事实"——
# 首跑 4 条假失败(Femto Mega/Mega I/Persee N1 的 PoE 在 power_supply 行
# 与 hardware.md 里,335Le 理想范围在 index.md 里)。凡该型号文档含字段
# 证据词面,事实可被回答,弃答期望不成立,不出题。
_DOC_EVIDENCE_TOKENS = {
    "poe_standard": ("poe", "802.3"),
    "depth_range_ideal": ("理想工作范围", "理想工作距离", "ideal working range"),
    "baseline": ("基线", "baseline"),
}


def _fact_in_docs(knowledge_dir: Path, model_id: str, fid: str) -> bool:
    tokens = _DOC_EVIDENCE_TOKENS.get(fid) or ()
    model_dir = knowledge_dir / model_id
    if not tokens or not model_dir.is_dir():
        return False
    for md in sorted(model_dir.glob("*.md")):
        hay = md.read_text(encoding="utf-8").casefold()
        if any(t in hay for t in tokens):
            return True
    return False


def _range_tokens(row) -> list[str] | None:
    if not isinstance(row.value, RangeValue):
        return None
    m = RANGE_PATTERN.search(row.raw_value)
    return [m.group(1), m.group(3)] if m else [_fmt(row.value.min), _fmt(row.value.max)]


def _cm_variants(meters: float) -> list[str]:
    cm = meters * 100
    cm_txt = str(int(cm)) if cm.is_integer() else str(round(cm, 1))
    return [cm_txt, _fmt(meters)]


def build_alias_cases(store: FactStore, aliases: dict[str, list[str]]) -> list[dict]:
    """别名问法:同一 fae_verified 事实,用 catalog 别名而不是全名问。"""
    cases = []
    for model_id in sorted(store.known_models):
        for alias in aliases.get(model_id, []):
            for fid, row in sorted(store.rows(model_id).items()):
                fdef = store.fields[fid]
                if row.status != "fae_verified" or not fdef.l0_assertable:
                    continue
                tokens = _range_tokens(row)
                if tokens is None:
                    if not fdef.l0_token_pattern:
                        continue
                    m = re.search(fdef.l0_token_pattern,
                                  f"{row.value} {row.raw_value}", re.IGNORECASE)
                    if not m:
                        continue
                    tokens = [m.group(0)]
                cases.append({
                    "id": f"l0h-alias-{model_id}-{fid}-{alias}".lower()
                          .replace("_", "-").replace(" ", ""),
                    "variant": "alias",
                    "question": f"{alias} 的{fdef.name_zh}是什么？",
                    "expected_outcome": "resolved",
                    "must_include_all": tokens,
                    "must_not": ["Knowledge/"],
                    "target": {"model": model_id, "field": fid, "alias": alias},
                })
                break     # 每个别名一题足够,防组合爆炸
    return cases


def build_unit_cases(store: FactStore) -> list[dict]:
    """单位换算:range 下限用厘米问,答案含米或厘米任一正确表述即可。"""
    cases = []
    for model_id in sorted(store.known_models):
        row = store.rows(model_id).get("depth_range_max")
        if row is None or row.status not in _TRUSTED:
            continue
        if not isinstance(row.value, RangeValue) or row.value.unit != "m":
            continue
        display = model_id.replace("_", " ")
        cases.append({
            "id": f"l0h-unit-{model_id}".lower().replace("_", "-"),
            "variant": "unit",
            "question": f"{display} 最近能拍多少厘米？",
            "expected_outcome": "resolved",
            "must_include_any": _cm_variants(row.value.min),
            "must_not": ["Knowledge/"],
            "target": {"model": model_id, "field": "depth_range_max",
                       "expected_min_m": row.value.min},
        })
    return cases


def build_dual_caliber_cases(store: FactStore) -> list[dict]:
    """双口径:同型号同时有工作范围和理想范围时并问,两个上限都得对。"""
    cases = []
    for model_id in sorted(store.known_models):
        rows = store.rows(model_id)
        work, ideal = rows.get("depth_range_max"), rows.get("depth_range_ideal")
        if not work or not ideal:
            continue
        if work.status not in _TRUSTED or ideal.status not in _TRUSTED:
            continue
        if not isinstance(work.value, RangeValue) or not isinstance(ideal.value, RangeValue):
            continue
        if work.value.max == ideal.value.max:
            continue          # 两口径同值时此题无区分度
        display = model_id.replace("_", " ")
        cases.append({
            "id": f"l0h-dual-{model_id}".lower().replace("_", "-"),
            "variant": "dual_caliber",
            "question": (f"{display} 的深度工作范围和理想范围分别是多少？"
                         "判断能否覆盖某距离时应该用哪一个？"),
            "expected_outcome": "resolved",
            "must_include_all": [_fmt(work.value.max), _fmt(ideal.value.max)],
            "must_not": ["Knowledge/"],
            "target": {"model": model_id,
                       "work_max": work.value.max, "ideal_max": ideal.value.max},
        })
    return cases


_NUMERIC_TOKEN = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|m)\b")


def _distinct_value_token(row, fdef) -> str | None:
    """取该行一个可断言 token:range 用上限;否则 l0 模式或数值+单位。"""
    tokens = _range_tokens(row)
    if tokens:
        return tokens[-1]
    hay = f"{row.value} {row.raw_value}"
    if fdef.l0_token_pattern:
        m = re.search(fdef.l0_token_pattern, hay, re.IGNORECASE)
        if m:
            return m.group(0)
    m = _NUMERIC_TOKEN.search(hay)
    return m.group(0) if m else None


def build_cross_model_cases(store: FactStore, pairs: list[tuple[str, str]]) -> list[dict]:
    """跨型号并问:易混对(同系不同后缀)同一字段并问,两个值都要对。"""
    cases = []
    for a, b in pairs:
        if a not in store.known_models or b not in store.known_models:
            continue
        for fid in ("baseline", "depth_range_max", "poe_standard", "power_supply"):
            ra, rb = store.rows(a).get(fid), store.rows(b).get(fid)
            if not ra or not rb or ra.status not in _TRUSTED or rb.status not in _TRUSTED:
                continue
            fdef = store.fields[fid]
            va = _distinct_value_token(ra, fdef)
            vb = _distinct_value_token(rb, fdef)
            if va is None or vb is None or va.replace(" ", "") == vb.replace(" ", ""):
                continue
            da, db = a.replace("_", " "), b.replace("_", " ")
            cases.append({
                "id": f"l0h-cross-{a}-{b}-{fid}".lower().replace("_", "-"),
                "variant": "cross_model",
                "question": f"{da} 和 {db} 的{fdef.name_zh}分别是多少？不要混。",
                "expected_outcome": "resolved",
                "must_include_all": [va, vb],
                "must_not": ["Knowledge/"],
                "target": {"models": [a, b], "field": fid},
            })
            break             # 每对一题
    return cases


def build_discontinued_cases(store: FactStore) -> list[dict]:
    """停产陷阱:对停产型号问新项目推荐,答案必须提示停产。"""
    cases = []
    for model_id in sorted(store.known_models):
        row = store.rows(model_id).get("lifecycle_status")
        if row is None or "停产" not in str(row.value):
            continue
        display = model_id.replace("_", " ")
        cases.append({
            "id": f"l0h-eol-{model_id}".lower().replace("_", "-"),
            "variant": "discontinued",
            "question": f"新项目想批量用 {display}，可以直接推荐吗？",
            "expected_outcome": "resolved",
            "must_include_any": ["停产", "已停产"],
            "must_not": ["Knowledge/"],
            "target": {"model": model_id, "field": "lifecycle_status"},
        })
    return cases


def build_missing_field_cases(store: FactStore,
                              knowledge_dir: Path) -> list[dict]:
    """未收录字段:矩阵无行且文档无证据词面的字段直问,正确答案=明说
    没有或有据的"不支持"(考 false-assert 红线)。

    前提校验:矩阵无行 + `_fact_in_docs` 无证据词面,弃答期望才成立;
    答案给出弃答/负向披露标记词任一即通过,编具体值则无标记可中。
    """
    probes = [("poe_standard", "支持哪种 PoE 供电标准"),
              ("depth_range_ideal", "理想工作范围是多少"),
              ("baseline", "基线是多少")]
    cases = []
    for model_id in sorted(store.known_models):
        rows = store.rows(model_id)
        for fid, ask in probes:
            if fid in rows:
                continue
            if _fact_in_docs(knowledge_dir, model_id, fid):
                continue      # 文档里有(如 PoE 在 power_supply 行/hardware.md)
            display = model_id.replace("_", " ")
            cases.append({
                "id": f"l0h-missing-{model_id}-{fid}".lower().replace("_", "-"),
                "variant": "missing_field",
                "question": f"{display} {ask}？",
                "expected_outcome": ["resolved", "safe_abstained"],
                "must_include_any": _ABSTAIN_MARKERS,
                "must_not": ["Knowledge/"],
                "target": {"model": model_id, "field": fid,
                           "note": "矩阵与文档均无该事实;答案不得编造具体值"},
            })
            break             # 每型号一题
    return cases
