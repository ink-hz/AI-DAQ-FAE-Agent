"""Catalog capability — product catalog evidence for overview questions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from src.agent.buckets.catalog_overview import (
    _OTHER_GROUP,
    _group_by_tech_path,
    _scenario_filter,
)
from src.agent.capabilities import register
from src.agent.evidence import Evidence, Fact
from src.agent.planner import Capability

_MAX_FACTS = 18
_CATALOG_SCOPE_SOURCE = "Knowledge/_通用资料/README.md:1"


def _display_model(model_key: str) -> str:
    return model_key.replace("_", " ")


def _source_ref(model_key: str) -> str:
    return f"Knowledge/{model_key}/index.md:1"


def _fact_statement(tech_path: str, entry: dict) -> str:
    model_key = str(entry.get("name") or "").strip()
    summary = str(entry.get("summary") or "").strip()
    model_name = _display_model(model_key)
    if summary:
        return f"Orbbec 产品目录: {tech_path} — {model_name}: {summary}"
    return f"Orbbec 产品目录: {tech_path} — {model_name}"


def _series_name(model_key: str) -> str:
    prefix = str(model_key).split("_", 1)[0].strip()
    return prefix or str(model_key)


def _flatten_group_count(grouped: dict[str, list[dict]]) -> int:
    return sum(len(entries) for entries in grouped.values())


def _scope_fact(catalog: dict, grouped: dict[str, list[dict]]) -> Fact:
    series_counts: dict[str, int] = {}
    for entries in grouped.values():
        for entry in entries:
            model_key = str(entry.get("name") or "").strip()
            if not model_key:
                continue
            series = _series_name(model_key)
            series_counts[series] = series_counts.get(series, 0) + 1

    ordered_series = sorted(series_counts.items(), key=lambda item: (-item[1], item[0]))
    series_text = ";".join(
        f"{series} 系列 {count} 个条目"
        for series, count in ordered_series
    )
    if not series_text:
        series_text = "无可计数系列"

    return Fact(
        statement=(
            f"Orbbec 产品目录口径: 本地知识库共收录 {len(catalog)} 个 Orbbec 产品条目;"
            f"本次问题筛选后目录包含 {_flatten_group_count(grouped)} 个条目;"
            "这是知识库口径,不是全部在售相机款数;代表型号列表不是总数。"
            f"系列计数: {series_text}。"
        ),
        source_ref=_CATALOG_SCOPE_SOURCE,
        confidence=1.0,
    )


def _series_list_fact(grouped: dict[str, list[dict]]) -> Fact | None:
    series_models: dict[str, list[str]] = {}
    for entries in grouped.values():
        for entry in entries:
            model_key = str(entry.get("name") or "").strip()
            if not model_key:
                continue
            series = _series_name(model_key)
            series_models.setdefault(series, [])
            display_name = _display_model(model_key)
            if display_name not in series_models[series]:
                series_models[series].append(display_name)

    if not series_models:
        return None

    ordered_series = sorted(series_models.items(), key=lambda item: (-len(item[1]), item[0]))
    series_text = ";".join(
        f"{series} 系列完整清单({len(models)} 个): " + "、".join(models)
        for series, models in ordered_series
    )
    return Fact(
        statement=(
            "Orbbec 产品目录系列清单: "
            f"{series_text}。该清单用于回答系列有哪些/多少个条目,不要只用代表型号替代全量。"
        ),
        source_ref=_CATALOG_SCOPE_SOURCE,
        confidence=1.0,
    )


def _main_3d_camera_series_fact(grouped: dict[str, list[dict]]) -> Fact | None:
    series_counts: dict[str, int] = {}
    for tech_path, entries in grouped.items():
        for entry in entries:
            model_key = str(entry.get("name") or "").strip()
            if not model_key:
                continue
            evidence_text = " ".join(
                str(part or "")
                for part in (
                    tech_path,
                    entry.get("summary"),
                    entry.get("category"),
                    entry.get("product_category"),
                )
            ).lower()
            if "激光雷达" in evidence_text or "lidar" in evidence_text:
                continue
            if not any(marker in evidence_text for marker in ("相机", "camera", "3d", "tof", "rgb-d")):
                continue
            series = _series_name(model_key)
            series_counts[series] = series_counts.get(series, 0) + 1

    if not series_counts:
        return None

    ordered_series = sorted(series_counts.items(), key=lambda item: (-item[1], item[0]))
    series_text = "、".join(
        f"{series}({count} 个条目)"
        for series, count in ordered_series
    )
    return Fact(
        statement=(
            f"Orbbec 主要 3D 相机系列(知识库口径): {series_text}。"
            "回答主要系列/有哪些系列时必须保留系列名,不要只按技术路线或代表型号压缩。"
        ),
        source_ref=_CATALOG_SCOPE_SOURCE,
        confidence=1.0,
    )


def _matrix_lifecycle_fact(catalog: dict, fact_store) -> Fact | None:
    """生命周期口径(D9→catalog):目录计数/推荐必须区分在售与停产。"""
    if fact_store is None:
        return None
    flagged: list[str] = []
    for model in sorted(catalog):
        r = fact_store.get_spec(model, "lifecycle_status")
        if r.status != "found":
            continue
        if r.row.status == "conflict":
            flagged.append(f"{_display_model(model)}(停产/量产状态存在来源冲突,待裁决)")
        elif r.row.value:
            flagged.append(f"{_display_model(model)}({r.row.value})")
    if not flagged:
        return None
    return Fact(
        statement=(
            f"产品目录生命周期口径: 以下 {len(flagged)} 款存在停产/维护边界标注,"
            "回答目录数量或做推荐时必须区分在售与停产: " + "; ".join(flagged) + "。"
        ),
        source_ref="Knowledge/型号风险索引.md:1",
        confidence=1.0,
    )


def _matrix_tech_route_fact(catalog: dict, fact_store) -> Fact | None:
    """技术路线分布(事实矩阵口径),替代靠段落检索拼凑的路线统计。"""
    if fact_store is None:
        return None
    counts: dict[str, list[str]] = {}
    for model in catalog:
        r = fact_store.get_spec(model, "tech_route")
        if r.status == "found" and str(r.row.value or "").strip():
            counts.setdefault(str(r.row.value).strip(), []).append(model)
    if not counts:
        return None
    parts = [
        f"{route}: {len(models)} 款"
        for route, models in sorted(counts.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    covered = sum(len(m) for m in counts.values())
    return Fact(
        statement=(
            f"技术路线分布(事实矩阵口径,覆盖 {covered} 款已录字段型号): "
            + "; ".join(parts) + "。未覆盖型号以文档为准。"
        ),
        source_ref="Knowledge/_facts/catalog.yaml:1",
        confidence=1.0,
    )


@dataclass
class CapabilityCatalog:
    name: Capability = "catalog"
    max_facts: int = _MAX_FACTS

    def run(
        self,
        query: str,
        *,
        schema,
        channel: str = "fae",
        product_catalog: dict | None = None,
        **kwargs,
    ) -> Evidence:
        t0 = perf_counter()
        catalog = product_catalog or {}
        if not catalog:
            return Evidence(
                source_module="catalog",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=["product_catalog 为空,无法生成产品目录 Evidence"],
                latency_ms=int((perf_counter() - t0) * 1000),
            )

        try:
            grouped_full = _group_by_tech_path(catalog)
            scenario = schema.scenario if schema else []
            grouped, decision, used_full_catalog = _scenario_filter(
                grouped_full,
                scenario,
                user_query=query,
            )
        except Exception as exc:
            return Evidence(
                source_module="catalog",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=[f"catalog 分组失败: {exc}"],
                latency_ms=int((perf_counter() - t0) * 1000),
                error=str(exc),
            )

        facts: list[Fact] = [_scope_fact(catalog, grouped)]
        fact_store = kwargs.get("fact_store")
        for extra in (
            _matrix_lifecycle_fact(catalog, fact_store),
            _matrix_tech_route_fact(catalog, fact_store),
        ):
            if extra and len(facts) < self.max_facts:
                facts.append(extra)
        main_series_fact = _main_3d_camera_series_fact(grouped)
        if main_series_fact and len(facts) < self.max_facts:
            facts.append(main_series_fact)
        series_fact = _series_list_fact(grouped)
        if series_fact and len(facts) < self.max_facts:
            facts.append(series_fact)
        for tech_path, entries in grouped.items():
            if tech_path == _OTHER_GROUP:
                continue
            for entry in entries:
                model_key = str(entry.get("name") or "").strip()
                if not model_key:
                    continue
                facts.append(Fact(
                    statement=_fact_statement(tech_path, entry),
                    source_ref=_source_ref(model_key),
                    confidence=1.0,
                ))
                if len(facts) >= self.max_facts:
                    break
            if len(facts) >= self.max_facts:
                break

        if not facts:
            return Evidence(
                source_module="catalog",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=["catalog 分组后没有可用产品事实"],
                latency_ms=int((perf_counter() - t0) * 1000),
            )

        notes: list[str] = []
        if scenario and not used_full_catalog:
            notes.append("按场景过滤产品目录")
        if used_full_catalog:
            notes.append("场景未精确匹配或接口元数据不足,返回全量核心目录")
        if decision == "no_match":
            notes.append("用户类别词未命中当前产品目录白名单")
        if len(facts) >= self.max_facts:
            notes.append(f"目录事实已截断到 {self.max_facts} 条")

        return Evidence(
            source_module="catalog",
            facts=facts,
            confidence="high",
            coverage="full",
            notes=notes,
            latency_ms=int((perf_counter() - t0) * 1000),
        )


register(CapabilityCatalog())
