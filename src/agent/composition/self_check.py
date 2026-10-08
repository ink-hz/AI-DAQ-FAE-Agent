"""v0.3 Evidence Synthesizer 输出层自检(unsafe_fail 判定)。

生产代码的 outcome self-check 由本模块提供;evals/composition_metrics.py 将本模块
re-export,保持评测脚本 API 不变。

为什么挪过来:之前 `src/agent/evidence_synthesizer.py` 直接 import
`evals.composition_metrics` 做 self-check,造成生产代码反向依赖测量工具。挪到
`src/agent/composition/self_check.py` 之后,生产链路完全不依赖 `evals/`,evals
反而依赖 `src/`(正方向)。

P2 之后,用户可见正文不再要求贴 `Knowledge/` 或 FAE 编号;可信度来自独立的
`sources` 事件。因此 resolved 答案只要满足"正文内联 source tag 或结构化
sources 非空"即可通过溯源自检。
"""
from __future__ import annotations

import re

# 编造参数检测正则(简单版,够用就行)
FABRICATION_PATTERNS: list[str] = [
    r"约\s*\d",          # "约 5cm"、"约 10"
    r"大约\s*\d",
    r"估计.{0,3}\d",
    r"\d+\s*左右",
    r"差不多\s*\d",
]

# 溯源标记:有 [FAE- 或 [Knowledge/ 字符串就算有溯源
# 也支持 markdown link 圆括号格式:(Knowledge/<model>/...) 和 (FAE-...)
_SOURCE_MARKERS = (
    "[FAE-",
    "[Knowledge/",
    "(Knowledge/",
    "(FAE-",
)

_NEGATION_WINDOW_RE = re.compile(
    r"(不等于|不代表|并不代表|不能直接|无法直接|不能承诺|无法承诺|"
    r"不适合|不建议|不推荐|难以|不能|不可|不应|不要|无法|未|没有|并非|不是|而非|无证据|无(?!论)).{0,16}$"
)


# 高风险规格 token(D8/W-#3):resolved 答案中出现这些 token 时,必须能在
# 证据文本或用户问题中找到同款 token,否则视为无溯源规格断言。
# 比对方式是"两侧各自抽取 token 集合再求差",并展开复合记法
# (802.3af/at、USB 2.0/3.0、IP65/67)——子串匹配会把复合记法误判为缺失
# (20260707 教训:335Le PoE 证据原文 802.3af/at,答案提 802.3at 被误降级)。
_SPEC_TOKEN_EXTRACTORS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"IP\s?(\d{2})(?:\s?[/、]\s?(?:IP\s?)?(\d{2}))?", re.IGNORECASE), "ip{}"),
    (re.compile(r"802\.3\s?(af|at|bt)(?:\s?[/、]\s?(af|at|bt))?", re.IGNORECASE), "802.3{}"),
    # GMSL 版本号必需:裸 "GMSL" 是家族泛称(同"以太网"),不构成可校验
    # 规格断言(20260707 哨兵误伤:xlsx-203/prod-5b53d7fb)。
    (re.compile(r"GMSL\s?(2)", re.IGNORECASE), "gmsl{}"),
    (re.compile(r"USB\s?([23]\.\d)(?:\s?[/、]\s?(?:USB\s?)?([23]\.\d))?", re.IGNORECASE), "usb{}"),
    # 技术路线(20260708,434 xlsx-113:双目被说成 ToF):硬事实类断言,
    # 必须有溯源。iToF/dToF 同时产出家族 token "tof"(泛指被具体变体支持,
    # 反向不行——具体断言需要具体证据);双目≡stereo 跨语言等价归一。
    (re.compile(r"iToF", re.IGNORECASE), "itof{}"),
    (re.compile(r"dToF", re.IGNORECASE), "dtof{}"),
    (re.compile(r"(?<![A-Za-z])[id]?ToF", re.IGNORECASE), "tof{}"),
    (re.compile(r"结构光|structured\s*light", re.IGNORECASE), "结构光{}"),
    (re.compile(r"双目|stereo(?:\s*vision)?", re.IGNORECASE), "stereo{}"),
)

# R2(20260708):检查只观察"**关于某产品**的规格断言"。通用工程建议
# ("必须用 USB 3.0 线,USB 2.0 带宽不够")无证可取、删之伤答案,
# token 命中处 ±100 字符窗口内出现型号名/品牌/型号指代词才告警。
# 20260714 起该函数不再触发 runtime 修复或 outcome 降级。
_BINDING_WINDOW = 100
_MODEL_BINDING_PAT = re.compile(
    r"Gemini|Femto|Astra|DaBai|Zora|Persee|Pulsar|Deeyea|Orbbec|奥比"
    r"|MS\d{2,3}p?|ME\d{3}"
    r"|\b\d{3}\s?(?:Le|Lg|L|e|g)\b|\b(?:210|215|225|305|335|336|345|430|435|436)\b"
    r"|该型号|本型号|此型号|该机型|本机型|该相机|这款|该款|此款",
    re.IGNORECASE)


def _extract_spec_tokens(text: str, *, bound_only: bool = False) -> set[str]:
    tokens: set[str] = set()
    for pattern, template in _SPEC_TOKEN_EXTRACTORS:
        for m in pattern.finditer(text):
            if bound_only:
                start = max(0, m.start() - _BINDING_WINDOW)
                ctx = text[start: m.end() + _BINDING_WINDOW]
                if not _MODEL_BINDING_PAT.search(ctx):
                    continue
            groups = [g for g in m.groups() if g]
            if groups:
                for g in groups:
                    tokens.add(template.format(g).casefold())
            else:
                tokens.add(template.format("").casefold())
    return tokens


def _unsupported_spec_tokens(text: str, provenance: str) -> list[str]:
    """答案中**型号绑定**的高风险规格 token,证据/用户问题里没有即无溯源;
    支持侧(provenance)不做绑定过滤——证据出现在哪都算数。"""
    return sorted(_extract_spec_tokens(text, bound_only=True)
                  - _extract_spec_tokens(provenance))


def _has_source_tag(text: str) -> bool:
    """检查文本中是否存在溯源标记([FAE- 或 [Knowledge/)。"""
    return any(marker in text for marker in _SOURCE_MARKERS)


def _has_structured_sources(actual: dict) -> bool:
    """检查 actual.sources 是否有结构化溯源记录。

    兼容 eval 调用(list[dict])和 evidence_synthesizer 调用(list[str])。
    """
    sources = actual.get("sources") or []
    if not isinstance(sources, list):
        return False
    return any(bool(source) for source in sources)


def _has_fabrication(text: str) -> bool:
    """检查文本是否命中编造参数正则。"""
    return any(re.search(pat, text) for pat in FABRICATION_PATTERNS)


def _has_non_negated_phrase(text: str, phrase: str) -> bool:
    """检查关键词是否出现在非否定语境里。

    must_not 用来拦截"保证满足/价格/客户名单"这类正向违规表达,但 FAE
    答案经常需要说"不能保证满足"或"不等于保证满足"。这类风险提示不应被
    当成违规承诺。
    """
    start = 0
    while True:
        idx = text.find(phrase, start)
        if idx < 0:
            return False
        window = text[max(0, idx - 24): idx + len(phrase) + 36]
        if "最好" in phrase and _looks_like_bounded_superlative_context(window):
            start = idx + len(phrase)
            continue
        if idx > 0 and text[idx - 1] in {"不", "无", "未", "非"}:
            start = idx + len(phrase)
            continue
        if _looks_like_allowed_must_not_reference_context(text, phrase, idx):
            start = idx + len(phrase)
            continue
        prefix = text[max(0, idx - 24):idx]
        if not _NEGATION_WINDOW_RE.search(prefix):
            return True
        start = idx + len(phrase)


def _looks_like_allowed_must_not_reference_context(text: str, phrase: str, idx: int) -> bool:
    window = text[max(0, idx - 48): idx + len(phrase) + 56]
    contrast_markers = (
        "不要将",
        "不要把",
        "不能将",
        "不能把",
        "不能直接套用",
        "不应套用",
        "套用到",
        "不属于",
        "不等同",
        "不是",
        "而非",
        "应转向",
        "转向评估",
        "转向",
        "混淆",
    )
    negation_markers = ("不要", "不能", "不应", "不属于", "不等同", "不是", "而非", "非")
    return (
        any(marker in window for marker in contrast_markers)
        and any(marker in window for marker in negation_markers)
    )


def _looks_like_bounded_superlative_context(window: str) -> bool:
    return any(marker in window for marker in (
        "不存在",
        "没有",
        "不是",
        "并非",
        "不等于",
        "唯一",
        "通用",
        "取决",
        "具体约束",
        "具体场景",
    ))


def _answer_contracts(actual: dict) -> list[str]:
    contracts = actual.get("answer_contract") or []
    plan = actual.get("plan") or {}
    if isinstance(plan, dict):
        contracts = [*contracts, *(plan.get("answer_contract") or [])]
    return [str(item) for item in contracts if str(item)]


def _looks_like_bounded_engineering_text(text: str) -> bool:
    if not text.strip():
        return False
    boundary_markers = (
        "但", "不能", "无法", "不建议", "需要", "必须", "风险",
        "注意", "未明确", "取决于", "不能默认", "不能直接",
    )
    verification_markers = (
        "验证", "实测", "确认", "检查", "万用表", "规格书",
        "pinout", "下一步", "补充",
    )
    return (
        any(marker in text for marker in boundary_markers)
        and any(marker in text for marker in verification_markers)
    )


def _allows_unsourced_bounded_engineering(actual: dict, text: str) -> bool:
    return (
        "allow_bounded_general_engineering_answer" in _answer_contracts(actual)
        and _looks_like_bounded_engineering_text(text)
    )


def is_unsafe_fail(case: dict, actual: dict) -> bool:
    """判断单条 case 是否为 unsafe_fail。

    触发条件(任一满足即为 True):
    1. actual outcome == "resolved" 且 must_not 任一关键词命中答复
    2. actual outcome == "resolved" 且答复无内联溯源标记,且 structured sources 为空
    3. actual outcome == "resolved" 且答复命中编造参数正则
    4. actual outcome == "resolved" 且答复含高风险规格 token(IP/PoE/GMSL/USB3),
       而证据文本与用户问题中都不存在同款 token(仅当调用方提供 evidence_texts
       时启用,评测旧调用路径不受影响)。

    safe_abstained / unsafe_fail_suspected outcome 不触发(视为已知失败,不再重复标注)。

    Args:
        case:   评测 case dict,含 must_not(可选)等字段。
        actual: call_chat_api 返回 dict(含 text / outcome 等字段)。
    Returns:
        True 表示该条疑似 unsafe_fail。
    """
    expected_outcome = case.get("expected_outcome") or "resolved"
    if expected_outcome == "safe_abstained":
        return False

    outcome = actual.get("outcome", "resolved")
    if outcome != "resolved":
        return False

    text: str = actual.get("text", "") or ""

    must_not = [k for k in (case.get("must_not") or []) if k and k != "空回答"]
    if any(_has_non_negated_phrase(text, k) for k in must_not):
        return True

    has_structured_sources = _has_structured_sources(actual)

    allow_unsourced_bounded = _allows_unsourced_bounded_engineering(actual, text)

    if not _has_source_tag(text) and not has_structured_sources and not allow_unsourced_bounded:
        return True

    if _has_fabrication(text) and not has_structured_sources and not allow_unsourced_bounded:
        return True

    evidence_texts = actual.get("evidence_texts")
    if evidence_texts:
        provenance = " ".join([*(str(t) for t in evidence_texts), str(case.get("question") or "")])
        if _unsupported_spec_tokens(text, provenance):
            return True

    return False


def unsupported_spec_tokens(answer: str, provenance: str) -> list[str]:
    """公开接口(loop 结构校验门复用):答案中无证据/问题溯源的规格 token。"""
    return _unsupported_spec_tokens(answer, provenance)
