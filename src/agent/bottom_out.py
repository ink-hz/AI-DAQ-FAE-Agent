"""Synthesizer / handler 软失败检测。

synthesizer 在依据不足时会输出"知识库未收录 / 未在公开规格书 / 请联系 FAE"
这类兜底文本(prompt 故意要求的,不是 bug)。但旧管线把它当成正常回答
emit,fallback_used 始终是 False — 评测会判 PASS,运营也看不到失败。

本模块给出一组 BOTTOM_OUT 指纹,orchestrator 在 emit done 之前用 detect()
扫一遍最终 body,命中即把 trace_state.fallback_used=True 翻起来。

跟 evals/run_eval._BOTTOM_OUT_PATTERNS 故意保持一致(同一组短语,两边互不
import 避免循环依赖)。
"""

# 与 evals/run_eval.py 一致;改一处必同步另一处。
_HARD_PATTERNS: tuple[str, ...] = (
    "知识库未收录",
    "请确认型号代码",
)

_CONDITIONAL_PATTERNS: tuple[str, ...] = (
    "未在公开规格书",
    "请联系 FAE",
    "请联系 Orbbec FAE",
    "未在已提取文档中完整列出",
)


def detect(text: str) -> str | None:
    """命中任一指纹则返回该指纹,否则返回 None。"""
    if not text:
        return None
    for p in _HARD_PATTERNS:
        if p in text and _looks_like_overall_abstention(text):
            return p
    for p in _CONDITIONAL_PATTERNS:
        if p in text and _looks_like_overall_abstention(text):
            return p
    return None


def _looks_like_overall_abstention(text: str) -> bool:
    stripped = text.strip()
    concrete_markers = (
        "Gemini", "Femto", "Astra", "MS", "USB", "PoE", "GMSL",
        "mm", "cm", "m", "fps", "FOV", "IP", "Baseline",
        "基线", "接口", "范围", "精度", "型号",
    )
    marker_hits = sum(1 for marker in concrete_markers if marker in stripped)
    table_or_list = stripped.count("|") >= 6 or stripped.count("\n- ") >= 3
    structured_answer = (
        "结论" in stripped
        and ("依据" in stripped or "注意" in stripped)
        and ("验证" in stripped or "下一步" in stripped)
    )
    if structured_answer and marker_hits >= 2:
        return False
    if table_or_list and marker_hits >= 4:
        return False
    if len(stripped) < 360:
        return True
    return marker_hits < 4 and not table_or_list
