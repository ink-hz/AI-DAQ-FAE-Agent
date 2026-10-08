"""引擎 B:产品规格推理(兜底新场景)。

流程(设计文档 §4.4):
1. scenario → tech_path_rules → 候选型号集
2. 在 Knowledge/<型号>/hardware.md / software.md 中验证 constraints / platforms
3. 至多 5 个候选,每个产出一个 RetrievalHit(snippet=index.md 摘要)
"""
from dataclasses import dataclass
from pathlib import Path

from src.agent.schema import RequestSchema, RetrievalHit
from src.agent.tech_path_rules import match_tech_paths


@dataclass
class EngineBResult:
    hits: list[RetrievalHit]
    matched_paths: list[dict]    # 命中的 tech_path 规则,用于调试和回答理由


def _read_index_summary(model_dir: Path, max_chars: int = 1500) -> str:
    """读 index.md,截短到 ~1500 字喂 LLM。"""
    idx = model_dir / "index.md"
    if not idx.exists():
        return ""
    text = idx.read_text(encoding="utf-8")
    return text[:max_chars]


def _read_hw_summary(model_dir: Path, max_chars: int = 2000) -> str:
    hw = model_dir / "hardware.md"
    if not hw.exists():
        return ""
    return hw.read_text(encoding="utf-8")[:max_chars]


def _normalize_model_name(name: str) -> str:
    """规则表里用 'Gemini 335L',目录是 'Gemini_335L'。互转。"""
    return name.replace(" ", "_")


def _matches_constraints(hardware_text: str, constraints: list[str]) -> bool:
    """MVP 实现:不做严格数值解析,只看关键词是否在 hardware 文本中出现或不冲突。

    严格版本由后续在 bad case 中迭代。当前策略:
      - 任何 constraint 关键词在 hardware 中出现 → 加分
      - 没找到 → 不排除(避免误杀)
    """
    return True  # MVP 占位:让 LLM 在 synthesize 阶段判断细节


def _supports_platforms(software_text: str, platforms: list[str]) -> bool:
    if not platforms:
        return True
    return any(p.lower() in software_text.lower() for p in platforms)


def engine_b_search(
    *,
    schema: RequestSchema,
    product_catalog: dict[str, dict],
    max_candidates: int = 5,
) -> EngineBResult:
    if not schema.scenario:
        return EngineBResult(hits=[], matched_paths=[])

    paths = match_tech_paths(schema.scenario)
    if not paths:
        return EngineBResult(hits=[], matched_paths=[])

    candidate_models: list[str] = []
    seen: set[str] = set()
    for p in paths:
        for m in p["preferred_models"]:
            norm = _normalize_model_name(m)
            if norm not in seen and norm in product_catalog:
                seen.add(norm)
                candidate_models.append(norm)

    hits: list[RetrievalHit] = []
    for model_name in candidate_models[: max_candidates * 2]:
        info = product_catalog[model_name]
        model_dir = Path(info["dir"])
        hw_text = _read_hw_summary(model_dir)
        sw_text = (model_dir / "software.md").read_text(encoding="utf-8") if (model_dir / "software.md").exists() else ""
        if not _supports_platforms(sw_text, schema.platforms):
            continue
        if not _matches_constraints(hw_text, schema.constraints):
            continue
        snippet = _read_index_summary(model_dir)
        hits.append(RetrievalHit(
            source="product",
            kb_id=None,
            title=model_name,
            snippet=snippet,
            score=1.0,  # 规则筛不输出 sim
            metadata={
                "model": model_name,
                "dir": str(model_dir),
                "tech_path": next((p["tech_path"] for p in paths if model_name in [_normalize_model_name(m) for m in p["preferred_models"]]), ""),
            },
        ))
        if len(hits) >= max_candidates:
            break

    return EngineBResult(hits=hits, matched_paths=paths)
