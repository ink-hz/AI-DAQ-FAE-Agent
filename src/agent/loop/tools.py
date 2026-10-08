"""循环骨架工具层 (M1):ToolResult 统一协议 + ToolBox 六工具。

设计约束(升级设计 §5.1):
- 纯代码、确定性、可单测;
- missing ≠ negative 下沉到协议:not_found(有口径无数据) 与
  field_not_exists(无此口径) 在 status 层区分;
- sources 由代码逐条给出,模型不复述来源;
- dispatch 永不抛异常,任何错误折叠为 status=tool_error。
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from pathlib import Path

from src.agent.capabilities.capability_sdk import (
    _best_code_snippet,
    _candidate_repos,
    _parse_sdk_intent,
    _record_allowed_by_intent,
    _score_sdk_record,
)
from src.agent.official_links import OfficialLink, OfficialLinkCatalog
from src.agent.sdk_coverage import (
    load_sdk_capability_coverage,
    matching_sdk_coverage,
)
from src.agent.sdk_device_support import (
    DeviceSupportMatrix,
    DeviceSupportQueryResult,
    load_device_support_matrix,
)
from src.data.product_loader import ProductChunk, _split_by_h2, iter_product_chunks
from src.facts.constraint_engine import filter_models as evaluate_model_constraints
from src.facts.coverage import FieldCoverageManifest, load_field_coverage
from src.facts.extract import scan_documented_field_models
from src.facts.model_coverage import load_model_source_coverage
from src.facts.resolver import ModelResolver, load_catalog
from src.facts.sampling_geometry import (
    SamplingProfileRegistry,
    evaluate_sampling_geometry,
    load_sampling_profiles,
)
from src.facts.schema import FieldDef, RangeValue, load_field_dictionary
from src.facts.store import FactRow, FactStore, load_fact_store
from src.index.chroma_client import embed_texts, ensure_embedding_model

CONFIDENCE_LAYERS = ("hard_fact", "doc", "code", "session")
_ACCURACY_FIELDS = ("spatial_accuracy", "depth_accuracy", "temporal_precision")
_CAMERA_PRODUCT_KINDS = frozenset({
    "structured_light_camera", "stereo_camera", "tof_camera", "camera_computer",
})
_NON_CAMERA_PRODUCT_KINDS = frozenset({"lidar", "android_board"})

_CJK_CHAR_RE = re.compile(r"[一-鿿]")

# 检索停用词:纯功能词不参与 AND 语义——"深度计算 主机 还是 相机"里的
# "还是"若计入必需词,信息词全中的节反被功能词漏配挤掉(F4 实跑教训)。
# 只收疑问/连接功能词,不收任何领域词。
_SEARCH_STOPWORDS = frozenset({
    "的", "了", "吗", "呢", "还是", "或者", "以及", "怎么", "如何",
    "请问", "一下", "什么", "哪个", "哪些", "是否", "能否", "可以",
})

_MODEL_CONSTRAINT_FACT_LEAF_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "约束标识,如 distance"},
        "field": {"type": "string", "description": "事实矩阵字段 ID"},
        "op": {
            "type": "string",
            "enum": [
                "covers", "supports_profile", "meets_profile_minimum",
                "gte", "lte", "contains", "not_contains", "eq",
            ],
        },
        "value": {
            "description": (
                "约束值。covers 可使用带单位字符串（如 300-500mm）、"
                "{min,max,unit} 或单位已写入键名的 {min_m,max_m};"
                "supports_profile 使用 width/height/format/fps 对象;"
                "meets_profile_minimum 使用 min_pixels 或 min_width+min_height,"
                "并可带 min_fps/format"
            ),
        },
        "role": {
            "type": "string",
            "enum": [
                "hard_requirement", "operating_boundary", "preferred_range",
            ],
            "description": (
                "缺省为 hard_requirement。工作范围必须用 operating_boundary + "
                "depth_range_max/covers；理想范围必须用 preferred_range + "
                "depth_range_ideal/covers"
            ),
        },
    },
    "required": ["field", "op", "value"],
    "additionalProperties": False,
}

_MODEL_CONSTRAINT_ASSESSMENT_LEAF_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "约束分支标识"},
        "op": {"type": "string", "enum": ["requires_assessment"]},
        "requirement": {
            "type": "string",
            "description": (
                "无法由单一事实字段证明的用户原始验收条件，必须原样保留；"
                "不得自行发明分辨率、帧率、距离等代理阈值"
            ),
        },
    },
    "required": ["id", "op", "requirement"],
    "additionalProperties": False,
}

_MODEL_CONSTRAINT_LEAF_SCHEMA = {
    "oneOf": [
        _MODEL_CONSTRAINT_FACT_LEAF_SCHEMA,
        _MODEL_CONSTRAINT_ASSESSMENT_LEAF_SCHEMA,
    ],
}

_MODEL_CONSTRAINT_GROUP_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "析取约束组标识"},
        "any_of": {
            "type": "array",
            "minItems": 1,
            "items": copy.deepcopy(_MODEL_CONSTRAINT_LEAF_SCHEMA),
            "description": "任一叶子满足即可满足该组;不允许嵌套",
        },
    },
    "required": ["any_of"],
    "additionalProperties": False,
}


@dataclass
class ToolResult:
    status: str                       # ok|not_found|field_not_exists|unknown_model|no_file|no_section|tool_error
    content: object = None
    sources: list[dict] = dc_field(default_factory=list)
    diagnostics: dict[str, object] = dc_field(default_factory=dict)

    def to_payload(self) -> dict:
        return {"status": self.status, "content": self.content, "sources": self.sources}


def _serialize_value(value: object) -> object:
    if isinstance(value, RangeValue):
        return asdict(value)
    return value


def _fact_source(model_id: str, row: FactRow) -> dict:
    return {
        "path": f"Knowledge/{model_id}/facts.yaml#{row.field}",
        "section": str(row.source.get("section", "")),
        "confidence_layer": "hard_fact",
    }


def _row_payload(row: FactRow) -> dict:
    payload = {
        "value": _serialize_value(row.value),
        "raw_value": row.raw_value,
        "verified": row.status,
    }
    if row.field == "spatial_accuracy":
        payload["interpretation_boundary"] = (
            "只适用于事实中明确列出的距离、分辨率和 ROI 条件;"
            "不得插值或外推到未列出的距离,也不得据此承诺现场绝对精度"
        )
    if row.field == "temporal_precision" and (
        "resolution" not in row.qualifier or "roi" not in row.qualifier
    ):
        payload["interpretation_boundary"] = (
            "本字段的分辨率或 ROI 未在 qualifier 中明确发布；回答和表格必须将未明确项"
            "标为未注明，不得把同表其他指标的测试条件移入时间精度。"
        )
    if row.field == "depth_range_ideal":
        payload["interpretation_boundary"] = (
            "理想/推荐范围不是操作范围边界;超出理想范围不能直接写成不支持或不可用,"
            "判断能否覆盖必须同时核对 depth_range_max"
        )
    if row.field == "variant_relation":
        payload["interpretation_boundary"] = (
            "direct_variant 可支撑 qualifier 指定型号对的核心差异；"
            "SDK 枚举、单边规格或系列相邻关系不能证明某能力为型号独有"
        )
    if row.field in {"optical_filter", "ambient_light_mitigation"}:
        payload["interpretation_boundary"] = (
            "环境光抑制或室内外定位不构成任意照度、距离、材质下的精度承诺；"
            "选型需保留事实 qualifier 并要求现场样机验证"
        )
    if row.field == "frame_sync_topology":
        payload["interpretation_boundary"] = (
            "VSYNC 帧触发与 PTP 网络授时不能互相替代；只能按 qualifier 中已发布的"
            "拓扑和电气边界设计，来源冲突与未声明项必须可见"
        )
    if row.qualifier:
        payload["qualifier"] = dict(row.qualifier)
    return payload


def _launch_recommendation_authority(
    target_model_ids: list[str],
    mappings: list[dict[str, str]],
) -> dict[str, object]:
    """Scope launch authorization to each exact canonical query target."""
    targets = sorted({str(model_id) for model_id in target_model_ids if model_id})
    mapped: dict[str, list[dict[str, str]]] = {model_id: [] for model_id in targets}
    seen: set[tuple[str, str, str]] = set()
    for mapping in mappings:
        model_id = str(mapping.get("model") or "")
        launch = str(mapping.get("launch") or "")
        branch = str(mapping.get("branch") or "")
        key = (model_id, launch, branch)
        if model_id not in mapped or not launch or key in seen:
            continue
        seen.add(key)
        payload = {"model": model_id, "launch": launch}
        if branch:
            payload["branch"] = branch
        mapped[model_id].append(payload)
    return {
        "scope": "per_model",
        "per_model_authority": {
            model_id: {
                "allowed": bool(model_mappings),
                "mappings": model_mappings,
                "reason": (
                    "explicit_model_launch_mapping"
                    if model_mappings
                    else "no_explicit_model_launch_mapping"
                ),
            }
            for model_id, model_mappings in mapped.items()
        },
    }


_SERIES_COVERAGE_QUALIFIER_KEYS = {
    "source_scope",
    "series_id",
    "source_applies_to",
    "coverage_gap",
}


def _series_fact_payload(
    row: FactRow,
    source_applies_to: frozenset[str],
    coverage_gap: frozenset[str],
) -> dict:
    """Return complete fact semantics with canonical series coverage metadata."""
    payload = _row_payload(row)
    qualifier = copy.deepcopy(row.qualifier)
    qualifier["source_applies_to"] = sorted(source_applies_to)
    qualifier["coverage_gap"] = sorted(coverage_gap)
    payload["qualifier"] = qualifier
    payload["source"] = dict(row.source)
    payload["note"] = row.note
    if row.profile_set is not None:
        payload["profile_table"] = asdict(row.profile_set)
    return payload


def _series_fact_semantic_fingerprint(
    row: FactRow,
    source_applies_to: frozenset[str],
    coverage_gap: frozenset[str],
) -> str:
    """Fingerprint fact meaning separately from catalog/source coverage."""
    payload = _series_fact_payload(row, source_applies_to, coverage_gap)
    qualifier = dict(payload["qualifier"])
    for key in _SERIES_COVERAGE_QUALIFIER_KEYS:
        qualifier.pop(key, None)
    payload["qualifier"] = qualifier
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


TOOL_SPECS: list[dict] = [
    {
        "name": "resolve_model",
        "description": "把用户提到的型号文本归一化为产品目录里的标准型号 ID。"
                       "回答任何型号相关问题前先调用;status=not_found 表示目录里没有该型号,禁止发明。",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "用户提到的型号文本"}},
            "required": ["text"],
        },
    },
    {
        "name": "fact_lookup",
        "description": "查询型号的硬事实规格(事实矩阵,唯一硬事实来源)。"
                       "给 field 查单字段;不给 field 返回该型号全部事实行。"
                       "分辨率/格式/帧率组合是否支持必须改用 filter_models 的"
                       "supports_profile,不能从本工具的规格文本自行判断。"
                       "status=not_found 表示该字段暂无数据(不等于不支持);field_not_exists 表示无此口径。",
        "input_schema": {
            "type": "object",
            "properties": {
                "model_id": {"type": "string", "description": "resolve_model 返回的标准型号 ID"},
                "field": {"type": "string", "description": "字段 ID,如 poe_standard/ip_rating;省略则返回全部"},
            },
            "required": ["model_id"],
        },
    },
    {
        "name": "series_fact_lookup",
        "description": (
            "查询受治理产品系列的系列级硬事实。系列问题必须用本工具，不能从单一型号"
            "外推。ok=全成员共享同一系列事实；not_found=部分/全部缺失且不等于不支持；"
            "conflict=成员值或系列范围不一致，必须并列说明。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "series": {
                    "type": "string",
                    "description": "resolve_model 返回的标准 series_id",
                },
                "field": {
                    "type": "string",
                    "description": "系列事实字段 ID",
                },
            },
            "required": ["series", "field"],
        },
    },
    {
        "name": "search_knowledge",
        "description": "按关键词在全部型号知识文档的 H2 节标题与正文中检索,返回命中节列表(不含全文)。"
                       "适合定位'哪个型号/哪份文档讲了这个主题',找到后用 read_doc 读整节。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string",
                                     "description": "检索关键词。知识库以中文为主,词面匹配"
                                                    "——query 必须用中文关键词(型号名/专有名词除外),"
                                                    "英文长句几乎不会命中;多个关键词用空格分隔,"
                                                    "词要短(2-6 字),长复合词命中率低"}},
            "required": ["query"],
        },
    },
    {
        "name": "read_doc",
        "description": "整读型号知识文档。只给 file 返回该文档的节目录(TOC);"
                       "给 section 返回该节完整内容,表格不切碎。",
        "input_schema": {
            "type": "object",
            "properties": {
                "model_id": {"type": "string", "description": "标准型号 ID"},
                "file": {"type": "string", "description": "文档名,如 hardware.md"},
                "section": {"type": "string", "description": "H2 节标题;省略则返回节目录"},
            },
            "required": ["model_id", "file"],
        },
    },
    {
        "name": "list_models",
        "description": "事实矩阵横向汇总:一次调用返回全部型号在指定字段上的值(最多2个字段)。"
                       "枚举/筛选型问题(哪些型号支持X/有没有非Y接口的相机)用它,不要逐型号查。"
                       "值为 null 表示该型号暂无该字段数据(不等于不支持)。"
                       "返回 total、by_family 产品条目计数和受治理的 product_kind 分类。"
                       "回答相机数量时用 camera_count，不能把 total 当相机数；"
                       "product_kind_coverage 不完整时明确说明类别缺口。",
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array", "items": {"type": "string"},
                    "description": "字段 ID 列表,如 [\"data_interface\"],最多 2 个",
                },
            },
            "required": ["fields"],
        },
    },
    {
        "name": "filter_models",
        "description": "用事实矩阵对候选型号逐项核对硬约束,返回型号×约束判定表。"
                       "同时给出范围关系和 fit_summary,但不排序、不推荐;"
                       "顶层约束按 AND 聚合;真实备选条件用一层 any_of 表达;"
                       "所有可确定性核对的硬约束必须放在同一次调用中,不得只核"
                       "部分条件后把其余条件留给正文推断;"
                       "不能由单一事实/profile 证明的场景验收分支用"
                       "requires_assessment 复用机器上下文给出的分支 id,保留"
                       "验收语义并返回 no_data,不得发明"
                       "数值代理阈值;"
                       "任一硬约束 unsatisfied 的型号不可进入首推候选集,软偏好"
                       "只能在全部硬约束 satisfied 的候选之间排序;"
                       "no_data 不等于不满足。用户给出候选时只核对这些型号;"
                       "开放式 Orbbec 选型未指定候选时把 models 设为 null 扫全目录。"
                       "工作范围显式用 operating_boundary + depth_range_max/covers;"
                       "理想范围用 preferred_range + depth_range_ideal/covers;"
                       "精确流格式用 supports_profile;最低像素/帧率条件用"
                       "meets_profile_minimum。",
        "input_schema": {
            "type": "object",
            "properties": {
                "constraints": {
                    "type": "array",
                    "minItems": 1,
                    "description": (
                        "全部可确定性硬约束。any_of 示例:用户接受 USB 或网口时传 "
                        "{id:'interface',any_of:[{field:'data_interface',"
                        "op:'contains',value:'USB 3.0'},{field:'data_interface',"
                        "op:'contains',value:'Ethernet'}]}。替代条件不得拆成顶层 AND。"
                        "不能由单一事实字段证明的分支传 {id:'机器上下文分支ID',"
                        "op:'requires_assessment',requirement:'验收条件'}。"
                    ),
                    "items": {
                        "oneOf": [
                            copy.deepcopy(_MODEL_CONSTRAINT_LEAF_SCHEMA),
                            copy.deepcopy(_MODEL_CONSTRAINT_GROUP_SCHEMA),
                        ],
                    },
                },
                "models": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "候选标准型号 ID;用户未指定候选且要求开放式 Orbbec 选型时传 null"
                    ),
                },
            },
            "required": ["constraints", "models"],
        },
    },
    {
        "name": "sampling_geometry",
        "description": (
            "按受治理的同一 stream profile 确定性计算物面覆盖宽高、mm/pixel 和"
            "可选目标横跨像素。结果是横向采样证据，不是深度/空间/测量精度；"
            "工具不排名、不推荐，no_data 不等于不支持。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "models": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                    "description": "待计算的标准型号 ID",
                },
                "distance_m": {"type": "number", "exclusiveMinimum": 0},
                "target_width_mm": {
                    "type": ["number", "null"], "exclusiveMinimum": 0,
                },
                "stream": {"type": "string", "enum": ["depth"]},
                "profile_id": {"type": ["string", "null"]},
            },
            "required": ["models", "distance_m", "stream"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sdk_evidence",
        "description": "检索 SDK 源码和文档验证过的证据(API 用法、示例、使用边界和排障步骤),"
                       "返回带 file:line 的片段。涉及 SDK 编程、使用或排障问题时使用;"
                       "答案中的代码块必须来自这里。"
                       "它不能判断命名仓库或官方资产是否存在、由谁发布或承担什么定位。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "API 名或功能关键词"}},
            "required": ["query"],
        },
    },
    {
        "name": "official_links",
        "description": (
            "查询已逐页核验的 Orbbec 官方仓库、固件、下载、文档、产品页，"
            "以及受发布方边界治理的集成文档。"
            "用户点名命名仓库或官方资产并询问其存在性、入口、发布方、定位或角色时必须调用。"
            "也用于用户询问链接/官网/仓库/clone/下载/固件/驱动/Viewer/手册/API入口。"
            "普通排障、可行性和经验问题不要为了装饰而调用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用户需要的链接或入口"},
                "model_id": {"type": "string", "description": "可选的标准型号 ID"},
                "link_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "sdk_repo", "firmware_release", "download",
                            "official_docs", "product_page", "integration_repo",
                        ],
                    },
                    "description": "可选链接类型过滤",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "session_state",
        "description": "读取当前会话状态(场景/约束/候选/排除/结论)。"
                       "处理依赖上文的追问时先调用,替代全量历史注入。"
                       "内部机制词(槽位/candidates 等)不要写进答案正文,"
                       "上下文缺失时用客户语言表述(如\"您之前没有提到具体型号\")。",
        "input_schema": {"type": "object", "properties": {}},
    },
]

# QA 经验库工具:仅当 ToolBox 带 qa_collection 时暴露(M5 前置能力面补齐)。
_QA_TOOL_SPEC: dict = {
    "name": "search_qa",
    "description": "检索历史 FAE 问答经验库(排查步骤/使用经验/案例)。"
                   "经验层证据:不承担硬事实职责——规格/接口/数值仍以 fact_lookup 为准。",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "问题描述"}},
        "required": ["query"],
    },
}


def _active_specs(include_qa: bool = False) -> list[dict]:
    return TOOL_SPECS + ([_QA_TOOL_SPEC] if include_qa else [])


def _spec_description(spec: dict, field_ids: list[str] | None) -> str:
    desc = spec["description"]
    if field_ids and spec["name"] in (
        "fact_lookup", "series_fact_lookup", "list_models",
    ):
        desc += " 可用字段: " + ", ".join(field_ids)
    return desc


def openai_tool_schemas(field_ids: list[str] | None = None,
                        include_qa: bool = False) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": _spec_description(s, field_ids),
                "parameters": s["input_schema"],
            },
        }
        for s in _active_specs(include_qa)
    ]


def anthropic_tool_schemas(field_ids: list[str] | None = None,
                           include_qa: bool = False) -> list[dict]:
    return [
        {"name": s["name"], "description": _spec_description(s, field_ids),
         "input_schema": s["input_schema"]}
        for s in _active_specs(include_qa)
    ]


class ToolBox:
    """六工具的唯一实现,pipeline 与循环运行时共同的消费接口。"""

    def __init__(self, knowledge_dir: Path, sdk_knowledge_dir: Path | None = None,
                 session_state=None, qa_collection=None,
                 openai_api_key: str = "", embed_model: str = "",
                 official_link_catalog: OfficialLinkCatalog | None = None):
        self.knowledge_dir = Path(knowledge_dir)
        self.sdk_knowledge_dir = Path(sdk_knowledge_dir) if sdk_knowledge_dir else None
        self._session_state = session_state
        # QA 经验库(可选):collection 缺席时 search_qa 不进 schema
        self._qa_collection = qa_collection
        self._openai_api_key = openai_api_key
        self._embed_model = embed_model
        self._official_link_catalog = official_link_catalog
        self._attachment_tools = None
        facts_dir = self.knowledge_dir / "_facts"
        self.fields: dict[str, FieldDef] = load_field_dictionary(
            facts_dir / "field_dictionary.yaml")
        self.store: FactStore = load_fact_store(self.knowledge_dir, self.fields)
        kind_manifest_path = facts_dir / "model_source_coverage.yaml"
        self.model_source_coverage = (
            load_model_source_coverage(kind_manifest_path, self.store.known_models)
            if kind_manifest_path.is_file() else None
        )
        self.field_coverage: FieldCoverageManifest = load_field_coverage(
            facts_dir / "field_coverage.yaml",
            self.fields,
            self.store,
            documented_field_models=scan_documented_field_models(
                self.knowledge_dir, self.fields
            ),
        )
        self.resolver: ModelResolver = load_catalog(
            facts_dir / "catalog.yaml", self.knowledge_dir)
        sampling_path = self.knowledge_dir / "_selection" / "sampling_profiles.yaml"
        self.sampling_profiles = (
            load_sampling_profiles(sampling_path, self.store.known_models)
            if sampling_path.is_file()
            else SamplingProfileRegistry((), frozenset(self.store.known_models))
        )
        self.device_support: DeviceSupportMatrix | None = None
        if self.sdk_knowledge_dir is not None:
            support_path = self.sdk_knowledge_dir / "device_support_matrix.yaml"
            if support_path.is_file():
                self.device_support = load_device_support_matrix(
                    support_path,
                    known_model_ids=self.resolver.entries.keys(),
                )

    def with_session(self, session_state, loop_state=None) -> "ToolBox":
        """浅拷贝注入会话状态:store/resolver/fields 共享,session 独立。"""
        boxed = copy.copy(self)
        boxed._session_state = session_state
        boxed._loop_state = loop_state
        return boxed

    def with_request_context(self, question: str) -> "ToolBox":
        """Preserve user constraints that a model-shortened tool query may omit."""
        boxed = copy.copy(self)
        boxed._request_context = str(question or "")
        return boxed

    def with_attachments(
        self, visible_attachments, attachment_store, vision=None,
    ) -> "ToolBox":
        from src.agent.attachment_tools import AttachmentTools

        boxed = copy.copy(self)
        boxed._attachment_tools = AttachmentTools(
            visible_attachments, attachment_store, vision=vision,
        )
        return boxed

    def tool_schemas(self) -> list[dict]:
        schemas = openai_tool_schemas(
            field_ids=sorted(self.fields),
            include_qa=self.has_qa,
        )
        if self._attachment_tools is None:
            return schemas
        schemas.extend({
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["input_schema"],
            },
        } for spec in self._attachment_tools.schemas())
        return schemas

    @property
    def attachment_source_ids(self) -> list[str]:
        if self._attachment_tools is None:
            return []
        return self._attachment_tools.source_ids

    @property
    def attachment_vision_model(self) -> str | None:
        if self._attachment_tools is None:
            return None
        return self._attachment_tools.vision_model

    def redact_tool_input(self, name: str, args: dict) -> dict:
        if self._attachment_tools is not None and name in {
            spec["name"] for spec in self._attachment_tools.schemas()
        }:
            return self._attachment_tools.redact_input(args)
        return dict(args or {})

    def _iter_search_chunks(self):
        """检索池 = 型号目录 chunk + _topics 横切专题 chunk(model 固定 '_topics')。"""
        yield from iter_product_chunks(self.knowledge_dir)
        topics_dir = self.knowledge_dir / "_topics"
        if not topics_dir.is_dir():
            return
        for md in sorted(topics_dir.glob("*.md")):
            for title, content in _split_by_h2(md.read_text(encoding="utf-8")):
                yield ProductChunk(model="_topics", md_file=md.name,
                                   section_title=title, content=content)

    # -- 工具实现 -----------------------------------------------------------

    def resolve_model(self, text: str) -> ToolResult:
        r = self.resolver.resolve(text)
        if r.status == "resolved":
            if r.entity_kind == "series":
                return ToolResult("ok", {
                    "entity_kind": "series",
                    "series_id": r.entity_id,
                    "model_ids": list(r.model_ids),
                    "note": (
                        "这是受治理的系列实体；系列级规格必须用 "
                        "series_fact_lookup 取证，不得从单一成员外推。"
                    ),
                }, [{
                    "path": f"Knowledge/_facts/catalog.yaml#{r.entity_id}",
                    "section": "models.series",
                    "confidence_layer": "hard_fact",
                }])
            model_id = r.model_ids[0]
            content = {"entity_kind": "model", "model_id": model_id}
            sources = []
            lifecycle = self.store.get_spec(model_id, "lifecycle_status")
            if lifecycle.status == "found":
                content["lifecycle_status"] = _row_payload(lifecycle.row)
                sources.append(_fact_source(model_id, lifecycle.row))
            return ToolResult("ok", content, sources)
        if r.status == "ambiguous":
            return ToolResult("ok", {
                "model_id": None,
                "candidates": list(r.model_ids),
                "note": "命中多个型号,需向用户澄清或按上下文选定",
            })
        return ToolResult("not_found", {
            "text": text,
            "note": ("产品目录中无此型号,禁止发明。"
                     "若这不是相机型号而是 SDK/wrapper/算法名(如 K4A、OpenNI、ROS),"
                     "改用 sdk_evidence 查代码证据或 search_knowledge 查文档。"),
        })

    def _canonical_model(self, model_id: str) -> str:
        """宽容输入:显示名/别名("Gemini 305")在工具内归一化,不浪费模型调用。"""
        if model_id in self.store.known_models:
            return model_id
        r = self.resolver.resolve(model_id)
        if r.status == "resolved" and r.entity_kind == "model":
            return r.model_ids[0]
        return model_id

    def fact_lookup(self, model_id: str, field: str | None = None) -> ToolResult:
        model_id = self._canonical_model(model_id)
        if field is not None:
            result = self.store.get_spec(model_id, field)
            if result.status == "found":
                row = result.row
                content = {"model": model_id, "field": field, **_row_payload(row)}
                return ToolResult("ok", content, [_fact_source(model_id, row)])
            if result.status == "not_found":
                content = {
                    "model": model_id, "field": field,
                    "evidence_state": "no_data",
                    "negative_claim_allowed": False,
                    "note": "该字段暂无核验数据;不等于不支持,可用 read_doc 查原文",
                }
                sources = []
                if field in _ACCURACY_FIELDS:
                    related = {}
                    for related_field in _ACCURACY_FIELDS:
                        if related_field == field:
                            continue
                        related_result = self.store.get_spec(model_id, related_field)
                        if related_result.status != "found":
                            continue
                        related[related_field] = _row_payload(related_result.row)
                        sources.append(_fact_source(model_id, related_result.row))
                    if related:
                        content["related_available_fields"] = related
                        content["note"] += ";已附同属精度概念但口径不同的可用字段,不得混为一谈"
                return ToolResult("not_found", content, sources)
            if result.status == "field_not_exists":
                return ToolResult("field_not_exists", {
                    "model": model_id, "field": field,
                    "available_fields": sorted(self.fields),
                    "note": "无此字段口径,请从 available_fields 中选择",
                })
            return ToolResult(result.status, {"model": model_id, "field": field})
        if model_id not in self.store.known_models:
            return ToolResult("unknown_model", {"model": model_id})
        rows = self.store.rows(model_id)
        if not rows:
            return ToolResult("not_found", {
                "model": model_id, "note": "该型号暂无事实矩阵数据"})
        return ToolResult(
            "ok",
            {"model": model_id, "rows": {fid: _row_payload(r) for fid, r in rows.items()}},
            [_fact_source(model_id, r) for r in rows.values()],
        )

    def series_fact_lookup(self, series: str, field: str) -> ToolResult:
        """Return a governed series-scoped fact without extrapolating one model."""
        resolved = self.resolver.resolve(series)
        if resolved.status != "resolved" or resolved.entity_kind != "series":
            return ToolResult("not_found", {
                "series": series,
                "series_id": "",
                "field": field,
                "governed_members": [],
                "source_applies_to": [],
                "coverage_gap": [],
                "coverage": "empty",
                "note": "产品目录中没有受治理的该系列实体；不得用型号前缀猜成员。",
            })
        model_ids = tuple(sorted(resolved.model_ids))
        governed_members = frozenset(model_ids)
        base_content = {
            "entity_kind": "series",
            "series_id": resolved.entity_id,
            "field": field,
            "model_ids": list(model_ids),
            "governed_members": list(model_ids),
            "source_applies_to": [],
            "coverage_gap": list(model_ids),
            "coverage": "empty",
        }
        if field not in self.fields:
            return ToolResult("field_not_exists", {
                **base_content,
                "available_fields": sorted(self.fields),
            })
        store_members = self.store.series_memberships.get(resolved.entity_id)
        if store_members != governed_members:
            return ToolResult("conflict", {
                **base_content,
                "coverage": "conflict",
                "invalid_scope": {
                    "catalog_membership": {
                        "resolver": list(model_ids),
                        "fact_store": sorted(store_members or ()),
                    },
                },
                "variants": [],
                "note": "系列治理成员不一致；必须修复 catalog，不能从 qualifier 反推。",
            })
        rows: dict[str, FactRow] = {}
        missing_models: list[str] = []
        invalid_scope: dict[str, dict] = {}
        scopes: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
        for model_id in model_ids:
            result = self.store.get_spec(model_id, field)
            if result.status != "found" or result.row is None:
                missing_models.append(model_id)
                continue
            row = result.row
            qualifier = row.qualifier
            rows[model_id] = row
            if (
                qualifier.get("source_scope") != "series"
                or str(qualifier.get("series_id") or "") != resolved.entity_id
            ):
                invalid_scope[model_id] = {
                    "qualifier": dict(qualifier),
                    "error": "source_scope or series_id mismatch",
                }
                continue
            try:
                scopes[model_id] = self.store.validate_series_qualifier(
                    model_id,
                    qualifier,
                    context=f"{model_id}/{field}",
                )
            except ValueError as exc:
                invalid_scope[model_id] = {
                    "qualifier": dict(qualifier),
                    "error": str(exc),
                }

        def conservative_scope() -> tuple[list[str], list[str]]:
            valid_applies = [applies for applies, _gap in scopes.values()]
            applies = (
                set.intersection(*(set(items) for items in valid_applies))
                if valid_applies
                else set()
            )
            return sorted(applies), sorted(governed_members - applies)

        def variants() -> list[dict]:
            fingerprints: dict[str, list[str]] = {}
            for model_id, row in rows.items():
                applies, gap = scopes.get(
                    model_id, (frozenset(), governed_members)
                )
                fingerprint = json.dumps({
                    "fact": _series_fact_payload(row, applies, gap),
                    "source_applies_to": sorted(applies),
                    "coverage_gap": sorted(gap),
                }, ensure_ascii=False, sort_keys=True)
                fingerprints.setdefault(fingerprint, []).append(model_id)
            return [
                {"models": model_group, **json.loads(fingerprint)}
                for fingerprint, model_group in fingerprints.items()
            ]

        sources = [
            _fact_source(model_id, row) for model_id, row in rows.items()
        ]
        if invalid_scope:
            applies, gap = conservative_scope()
            return ToolResult("conflict", {
                **base_content,
                "source_applies_to": applies,
                "coverage_gap": gap,
                "coverage": "conflict",
                "missing_models": missing_models,
                "invalid_scope": invalid_scope,
                "variants": variants(),
                "note": (
                    "系列事实适用范围无效；必须先暴露范围冲突，不得被成员缺失隐藏。"
                    "缺失和 coverage_gap 都不是不支持的负向证据。"
                ),
            }, sources)
        if not rows:
            return ToolResult("not_found", {
                **base_content,
                "missing_models": missing_models,
                "note": "全部系列成员缺少该字段；证据缺失不等于不支持。",
            })

        scope_groups: dict[
            tuple[tuple[str, ...], tuple[str, ...]], list[str]
        ] = {}
        for model_id, (applies, gap) in scopes.items():
            scope_groups.setdefault(
                (tuple(sorted(applies)), tuple(sorted(gap))), []
            ).append(model_id)
        if len(scope_groups) != 1:
            applies, gap = conservative_scope()
            return ToolResult("conflict", {
                **base_content,
                "source_applies_to": applies,
                "coverage_gap": gap,
                "coverage": "conflict",
                "missing_models": missing_models,
                "invalid_scope": {},
                "variants": variants(),
                "note": (
                    "系列事实来源适用范围不一致；必须并列说明，不得静默选边。"
                    "coverage_gap 是证据缺口，不是负向证据。"
                ),
            }, sources)

        source_applies_to, coverage_gap = next(iter(scope_groups))
        fingerprints: dict[str, list[str]] = {}
        for model_id, row in rows.items():
            applies, gap = scopes[model_id]
            fingerprint = _series_fact_semantic_fingerprint(
                row, applies, gap
            )
            fingerprints.setdefault(fingerprint, []).append(model_id)
        if len(fingerprints) != 1:
            return ToolResult("conflict", {
                **base_content,
                "source_applies_to": list(source_applies_to),
                "coverage_gap": list(coverage_gap),
                "coverage": "conflict",
                "missing_models": missing_models,
                "invalid_scope": {},
                "variants": variants(),
                "note": (
                    "系列事实值或范围不一致；必须并列说明，不得静默选边。"
                    "不同成员来源措辞缺少某个限定词，不等于该成员不支持；"
                    "不得把措辞差异外推为仅某一成员具备能力。"
                ),
            }, sources)
        first = next(iter(rows.values()))
        if missing_models:
            return ToolResult("not_found", {
                **base_content,
                "source_applies_to": list(source_applies_to),
                "coverage_gap": list(coverage_gap),
                "coverage": "partial",
                "missing_models": missing_models,
                **_row_payload(first),
                "note": (
                    "coverage_gap 是来源适用范围的证据缺口，不等于不支持；"
                    "已返回有事实成员的来源，禁止把缺口外推为负向结论。"
                ),
            }, sources)
        return ToolResult("ok", {
            **base_content,
            "source_applies_to": list(source_applies_to),
            "coverage_gap": list(coverage_gap),
            "coverage": "full",
            "source_scope": "series",
            **_row_payload(first),
        }, sources)

    _SEARCH_HIT_CAP = 20
    _SECTION_CHAR_CAP = 4000
    _LIST_VALUE_CHAR_CAP = 160   # 60 会把 "GMSL2/FAKRA" 类接口值截残,证据缺 token
    _LIST_FIELDS_MAX = 2

    def list_models(self, fields: list[str]) -> ToolResult:
        """矩阵横向汇总:枚举/筛选型问题的一次性全目录扫描。"""
        fields = [str(f) for f in (fields or [])][: self._LIST_FIELDS_MAX]
        if not fields:
            return ToolResult("tool_error", {"error": "fields 不能为空"})
        unknown = [f for f in fields if f not in self.fields]
        if unknown:
            return ToolResult("field_not_exists", {
                "fields": unknown,
                "available_fields": sorted(self.fields),
                "note": "无此字段口径,请从 available_fields 中选择",
            })
        models: dict[str, dict] = {}
        evidence_states: dict[str, dict[str, str]] = {}
        comparison_cells: dict[str, dict[str, dict[str, object]]] = {}
        for model_id in sorted(self.store.known_models):
            rows = self.store.rows(model_id)
            out: dict[str, object] = {}
            states: dict[str, str] = {}
            cells: dict[str, dict[str, object]] = {}
            for f in fields:
                if f not in rows:
                    out[f] = None
                    states[f] = "no_data"
                    cells[f] = {
                        "evidence_state": "no_data",
                        "negative_or_difference_claim_allowed": False,
                        "allowed_phrasing": ["未发布", "未取到", "无法确认"],
                    }
                    continue
                row = rows[f]
                # value 优先(结论字段),raw_value 仅兜底——xlsx-430 类教训
                val = _serialize_value(row.value)
                if val is None or val == "":
                    val = row.raw_value
                if isinstance(val, str):
                    val = val[: self._LIST_VALUE_CHAR_CAP]
                out[f] = val
                if row.status == "conflict":
                    states[f] = "conflict"
                    cells[f] = {
                        "evidence_state": "conflict",
                        "negative_or_difference_claim_allowed": False,
                        "requires_conflict_disclosure": True,
                    }
                else:
                    states[f] = "found"
                    cells[f] = {
                        "evidence_state": "found",
                        "negative_or_difference_claim_allowed": True,
                    }
            models[model_id] = out
            evidence_states[model_id] = states
            comparison_cells[model_id] = cells
        # 计数由代码算好。total/by_family 负责产品条目，camera_count 负责相机；
        # 禁止模型从长矩阵自行数型号或把全部产品数当成相机数。
        by_family: dict[str, int] = {}
        for model_id in models:
            fam = model_id.split("_")[0]
            by_family[fam] = by_family.get(fam, 0) + 1
        product_kind_by_model: dict[str, str] = {}
        by_product_kind: dict[str, int] = {}
        kind_coverage = "unavailable"
        camera_count: int | None = None
        if self.model_source_coverage is not None:
            product_kind_by_model = {
                model_id: self.model_source_coverage.models[model_id].product_kind
                for model_id in models
            }
            for kind in product_kind_by_model.values():
                by_product_kind[kind] = by_product_kind.get(kind, 0) + 1
            unknown_kinds = set(by_product_kind) - (
                _CAMERA_PRODUCT_KINDS | _NON_CAMERA_PRODUCT_KINDS
            )
            kind_coverage = "unclassified" if unknown_kinds else "complete"
            if not unknown_kinds:
                camera_count = sum(
                    by_product_kind.get(kind, 0) for kind in _CAMERA_PRODUCT_KINDS
                )
        non_camera_models = {
            kind: sorted(
                model_id for model_id, model_kind in product_kind_by_model.items()
                if model_kind == kind
            )
            for kind in sorted(_NON_CAMERA_PRODUCT_KINDS)
            if by_product_kind.get(kind)
        }
        # 工具消息有长度上限，计数和分类依据必须置于完整矩阵前面。
        content = {
            "total": len(models),
            "by_family": dict(sorted(by_family.items())),
            "product_kind_coverage": kind_coverage,
            "by_product_kind": dict(sorted(by_product_kind.items())),
            "camera_count": camera_count,
            "non_camera_models": non_camera_models,
            "product_kind_by_model": product_kind_by_model,
            "note": "null=该型号暂无该字段数据,不等于不支持;"
                    "total/by_family 是本地产品条目的权威计数,不能作为相机数量。"
                    "product_kind_coverage=complete 时 camera_count 是受治理的相机计数,"
                    "by_product_kind 保留非相机类别;即使后续矩阵截断也直接采用这些计数。"
                    "类别覆盖不完整时说明缺口,不得把全部产品条目称为相机。",
            "fields": fields,
            "models": models,
            "evidence_states": evidence_states,
            "field_coverage": {
                field: self.field_coverage.summary(field, self.store)
                for field in fields
            },
            "comparison_boundary": {
                "scope": "per_model_field",
                "cells": comparison_cells,
            },
        }
        if "depth_range_ideal" in fields:
            content["field_boundaries"] = {
                "depth_range_ideal": (
                    "理想/推荐范围不是操作范围边界;不得据此断言范围外不支持。"
                    "覆盖判断必须另查 depth_range_max"
                )
            }
        sources = [
            {"path": "Knowledge/*/facts.yaml", "section": f,
             "confidence_layer": "hard_fact"} for f in fields
        ]
        if self.model_source_coverage is not None:
            sources.append({
                "path": "Knowledge/_facts/model_source_coverage.yaml",
                "section": "models.product_kind",
                "confidence_layer": "hard_fact",
            })
        return ToolResult("ok", content, sources)

    def filter_models(self, constraints: list[dict],
                      models: list[str] | None) -> ToolResult:
        """Deterministically compare candidate models without ranking them."""
        if not constraints:
            return ToolResult("tool_error", {"error": "constraints 不能为空"})
        canonical_models = None
        if models is not None:
            canonical_models = [self._canonical_model(str(model)) for model in models]
            unknown = sorted({
                model for model in canonical_models
                if model not in self.store.known_models
            })
            if unknown:
                return ToolResult("unknown_model", {
                    "models": unknown,
                    "note": "候选中含未知型号,请先用 resolve_model 归一化",
                })
        result = evaluate_model_constraints(
            self.store, constraints, models=canonical_models)
        if result.get("status") != "ok":
            return ToolResult("tool_error", {
                "error": str(result.get("status") or "invalid_constraints"),
                "validation_errors": list(result.get("validation_errors") or []),
            })
        sources = []
        seen = set()
        fields = []
        for constraint in result["constraints"]:
            children = constraint.get("any_of")
            leaves = children if isinstance(children, list) else [constraint]
            for leaf in leaves:
                field = str(leaf.get("field") or "")
                if field and field not in fields:
                    fields.append(field)
        for model_id in result["table"]:
            rows = self.store.rows(model_id)
            for field in fields:
                row = rows.get(field)
                if row is None:
                    continue
                key = (model_id, field)
                if key not in seen:
                    seen.add(key)
                    sources.append(_fact_source(model_id, row))
        return ToolResult("ok", result, sources)

    def sampling_geometry(
        self,
        models: list[str],
        distance_m: float,
        stream: str,
        target_width_mm: float | None = None,
        profile_id: str | None = None,
    ) -> ToolResult:
        """Return deterministic angular sampling without product ranking."""
        canonical_models = [self._canonical_model(str(model)) for model in models or []]
        if not canonical_models or len(canonical_models) != len(set(canonical_models)):
            return ToolResult("tool_error", {
                "error": "models must contain unique canonical model IDs",
            })
        unknown = sorted(
            model for model in canonical_models
            if model not in self.store.known_models
        )
        if unknown:
            return ToolResult("unknown_model", {
                "models": unknown,
                "note": "候选中含未知型号，请先用型号归一化工具",
            })
        try:
            content = evaluate_sampling_geometry(
                self.sampling_profiles,
                models=canonical_models,
                distance_m=distance_m,
                target_width_mm=target_width_mm,
                stream=str(stream),
                profile_id=str(profile_id) if profile_id else None,
            )
        except ValueError as exc:
            return ToolResult("tool_error", {"error": str(exc)})
        sources = []
        seen = set()
        for cell in content["cells"].values():
            for source in cell.get("sources", []):
                key = (source["path"], source["section"])
                if key in seen:
                    continue
                seen.add(key)
                sources.append({
                    **source,
                    "confidence_layer": "hard_fact",
                })
        return ToolResult("ok", content, sources)

    def search_knowledge(self, query: str) -> ToolResult:
        # 横切知识层(20260708,问题三):知识库第二维度。型号目录之外的
        # 竞品对照/跨型号专题放 Knowledge/_topics/,与型号 chunk 同池检索;
        # 不进 list_models/resolve_model(不是型号)。        # 分词 AND 优先;无命中降级为部分匹配(按命中词数排序,标 partial)——
        # 长查询(场景+约束多词)全词命中率低,盲目 not_found 只会让模型反复改写重试。
        # CJK 降级(F4 20260709):模型常发无空格复合词("物流体积测量"),
        # 与条目词面("物流固定体积测量")差一两个字就整词不中——两轮 434
        # 实跑证实这是先验/边界检索不回的类级原因。≥4 字中文词直查不中时
        # 按字符 bigram 覆盖率软匹配(≥60% 且 ≥3 个 bigram);部分匹配排序
        # 加权命中字符数,防止"主机/相机"类高频短词把长信息词命中的节挤出截断。
        terms = [t for t in re.split(r"\s+", query.strip().casefold()) if t]
        informative = [t for t in terms if t not in _SEARCH_STOPWORDS]
        terms = informative or terms
        if not terms:
            return ToolResult("not_found", {"query": query, "note": "空查询"})
        scored: list[tuple[tuple[int, int, int, int], dict]] = []
        for chunk in self._iter_search_chunks():
            model_hay = chunk.model.replace("_", " ").casefold()
            hay = f"{chunk.section_title}\n{chunk.content}".casefold()
            matched, approx = [], []
            model_matches = 0
            for t in terms:
                if t in model_hay:
                    matched.append(t)
                    model_matches += 1
                elif t in hay:
                    matched.append(t)
                elif len(t) >= 4 and _CJK_CHAR_RE.search(t):
                    grams = [t[i:i + 2] for i in range(len(t) - 1)]
                    hit = sum(1 for g in grams if g in hay)
                    if hit >= 3 and hit / len(grams) >= 0.6:
                        approx.append(t)
            if not matched and not approx:
                continue
            n = len(matched) + len(approx)
            chars = sum(len(t) for t in matched) + sum(len(t) for t in approx)
            entry = {
                "model": chunk.model,
                "file": chunk.md_file,
                "section": chunk.section_title,
                "matched_terms": matched + approx,
            }
            if approx:
                entry["approx_terms"] = approx
            scored.append(((n, model_matches, len(matched), chars), entry))
        if not scored:
            return ToolResult("not_found", {
                "query": query, "note": "知识文档无命中;不等于不支持"})
        # 排序:命中词数 > 型号元数据命中 > 硬命中词数 > 命中字符数。
        # 型号词不能只在正文里匹配:跨型号边界段落经常提到其它型号，若忽略
        # chunk.model，反而会把被提及型号的证据排在其自身文档之前。
        ranked = sorted(
            scored,
            key=lambda x: (-x[0][0], -x[0][1], -x[0][2], -x[0][3]),
        )
        full = [h for (n, _model, _hard, _c), h in ranked if n == len(terms)]
        partial = not full
        supplemental_partial = False
        if full:
            hits = full[: self._SEARCH_HIT_CAP]
            truncated = len(full) > self._SEARCH_HIT_CAP
            # A single lexical full match can be a broad overview while a
            # same-model evidence section misses only one wording variant
            # (for example "connector" versus "interface").  Dropping every
            # partial hit in that case makes retrieval unstable under harmless
            # query rewrites.  Preserve only near-full hits anchored by the
            # strongest model-name match; do not reopen the general partial
            # pool or admit cross-model mentions.
            max_model_matches = max(score[1] for score, _hit in ranked)
            minimum_matches = max(2, len(terms) - 1)
            if len(terms) >= 3 and max_model_matches > 0:
                anchor_models = {
                    hit["model"]
                    for score, hit in ranked
                    if score[0] == len(terms)
                    and score[1] == max_model_matches
                }
                supplements = [
                    hit
                    for (score, hit) in ranked
                    if score[0] < len(terms)
                    and score[0] >= minimum_matches
                    and score[1] == max_model_matches
                    and hit["model"] in anchor_models
                ]
                remaining = self._SEARCH_HIT_CAP - len(hits)
                if remaining > 0 and supplements:
                    hits.extend(supplements[:remaining])
                    supplemental_partial = True
                    truncated = truncated or len(supplements) > remaining
        else:
            hits = [h for _s, h in ranked[: self._SEARCH_HIT_CAP]]
            truncated = len(ranked) > self._SEARCH_HIT_CAP
        content: dict = {"query": query, "hits": hits, "truncated": truncated}
        if partial:
            content["partial"] = True
            content["note"] = "无全词命中,已按命中词数排序返回部分匹配"
        elif supplemental_partial:
            content["supplemental_partial"] = True
            content["note"] = (
                "包含同型号近全词证据，防止宽泛全词命中遮蔽更具体章节"
            )
        return ToolResult(
            "ok",
            content,
            [{
                "path": f"Knowledge/{h['model']}/{h['file']}",
                "section": h["section"],
                "confidence_layer": "doc",
            } for h in hits],
        )

    def read_doc(self, model_id: str, file: str, section: str | None = None) -> ToolResult:
        model_id = self._canonical_model(model_id)
        path = self.knowledge_dir / model_id / file
        if not path.is_file():
            model_dir = self.knowledge_dir / model_id
            available = sorted(
                p.name for p in model_dir.iterdir()
                if p.is_file() and p.suffix == ".md"
            ) if model_dir.is_dir() else []
            return ToolResult("no_file", {
                "model": model_id, "file": file,
                "available_files": available,
                "note": "无此文档,请从 available_files 中选择",
            })
        sections = _split_by_h2(path.read_text(encoding="utf-8"))
        if section is None:
            return ToolResult(
                "ok",
                {"model": model_id, "file": file,
                 "sections": [title for title, _ in sections]},
                [{"path": f"Knowledge/{model_id}/{file}", "section": "",
                  "confidence_layer": "doc"}],
            )
        wanted = section.strip()
        for title, content in sections:
            if title == wanted:
                truncated = len(content) > self._SECTION_CHAR_CAP
                return ToolResult(
                    "ok",
                    {"model": model_id, "file": file, "section": title,
                     "content": content[:self._SECTION_CHAR_CAP],
                     "truncated": truncated},
                    [{"path": f"Knowledge/{model_id}/{file}", "section": title,
                      "confidence_layer": "doc"}],
                )
        return ToolResult("no_section", {
            "model": model_id, "file": file, "section": section,
            "available_sections": [title for title, _ in sections]})

    _SDK_HIT_CAP = 5

    @staticmethod
    def _official_link_payload(link: OfficialLink, *, sdk_join: bool = False) -> dict:
        payload = {
            "id": link.id,
            "link_type": link.link_type,
            "title": link.title,
            "url": link.url,
            "publisher": link.publisher,
            "models": list(link.models),
            "repo": link.repo,
            "sdk_layers": list(link.sdk_layers),
        }
        if link.applicability is not None:
            payload["applicability"] = {
                "scope": link.applicability.scope,
                "boundary": link.applicability.boundary,
            }
        if link.content_type:
            payload["content_type"] = link.content_type
        if link.asset_kind:
            payload["asset_kind"] = link.asset_kind
        if sdk_join:
            payload["repo_url"] = link.url
        return payload

    @staticmethod
    def _official_link_sources(links: list[OfficialLink]) -> list[dict]:
        return [
            {
                "path": f"Knowledge_Links#{link.id}",
                "section": link.title,
                "confidence_layer": "doc",
            }
            for link in links
        ]

    def official_links(
        self,
        query: str,
        model_id: str | None = None,
        link_types: list[str] | None = None,
    ) -> ToolResult:
        """Return only browser-reviewed authoritative URLs; never construct one."""
        if self._official_link_catalog is None:
            return ToolResult("not_found", {
                "query": query,
                "evidence_state": "unknown",
                "note": (
                    "权威链接知识层未配置;不得猜测或拼接 URL;"
                    "未命中不等于不存在或不支持"
                ),
            })
        if not str(query or "").strip():
            return ToolResult("not_found", {
                "query": query,
                "evidence_state": "unknown",
                "note": (
                    "缺少明确的链接需求;不得猜测或返回装饰性链接;"
                    "未命中不等于不存在或不支持"
                ),
            })
        links = self._official_link_catalog.search(
            query=query,
            model_id=model_id,
            link_types=tuple(link_types or ()),
            limit=5,
        )
        support_result, support_boundaries, support_sources = (
            self._model_link_support_context(model_id, query)
        )
        device_records = support_result.records
        allowed_repos, compatibility_scoped = self._confirmed_sdk_repos(
            support_result, query
        )
        compatibility_filtered = any(
            match.status == "surface_not_listed"
            for match in support_result.matches
        )
        if compatibility_scoped:
            compatible_links = [
                link for link in links
                if link.link_type != "sdk_repo"
                or link.repo.casefold() in allowed_repos
            ]
            compatibility_filtered = (
                compatibility_filtered or len(compatible_links) != len(links)
            )
            links = compatible_links
            explicit_types = {
                str(value).casefold() for value in (link_types or ())
            }
            sdk_link_requested = (
                "sdk_repo" in explicit_types
                or any(
                    marker in str(query or "").casefold()
                    for marker in ("sdk", "开发资料", "开发资源", "仓库", "repo")
                )
            )
            if sdk_link_requested:
                by_id = {link.id: link for link in links}
                for link in self._official_link_catalog.links:
                    if (
                        link.link_type == "sdk_repo"
                        and link.repo.casefold() in allowed_repos
                    ):
                        by_id[link.id] = link
                links = list(links)
                existing = {link.id for link in links}
                links.extend(
                    by_id[key]
                    for key in sorted(by_id)
                    if key not in existing
                )
        if not links:
            note = "没有匹配的已核验官方链接;不得猜测、拼接或转述模型生成的 URL"
            if compatibility_scoped:
                note = (
                    "该型号已有兼容矩阵；未确认支持的软件入口不得仅凭通用仓库存在性返回"
                )
            return ToolResult("not_found", {
                "query": query,
                "evidence_state": "unknown",
                "model_id": model_id,
                "link_types": list(link_types or []),
                "compatibility_filtered": compatibility_filtered,
                "device_support": [
                    record.to_payload(
                        freshness_days=self.device_support.freshness_days,
                    )
                    for record in device_records
                ],
                "device_support_matches": [
                    match.to_payload(
                        freshness_days=self.device_support.freshness_days,
                    )
                    for match in support_result.matches
                ],
                "model_support_boundaries": support_boundaries,
                "note": f"{note};未命中不等于不存在或不支持",
            }, support_sources)
        return ToolResult(
            "ok",
            {
                "query": query,
                "evidence_state": "verified",
                "links": [self._official_link_payload(link) for link in links],
                "device_support": [
                    record.to_payload(
                        freshness_days=self.device_support.freshness_days,
                    )
                    for record in device_records
                ],
                "device_support_matches": [
                    match.to_payload(
                        freshness_days=self.device_support.freshness_days,
                    )
                    for match in support_result.matches
                ],
                "compatibility_filtered": compatibility_filtered,
                "model_support_boundaries": support_boundaries,
                "note": (
                    "仅包含逐页核验通过的官方链接；models 非空的条目才按 model_id"
                    " 过滤，models=[] 表示通用或未按型号绑定的入口；"
                    "若 links[].applicability 存在，必须遵守其"
                    " applicability.boundary，不得把通用入口推导为型号专属支持或下载；"
                    "device_support 的分支、固件和启动文件必须一并用于回答;"
                    "model_support_boundaries 是该型号平台边界。回答和 git clone 命令"
                    "必须原样复制 url 字段,不得追加 .git、路径或查询参数"
                ),
            },
            [*self._official_link_sources(links), *support_sources],
        )

    def _model_link_support_context(
        self,
        model_text: str | None,
        query: str,
    ) -> tuple[DeviceSupportQueryResult, dict[str, dict], list[dict]]:
        """Join model compatibility facts to the link path the model selected.

        A repository URL proves that a repository exists, while a device-support
        record proves which branch and firmware apply to a model.  Keep both in
        one tool response so callers cannot accidentally turn a generic SDK URL
        into a model compatibility claim.
        """
        if not str(model_text or "").strip():
            return DeviceSupportQueryResult(), {}, []
        resolved = self.resolver.resolve(str(model_text))
        if resolved.status != "resolved":
            return DeviceSupportQueryResult(), {}, []
        model_id = resolved.model_ids[0]
        support_result = (
            self.device_support.match_query(
                "\n".join((str(model_text), query)),
                resolver=self.resolver,
            )
            if self.device_support is not None
            else DeviceSupportQueryResult()
        )
        records = support_result.records
        requested_repos = {
            repo.casefold()
            for repo in _candidate_repos(_parse_sdk_intent(query))
        }
        if requested_repos:
            records = tuple(
                row
                for row in records
                if row.implementation.casefold() in requested_repos
            )
        boundaries: dict[str, dict] = {}
        sources: list[dict] = []
        platform = self.store.get_spec(model_id, "platform_support")
        if platform.status == "found":
            boundaries["platform_support"] = _row_payload(platform.row)
            sources.append(_fact_source(model_id, platform.row))
        if self.device_support is not None:
            sources.extend({
                "path": record.source["url"],
                "section": f"{record.source['ref']}:{record.source['path']}",
                "confidence_layer": "code",
            } for record in records)
            sources.extend({
                "path": match.roster_evidence.source["url"],
                "section": (
                    f"{match.roster_evidence.source['ref']}:"
                    f"{match.roster_evidence.source['path']}"
                ),
                "confidence_layer": "code",
            } for match in support_result.matches
              if match.roster_evidence is not None)
        return support_result, boundaries, sources

    def _confirmed_sdk_repos(
        self,
        support_result: DeviceSupportQueryResult,
        query: str,
    ) -> tuple[set[str], bool]:
        """Return model-confirmed implementations for a model-scoped link request.

        The official-link catalog proves that a repository exists.  It does not
        prove that every device supports that repository.  Once a model has a
        device-support matrix entry, generic SDK links are therefore restricted
        to positively supported implementations and any explicit SDK layer in
        the request.
        """
        allowed = {
            row.implementation.casefold()
            for row in support_result.records
            if row.support_state in {"supported", "recommended_for_new_designs"}
        }
        allowed.update(
            match.roster_evidence.implementation.casefold()
            for match in support_result.matches
            if match.status == "roster_listed"
            and match.roster_evidence is not None
        )
        requested = {
            repo.casefold()
            for repo in _candidate_repos(_parse_sdk_intent(query))
        }
        if requested:
            allowed &= requested
        compatibility_scoped = any(
            match.status in {"matched", "roster_listed", "surface_not_listed"}
            for match in support_result.matches
        )
        return allowed, compatibility_scoped

    def _matching_sdk_links(
        self,
        effective_query: str,
        hits: list[dict],
        intent_layers: tuple[str, ...],
        support_result: DeviceSupportQueryResult | None = None,
    ) -> list[OfficialLink]:
        support_result = support_result or DeviceSupportQueryResult()
        if self._official_link_catalog is None:
            return []
        matches = self._official_link_catalog.search(
            effective_query,
            link_types=("sdk_repo",),
            limit=self._SDK_HIT_CAP,
        )
        layer_aliases = {
            "cpp": {"cpp", "core"},
            "k4a_compat": {"k4a", "k4a_compat"},
            "dotnet": {"dotnet", "csharp", "dotnet_framework"},
            "unity": {"unity"},
        }
        requested_layers = {
            alias
            for layer in intent_layers
            for alias in layer_aliases.get(layer, {layer})
        }
        if requested_layers:
            matches = [
                link for link in matches
                if requested_layers.intersection(
                    value.casefold() for value in link.sdk_layers
                )
            ]
        supported_repos = {
            row.implementation.casefold()
            for row in support_result.records
            if row.support_state in {"supported", "recommended_for_new_designs"}
        }
        supported_repos.update(
            match.roster_evidence.implementation.casefold()
            for match in support_result.matches
            if match.status == "roster_listed"
            and match.roster_evidence is not None
        )
        compatibility_scoped = any(
            match.status in {"matched", "roster_listed", "surface_not_listed"}
            for match in support_result.matches
        )
        requested_repos = {
            repo.casefold()
            for repo in _candidate_repos(_parse_sdk_intent(effective_query))
        }
        if requested_repos:
            supported_repos &= requested_repos
        if compatibility_scoped:
            matches = [
                link for link in matches
                if link.repo.casefold() in supported_repos
            ]
        by_id = {link.id: link for link in matches}
        if compatibility_scoped:
            for link in self._official_link_catalog.links:
                if (
                    link.link_type == "sdk_repo"
                    and link.repo.casefold() in supported_repos
                ):
                    by_id[link.id] = link
        for hit in hits:
            repo = str(hit.get("repo") or "")
            if not repo:
                continue
            for link in self._official_link_catalog.search(
                repo,
                link_types=("sdk_repo",),
                limit=self._SDK_HIT_CAP,
            ):
                if (
                    link.repo.casefold() == repo.casefold()
                    and (
                        not compatibility_scoped
                        or link.repo.casefold() in supported_repos
                    )
                ):
                    by_id[link.id] = link
        return [by_id[key] for key in sorted(by_id)]

    def sdk_evidence(self, query: str) -> ToolResult:
        # 原问题保留 wrapper/language 约束。模型常把“Python 和 C# 回放”缩成
        # “bag playback”，只看短 query 会再次跨 layer 外推。
        request_context = str(getattr(self, "_request_context", "") or "")
        effective_query = "\n".join(v for v in (request_context, query) if v)
        # 复用 capability_sdk 的意图解析与打分,禁止另起一套检索
        intent = _parse_sdk_intent(effective_query)
        support_result = (
            self.device_support.match_query(effective_query, resolver=self.resolver)
            if self.device_support is not None
            else DeviceSupportQueryResult()
        )
        device_records = support_result.records
        records: list[dict] = []
        knowledge_note = ""
        if self.sdk_knowledge_dir is None:
            knowledge_note = "sdk_knowledge_dir 未配置,无法提供 SDK 代码证据"
        else:
            jsonl_path = self.sdk_knowledge_dir / "SDK_Knowledge.jsonl"
            if not jsonl_path.exists():
                knowledge_note = "SDK 知识库文件缺失,视同未配置"
            else:
                records = [
                    json.loads(line)
                    for line in jsonl_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
        scored = sorted(
            (
                (_score_sdk_record(effective_query, record), i, record)
                for i, record in enumerate(records)
                if record.get("source_refs")
                and _record_allowed_by_intent(record, intent)
                and (not intent.sdk_layers
                     or str(record.get("sdk_layer") or "") in intent.sdk_layers)
            ),
            key=lambda item: (-item[0], item[1]),
        )
        hits = [record for score, _i, record in scored if score > 0][:self._SDK_HIT_CAP]
        coverage_rows = load_sdk_capability_coverage(
            self.sdk_knowledge_dir / "capability_coverage.yaml"
        ) if self.sdk_knowledge_dir is not None else []
        coverage = matching_sdk_coverage(
            coverage_rows, effective_query, intent.sdk_layers)
        official_links = self._matching_sdk_links(
            effective_query, hits, intent.sdk_layers, support_result
        )
        if (
            not hits
            and not coverage
            and not official_links
            and not device_records
            and not support_result.matches
        ):
            return ToolResult("not_found", {
                "query": query,
                "note": knowledge_note or "SDK 知识库无命中;不等于 SDK 不支持",
            })
        code_hit = None
        if self.sdk_knowledge_dir is not None:
            code_hit, _notes = _best_code_snippet(
                effective_query, self.sdk_knowledge_dir, intent, hits)
        launch_mappings = [
            {
                "model": record.model,
                "launch": record.launch,
                "branch": record.branch,
            }
            for record in device_records
            if record.launch
            and record.support_state in {
                "supported", "recommended_for_new_designs",
            }
        ]
        launch_targets = sorted({
            model_id
            for match in support_result.matches
            for model_id in match.model_ids
        })
        content: dict = {
            "query": query,
            "request_sdk_layers": list(intent.sdk_layers),
            "device_support": [
                record.to_payload(
                    freshness_days=self.device_support.freshness_days,
                )
                for record in device_records
            ],
            "device_support_matches": [
                match.to_payload(
                    freshness_days=self.device_support.freshness_days,
                )
                for match in support_result.matches
            ],
            "compatibility_filtered": any(
                match.status == "surface_not_listed"
                for match in support_result.matches
            ),
            "capability_coverage": [row.to_payload() for row in coverage],
            "launch_recommendation_authority": (
                _launch_recommendation_authority(
                    launch_targets, launch_mappings,
                )
            ),
            "hits": [],
            "official_links": [
                self._official_link_payload(link, sdk_join=True)
                for link in official_links
            ],
        }
        for record in hits:
            repo = str(record.get("repo") or "")
            sdk_layer = str(record.get("sdk_layer") or "")
            hit_payload = {
                "evidence_id": str(record.get("kb_id") or ""),
                "title": str(record.get("title") or record.get("kb_id") or ""),
                "answer": str(record.get("answer") or ""),
                "symbols": [str(s) for s in (record.get("symbols") or []) if s],
                "repo": repo,
                "sdk_layer": sdk_layer,
                "doc_type": str(record.get("doc_type") or ""),
                "topics": [str(t) for t in (record.get("topics") or []) if t],
                "evidence_state": "positive_source",
                "confidence": str(record.get("confidence") or ""),
            }
            for scoped_key in ("evidence_scope", "pinned_upstream"):
                if scoped_key in record:
                    hit_payload[scoped_key] = copy.deepcopy(record[scoped_key])
            pinned = record.get("pinned_upstream") or []
            pinned_launch_mappings = [
                {
                    "model": str(item.get("model") or ""),
                    "launch": str(item.get("launch") or ""),
                    "branch": str(item.get("branch") or ""),
                }
                for item in pinned
                if isinstance(item, dict)
                and str(item.get("evidence_kind") or "") in {
                    "launch_mapping", "model_launch_mapping",
                }
                and str(item.get("model") or "")
                and str(item.get("launch") or "")
            ]
            hit_payload["launch_recommendation_authority"] = (
                _launch_recommendation_authority(
                    launch_targets, pinned_launch_mappings,
                )
            )
            if self._official_link_catalog is not None:
                repo_url = self._official_link_catalog.repo_url(
                    repo, (sdk_layer,) if sdk_layer else ()
                )
                if repo_url:
                    hit_payload["repo_url"] = repo_url
            content["hits"].append(hit_payload)
        sources = [
            {"path": str(ref), "section": "", "confidence_layer": "code"}
            for r in hits
            for ref in (r.get("source_refs") or [])
        ]
        sources.extend({
            "path": record.source["url"],
            "section": f"{record.source['ref']}:{record.source['path']}",
            "confidence_layer": "code",
        } for record in device_records)
        sources.extend({
            "path": configuration.source["url"],
            "section": (
                f"{configuration.source['ref']}:"
                f"{configuration.source['path']}"
            ),
            "confidence_layer": "code",
        } for record in device_records
          for configuration in record.configurations
          if configuration.source)
        sources.extend({
            "path": match.roster_evidence.source["url"],
            "section": (
                f"{match.roster_evidence.source['ref']}:"
                f"{match.roster_evidence.source['path']}"
            ),
            "confidence_layer": "code",
        } for match in support_result.matches
          if match.roster_evidence is not None)
        sources.extend(self._official_link_sources(official_links))
        if code_hit is not None:
            snippet = code_hit.snippet
            content["code_snippet"] = {
                "sdk_layer": str(snippet.get("sdk_layer") or ""),
                "language": str(snippet.get("language") or ""),
                "title": str(snippet.get("title") or ""),
                "snippet": str(snippet.get("snippet") or ""),
            }
            sources.append({
                "path": f"Knowledge_SDK_CODE:{snippet.get('snippet_id')}",
                "section": "", "confidence_layer": "code",
            })
        return ToolResult("ok", content, sources)

    def get_session_state(self) -> ToolResult:
        state = self._session_state
        content = {
            "scenario": list(getattr(state, "scenario", []) or []),
            "constraints": list(getattr(state, "accumulated_constraints", []) or []),
            "candidates": list(getattr(state, "candidate_models", []) or []),
            "excluded": list(getattr(state, "excluded_models", []) or []),
            "pending_questions": list(getattr(state, "pending_questions", []) or []),
            "last_assistant_summary": str(getattr(state, "last_assistant_summary", "") or ""),
        }
        # loop 显式状态(确定性写入):含归档话题,支持"回头引用"读档
        loop_state = getattr(self, "_loop_state", None)
        if loop_state is not None:
            content["loop"] = {
                "active_models": list(getattr(loop_state, "active_models", []) or []),
                "constraints": list(getattr(loop_state, "constraints", []) or []),
                "conclusions": list(getattr(loop_state, "conclusions", []) or []),
                "topic": str(getattr(loop_state, "topic", "") or ""),
                "previous_topic": getattr(loop_state, "previous_topic", None),
            }
        return ToolResult("ok", content, [{
            "path": "session", "section": "", "confidence_layer": "session"}])

    # -- 统一入口 -----------------------------------------------------------

    _DISPATCH = {
        "resolve_model": "resolve_model",
        "fact_lookup": "fact_lookup",
        "series_fact_lookup": "series_fact_lookup",
        "list_models": "list_models",
        "filter_models": "filter_models",
        "sampling_geometry": "sampling_geometry",
        "search_knowledge": "search_knowledge",
        "read_doc": "read_doc",
        "sdk_evidence": "sdk_evidence",
        "official_links": "official_links",
        "session_state": "get_session_state",
        "search_qa": "search_qa",
    }

    @property
    def has_qa(self) -> bool:
        return self._qa_collection is not None

    def search_qa(self, query: str) -> ToolResult:
        """QA 经验库检索(可选工具):经验层证据,不承担硬事实职责。"""
        if self._qa_collection is None:
            raise RuntimeError("QA 经验库未启用")
        ensure_embedding_model(self._qa_collection, self._embed_model)
        embedding = embed_texts([query], api_key=self._openai_api_key,
                                model=self._embed_model)[0]
        raw = self._qa_collection.query(query_embeddings=[embedding], n_results=5)
        hits = []
        srcs = []
        for i, kb_id in enumerate((raw.get("ids") or [[]])[0]):
            meta = raw["metadatas"][0][i] or {}
            hits.append({
                "kb_id": kb_id,
                "title": meta.get("title", ""),
                "snippet": str(raw["documents"][0][i])[:300],
                "sim": round(max(0.0, 1.0 - raw["distances"][0][i]), 3),
            })
            srcs.append({"path": f"QA_KB#{kb_id}", "section": meta.get("title", ""),
                         "confidence_layer": "doc"})
        if not hits:
            return ToolResult("not_found", {
                "query": query, "note": "QA 经验库无命中;不等于不支持"})
        return ToolResult("ok", {
            "query": query, "hits": hits,
            "note": "经验层证据:规格/接口/数值等硬事实仍以 fact_lookup 为准",
        }, srcs)

    def dispatch(self, name: str, args: dict) -> ToolResult:
        if self._attachment_tools is not None and name in {
            spec["name"] for spec in self._attachment_tools.schemas()
        }:
            return self._attachment_tools.dispatch(name, args)
        method_name = self._DISPATCH.get(name)
        if method_name is None:
            return ToolResult("tool_error", {"error": f"unknown tool: {name}"})
        try:
            return getattr(self, method_name)(**dict(args or {}))
        except TypeError as exc:
            # 长上下文下模型会丢参数(漏 model_id 等):返回参数契约让它一次自纠
            spec = next((s for s in TOOL_SPECS if s["name"] == name), None)
            required = list((spec or {}).get("input_schema", {}).get("required", []))
            return ToolResult("tool_error", {
                "error": "missing_or_invalid_arguments",
                "tool": name,
                "required": required,
                "got": sorted(dict(args or {})),
                "detail": str(exc)[:120],
            })
        except Exception as exc:  # 工具层永不向循环抛异常
            return ToolResult("tool_error", {
                "error": f"{type(exc).__name__}: {exc}", "tool": name})
