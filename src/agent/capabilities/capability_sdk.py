"""capability_sdk — 从独立 Knowledge_SDK 或 Knowledge/<model>/software.md 抽 SDK Fact。

不调 LLM。优先使用 Knowledge_SDK/SDK_Knowledge.jsonl;未配置或未命中时,
保留旧的产品 software.md 兜底路径。
Evidence Synthesizer 拿到 Facts 后再决定怎么合成回答。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

# 复用 v0.2 已有工具
from src.agent.buckets.spec_or_compat import _resolve_model
from src.agent.capabilities import register
from src.agent.evidence import Evidence, Fact
from src.agent.planner import Capability
from src.agent.sdk_device_support import (
    DeviceConfiguration,
    DeviceSupportMatch,
    DeviceSupportRecord,
    load_device_support_matrix,
)
from src.data.product_loader import _split_by_h2

# 每条 Fact 截到 ≤200 字(含标题),避免单 Fact 过长
_FACT_MAX_CHARS = 200
_SDK_FACT_MAX_CHARS = 420
_SDK_SNIPPET_FACT_MAX_CHARS = 4000

# SDK 相关段标题关键词白名单 (大小写不敏感 ASCII,CJK 精确子串匹配)
_SDK_SECTION_KEYWORDS: tuple[str, ...] = (
    "SDK",
    "API",
    "平台",
    "快速入门",
    "Python",
    "C++",
    "C#",
    "ROS",
    "Android",
    "Linux",
    "Windows",
)

_LAYER_HINTS: dict[str, tuple[str, ...]] = {
    "cpp": ("c++", "cpp", "c api", "c 语言", "orbbecsdk v2", "native"),
    "python": ("python", "pyorbbecsdk", "pip", "wheel", "import"),
    "ros1": ("ros1", "ros 1", "roslaunch", "rostopic", "rosservice", "catkin"),
    "ros2": ("ros2", "ros 2", "launch", "topic", "service", "rviz", "colcon"),
    "k4a_compat": ("k4a", "azure kinect", "k4a wrapper", "k4a_device", "k4a_capture"),
    "dotnet": (".net", "dotnet", "c#", "csharp", "orbbecsharp", "net8"),
    "unity": ("unity", "unitypackage", "gameobject"),
    "android": ("android", "java", "jni", "gradle", "ndk", "aar"),
    "mipi_driver": ("mipi", "gmsl", "jetson", "jetpack", "driver", "dtbo", "kernel"),
}

_SDK_LAYER_TO_REPOS: dict[str, tuple[str, ...]] = {
    "cpp": ("OrbbecSDK_v2",),
    "python": ("pyorbbecsdk",),
    "ros2": ("OrbbecSDK_ROS2",),
    "ros1": ("OrbbecSDK_ROS1",),
    "k4a_compat": ("OrbbecSDK-K4A-Wrapper",),
    "dotnet": ("OrbbecSDK_DotNet",),
    "unity": ("OrbbecUnitySDK",),
    "android": ("OrbbecSDK-Android-Wrapper",),
    "mipi_driver": ("MIPI_Camera_Platform_Driver",),
}

_CROSS_REPO_MARKERS: tuple[str, ...] = (
    "sdk 生态",
    "软件生态",
    "覆盖哪些入口",
    "支持哪些入口",
    "有哪些入口",
    "本地资料入口",
    "本地 sdk/api",
    "语言入口",
    "平台入口",
)

_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "example": ("示例", "代码", "sample", "example", "demo"),
    "streaming": ("示例代码", "打开设备", "取图", "取帧", "capture", "streaming", "start cameras"),
    "quick_start": ("快速开始", "快速入门", "最小", "示例", "代码", "获取", "取帧", "取图", "quick start"),
    "device_property": ("属性", "property", "supported property", "getsupportedproperty"),
    "pipeline": ("pipeline", "启流", "取流", "获取", "取帧", "取图", "start", "wait", "waitforframeset"),
    "pipeline_frame": ("深度帧", "depth frame", "frame", "frameset", "pipeline"),
    "frame": ("深度帧", "彩色帧", "帧", "frame", "frameset"),
    "services": ("service", "服务", "laser", "set_laser", "rosservice"),
    "service": ("service", "服务", "laser", "set_laser", "rosservice"),
    "mipi_driver": ("gmsl", "mipi", "jetson", "jetpack", "驱动", "driver"),
    "install_choice": ("安装", "安装方式", "install", "bin", "patch", "预编译"),
    "precompiled_driver": ("安装", "预编译", "precompiled", "bin"),
    "patch_driver": ("安装", "patch", "编译", "build"),
    "d2c": ("d2c", "对齐", "align", "alignment", "depth to color"),
    "wait_for_frames": ("wait_for_frames", "空帧", "超时", "timeout", "none"),
    "depth": ("深度图", "深度", "depth"),
    "point_cloud": ("点云", "point cloud", "ply"),
    "callback": ("callback", "回调", "异步"),
    "profile": ("profile", "分辨率", "fps"),
    "troubleshooting": ("排查", "异常", "不出图", "丢帧", "卡顿"),
    "multi_device": ("多机", "多相机", "多设备", "multi device", "multidevice"),
    "multi_camera": ("多机", "多相机", "多设备", "multi camera", "multicamera"),
    "sync": ("同步", "sync", "触发", "trigger"),
    "trigger": ("触发", "trigger", "同步"),
    "frame_drop": ("丢帧", "掉帧", "frame drop", "frame_drop"),
    "performance": ("丢帧", "延迟", "带宽", "性能", "卡顿", "performance"),
    "usbfs": ("usbfs", "带宽", "多设备", "多机"),
}

_GENERIC_QUERY_TOKENS = {
    "sdk", "orbbec", "gemini", "怎么", "如何", "调用", "使用", "支持", "是否",
    "相机", "camera",
}


def _is_sdk_section(title: str) -> bool:
    """判断 H2 标题是否属于 SDK 相关段(关键词白名单子串匹配)。"""
    for kw in _SDK_SECTION_KEYWORDS:
        if kw in title:
            return True
    return False


def _norm(text: object) -> str:
    return str(text or "").lower()


def _iter_record_text_parts(record: dict) -> list[str]:
    parts: list[str] = []
    for key in ("kb_id", "repo", "sdk_layer", "doc_type", "title", "answer", "confidence"):
        val = record.get(key)
        if val:
            parts.append(str(val))
    for key in ("question_patterns", "symbols", "commands", "platforms", "products", "topics"):
        vals = record.get(key) or []
        if isinstance(vals, list):
            parts.extend(str(v) for v in vals if v)
    return parts


def _query_tokens(query: str) -> list[str]:
    ascii_tokens = re.findall(r"[A-Za-z0-9_+#./:-]{2,}", query)
    cjk_chunks = [
        chunk.strip()
        for chunk in re.split(r"[\s,，。？?；;：:、/()（）]+", query)
        if len(chunk.strip()) >= 2
    ]
    tokens = ascii_tokens + cjk_chunks
    deduped: list[str] = []
    for token in tokens:
        low = token.lower()
        if low in _GENERIC_QUERY_TOKENS:
            continue
        if low not in deduped:
            deduped.append(low)
    return deduped


def _score_sdk_record(query: str, record: dict) -> int:
    q = _norm(query)
    haystack = _norm(" ".join(_iter_record_text_parts(record)))
    score = 0

    for pattern in record.get("question_patterns") or []:
        p = _norm(pattern)
        if p and (p in q or q in p):
            score += 12
        elif p:
            for token in _query_tokens(query):
                if token in p:
                    score += 3

    for token in _query_tokens(query):
        if token in haystack:
            score += 2

    layer = _norm(record.get("sdk_layer"))
    for hint_layer, hints in _LAYER_HINTS.items():
        if any(h in q for h in hints):
            score += 5 if layer == hint_layer else -1

    topics = {_norm(t) for t in (record.get("topics") or [])}
    for topic, aliases in _TOPIC_ALIASES.items():
        if topic in topics and any(alias in q for alias in aliases):
            score += 9

    is_howto_query = any(
        k in q for k in ("怎么", "如何", "获取", "调用", "设置", "打开", "配置", "枚举", "取帧", "取图")
    )
    is_build_install_query = any(
        k in q for k in ("安装", "编译", "构建", "build", "install", "cmake", "gradle", "ndk")
    )
    doc_type = str(record.get("doc_type") or "")
    if is_howto_query and not is_build_install_query:
        if doc_type in {"api", "example", "source_trace", "troubleshooting"}:
            score += 3
        elif doc_type in {"concept", "build", "install"}:
            score -= 3
    if is_build_install_query and doc_type in {"install", "build"}:
        score += 5

    if doc_type == "example" and any(
        k in q for k in ("示例", "代码", "sample", "example", "demo", "howto", "怎么用")
    ):
        score += 5

    for symbol in record.get("symbols") or []:
        sym = _norm(symbol)
        if sym and sym in q:
            score += 5
        elif sym and any(part and part in q for part in re.split(r"[:.()>\\s]+", sym.lower())):
            score += 2

    if record.get("doc_type") == "troubleshooting" and any(k in q for k in ("排查", "异常", "不出图", "丢帧", "超时")):
        score += 4
    return score


@dataclass(frozen=True)
class SdkIntent:
    sdk_layers: tuple[str, ...] = ()
    task_type: str = "unknown"
    allow_cross_repo: bool = False


@dataclass(frozen=True)
class SdkCodeHit:
    score: int
    snippet: dict


def _detect_sdk_layers(query: str) -> tuple[str, ...]:
    q = _norm(query)
    layers: list[str] = []
    for layer, hints in _LAYER_HINTS.items():
        if any(h in q for h in hints):
            layers.append(layer)
    return tuple(layers)


def _detect_sdk_task_type(query: str) -> str:
    q = _norm(query)
    if any(k in q for k in ("示例代码", "给代码", "代码", "sample", "example", "demo")):
        return "code_example"
    if any(k in q for k in ("安装", "编译", "构建", "build", "install", "cmake", "gradle", "colcon", "catkin", "udev")):
        return "install_build"
    if any(k in q for k in ("报错", "失败", "打不开", "无流", "丢帧", "超时", "排查", "异常", "不出图")):
        return "troubleshooting"
    if any(k in q for k in ("兼容", "支持吗", "支持哪些", "版本", "平台", "设备范围")):
        return "compatibility"
    if any(k in q for k in ("源码", "内部", "实现", "在哪里")):
        return "source_explain"
    if any(k in q for k in ("怎么", "如何", "调用", "设置", "获取", "枚举", "配置", "打开", "api")):
        return "api_howto"
    return "unknown"


def _allow_cross_repo(query: str, sdk_layers: tuple[str, ...]) -> bool:
    q = _norm(query)
    if len(sdk_layers) > 1:
        return True
    return any(marker in q for marker in _CROSS_REPO_MARKERS)


def _parse_sdk_intent(query: str) -> SdkIntent:
    layers = _detect_sdk_layers(query)
    return SdkIntent(
        sdk_layers=layers,
        task_type=_detect_sdk_task_type(query),
        allow_cross_repo=_allow_cross_repo(query, layers),
    )


def _candidate_repos(intent: SdkIntent) -> tuple[str, ...]:
    repos: list[str] = []
    for layer in intent.sdk_layers:
        for repo in _SDK_LAYER_TO_REPOS.get(layer, ()):
            if repo not in repos:
                repos.append(repo)
    return tuple(repos)


def _record_allowed_by_intent(record: dict, intent: SdkIntent) -> bool:
    if intent.allow_cross_repo:
        return True
    candidate_repos = set(_candidate_repos(intent))
    if not candidate_repos:
        return True
    return str(record.get("repo") or "") in candidate_repos


def _score_sdk_snippet(query: str, snippet: dict) -> int:
    if snippet.get("confidence") != "code_verified" or not str(snippet.get("snippet") or "").strip():
        return 0

    q = _norm(query)
    parts: list[str] = []
    for key in ("snippet_id", "repo", "sdk_layer", "language", "title", "confidence"):
        val = snippet.get(key)
        if val:
            parts.append(str(val))
    for key in ("question_patterns", "symbols", "topics"):
        vals = snippet.get(key) or []
        if isinstance(vals, list):
            parts.extend(str(v) for v in vals if v)
    haystack = _norm(" ".join(parts))

    score = 0
    for pattern in snippet.get("question_patterns") or []:
        p = _norm(pattern)
        if p and (p in q or q in p):
            score += 14
        elif p:
            for token in _query_tokens(query):
                if token in p:
                    score += 3

    for token in _query_tokens(query):
        if token in haystack:
            score += 2

    layer = _norm(snippet.get("sdk_layer"))
    for hint_layer, hints in _LAYER_HINTS.items():
        if any(h in q for h in hints):
            score += 5 if layer == hint_layer else -1

    topics = {_norm(t) for t in (snippet.get("topics") or [])}
    for topic, aliases in _TOPIC_ALIASES.items():
        if topic in topics and any(alias in q for alias in aliases):
            score += 9

    for symbol in snippet.get("symbols") or []:
        sym = _norm(symbol)
        if sym and sym in q:
            score += 5
        elif sym and any(part and part in q for part in re.split(r"[:.()>\\s]+", sym.lower())):
            score += 2

    return score


def _load_code_snippets(sdk_dir: Path, repos: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    snippets: list[dict] = []
    notes: list[str] = []
    for repo in repos:
        path = sdk_dir / repo / "code_snippets.jsonl"
        if not path.exists():
            continue
        try:
            repo_snippets = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except Exception as exc:
            notes.append(f"{repo}/code_snippets.jsonl 读取失败: {exc}")
            continue
        snippets.extend(
            snippet
            for snippet in repo_snippets
            if str(snippet.get("repo") or repo) == repo
        )
    return snippets, notes


def _repos_for_code_search(intent: SdkIntent, hits: list[dict]) -> tuple[str, ...]:
    candidate_repos = _candidate_repos(intent)
    if candidate_repos and not intent.allow_cross_repo:
        return candidate_repos

    repos: list[str] = []
    for record in hits:
        repo = str(record.get("repo") or "")
        if repo and repo not in repos:
            repos.append(repo)
    return tuple(repos)


def _best_code_snippet(
    query: str,
    sdk_dir: Path,
    intent: SdkIntent,
    hits: list[dict],
) -> tuple[SdkCodeHit | None, list[str]]:
    if intent.task_type != "code_example":
        return None, []

    repos = _repos_for_code_search(intent, hits)
    if not repos:
        return None, []

    snippets, notes = _load_code_snippets(sdk_dir, repos)
    scored = [
        SdkCodeHit(score=_score_sdk_snippet(query, snippet), snippet=snippet)
        for snippet in snippets
    ]
    code_hits = [hit for hit in scored if hit.score > 0]
    if not code_hits:
        return None, notes
    return sorted(code_hits, key=lambda hit: -hit.score)[0], notes


def _snippet_to_fact(snippet: dict) -> Fact:
    snippet_id = str(snippet.get("snippet_id") or "sdk-code-snippet").strip()
    language = str(snippet.get("language") or "code").strip()
    title = str(snippet.get("title") or snippet_id).strip()
    symbols = [str(s) for s in (snippet.get("symbols") or []) if s]
    snippet_body = str(snippet.get("snippet") or "").strip()

    statement = (
        f"代码片段: {language} / {title}. "
        "confidence=code_verified; executable_code_allowed=true; "
    )
    if symbols:
        statement += "关键符号: " + ", ".join(symbols[:8]) + ". "
    statement += "代码:\n" + snippet_body

    return Fact(
        statement=statement[:_SDK_SNIPPET_FACT_MAX_CHARS].strip(),
        source_ref=f"Knowledge_SDK_CODE:{snippet_id}",
        confidence=1.0,
    )


def _record_confidences(records: list[dict]) -> tuple[str, ...]:
    values: list[str] = []
    for record in records:
        confidence = str(record.get("confidence") or "unknown").strip() or "unknown"
        if confidence not in values:
            values.append(confidence)
    return tuple(values)


def _code_evidence_level(record_confidences: tuple[str, ...], code_hit: SdkCodeHit | None) -> str:
    if code_hit is not None:
        return "snippet"
    if "code_verified" in record_confidences:
        return "record_only"
    if "doc_verified" in record_confidences:
        return "doc_verified"
    if "inferred" in record_confidences:
        return "inferred"
    return "unknown"


def _intent_metadata(intent: SdkIntent) -> dict[str, object]:
    return {
        "sdk_layers": list(intent.sdk_layers),
        "task_type": intent.task_type,
        "allow_cross_repo": intent.allow_cross_repo,
    }


def _fact_hit_metadata(record: dict, score: int) -> dict[str, object]:
    return {
        "kb_id": str(record.get("kb_id") or ""),
        "repo": str(record.get("repo") or ""),
        "sdk_layer": str(record.get("sdk_layer") or ""),
        "confidence": str(record.get("confidence") or "unknown"),
        "doc_type": str(record.get("doc_type") or ""),
        "topics": list(record.get("topics") or []),
        "score": score,
    }


def _code_hit_metadata(code_hit: SdkCodeHit) -> dict[str, object]:
    snippet = code_hit.snippet
    return {
        "snippet_id": str(snippet.get("snippet_id") or ""),
        "repo": str(snippet.get("repo") or ""),
        "sdk_layer": str(snippet.get("sdk_layer") or ""),
        "language": str(snippet.get("language") or ""),
        "title": str(snippet.get("title") or ""),
        "confidence": str(snippet.get("confidence") or "unknown"),
        "topics": list(snippet.get("topics") or []),
        "symbols": list(snippet.get("symbols") or []),
        "snippet": str(snippet.get("snippet") or ""),
        "score": code_hit.score,
    }


def _confidence_gate_metadata(
    record_confidences: tuple[str, ...],
    code_evidence_level: str,
) -> dict[str, object]:
    executable_code_allowed = code_evidence_level == "snippet"
    reasons = {
        "snippet": "code_verified snippet matched",
        "record_only": "code_verified record matched but no snippet matched",
        "doc_verified": "doc_verified record only",
        "inferred": "inferred record only",
    }
    return {
        "record_confidence": list(record_confidences),
        "code_evidence_level": code_evidence_level,
        "executable_code_allowed": executable_code_allowed,
        "reason": reasons.get(code_evidence_level, "no verified code evidence"),
    }


def _repo_candidates_metadata(intent: SdkIntent, hits: list[dict]) -> list[str]:
    candidates = list(_candidate_repos(intent))
    if candidates:
        return candidates
    repos: list[str] = []
    for record in hits:
        repo = str(record.get("repo") or "")
        if repo and repo not in repos:
            repos.append(repo)
    return repos


def _confidence_to_float(confidence: str) -> float:
    if confidence == "inferred":
        return 0.7
    if confidence in {"code_verified", "doc_verified"}:
        return 1.0
    return 0.8


def _record_to_fact(record: dict) -> Fact:
    title = str(record.get("title") or record.get("kb_id") or "SDK knowledge").strip()
    repo = str(record.get("repo") or "").strip()
    answer = str(record.get("answer") or "").strip()
    symbols = [str(s) for s in (record.get("symbols") or []) if s]
    commands = [str(c) for c in (record.get("commands") or []) if c]

    extras: list[str] = []
    if symbols:
        extras.append("关键符号: " + ", ".join(symbols[:8]))
    if commands:
        extras.append("常用命令: " + " ; ".join(commands[:4]))

    repo_label = repo
    if repo == "OrbbecSDK_ROS2" and "wrapper" not in title.lower():
        repo_label = "OrbbecSDK_ROS2 ROS2 Wrapper"
    if repo_label and repo_label not in title:
        title = f"{repo_label} / {title}"
    statement = f"{title}: {answer}"
    if extras:
        statement += " " + " ".join(extras)
    statement = statement[:_SDK_FACT_MAX_CHARS].strip()

    kb_id = str(record.get("kb_id") or title).strip()
    return Fact(
        statement=statement,
        source_ref=f"Knowledge_SDK:{kb_id}",
        confidence=_confidence_to_float(str(record.get("confidence") or "")),
    )


def _matrix_sdk_facts(
    resolved_models: list[str], fact_store
) -> tuple[list[Fact], list[str]]:
    """事实矩阵 SDK/平台聚合行 → 前置 Fact(W5/D6)。fact_store 缺失时不改变行为。"""
    if fact_store is None or not resolved_models:
        return [], []
    facts: list[Fact] = []
    notes: list[str] = []
    for model in resolved_models:
        for fid, label in (
            ("sdk_wrapper_support", "SDK 与 Wrapper 支持"),
            ("platform_support", "平台支持"),
        ):
            result = fact_store.get_spec(model, fid)
            if result.status != "found":
                continue
            row = result.row
            facts.append(Fact(
                statement=f"{model.replace('_', ' ')} {label}: {row.raw_value}",
                source_ref=f"Knowledge/{model}/facts.yaml#{fid}",
                confidence=1.0 if row.status == "fae_verified" else 0.9,
            ))
    if facts:
        notes.append(f"事实矩阵 SDK/平台聚合证据命中 {len(facts)} 行(查表优先)")
    return facts, notes


def _should_merge_product_software(query: str) -> bool:
    q = _norm(query)
    return any(
        marker in q
        for marker in (
            "sdk 生态",
            "软件生态",
            "覆盖哪些入口",
            "支持哪些入口",
            "本地资料入口",
            "本地 sdk/api",
            "语言",
            "平台入口",
            "c#",
            "csharp",
            "dotnet",
            ".net",
            "k4a",
            "unity",
        )
    )


def _display_model(model: str) -> str:
    return str(model).replace("_", " ")


def _github_source_ref(source: dict[str, str]) -> str:
    revision = source.get("commit") or source["ref"]
    return (
        f"{source['url'].rstrip('/')}/blob/{revision}/"
        f"{source['path'].lstrip('/')}"
    )


def _support_state_text(state: str) -> str:
    return {
        "supported": "支持",
        "recommended_for_new_designs": "支持，且推荐用于新设计",
        "not_supported": "不支持",
        "unknown": "未知",
    }[state]


def _optional_bool_text(value: bool | None, *, positive: str, negative: str) -> str:
    if value is True:
        return positive
    if value is False:
        return negative
    return "未知"


def _configuration_fact(
    record: DeviceSupportRecord,
    configuration: DeviceConfiguration,
) -> Fact:
    label = configuration.capability.upper()
    statement = (
        f"{_display_model(record.model)} 在 {record.implementation} / "
        f"{record.branch or '未指定分支'} 的 {label}："
        f"{_optional_bool_text(configuration.supported, positive='支持', negative='不支持')}，"
        f"{_optional_bool_text(configuration.default_enabled, positive='默认开启', negative='默认关闭')}。"
    )
    if configuration.parameter:
        statement += f"配置参数为 {configuration.parameter}。"
    if configuration.activation:
        statement += f"显式启用方式为 {configuration.activation}。"
    if configuration.default_mode:
        statement += f"默认模式为 {configuration.default_mode}。"
    return Fact(
        statement=statement[:_SDK_FACT_MAX_CHARS].strip(),
        source_ref=_github_source_ref(configuration.source or record.source),
        confidence=1.0,
    )


def _support_record_fact(record: DeviceSupportRecord) -> Fact:
    statement = (
        f"{_display_model(record.model)} 在 {record.implementation} / "
        f"{record.branch or '未指定分支'} 的支持状态："
        f"{_support_state_text(record.support_state)}。"
    )
    if record.firmware:
        if record.firmware.get("minimum"):
            statement += f"最低固件 {record.firmware['minimum']}。"
        if record.firmware.get("recommended"):
            statement += f"推荐固件 {record.firmware['recommended']}。"
    if record.launch:
        statement += f"启动文件 {record.launch}。"
    if record.platforms:
        statement += "已列平台：" + "、".join(record.platforms) + "。"
    return Fact(
        statement=statement[:_SDK_FACT_MAX_CHARS].strip(),
        source_ref=_github_source_ref(record.source),
        confidence=1.0,
    )


def _device_support_facts(
    records: tuple[DeviceSupportRecord, ...],
    query: str,
) -> list[Fact]:
    facts: list[Fact] = []
    requested_configurations = {
        capability
        for capability, aliases in {
            "d2c": ("d2c", "depth to color", "深度对齐", "深度到彩色"),
        }.items()
        if any(alias in query.casefold() for alias in aliases)
    }
    for record in records:
        configurations = list(record.configurations)
        matching = [
            row
            for row in configurations
            if row.capability.casefold() in requested_configurations
        ]
        remaining = [row for row in configurations if row not in matching]
        facts.extend(_configuration_fact(record, row) for row in matching)
        facts.append(_support_record_fact(record))
        facts.extend(_configuration_fact(record, row) for row in remaining)
    return facts


def _device_support_match_facts(
    matches: tuple[DeviceSupportMatch, ...],
) -> list[Fact]:
    facts: list[Fact] = []
    for match in matches:
        evidence = match.roster_evidence
        if evidence is None or match.status not in {
            "roster_listed",
            "surface_not_listed",
        }:
            continue
        model = _display_model(match.model_ids[0]) if match.model_ids else "该型号"
        if match.status == "roster_listed":
            upstream_names = "、".join(
                entry.official_name for entry in evidence.entries
            )
            statement = (
                f"{model}：当前官方 {evidence.surface.title()} 支持名单列出该型号"
                f"（上游名称：{upstream_names}）。"
            )
        else:
            statement = (
                f"{model}：当前官方 {evidence.surface.title()} 支持名单未列出该型号；"
                "这表示尚无当前名单的明确支持确认，不等于技术上明确不支持。"
            )
        facts.append(Fact(
            statement=statement[:_SDK_FACT_MAX_CHARS].strip(),
            source_ref=_github_source_ref(evidence.source),
            confidence=1.0,
        ))
    return facts


def _metadata_with_device_support(
    metadata: dict[str, object] | None,
    payloads: list[dict[str, object]],
    match_payloads: list[dict[str, object]],
) -> dict[str, object]:
    merged = dict(metadata or {})
    sdk_metadata = dict(merged.get("sdk") or {})
    sdk_metadata["device_support"] = payloads
    sdk_metadata["device_support_matches"] = match_payloads
    merged["sdk"] = sdk_metadata
    return merged


def _surface_token_present(text: str, alias: str) -> bool:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return pattern.search(text) is not None


def _ecosystem_summary_fact(
    md_path: Path,
    model: str,
    *,
    excluded_surfaces: frozenset[str] = frozenset(),
) -> Fact | None:
    text = md_path.read_text(encoding="utf-8")
    entries: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("cpp", "C++", ("c++", "cpp")),
        ("python", "Python", ("python", "pyorbbecsdk")),
        ("ros2", "ROS2", ("ros2", "ros 2")),
        ("csharp", "C#", ("c#", "csharp")),
        ("dotnet", "DotNet", ("dotnet", ".net")),
        ("k4a_compat", "K4A Wrapper", ("k4a wrapper", "k4a")),
        ("unity", "Unity", ("unity",)),
    )
    supported = [
        label
        for surface, label, aliases in entries
        if surface not in excluded_surfaces
        and any(_surface_token_present(text, alias) for alias in aliases)
    ]
    if not supported:
        return None
    statement = (
        f"{_display_model(model)} 本地 software.md 检索到的 SDK 文档/入口："
        + "、".join(supported)
        + "。该汇总仅表示本地资料入口，不构成型号级兼容性证明。"
    )
    return Fact(
        statement=statement[:_FACT_MAX_CHARS].strip(),
        source_ref=f"Knowledge/{model}/software.md:1",
        confidence=1.0,
    )


@dataclass
class CapabilitySdk:

    name: Capability = "sdk"
    sdk_knowledge_dir: Path | None = None
    top_k: int = 5

    def run(
        self,
        query: str,
        *,
        schema,                    # src.agent.schema.RequestSchema
        channel: str = "fae",
        product_catalog: dict[str, dict] | None = None,
        **kwargs,
    ) -> Evidence:
        t0 = perf_counter()
        catalog = product_catalog or {}
        sdk_knowledge_dir = kwargs.get("sdk_knowledge_dir") or self.sdk_knowledge_dir

        product_names = schema.products if schema else []

        resolved_models: list[str] = []
        for raw in product_names:
            key = _resolve_model(raw, catalog)
            if key:
                resolved_models.append(key)

        # 事实矩阵聚合证据(W5/D6):SDK wrapper 与平台支持整节,查表优先。
        matrix_facts, matrix_notes = _matrix_sdk_facts(
            resolved_models, kwargs.get("fact_store")
        )

        # 分支/固件/launch/默认配置属于独立软件支持事实，不从产品 facts
        # 或自由文本推导。即使常规 SDK 文本无命中，也保留这些结构化证据。
        device_facts: list[Fact] = []
        device_notes: list[str] = []
        device_payloads: list[dict[str, object]] = []
        device_match_payloads: list[dict[str, object]] = []
        excluded_summary_surfaces: frozenset[str] = frozenset()
        resolver = kwargs.get("model_resolver")
        if sdk_knowledge_dir and resolver is not None:
            support_path = Path(sdk_knowledge_dir) / "device_support_matrix.yaml"
            if support_path.is_file():
                support_matrix = load_device_support_matrix(
                    support_path,
                    known_model_ids=resolver.entries.keys(),
                )
                support_query = "\n".join([query, *product_names])
                support_result = support_matrix.match_query(
                    support_query,
                    resolver=resolver,
                )
                support_records = support_result.records
                device_facts = _device_support_facts(support_records, query)
                device_facts.extend(
                    _device_support_match_facts(support_result.matches)
                )
                device_payloads = [
                    record.to_payload(
                        freshness_days=support_matrix.freshness_days,
                    )
                    for record in support_records
                ]
                device_match_payloads = [
                    match.to_payload(
                        freshness_days=support_matrix.freshness_days,
                    )
                    for match in support_result.matches
                ]
                excluded_summary_surfaces = frozenset(
                    match.roster_evidence.surface.casefold()
                    for match in support_result.matches
                    if match.status == "surface_not_listed"
                    and match.roster_evidence is not None
                )
                if support_records or support_result.matches:
                    device_notes.append(
                        "结构化 SDK 设备支持证据："
                        f"{len(support_records)} 个分支事实，"
                        f"{len(support_result.matches)} 个型号查询状态"
                    )
                    if any(
                        payload["evidence_state"] == "stale"
                        for payload in device_payloads
                    ):
                        device_notes.append("SDK 设备支持证据已过新鲜度阈值，结论需复核")

        if sdk_knowledge_dir:
            sdk_evidence = self._run_sdk_knowledge(query, Path(sdk_knowledge_dir), t0)
            if sdk_evidence is not None:
                if resolved_models and _should_merge_product_software(query):
                    product_facts, product_notes = self._extract_product_software_facts(
                        resolved_models,
                        catalog,
                        query=query,
                        excluded_summary_surfaces=excluded_summary_surfaces,
                    )
                    if product_facts or matrix_facts or device_facts:
                        seen_refs = {
                            fact.source_ref
                            for fact in [*device_facts, *matrix_facts]
                        }
                        seen_refs.update(fact.source_ref for fact in sdk_evidence.facts)
                        merged_facts = [
                            *device_facts,
                            *matrix_facts,
                            *sdk_evidence.facts,
                        ]
                        merged_facts.extend(
                            fact
                            for fact in product_facts
                            if fact.source_ref not in seen_refs
                        )
                        return Evidence(
                            source_module="sdk",
                            facts=merged_facts,
                            confidence="high",
                            coverage="full",
                            notes=[
                                *sdk_evidence.notes,
                                *device_notes,
                                "补充产品 software.md SDK 入口",
                                *product_notes,
                                *matrix_notes,
                            ],
                            latency_ms=int((perf_counter() - t0) * 1000),
                            metadata=_metadata_with_device_support(
                                sdk_evidence.metadata,
                                device_payloads,
                                device_match_payloads,
                            ),
                        )
                if matrix_facts or device_facts:
                    return Evidence(
                        source_module="sdk",
                        facts=[*device_facts, *matrix_facts, *sdk_evidence.facts],
                        confidence=sdk_evidence.confidence,
                        coverage=sdk_evidence.coverage,
                        notes=[*sdk_evidence.notes, *device_notes, *matrix_notes],
                        latency_ms=int((perf_counter() - t0) * 1000),
                        metadata=_metadata_with_device_support(
                            sdk_evidence.metadata,
                            device_payloads,
                            device_match_payloads,
                        ),
                    )
                return sdk_evidence

        if not resolved_models and not device_facts:
            return Evidence(
                source_module="sdk",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=["缺产品锚点(schema.products 为空或无法解析到 catalog)"],
                latency_ms=int((perf_counter() - t0) * 1000),
            )

        facts, notes = self._extract_product_software_facts(
            resolved_models,
            catalog,
            query=query,
            excluded_summary_surfaces=excluded_summary_surfaces,
        )
        facts = [*device_facts, *matrix_facts, *facts]
        notes = [
            "使用产品 software.md 作为 SDK 入口兜底",
            *device_notes,
            *notes,
            *matrix_notes,
        ]

        coverage = "full" if facts else "empty"
        if facts and len(resolved_models) < len(product_names):
            coverage = "partial"

        return Evidence(
            source_module="sdk",
            facts=facts,
            confidence="high" if facts else "low",
            coverage=coverage,
            notes=notes,
            latency_ms=int((perf_counter() - t0) * 1000),
            metadata=_metadata_with_device_support(
                {}, device_payloads, device_match_payloads
            ),
        )

    def _extract_product_software_facts(
        self,
        resolved_models: list[str],
        catalog: dict[str, dict],
        *,
        query: str = "",
        excluded_summary_surfaces: frozenset[str] = frozenset(),
    ) -> tuple[list[Fact], list[str]]:
        facts: list[Fact] = []
        notes: list[str] = []
        include_ecosystem_summary = _should_merge_product_software(query)
        for model in resolved_models:
            model_dir = Path(catalog[model]["dir"])
            md_path = model_dir / "software.md"
            if not md_path.exists():
                notes.append(f"{model}/software.md 不存在")
                continue
            try:
                model_facts = self._extract_facts(md_path, model)
                if include_ecosystem_summary:
                    summary_fact = _ecosystem_summary_fact(
                        md_path,
                        model,
                        excluded_surfaces=excluded_summary_surfaces,
                    )
                    if summary_fact:
                        model_facts = [summary_fact, *model_facts]
                if not model_facts:
                    notes.append(f"{model}/software.md 无 SDK 相关段")
                facts.extend(model_facts)
            except Exception as exc:
                notes.append(f"{model}/software.md 抽取失败: {exc}")
        return facts, notes

    def _run_sdk_knowledge(self, query: str, sdk_dir: Path, t0: float) -> Evidence | None:
        jsonl_path = sdk_dir / "SDK_Knowledge.jsonl"
        if not jsonl_path.exists():
            return None

        try:
            records = [
                json.loads(line)
                for line in jsonl_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except Exception as exc:
            return Evidence(
                source_module="sdk",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=[f"Knowledge_SDK 读取失败: {exc}"],
                latency_ms=int((perf_counter() - t0) * 1000),
                error=str(exc),
            )

        intent = _parse_sdk_intent(query)
        scored = [
            (_score_sdk_record(query, record), i, record)
            for i, record in enumerate(records)
            if record.get("source_refs") and _record_allowed_by_intent(record, intent)
        ]
        hit_items = [
            (score, record)
            for score, _i, record in sorted(scored, key=lambda item: (-item[0], item[1]))
            if score > 0
        ][:self.top_k]
        hits = [record for _score, record in hit_items]
        if not hits:
            return None

        record_confidences = _record_confidences(hits)
        code_hit, snippet_notes = _best_code_snippet(query, sdk_dir, intent, hits)
        facts = [_record_to_fact(record) for record in hits]
        if code_hit is not None:
            facts = [_snippet_to_fact(code_hit.snippet), *facts]

        code_evidence_level = _code_evidence_level(record_confidences, code_hit)
        executable_code_allowed = "true" if code_evidence_level == "snippet" else "false"
        filter_note = ""
        candidate_repos = _candidate_repos(intent)
        if candidate_repos and not intent.allow_cross_repo:
            filter_note = "SDK repo 硬过滤: " + ", ".join(candidate_repos)
        elif intent.allow_cross_repo:
            filter_note = "SDK repo 跨库检索: enabled"
        gate_notes = [
            "record_confidence=" + ",".join(record_confidences),
            f"code_evidence_level={code_evidence_level}",
            f"executable_code_allowed={executable_code_allowed}",
        ]
        confidence_gate = _confidence_gate_metadata(record_confidences, code_evidence_level)
        return Evidence(
            source_module="sdk",
            facts=facts,
            confidence="high",
            coverage="full",
            notes=[
                note
                for note in (
                    f"Knowledge_SDK 命中 {len(hits)} 条",
                    filter_note,
                    *snippet_notes,
                    *gate_notes,
                )
                if note
            ],
            latency_ms=int((perf_counter() - t0) * 1000),
            metadata={
                "sdk": {
                    "sdk_intent": _intent_metadata(intent),
                    "repo_candidates": _repo_candidates_metadata(intent, hits),
                    "fact_hits": [
                        _fact_hit_metadata(record, score)
                        for score, record in hit_items
                    ],
                    "code_hits": [_code_hit_metadata(code_hit)] if code_hit else [],
                    "confidence_gate": confidence_gate,
                    "coverage": "full",
                    "fallback_used": False,
                    "fallback_reason": None,
                },
            },
        )

    def _extract_facts(self, md_path: Path, model: str) -> list[Fact]:
        """Markdown ## 段切片 → 白名单过滤 SDK 段 → Fact list,每段首 _FACT_MAX_CHARS 字。

        source_ref 用 `Knowledge/<model>/software.md:<line>` 形式,行号 = 段标题行。
        """
        text = md_path.read_text(encoding="utf-8")
        sections = _split_by_h2(text)
        if not sections:
            return []

        # 算每段标题在原文里的行号
        lines = text.splitlines()
        title_to_line: dict[str, int] = {}
        for i, line in enumerate(lines, 1):
            if line.startswith("## "):
                title = line[3:].strip()
                title_to_line.setdefault(title, i)

        facts: list[Fact] = []
        for title, content in sections:
            if not _is_sdk_section(title):
                continue
            line_no = title_to_line.get(title, 1)
            statement = content[:_FACT_MAX_CHARS].strip()
            facts.append(Fact(
                statement=statement,
                source_ref=f"Knowledge/{model}/software.md:{line_no}",
                confidence=1.0,
            ))
        return facts


# 模块 import 时自动注册
register(CapabilitySdk())
