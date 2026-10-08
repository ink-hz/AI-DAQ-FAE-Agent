"""红线检查、强制提示、溯源验证。"""
import re

from src.agent.output_templates import extract_source_tags
from src.agent.schema import RequestSchema, RetrievalHit

# ---------- 拒答模板 ----------

REFUSAL_PRICE = (
    "Agent 不掌握价格、库存、MOQ、交期等商务信息,请联系销售或电商代表。"
)
REFUSAL_CUSTOMER = (
    "Agent 不能编造客户案例,也不提供客户或项目隐私信息。"
    "如需真实案例,请走内部授权流程,或仅使用公开、可核验、已授权资料。"
)
REFUSAL_BRAND_SAFETY = (
    "不支持生成不客观的竞品宣传话术。可以改为基于公开规格、实测数据和适用场景做中立对比。"
)
REFUSAL_OUT_OF_SCOPE = (
    "本助手只覆盖 Orbbec 3D 相机和激光雷达知识。该问题不在本助手覆盖范围。"
)
REFUSAL_UNKNOWN_MODEL = (
    "知识库未收录该型号。请确认型号代码,或联系 FAE 团队。"
)


# ---------- 前置关键词 ----------

_PRICE_PATTERNS = [
    r"多少钱", r"价格", r"报价", r"折扣", r"MOQ", r"最小起订", r"交期", r"什么时候出货",
]
_CUSTOMER_PATTERNS = [
    r"客户(?:名单|名称|姓名|名录|信息|资料|联系方式|电话|邮箱)",
    r"客户.{0,12}(?:采购记录|联系方式|电话|邮箱)",
    r"(?:采购记录|联系方式|电话|邮箱).{0,12}客户",
    r"(?:具体|某个|某家|指定).{0,6}客户",
    r"(?:编一个|编造|虚构|杜撰|造一个|写一个假的|假的).{0,16}(?:客户|案例|落地|项目)",
    r"(?:客户|案例|落地|项目).{0,16}(?:编一个|编造|虚构|杜撰|造一个|写一个假的|假的)",
    r"项目编号",
    r"PO\s*单",
    r"我们的客户(?:名单|名称|信息|资料)",
]
_BRAND_SAFETY_PATTERNS = [
    r"攻击.{0,8}竞品",
    r"竞品.{0,8}攻击",
    r"贬低.{0,8}竞品",
    r"竞品.{0,8}贬低",
    r"抹黑.{0,8}竞品",
    r"拉踩.{0,8}竞品",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def pre_check_user_message(message: str) -> str | None:
    """命中红线返回固定拒答文案;否则返回 None。"""
    if _matches_any(message, _PRICE_PATTERNS):
        return REFUSAL_PRICE
    if _matches_any(message, _CUSTOMER_PATTERNS):
        return REFUSAL_CUSTOMER
    if _matches_any(message, _BRAND_SAFETY_PATTERNS):
        return REFUSAL_BRAND_SAFETY
    return None


def categorize_refusal(message: str) -> tuple[str, str] | None:
    """E 桶 §7.4 — 按关键词正则把红线拒答分到精细 category。

    Returns:
      ("price", REFUSAL_PRICE)     — 命中价格/MOQ/交期等
      ("customer", REFUSAL_CUSTOMER) — 命中客户/PO/项目编号
      None — 未命中任何红线关键词

    "general_out_of_scope" 由 caller(E 桶 handle)在关键词不命中
    但 schema.intent="out_of_scope" 时自行兜底,不在本函数。
    """
    if _matches_any(message, _PRICE_PATTERNS):
        return ("price", REFUSAL_PRICE)
    if _matches_any(message, _CUSTOMER_PATTERNS):
        return ("customer", REFUSAL_CUSTOMER)
    if _matches_any(message, _BRAND_SAFETY_PATTERNS):
        return ("brand_safety", REFUSAL_BRAND_SAFETY)
    return None


# ---------- 强制风险提示 ----------

_HIGH_PRECISION_KW = ["≤1mm", "<1mm", "1mm 精度", "亚毫米", "高精度"]
_OUTDOOR_KW = ["户外", "阳光", "强光", "户外强光", "反光"]
_TRANSPARENT_KW = ["透明", "周转箱", "低反"]
_MULTI_DEVICE_KW = ["多机", "3 台以上", "5 台", "10 台"]
_CERT_KW = ["CE", "FCC", "RoHS", "Class 1", "Class1", "认证"]
_CAD_KW = ["CAD", "STP", "DWG", "尺寸", "公差", "装配"]


def _has(seq: list[str], keywords: list[str]) -> bool:
    text = " ".join(seq)
    return any(k.lower() in text.lower() for k in keywords)


def inject_mandatory_warnings(schema: RequestSchema) -> list[str]:
    warnings: list[str] = []
    if _has(schema.constraints + schema.scenario, _HIGH_PRECISION_KW):
        warnings.append("实际精度受目标材质/距离/ROI 影响,需实测确认。")
    if _has(schema.scenario + schema.constraints, _OUTDOOR_KW + _TRANSPARENT_KW):
        warnings.append("建议送样测试,实际效果取决于目标光学特性。")
    if _has(schema.scenario + schema.constraints, _MULTI_DEVICE_KW):
        warnings.append("多机部署建议先做 PoC,测干扰和带宽。")
    if _has(schema.scenario + schema.constraints, _CERT_KW):
        warnings.append("认证编号、有效期需回源证书 PDF 核对。")
    if _has(schema.constraints, _CAD_KW):
        warnings.append("请回源 STP/DWG 文件核对精确尺寸/公差。")
    return warnings


# ---------- confidence 免责声明 ----------

def add_confidence_disclaimer(text: str, hits: list[RetrievalHit]) -> str:
    qa_hits = [h for h in hits if h.source == "qa"]
    if not qa_hits:
        return text
    if any(h.metadata.get("confidence") == "high" for h in qa_hits):
        return text
    if any(h.metadata.get("confidence") == "medium" for h in qa_hits):
        prefix = "以下回答基于历史 FAE 记录,具体场景请实测确认。\n\n"
        return prefix + text
    return text


# ---------- 溯源验证 ----------

class GuardrailViolation(Exception):
    pass


_INLINE_SOURCE_PATTERNS = [
    # Markdown links used by the old prompts: [详情](Knowledge/Model/index.md)
    re.compile(r"\s*\[详情\]\(Knowledge/[^)]+\)"),
    # Inline source markers used by legacy bucket prompts.
    re.compile(r"\s*\[Knowledge/[^\]]+\]"),
    re.compile(r"\s*\[Knowledge_SDK:[^\]]+\]"),
    re.compile(r"\s*\(Knowledge/[^)]+\)"),
    re.compile(r"\s*\(Knowledge_SDK:[^)]+\)"),
    re.compile(r"\s*\[FAE-[^\]]+\]"),
    re.compile(r"\s*\[(?:faq|gap)-[^\]]+\]", re.IGNORECASE),
    re.compile(r"\s*\[src/[^\]]+\]"),
    re.compile(r"\s*\[safety_rules\.json:[^\]]+\]"),
]

_SOURCE_WARN_PATTERN = re.compile(
    r"\n*\s*_?\(本回答未通过溯源校验,可能引用事实不充分,建议联系 FAE 复核\)_?",
)

_UNSUPPORTED_ESTIMATE_SENTENCE_PATTERN = re.compile(
    r"[^。！？\n]*(?:约\s*\d|大约\s*\d|估计.{0,3}\d|\d+\s*左右|差不多\s*\d|\d+\s*个?\s*像素)[^。！？\n]*(?:[。！？]|$)"
)
_UNSUPPORTED_ESTIMATE_TABLE_ROW_PATTERN = re.compile(
    r"(?m)^\|[^\n]*(?:约\s*\d|大约\s*\d|估计.{0,3}\d|\d+\s*左右|差不多\s*\d|\d+\s*个?\s*像素)[^\n]*\|\s*$\n?"
)


def validate_sources(text: str, *, require_at_least: int = 1) -> None:
    tags = extract_source_tags(text)
    if len(tags) < require_at_least:
        raise GuardrailViolation(
            f"输出缺少溯源标记(需要至少 {require_at_least} 处 [FAE-xxx] 或 [Model/file.md:N])。"
        )


def validate_sources_event(sources: list[dict], *, require_at_least: int = 1) -> None:
    """Validate the structured SSE sources payload, not inline body markers."""
    valid_sources = [s for s in sources if isinstance(s, dict) and s]
    if len(valid_sources) < require_at_least:
        raise GuardrailViolation(
            f"sources 事件缺少溯源记录(需要至少 {require_at_least} 条)。"
        )


def clean_answer_text(text: str, *, strip: bool = True) -> str:
    """Remove machine-facing source/path markers from user-visible answer text."""
    cleaned = _SOURCE_WARN_PATTERN.sub("", text)
    for pattern in _INLINE_SOURCE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = _UNSUPPORTED_ESTIMATE_TABLE_ROW_PATTERN.sub("", cleaned)
    cleaned = _UNSUPPORTED_ESTIMATE_SENTENCE_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", cleaned)
    cleaned = re.sub(r"(?:^|\n)\*\*[^*\n]{1,40}[：:]\*\*\s*\n+(?=\*\*)", "\n", cleaned)
    cleaned = re.sub(r"[ \t]+([。，“”、；：！？,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() if strip else cleaned
