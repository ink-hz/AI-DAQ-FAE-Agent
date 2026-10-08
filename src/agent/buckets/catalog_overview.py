"""A 桶 — catalog_overview。工作流设计 §3。

DAG:_group_by_tech_path → scenario_filter → overview_synth_stream

handle() 返回 CatalogOverviewResult:
  - decision: "answered" | "no_match"
  - grouped: dict[tech_path → list[{name, summary, dir}]]
  - hits:    list[RetrievalHit] — 每个 model 一个 hit(供溯源 / sources event)
  - chunks:  Iterator[str] | None — overview_synth_stream 返回的 lazy iterator
  - used_full_catalog: bool — True 表示用户类别词不在白名单(或接口词触发兜底),
                             prompt 据此先说 "该类别不在 Orbbec 产品范围,以下是全量目录"

scenario_filter 白名单(case-insensitive 子串匹配 schema.scenario 拼接串):
  - tech_path 关键词:["ToF", "iToF", "激光雷达", "雷达", "双目", "结构光", "单目"]
    → 命中 → 只保留组名含该关键词的分组(其余型号被过滤)
  - 接口关键词:["GMSL", "PoE", "USB3", "以太网"]
    → MVP 简化:接口元数据未预加载到 catalog,走全量 + used_full_catalog=True
  - filter 后空(scenario 非空且无任何白名单关键词命中)→ no_match + 全量 + used_full_catalog=True
  - empty scenario → 全量 + answered + used_full_catalog=False

为什么不在 handle 里 consume chunks:与 T4/T5 同 — 让 orchestrator 决定真跑 LLM,
chunks 是 lazy iterator,被 for-loop 才发请求。
"""
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from src.agent.engine_b import _read_index_summary
from src.agent.schema import RequestSchema, RetrievalHit
from src.agent.synthesizer import overview_synth_stream
from src.agent.tech_path_rules import TECH_PATH_RULES

# index.md 摘要最大输出长度,工作流设计 §3.5 第 3 条
# 注意:_read_index_summary 的读取窗口要更大(否则截不到 ## 型号身份 表的 产品类别 行)
_SUMMARY_MAX_CHARS = 80
_SUMMARY_READ_WINDOW = 1500

# §3.3 scenario_filter 白名单
_TECH_PATH_KEYWORDS = ["ToF", "iToF", "激光雷达", "雷达", "双目", "结构光", "单目"]
_INTERFACE_KEYWORDS = ["GMSL", "PoE", "USB3", "以太网"]

_OTHER_GROUP = "其他"

Decision = Literal["answered", "no_match"]


@dataclass
class CatalogOverviewResult:
    """A 桶产出。

    answered:  scenario 空或命中 tech_path 白名单 → grouped 是按 tech_path 分组的目录
    no_match:  scenario 非空且无白名单关键词命中 → grouped 仍为全量(由 LLM 提示用户)
    """
    decision: Decision
    grouped: dict[str, list[dict]] = field(default_factory=dict)
    hits: list[RetrievalHit] = field(default_factory=list)
    chunks: Iterator[str] | None = None
    used_full_catalog: bool = False


# ---------------- internal helpers ----------------


def _summary_first_line(model_dir: Path) -> str:
    """提取 index.md 里 model 的 "一句话身份" 用于 LLM 喂料。

    Knowledge/<model>/index.md 有两类首段结构:

      Pattern A(老格式 / 多数型号):
        # X 型号知识入口
        ## 型号身份
        | 字段 | 内容 |
        | --- | --- |
        | 标准型号 | X |
        | 产品类别 | <这一行就是最佳一句话身份> |

      Pattern B(新格式):
        # X
        X 是 ... <这一段就是最佳介绍>

    策略:
      1. 跳过 H1 标题(`# X 型号知识入口` 之类是占位,不算摘要)
      2. 优先抓 `| 产品类别 | ...|` table 行(语义最准)
      3. 否则取第一行 "非 heading / 非 table 分隔 / 非图片" 的实质文本
      4. 截到 _SUMMARY_MAX_CHARS;找不到 → 返回 ""(LLM 可接受空摘要)
    """
    text = _read_index_summary(model_dir, max_chars=_SUMMARY_READ_WINDOW)
    if not text:
        return ""

    lines = text.splitlines()
    saw_h1 = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # H1:跳过(可能是 "# X 型号知识入口" 或 "# X")
        if line.startswith("# ") and not line.startswith("## "):
            saw_h1 = True
            continue

        # 已进入下一个 H2 段标题:跳过 — 例如 "## 型号身份" / "## 快速事实"
        if line.startswith("## "):
            continue

        # 表分隔符 / 表头 — 跳过
        if set(line.replace("|", "").replace("-", "").replace(":", "").strip()) <= {" "}:
            continue
        if line.startswith("| 字段") or line.startswith("|字段"):
            continue

        # `| 产品类别 | XXX |` table row → 最佳摘要
        if line.startswith("|") and "产品类别" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            # 找 "产品类别" 后的下一个 cell
            for i, c in enumerate(cells):
                if "产品类别" in c and i + 1 < len(cells):
                    val = cells[i + 1]
                    if val:
                        return val[:_SUMMARY_MAX_CHARS]
            continue

        # 其他 table 行(非 产品类别)— 跳过,继续找 产品类别 或 intro 段
        if line.startswith("|"):
            continue

        # 图片(Pattern B 介绍后常跟 ![X](...))— 跳过
        if line.startswith("!["):
            continue

        # 列表项(Pattern B 的某些资料导航段)— 也跳过,不算摘要
        if line.startswith(("- ", "* ", "+ ")):
            continue

        # 实质文本段(Pattern B 的介绍句)— 必须在 H1 之后
        if saw_h1:
            return line[:_SUMMARY_MAX_CHARS]

    return ""


def _normalize_model_name(name: str) -> str:
    """rules 表里用 'Femto Bolt',catalog key 是 'Femto_Bolt'。"""
    return name.replace(" ", "_")


def _group_by_tech_path(catalog: dict[str, dict]) -> dict[str, list[dict]]:
    """把 catalog 按 tech_path 分组。每个 model 只落到第一条命中规则的 tech_path 中。

    Returns:
      dict[tech_path → list[{"name", "summary", "dir"}]]

    未在任何 rule.preferred_models 中出现的 model → 落到 "其他" 组,不丢失。
    """
    # 1. 为每个 catalog 型号决定 tech_path(按 TECH_PATH_RULES 顺序首次命中)
    model_to_path: dict[str, str] = {}
    for model_key in catalog:
        for rule in TECH_PATH_RULES:
            preferred_norm = {_normalize_model_name(m) for m in rule["preferred_models"]}
            if model_key in preferred_norm:
                model_to_path[model_key] = rule["tech_path"]
                break
        else:
            model_to_path[model_key] = _OTHER_GROUP

    # 2. 反向聚合 — 保持 TECH_PATH_RULES 出现顺序,其他放最后
    grouped: dict[str, list[dict]] = {}
    seen_paths: list[str] = []
    for rule in TECH_PATH_RULES:
        if rule["tech_path"] not in seen_paths:
            seen_paths.append(rule["tech_path"])
    seen_paths.append(_OTHER_GROUP)

    for path in seen_paths:
        models_in_path = [k for k, p in model_to_path.items() if p == path]
        if not models_in_path:
            continue
        # 稳定 — 按 catalog 出现顺序(dict 自 3.7 保序)
        ordered = [k for k in catalog if k in models_in_path]
        entries = []
        for model_key in ordered:
            info = catalog[model_key]
            model_dir = Path(info["dir"])
            entries.append({
                "name": model_key,
                "summary": _summary_first_line(model_dir),
                "dir": str(model_dir),
            })
        grouped[path] = entries
    return grouped


def _scenario_filter(
    grouped: dict[str, list[dict]],
    scenario: list[str],
    *,
    user_query: str = "",
) -> tuple[dict[str, list[dict]], Decision, bool]:
    """根据 schema.scenario 过滤 grouped。

    Returns:
      (filtered_grouped, decision, used_full_catalog)

    规则(§3.3):
      - scenario 空 → grouped 原样 + answered + used_full_catalog=False
      - tech_path 关键词命中(case-insensitive 子串)→ 只留组名含该关键词的分组
        + answered + used_full_catalog=False
      - 接口关键词命中 → MVP 走全量 + answered + used_full_catalog=True
      - scenario 非空但无白名单命中 → 全量 + no_match + used_full_catalog=True
    """
    query_text = user_query.lower()
    asks_camera_not_lidar = (
        ("相机" in user_query or "camera" in query_text)
        and not any(term in user_query or term in query_text for term in ("雷达", "激光雷达", "lidar"))
    )
    if asks_camera_not_lidar:
        camera_grouped = {
            path: entries
            for path, entries in grouped.items()
            if "雷达" not in path and "lidar" not in path.lower()
        }
        if camera_grouped:
            grouped = camera_grouped

    if not scenario and not user_query.strip():
        return grouped, "answered", False

    haystack = " ".join([*scenario, user_query]).lower()

    # tech_path 关键词优先
    matched_tech_kws = [kw for kw in _TECH_PATH_KEYWORDS if kw.lower() in haystack]
    if matched_tech_kws:
        # 只保留组名含任一命中关键词的分组(case-insensitive 子串)
        filtered = {
            path: entries
            for path, entries in grouped.items()
            if any(kw.lower() in path.lower() for kw in matched_tech_kws)
        }
        if filtered:
            return filtered, "answered", False
        # tech_path 关键词命中但 catalog 内无对应分组 → 走全量兜底
        return grouped, "no_match", True

    # 接口关键词 — MVP 走全量(接口元数据未预加载到 catalog)
    if any(kw.lower() in haystack for kw in _INTERFACE_KEYWORDS):
        return grouped, "answered", True

    if not scenario:
        return grouped, "answered", False

    # scenario 非空但全无命中 → no_match,但仍展示全量
    return grouped, "no_match", True


def _flatten_hits(grouped: dict[str, list[dict]]) -> list[RetrievalHit]:
    """把分组目录铺平成 RetrievalHit 列表,供 orchestrator 发 sources 事件。"""
    hits: list[RetrievalHit] = []
    for tech_path, entries in grouped.items():
        for m in entries:
            hits.append(RetrievalHit(
                source="product",
                kb_id=None,
                title=m["name"],
                snippet=m.get("summary", ""),
                score=1.0,
                metadata={
                    "model": m["name"],
                    "dir": m.get("dir", ""),
                    "tech_path": tech_path,
                },
            ))
    return hits


# ---------------- public handle ----------------


def handle(
    *,
    schema: RequestSchema,
    user_query: str,
    product_catalog: dict[str, dict],
    provider: str,
    api_key: str,
    model_main: str,
    prompts_dir: Path,
    base_url: str = "",
    consultation_context: str = "",
) -> CatalogOverviewResult:
    """A 桶入口。步骤见 module docstring。

    Args:
        schema: RequestSchema;scenario 可空(展示全量)/含 tech_path 关键词(过滤)
        product_catalog: load_product_catalog() 返回的 dict {model: {"dir": str, ...}}
        api_key: 主合成模型 key
        model_main: 主合成模型名(overview_synth_stream 用)
    """
    # 1. catalog_group — 纯 Python 分组
    grouped_full = _group_by_tech_path(product_catalog)

    # 2. scenario_filter
    grouped_after_filter, decision, used_full_catalog = _scenario_filter(
        grouped_full,
        schema.scenario,
        user_query=user_query,
    )

    # 3. T6 follow-up:把 "其他" 组从 LLM-facing block 中剥离
    #    — 该组里都是 tech_path_rules.preferred_models 未覆盖的冷门 / 历史型号(Astra 家族、
    #      DaBai 家族、Persee、Zora E1 之类),全部喂给 LLM 会污染回答(40+ 模糊型号)。
    #    — 但溯源 hits 仍包含全量,sources 事件不丢任何 model。
    other_entries = grouped_after_filter.get(_OTHER_GROUP, [])
    other_count = len(other_entries)
    grouped_for_llm = {
        path: entries
        for path, entries in grouped_after_filter.items()
        if path != _OTHER_GROUP
    }

    # 4. overview_synth_stream — lazy iterator,handle 不 consume
    chunks = overview_synth_stream(
        user_query=user_query,
        schema=schema,
        grouped=grouped_for_llm,
        used_full_catalog=used_full_catalog,
        other_count=other_count,
        provider=provider,
        api_key=api_key,
        model=model_main,
        prompts_dir=prompts_dir,
        base_url=base_url,
        consultation_context=consultation_context,
    )

    return CatalogOverviewResult(
        decision=decision,
        grouped=grouped_for_llm,
        hits=_flatten_hits(grouped_after_filter),  # 仍含 "其他" 组型号,溯源不丢
        chunks=chunks,
        used_full_catalog=used_full_catalog,
    )
