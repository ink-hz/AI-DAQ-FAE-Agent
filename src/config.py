"""配置加载层 — 所有路径和密钥都从这里取,不在业务代码里写死。

LLM Provider 抽象:
  LLM_PROVIDER=openai_compat → 用 OpenAI SDK + 自定义 base_url(默认 GLM)
  LLM_PROVIDER=anthropic     → 用 Anthropic SDK

Embedding 始终走 OpenAI(text-embedding-3-large)。后期可切本地开源。
"""
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent


LLMProvider = Literal["openai_compat", "anthropic"]
VisionProvider = Literal["inherit", "openai_compat", "anthropic"]
VisionConfigMode = Literal["disabled", "inherit", "dedicated"]


@dataclass(frozen=True)
class ResolvedVisionConfig:
    enabled: bool
    mode: VisionConfigMode
    provider: LLMProvider | None
    api_key: str
    auth_token: str
    base_url: str
    model: str
    timeout_seconds: int
    tool_choice_strategy: str = "forced"


@dataclass(frozen=True)
class Config:
    # LLM provider 抽象
    llm_provider: LLMProvider
    llm_api_key: str
    llm_base_url: str       # 仅 openai_compat 用
    llm_model_main: str
    llm_model_fast: str

    # 兼容字段:provider=anthropic 时使用
    anthropic_api_key: str
    anthropic_auth_token: str
    anthropic_base_url: str
    anthropic_model_main: str
    anthropic_model_fast: str
    anthropic_main_thinking_mode: str
    anthropic_main_effort: str | None
    anthropic_main_tool_choice_strategy: str

    # Embedding
    openai_api_key: str
    embed_model: str

    # 数据路径
    chroma_qa_path: Path
    chroma_product_path: Path
    knowledge_dir: Path
    knowledge_qa_dir: Path
    knowledge_sdk_dir: Path
    knowledge_links_dir: Path
    prompts_dir: Path
    log_dir: Path
    enable_qa_kb: bool

    # 生产数据飞轮
    database_url: str
    data_flywheel_fallback_path: Path

    # Agent Platform 私有任务入口；默认关闭，不改变公网 FAE 行为。
    platform_task_enabled: bool
    platform_task_capability_version: int
    platform_task_public_key_paths: tuple[tuple[str, Path], ...]
    platform_task_content_keyring_file: Path | None

    # Platform 身份只通过回环 backchannel 换成 FAE 自有 Session。
    # 默认关闭，公网客户模式保持原样。
    platform_identity_enabled: bool
    platform_identity_base_url: str
    platform_enterprise_session_keyring_file: Path | None
    # 认证会话内容密钥与浏览器会话密钥、Platform 任务内容密钥必须彼此独立。
    platform_authenticated_content_keyring_file: Path | None
    platform_enterprise_validation_cache_seconds: int
    platform_enterprise_idle_ttl_seconds: int
    platform_enterprise_absolute_ttl_seconds: int
    platform_enterprise_public_origin: str
    platform_partner_auth_start_url: str | None
    platform_partner_provider_kind: str | None
    platform_partner_provider_release_file: Path | None
    platform_partner_provider_release_sha256: str | None
    platform_partner_login_reason: str

    # 追踪配置
    trace_provider: str
    trace_sample_rate: float
    trace_log_path: Path
    trace_environment: str
    trace_release: str
    langfuse_base_url: str
    langfuse_public_key: str
    langfuse_secret_key: str

    # 运行配置
    session_ttl_seconds: int
    chat_max_concurrent_agent_requests: int

    # 匿名 Session 临时附件
    attachment_storage_dir: Path
    attachment_ttl_seconds: int
    attachment_max_files: int
    attachment_max_batch_bytes: int
    attachment_storage_capacity_bytes: int
    attachment_upload_max_concurrent: int
    attachment_upload_batches_per_minute: int
    attachment_trusted_proxy_hosts: tuple[str, ...]
    attachment_archive_enabled: bool
    attachment_archive_handoff_seconds: int

    # 视觉通道默认继承主 Provider；仅接收归一化图片和本轮问题。
    vision_enabled: bool
    vision_provider: VisionProvider
    vision_api_key: str
    vision_auth_token: str
    vision_base_url: str
    vision_model: str
    vision_timeout_seconds: int

    # 循环骨架路由(M3):pipeline=现状默认;loop=模型驱动工具循环;
    # shadow=主路径照常+循环后台影子双写。非法值保守回落 pipeline。
    composition_mode: str
    # 循环工具预算与时长上限(O2 20260707:434 全量实验需要放大预算)
    loop_max_tool_calls: int
    loop_max_duration_s: int
    # 单次模型调用输出上限(R1 20260708:GLM-5.2 reasoning 先烧输出预算,
    # 2000 常被烧光导致空答案/length 截断)
    loop_max_output_tokens: int
    # Anthropic Loop 单轮传输最多 1 + attempts 次；不允许静默换模型。
    loop_provider_retry_attempts: int
    loop_provider_retry_backoff_s: float
    loop_provider_retry_max_delay_s: float

    # 用户可见公开思考计划:基于问题/schema/planner 生成本轮动态 steps,不暴露隐藏思维链。
    public_reasoning_enabled: bool
    public_reasoning_language: str
    public_reasoning_max_steps: int

    @property
    def platform_enterprise_identity_enabled(self) -> bool:
        """One-release read-only alias for callers using the enterprise name."""
        return self.platform_identity_enabled


def _path(env_key: str, default: str) -> Path:
    val = os.getenv(env_key, default)
    p = Path(val)
    return p if p.is_absolute() else (ROOT / val).resolve()


def load_config() -> Config:
    provider = os.getenv("LLM_PROVIDER", "openai_compat")
    if provider not in ("openai_compat", "anthropic"):
        raise ValueError(f"LLM_PROVIDER must be 'openai_compat' or 'anthropic', got: {provider}")

    if provider == "openai_compat":
        llm_api_key = os.getenv("LLM_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
        if not llm_api_key:
            raise ValueError(
                "LLM_PROVIDER=openai_compat requires LLM_API_KEY or DEEPSEEK_API_KEY to be set in .env"
            )
    else:
        llm_api_key = ""

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
    if provider == "anthropic" and not (anthropic_api_key or anthropic_auth_token):
        raise ValueError(
            "LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY or "
            "ANTHROPIC_AUTH_TOKEN to be set in .env"
        )

    anthropic_main_thinking_mode = os.getenv(
        "ANTHROPIC_MAIN_THINKING_MODE", "legacy",
    ).strip().lower()
    if anthropic_main_thinking_mode not in {"legacy", "adaptive"}:
        raise ValueError(
            "ANTHROPIC_MAIN_THINKING_MODE must be 'legacy' or 'adaptive'"
        )
    anthropic_main_effort = os.getenv("ANTHROPIC_MAIN_EFFORT", "").strip().lower() or None
    if anthropic_main_effort is not None and anthropic_main_effort not in {
        "low", "medium", "high", "xhigh", "max",
    }:
        raise ValueError(
            "ANTHROPIC_MAIN_EFFORT must be low, medium, high, xhigh, or max"
        )
    if anthropic_main_thinking_mode == "legacy" and anthropic_main_effort is not None:
        raise ValueError("ANTHROPIC_MAIN_EFFORT is only valid with adaptive thinking")
    if anthropic_main_thinking_mode == "adaptive" and anthropic_main_effort is None:
        raise ValueError("ANTHROPIC_MAIN_EFFORT is required with adaptive thinking")
    anthropic_main_tool_choice_strategy = os.getenv(
        "ANTHROPIC_MAIN_TOOL_CHOICE_STRATEGY", "forced",
    ).strip().lower()
    if anthropic_main_tool_choice_strategy not in {"forced", "submit_only_auto"}:
        raise ValueError(
            "ANTHROPIC_MAIN_TOOL_CHOICE_STRATEGY must be 'forced' or 'submit_only_auto'"
        )

    trace_provider = os.getenv("TRACE_PROVIDER") or os.getenv("JARVIS_TRACE_PROVIDER") or "none"
    if trace_provider not in ("none", "langfuse"):
        raise ValueError(f"TRACE_PROVIDER must be 'none' or 'langfuse', got: {trace_provider}")
    vision_provider = os.getenv("VISION_PROVIDER", "inherit")
    if vision_provider not in ("inherit", "openai_compat", "anthropic"):
        raise ValueError(
            "VISION_PROVIDER must be 'inherit', 'openai_compat', or 'anthropic', "
            f"got: {vision_provider}"
        )
    attachment_archive_raw = os.getenv("ATTACHMENT_ARCHIVE_ENABLED")
    if attachment_archive_raw not in (None, "") and attachment_archive_raw.strip().lower() not in {
        "1", "true", "yes", "y", "on", "0", "false", "no", "n", "off",
    }:
        raise ValueError("attachment_archive_enabled_invalid")
    attachment_archive_enabled = _parse_bool(attachment_archive_raw, default=False)
    archive_handoff_raw = os.getenv("ATTACHMENT_ARCHIVE_HANDOFF_SECONDS", "604800")
    try:
        attachment_archive_handoff_seconds = int(archive_handoff_raw)
    except ValueError as exc:
        raise ValueError("attachment_archive_handoff_seconds_invalid") from exc
    if not 86400 <= attachment_archive_handoff_seconds <= 604800:
        raise ValueError("attachment_archive_handoff_seconds_invalid")
    database_url = os.getenv("DATABASE_URL", "")
    if attachment_archive_enabled and not database_url:
        raise ValueError("attachment_archive_requires_database_url")

    platform_task_enabled_raw = os.getenv("PLATFORM_TASK_ENABLED")
    if platform_task_enabled_raw not in (None, "") and platform_task_enabled_raw.strip().lower() not in {
        "1", "true", "yes", "y", "on", "0", "false", "no", "n", "off",
    }:
        raise ValueError("platform_task_enabled_invalid")
    platform_task_enabled = _parse_bool(platform_task_enabled_raw, default=False)
    capability_raw = os.getenv("PLATFORM_TASK_CAPABILITY_VERSION", "2")
    try:
        platform_task_capability_version = int(capability_raw)
    except ValueError as exc:
        raise ValueError("platform_task_capability_version_invalid") from exc
    if not 1 <= platform_task_capability_version <= 2_147_483_647:
        raise ValueError("platform_task_capability_version_invalid")
    platform_task_public_key_paths: tuple[tuple[str, Path], ...] = ()
    platform_task_content_keyring_file: Path | None = None
    if platform_task_enabled:
        if not database_url:
            raise ValueError("platform_task_requires_database_url")
        try:
            public_paths_raw = json.loads(
                os.getenv("PLATFORM_TASK_PUBLIC_KEY_PATHS_JSON", "")
            )
            if not isinstance(public_paths_raw, dict) or not public_paths_raw:
                raise ValueError
            parsed_paths = []
            for kid, raw_path in public_paths_raw.items():
                path = Path(raw_path)
                if (
                    not isinstance(kid, str)
                    or not kid
                    or not isinstance(raw_path, str)
                    or not path.is_absolute()
                ):
                    raise ValueError
                parsed_paths.append((kid, path))
            platform_task_public_key_paths = tuple(sorted(parsed_paths))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("platform_task_public_keys_invalid") from None
        content_keyring_raw = os.getenv("PLATFORM_TASK_CONTENT_KEYRING_FILE", "")
        content_keyring_path = Path(content_keyring_raw)
        if not content_keyring_raw or not content_keyring_path.is_absolute():
            raise ValueError("platform_task_content_keyring_invalid")
        platform_task_content_keyring_file = content_keyring_path

    platform_identity_raw = os.getenv("PLATFORM_IDENTITY_ENABLED")
    enterprise_identity_raw = os.getenv("PLATFORM_ENTERPRISE_IDENTITY_ENABLED")
    valid_boolean_values = {
        "1", "true", "yes", "y", "on", "0", "false", "no", "n", "off",
    }
    if (
        platform_identity_raw is not None
        and platform_identity_raw.strip().lower() not in valid_boolean_values
    ):
        raise ValueError("platform_identity_enabled_invalid")
    canonical_present = platform_identity_raw is not None
    legacy_present = enterprise_identity_raw not in (None, "")
    if (
        legacy_present
        and enterprise_identity_raw.strip().lower() not in valid_boolean_values
    ):
        # A deployment still on the legacy variable alone keeps its stable code.
        raise ValueError(
            "platform_identity_enabled_invalid"
            if canonical_present
            else "platform_enterprise_identity_enabled_invalid"
        )
    if canonical_present and legacy_present:
        canonical_enabled = _parse_bool(platform_identity_raw, default=False)
        legacy_enabled = _parse_bool(enterprise_identity_raw, default=False)
        if canonical_enabled != legacy_enabled:
            raise ValueError("platform_identity_enablement_conflict")
        platform_identity_enabled = canonical_enabled
    elif canonical_present:
        platform_identity_enabled = _parse_bool(
            platform_identity_raw, default=False
        )
    else:
        platform_identity_enabled = _parse_bool(
            enterprise_identity_raw, default=False
        )
    partner_auth_start_raw = os.getenv("PLATFORM_PARTNER_AUTH_START_URL")
    if partner_auth_start_raw in (None, ""):
        platform_partner_auth_start_url = None
    elif (
        partner_auth_start_raw
        == "https://agent.orbbec.com.cn/partner-auth/start"
    ):
        platform_partner_auth_start_url = partner_auth_start_raw
    else:
        raise ValueError("platform_partner_auth_start_url_invalid")
    platform_partner_provider_kind = (
        os.getenv("PLATFORM_PARTNER_PROVIDER_KIND", "").strip() or None
    )
    release_file_raw = os.getenv("PLATFORM_PARTNER_PROVIDER_RELEASE_FILE", "").strip()
    if release_file_raw:
        platform_partner_provider_release_file = Path(release_file_raw)
        if not platform_partner_provider_release_file.is_absolute():
            raise ValueError("platform_partner_release_file_invalid")
    else:
        platform_partner_provider_release_file = None
    platform_partner_provider_release_sha256 = (
        os.getenv("PLATFORM_PARTNER_PROVIDER_RELEASE_SHA256", "").strip() or None
    )
    trace_environment = (
        os.getenv("TRACE_ENVIRONMENT")
        or os.getenv("JARVIS_TRACE_ENVIRONMENT")
        or "development"
    )
    if trace_environment == "production":
        from src.platform_identity.partner_release import resolve_partner_login

        partner_gate = resolve_partner_login(
            start_url=platform_partner_auth_start_url,
            provider_kind=platform_partner_provider_kind,
            release_file=(
                str(platform_partner_provider_release_file)
                if platform_partner_provider_release_file is not None
                else None
            ),
            release_sha256=platform_partner_provider_release_sha256,
        )
        platform_partner_auth_start_url = partner_gate.start_url
        platform_partner_login_reason = partner_gate.reason
    elif trace_environment in {"development", "test"}:
        platform_partner_login_reason = (
            "partner_login_not_configured"
            if platform_partner_auth_start_url is None
            else "partner_release_not_required_outside_production"
        )
    else:
        # Trace environment is historically free-form, so keep reporting the
        # configured value while treating unknown deployment modes as hostile
        # for the production Partner-login boundary.
        platform_partner_auth_start_url = None
        platform_partner_login_reason = "partner_environment_invalid"
    if platform_partner_auth_start_url is not None and not platform_identity_enabled:
        platform_partner_auth_start_url = None
        platform_partner_login_reason = "partner_identity_required"
    platform_enterprise_session_keyring_file: Path | None = None
    platform_authenticated_content_keyring_file: Path | None = None
    cache_raw = os.getenv("PLATFORM_ENTERPRISE_VALIDATION_CACHE_SECONDS", "60")
    try:
        platform_enterprise_validation_cache_seconds = int(cache_raw)
    except ValueError as exc:
        raise ValueError("platform_enterprise_validation_cache_invalid") from exc
    if not 0 <= platform_enterprise_validation_cache_seconds <= 60:
        raise ValueError("platform_enterprise_validation_cache_invalid")
    platform_enterprise_idle_ttl_seconds = _parse_positive_int(
        os.getenv("PLATFORM_ENTERPRISE_IDLE_TTL_SECONDS"), default=8 * 60 * 60
    )
    platform_enterprise_absolute_ttl_seconds = _parse_positive_int(
        os.getenv("PLATFORM_ENTERPRISE_ABSOLUTE_TTL_SECONDS"), default=24 * 60 * 60
    )
    if platform_enterprise_absolute_ttl_seconds < platform_enterprise_idle_ttl_seconds:
        raise ValueError("platform_enterprise_session_ttl_invalid")
    if platform_identity_enabled:
        if not database_url:
            raise ValueError("platform_enterprise_identity_requires_database_url")
        keyring_raw = os.getenv("PLATFORM_ENTERPRISE_SESSION_KEYRING_FILE", "")
        keyring_path = Path(keyring_raw)
        if not keyring_raw or not keyring_path.is_absolute():
            raise ValueError("platform_enterprise_session_keyring_invalid")
        platform_enterprise_session_keyring_file = keyring_path
        conversation_keyring_raw = os.getenv(
            "PLATFORM_AUTHENTICATED_CONTENT_KEYRING_FILE", ""
        )
        conversation_keyring_path = Path(conversation_keyring_raw)
        if not conversation_keyring_raw or not conversation_keyring_path.is_absolute():
            raise ValueError("platform_authenticated_content_keyring_invalid")
        # Conversation content is sealed with its own key material: reusing the
        # browser session key or the Platform task content key would let one
        # compromised secret decrypt a second, unrelated class of data.
        if conversation_keyring_path in {
            keyring_path,
            platform_task_content_keyring_file,
        }:
            raise ValueError("platform_authenticated_content_keyring_conflict")
        platform_authenticated_content_keyring_file = conversation_keyring_path

    return Config(
        llm_provider=provider,  # type: ignore
        llm_api_key=llm_api_key,
        llm_base_url=os.getenv("LLM_BASE_URL", ""),
        llm_model_main=os.getenv("LLM_MODEL_MAIN", "glm-5.1"),
        llm_model_fast=os.getenv("LLM_MODEL_FAST", "glm-5.1"),

        anthropic_api_key=anthropic_api_key,
        anthropic_auth_token=anthropic_auth_token,
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL", ""),
        anthropic_model_main=os.getenv("ANTHROPIC_MODEL_MAIN", "claude-sonnet-4-6"),
        anthropic_model_fast=os.getenv("ANTHROPIC_MODEL_FAST", "claude-haiku-4-5"),
        anthropic_main_thinking_mode=anthropic_main_thinking_mode,
        anthropic_main_effort=anthropic_main_effort,
        anthropic_main_tool_choice_strategy=anthropic_main_tool_choice_strategy,

        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        embed_model=os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-large"),

        chroma_qa_path=_path("CHROMA_QA_PATH", "data/chroma_qa"),
        chroma_product_path=_path("CHROMA_PRODUCT_PATH", "data/chroma_product"),
        knowledge_dir=_path("KNOWLEDGE_DIR", "Knowledge"),
        knowledge_qa_dir=_path("KNOWLEDGE_QA_DIR", "Knowledge_QA"),
        knowledge_sdk_dir=_path("KNOWLEDGE_SDK_DIR", "Knowledge_SDK"),
        knowledge_links_dir=_path("KNOWLEDGE_LINKS_DIR", "Knowledge_Links"),
        prompts_dir=_path("PROMPTS_DIR", "prompts"),
        log_dir=_path("LOG_DIR", "data/logs"),
        enable_qa_kb=_parse_bool(os.getenv("ENABLE_QA_KB"), default=False),
        database_url=database_url,
        data_flywheel_fallback_path=_path(
            "DATA_FLYWHEEL_FALLBACK_PATH",
            str(Path(os.getenv("LOG_DIR", "data/logs")) / "data_flywheel_fallback.jsonl"),
        ),
        platform_task_enabled=platform_task_enabled,
        platform_task_capability_version=platform_task_capability_version,
        platform_task_public_key_paths=platform_task_public_key_paths,
        platform_task_content_keyring_file=platform_task_content_keyring_file,
        platform_identity_enabled=platform_identity_enabled,
        platform_identity_base_url=os.getenv(
            "PLATFORM_IDENTITY_BASE_URL", "http://127.0.0.1:8080"
        ),
        platform_enterprise_session_keyring_file=(
            platform_enterprise_session_keyring_file
        ),
        platform_authenticated_content_keyring_file=(
            platform_authenticated_content_keyring_file
        ),
        platform_enterprise_validation_cache_seconds=(
            platform_enterprise_validation_cache_seconds
        ),
        platform_enterprise_idle_ttl_seconds=platform_enterprise_idle_ttl_seconds,
        platform_enterprise_absolute_ttl_seconds=(
            platform_enterprise_absolute_ttl_seconds
        ),
        platform_enterprise_public_origin=os.getenv(
            "PLATFORM_ENTERPRISE_PUBLIC_ORIGIN", "https://fae.orbbec.com.cn"
        ),
        platform_partner_auth_start_url=platform_partner_auth_start_url,
        platform_partner_provider_kind=platform_partner_provider_kind,
        platform_partner_provider_release_file=platform_partner_provider_release_file,
        platform_partner_provider_release_sha256=(
            platform_partner_provider_release_sha256
        ),
        platform_partner_login_reason=platform_partner_login_reason,

        trace_provider=trace_provider,
        trace_sample_rate=float(os.getenv("TRACE_SAMPLE_RATE") or os.getenv("JARVIS_TRACE_SAMPLE_RATE") or "1"),
        trace_log_path=_path("TRACE_LOG_PATH", "data/logs/traces.jsonl"),
        trace_environment=trace_environment,
        trace_release=os.getenv("TRACE_RELEASE") or os.getenv("JARVIS_TRACE_RELEASE") or "local",
        langfuse_base_url=os.getenv("LANGFUSE_BASE_URL") or "http://localhost:3001",
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY") or "",
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY") or "",

        session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", "3600")),
        chat_max_concurrent_agent_requests=_parse_positive_int(
            os.getenv("CHAT_MAX_CONCURRENT_AGENT_REQUESTS"),
            default=4,
        ),
        attachment_storage_dir=_path(
            "ATTACHMENT_STORAGE_DIR", "data/attachments",
        ),
        attachment_ttl_seconds=_parse_positive_int(
            os.getenv("ATTACHMENT_TTL_SECONDS"), default=86400,
        ),
        attachment_max_files=_parse_positive_int(
            os.getenv("ATTACHMENT_MAX_FILES"), default=5,
        ),
        attachment_max_batch_bytes=_parse_positive_int(
            os.getenv("ATTACHMENT_MAX_BATCH_BYTES"), default=50 * 1024 * 1024,
        ),
        attachment_storage_capacity_bytes=_parse_positive_int(
            os.getenv("ATTACHMENT_STORAGE_CAPACITY_BYTES"),
            default=5 * 1024 * 1024 * 1024,
        ),
        attachment_upload_max_concurrent=_parse_positive_int(
            os.getenv("ATTACHMENT_UPLOAD_MAX_CONCURRENT"), default=2,
        ),
        attachment_upload_batches_per_minute=_parse_positive_int(
            os.getenv("ATTACHMENT_UPLOAD_BATCHES_PER_MINUTE"), default=5,
        ),
        attachment_trusted_proxy_hosts=tuple(
            item.strip()
            for item in os.getenv("ATTACHMENT_TRUSTED_PROXY_HOSTS", "127.0.0.1,::1").split(",")
            if item.strip()
        ),
        attachment_archive_enabled=attachment_archive_enabled,
        attachment_archive_handoff_seconds=attachment_archive_handoff_seconds,
        vision_enabled=_parse_bool(os.getenv("VISION_ENABLED"), default=True),
        vision_provider=vision_provider,  # type: ignore[arg-type]
        vision_api_key=os.getenv("VISION_API_KEY", ""),
        vision_auth_token=os.getenv("VISION_AUTH_TOKEN", ""),
        vision_base_url=os.getenv("VISION_BASE_URL", ""),
        vision_model=os.getenv("VISION_MODEL", ""),
        vision_timeout_seconds=_parse_positive_int(
            os.getenv("VISION_TIMEOUT_SECONDS"), default=60,
        ),

        composition_mode=_parse_composition_mode(os.getenv("COMPOSITION_MODE")),
        loop_max_tool_calls=_parse_positive_int(
            os.getenv("LOOP_MAX_TOOL_CALLS"), default=24,
        ),
        loop_max_duration_s=_parse_positive_int(
            os.getenv("LOOP_MAX_DURATION_S"), default=300,
        ),
        loop_max_output_tokens=_parse_positive_int(
            os.getenv("LOOP_MAX_OUTPUT_TOKENS"), default=8192,
        ),
        loop_provider_retry_attempts=_parse_nonnegative_int(
            os.getenv("LOOP_PROVIDER_RETRY_ATTEMPTS"), default=2,
        ),
        loop_provider_retry_backoff_s=_parse_positive_float(
            os.getenv("LOOP_PROVIDER_RETRY_BACKOFF_S"), default=1.5,
        ),
        loop_provider_retry_max_delay_s=_parse_positive_float(
            os.getenv("LOOP_PROVIDER_RETRY_MAX_DELAY_S"), default=15.0,
        ),
        public_reasoning_enabled=_parse_bool(
            os.getenv("PUBLIC_REASONING_ENABLED"),
            default=True,
        ),
        public_reasoning_language=os.getenv("PUBLIC_REASONING_LANGUAGE", "zh"),
        public_reasoning_max_steps=_parse_positive_int(
            os.getenv("PUBLIC_REASONING_MAX_STEPS"),
            default=4,
        ),
    )


def resolve_vision_config(config: Config) -> ResolvedVisionConfig:
    """Resolve one complete vision profile without cross-profile credential mixing."""
    if not config.vision_enabled:
        return ResolvedVisionConfig(
            enabled=False,
            mode="disabled",
            provider=None,
            api_key="",
            auth_token="",
            base_url="",
            model="",
            timeout_seconds=config.vision_timeout_seconds,
        )

    if config.vision_provider == "inherit":
        if config.llm_provider == "anthropic":
            resolved = ResolvedVisionConfig(
                enabled=True,
                mode="inherit",
                provider="anthropic",
                api_key=config.anthropic_api_key,
                auth_token=config.anthropic_auth_token,
                base_url=config.anthropic_base_url,
                model=config.anthropic_model_main,
                timeout_seconds=config.vision_timeout_seconds,
                tool_choice_strategy=config.anthropic_main_tool_choice_strategy,
            )
        else:
            resolved = ResolvedVisionConfig(
                enabled=True,
                mode="inherit",
                provider="openai_compat",
                api_key=config.llm_api_key,
                auth_token="",
                base_url=config.llm_base_url,
                model=config.llm_model_main,
                timeout_seconds=config.vision_timeout_seconds,
            )
    else:
        resolved = ResolvedVisionConfig(
            enabled=True,
            mode="dedicated",
            provider=config.vision_provider,
            api_key=config.vision_api_key,
            auth_token=config.vision_auth_token,
            base_url=config.vision_base_url,
            model=config.vision_model,
            timeout_seconds=config.vision_timeout_seconds,
            tool_choice_strategy=(
                config.anthropic_main_tool_choice_strategy
                if config.vision_provider == "anthropic"
                and config.vision_model == config.anthropic_model_main
                else "forced"
            ),
        )

    profile = "inherited" if resolved.mode == "inherit" else "dedicated"
    if not resolved.model:
        model_name = (
            "VISION_MODEL"
            if resolved.mode == "dedicated"
            else "ANTHROPIC_MODEL_MAIN or LLM_MODEL_MAIN"
        )
        raise ValueError(f"{profile} vision profile requires {model_name}")
    if not resolved.base_url:
        base_name = (
            "VISION_BASE_URL"
            if resolved.mode == "dedicated"
            else "ANTHROPIC_BASE_URL or LLM_BASE_URL"
        )
        raise ValueError(f"{profile} vision profile requires {base_name}")
    if resolved.provider == "openai_compat" and not resolved.api_key:
        key_name = "LLM_API_KEY" if resolved.mode == "inherit" else "VISION_API_KEY"
        raise ValueError(f"{profile} vision profile requires {key_name}")
    if resolved.provider == "anthropic" and not (
        resolved.api_key or resolved.auth_token
    ):
        key_names = (
            "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN"
            if resolved.mode == "inherit"
            else "VISION_API_KEY or VISION_AUTH_TOKEN"
        )
        raise ValueError(f"{profile} vision profile requires {key_names}")
    return resolved


def _parse_composition_mode(raw: str | None) -> str:
    """COMPOSITION_MODE 解析:loop(默认,M5 20260709 翻默认)|pipeline|shadow;
    空/非法回落 loop——循环骨架已是主路径(434 全量 397/434,语义 worse -66%)。"""
    val = (raw or "").strip().lower()
    return val if val in ("pipeline", "loop", "shadow") else "loop"


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_positive_int(raw: str | None, *, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _parse_nonnegative_int(raw: str | None, *, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _parse_positive_float(raw: str | None, *, default: float) -> float:
    if raw is None or raw == "":
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else default
