"""长参数块反查:用户贴规格片段时,按事实矩阵反推候选型号 (D2 收尾)。

纯代码确定性匹配:数值带容差比较、token 完全匹配;score 低于阈值不返回,
禁止猜测。命中的型号必然存在于矩阵。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from src.facts.lexicon import SPEC_TOKEN_FIELDS
from src.facts.schema import RANGE_PATTERN, RangeValue
from src.facts.store import FactStore

_IP_RE = re.compile(r"IP\s*(\d{2})", re.IGNORECASE)
_BASELINE_RE = re.compile(
    r"(?:基线|baseline)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*mm|"
    r"(\d+(?:\.\d+)?)\s*mm\s*(?:基线|baseline)",
    re.IGNORECASE,
)
_UNIT_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0}
_TOKEN_FIELDS = SPEC_TOKEN_FIELDS
_REL_TOLERANCE = 0.08
_DISTINCTIVE_DECIMALS = 2
_MAX_DISTINCTIVE_MODELS = 3


@dataclass(frozen=True)
class SpecBlockMatch:
    model: str
    score: int
    matched_fields: tuple[str, ...]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(_REL_TOLERANCE * max(abs(a), abs(b)), 0.02)


def _decimal_places(token: str) -> int:
    return len(token.partition(".")[2]) if "." in token else 0


def _extract_signals(text: str) -> dict:
    ranges: list[tuple[float, float, int, int]] = []
    for m in RANGE_PATTERN.finditer(text):
        lo, lo_unit, hi, hi_unit, _plus = m.groups()
        if not (lo_unit or hi_unit):
            continue
        f_lo = _UNIT_TO_M[(lo_unit or hi_unit).lower()]
        f_hi = _UNIT_TO_M[(hi_unit or lo_unit).lower()]
        ranges.append((
            float(lo) * f_lo,
            float(hi) * f_hi,
            _decimal_places(lo),
            _decimal_places(hi),
        ))
    ip = None
    ip_m = _IP_RE.search(text)
    if ip_m:
        ip = int(ip_m.group(1))
    baseline_mm = None
    b_m = _BASELINE_RE.search(text)
    if b_m:
        baseline_mm = float(b_m.group(1) or b_m.group(2))
    lowered = text.casefold()
    tokens = [
        (group, fid) for group, fid in _TOKEN_FIELDS
        if any(tok in lowered for tok in group)
    ]
    return {"ranges": ranges, "ip": ip, "baseline_mm": baseline_mm, "tokens": tokens}


def signal_count(text: str) -> int:
    """参数块信号数(供调用方判断'这像不像一段规格')。"""
    s = _extract_signals(text)
    distinctive_endpoints = sum(
        (lo_decimals >= _DISTINCTIVE_DECIMALS)
        + (hi_decimals >= _DISTINCTIVE_DECIMALS)
        for _lo, _hi, lo_decimals, hi_decimals in s["ranges"]
    )
    return (
        len(s["ranges"])
        + distinctive_endpoints
        + (s["ip"] is not None)
        + (s["baseline_mm"] is not None)
        + len(s["tokens"])
    )


def match_models_by_spec_block(
    text: str,
    store: FactStore,
    *,
    top_k: int = 3,
    min_score: int = 2,
) -> list[SpecBlockMatch]:
    signals = _extract_signals(text)
    if not signals["ranges"] and signals["ip"] is None and signals["baseline_mm"] is None:
        return []

    # Two-decimal range endpoints such as 5.46m often act as a model-family
    # fingerprint. Count an endpoint only when it occurs in very few models;
    # common values such as 2.00m must not become a guessing shortcut.
    endpoint_models = {"min": {}, "max": {}}
    for model in sorted(store.known_models):
        rows = store.rows(model)
        for fid in ("depth_range_max", "depth_range_ideal"):
            row = rows.get(fid)
            if row is None or not isinstance(row.value, RangeValue):
                continue
            endpoint_models["min"].setdefault(round(row.value.min, 6), set()).add(model)
            endpoint_models["max"].setdefault(round(row.value.max, 6), set()).add(model)
    endpoint_counts = {
        side: Counter({value: len(models) for value, models in values.items()})
        for side, values in endpoint_models.items()
    }

    results: list[SpecBlockMatch] = []
    for model in sorted(store.known_models):
        rows = store.rows(model)
        if not rows:
            continue
        score = 0
        matched: list[str] = []

        for lo, hi, lo_decimals, hi_decimals in signals["ranges"]:
            for fid in ("depth_range_max", "depth_range_ideal"):
                row = rows.get(fid)
                if row is not None and isinstance(row.value, RangeValue):
                    if _close(lo, row.value.min) and _close(hi, row.value.max):
                        score += 1
                        matched.append(fid)
                        break
                    distinctive = []
                    if (
                        lo_decimals >= _DISTINCTIVE_DECIMALS
                        and abs(lo - row.value.min) < 1e-6
                        and endpoint_counts["min"][round(lo, 6)]
                        <= _MAX_DISTINCTIVE_MODELS
                    ):
                        distinctive.append("min")
                    if (
                        hi_decimals >= _DISTINCTIVE_DECIMALS
                        and abs(hi - row.value.max) < 1e-6
                        and endpoint_counts["max"][round(hi, 6)]
                        <= _MAX_DISTINCTIVE_MODELS
                    ):
                        distinctive.append("max")
                    if distinctive:
                        score += 2
                        matched.extend(f"{fid}:{side}" for side in distinctive)
                        break

        if signals["ip"] is not None:
            row = rows.get("ip_rating")
            if row is not None:
                m = _IP_RE.search(f"{row.value} {row.raw_value}")
                if m and int(m.group(1)) == signals["ip"]:
                    score += 1
                    matched.append("ip_rating")

        if signals["baseline_mm"] is not None:
            row = rows.get("baseline")
            if row is not None:
                b = re.search(r"(\d+(?:\.\d+)?)\s*mm", f"{row.value} {row.raw_value}")
                if b and _close(float(b.group(1)), signals["baseline_mm"]):
                    score += 1
                    matched.append("baseline")

        for group, fid in signals["tokens"]:
            row = rows.get(fid)
            if row is not None:
                row_text = f"{row.value} {row.raw_value}".casefold()
                if any(tok in row_text for tok in group):
                    score += 1
                    matched.append(f"{fid}:{group[0]}")

        if score >= min_score:
            results.append(SpecBlockMatch(model, score, tuple(dict.fromkeys(matched))))

    results.sort(key=lambda r: (-r.score, r.model))
    return results[:top_k]
