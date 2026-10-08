"""K3 双源佐证过滤器。

把 20260706 人工驳回 4/23 佐证候选的三个原因下沉为可测代码:
1. 别名过短(如 Gemini_2 的 "2")与任意文本误配 → min_len 过滤;
2. 佐证答案同时谈多个型号,token 可能属于别的型号 → 跨型号污染拒绝;
3. 多口径字段(IR/Color 快门、多接口供电、分距离精度)不带限定语的
   佐证不可信(Gemini_2 快门教训:RGB 是卷帘,笼统 Global Shutter 佐证误导)。

纪律:佐证 ≠ 核验,这里只做过滤与分级,升级 fae_verified 永远是人工动作。
"""
from __future__ import annotations

import re

from src.facts.resolver import ModelResolver, derived_aliases

MULTI_QUALIFIER_FIELDS = frozenset({
    "shutter_type",      # IR 传感器 vs Color 传感器
    "power_supply",      # 按接口模式(MIPI/GMSL/PoE)多列口径
    "spatial_accuracy",  # 按距离档
})

_QUALIFIER_MARKS = (":", "：", "(", "（", "@")
_MIN_ALIAS_LEN = 3


def model_aliases(resolver: ModelResolver, model_id: str,
                  min_len: int = _MIN_ALIAS_LEN) -> set[str]:
    entry = resolver.entries.get(model_id)
    names = {model_id.replace("_", " ").casefold()}
    if entry:
        names.add(entry.display_name.casefold())
        names |= {a.casefold() for a in entry.aliases}
        names |= derived_aliases(entry.id, entry.display_name)
    return {n for n in names if len(n) >= min_len}


def mentions_other_model(text: str, model_id: str,
                         resolver: ModelResolver) -> bool:
    """佐证文本是否命中其他型号——命中则 token 归属不可靠。

    别名有前缀关系时(335L 是 335Le 的前缀)按最长匹配认领:所有型号的
    别名按长度降序扫描,命中即从文本挖除,归属记在最长别名的型号上。
    """
    lowered = text.casefold()
    pairs: list[tuple[str, str]] = []
    for mid in resolver.entries:
        pairs.extend((alias, mid) for alias in model_aliases(resolver, mid))
    found_other = False
    for alias, mid in sorted(pairs, key=lambda p: -len(p[0])):
        if alias in lowered:
            if mid != model_id:
                found_other = True
            lowered = lowered.replace(alias, " ")
    return found_other


def needs_qualifier_guard(field_id: str, value_text: str) -> bool:
    """多口径字段的 value 无限定语 → 佐证不得计强(见 SOP §3)。"""
    if field_id not in MULTI_QUALIFIER_FIELDS:
        return False
    return not any(m in str(value_text) for m in _QUALIFIER_MARKS)


def corroborate_verdict(
    field_id: str,
    tokens: list[str],
    answer_text: str,
    model_id: str,
    resolver: ModelResolver,
    value_text: str,
) -> tuple[str, list[str]]:
    """返回 (strength, reasons);strength ∈ {"strong","weak","rejected"}。"""
    reasons: list[str] = []
    if mentions_other_model(answer_text, model_id, resolver):
        return "rejected", ["跨型号污染:佐证答案涉及其他型号,token 归属不可靠"]
    strength = "strong"
    if all(re.fullmatch(r"[\d.]+", t) for t in tokens):
        strength = "weak"
        reasons.append("纯数字 token,可能误配")
    if needs_qualifier_guard(field_id, value_text):
        strength = "weak"
        reasons.append("多口径字段 value 无限定语,佐证不得计强")
    return strength, reasons
