"""FastAPI 应用入口。"""
import json
import os
import shutil
import socket

from fastapi import FastAPI

from src.agent.official_links import OfficialLinkCatalog
from src.agent.orchestrator import Orchestrator
from src.agent.session import SessionStore
from src.agent.tech_path_rules import TECH_PATH_RULES
from src.agent.tracing import TraceConfig, build_recorder
from src.api.attachment_limits import AttachmentUploadGate
from src.api.attachment_routes import register_attachment_routes
from src.api.concurrency import ChatConcurrencyGate
from src.api.review_routes import register_review_routes
from src.api.routes import register_routes
from src.api.webui import mount_webui
from src.attachments.archive_service import AttachmentArchiveService
from src.attachments.models import AttachmentLimits
from src.attachments.service import AttachmentService
from src.attachments.store import AttachmentGCWorker, AttachmentStore
from src.attachments.vision import build_vision_adapter
from src.build_identity import load_build_identity
from src.config import load_config, resolve_vision_config
from src.data.product_loader import load_product_catalog
from src.facts.coverage import load_field_coverage
from src.facts.extract import scan_documented_field_models
from src.facts.model_coverage import (
    audit_model_source_coverage,
    load_model_source_coverage,
)
from src.facts.resolver import load_catalog as load_facts_catalog
from src.facts.schema import load_field_dictionary
from src.facts.store import load_fact_store
from src.index.chroma_client import get_chroma_client
from src.platform_identity.client import PlatformIdentityClient
from src.platform_identity.routes import register_platform_identity_routes
from src.platform_identity.service import (
    AuthenticatedSessionService,
    PostgresAuthenticatedSessionRepository,
    SessionTokenKeyring,
)
from src.platform_tasks.crypto import TaskContentCodec
from src.platform_tasks.identity import TaskTokenVerifier
from src.platform_tasks.postgres_store import PostgresPlatformTaskStore
from src.platform_tasks.routes import (
    PlatformTaskCapabilities,
    register_platform_task_routes,
)
from src.platform_tasks.worker import PlatformTaskWorker
from src.storage.authenticated_conversations import (
    AuthenticatedConversationRepository,
    ConversationContentCodec,
)
from src.storage.factory import (
    build_attachment_archive_repository,
    build_data_flywheel_store,
    build_review_center_store,
)


def create_app() -> FastAPI:
    cfg = load_config()
    vision_config = resolve_vision_config(cfg)
    build_identity = load_build_identity()
    app = FastAPI(title="AI FAE Agent", version="0.1.0")

    product_catalog = load_product_catalog(cfg.knowledge_dir)
    # 事实矩阵与 curated 目录:装载失败即配置错误,显式抛出,不做静默降级。
    facts_dir = cfg.knowledge_dir / "_facts"
    field_dictionary = load_field_dictionary(facts_dir / "field_dictionary.yaml")
    fact_store = load_fact_store(cfg.knowledge_dir, field_dictionary)
    field_coverage = load_field_coverage(
        facts_dir / "field_coverage.yaml",
        field_dictionary,
        fact_store,
        documented_field_models=scan_documented_field_models(
            cfg.knowledge_dir, field_dictionary
        ),
    )
    model_resolver = load_facts_catalog(facts_dir / "catalog.yaml", cfg.knowledge_dir)
    model_source_coverage = load_model_source_coverage(
        facts_dir / "model_source_coverage.yaml",
        known_models=model_resolver.entries,
    )
    model_source_freshness = audit_model_source_coverage(model_source_coverage)
    # 权威链接知识层是回答契约的一部分:目录、审计或 verified 记录不合法时
    # 启动必须失败，禁止静默退化为模型猜测 URL。
    official_link_catalog = OfficialLinkCatalog.load(cfg.knowledge_links_dir)
    qa_coll = None
    if cfg.enable_qa_kb:
        qa_client = get_chroma_client(cfg.chroma_qa_path)
        try:
            qa_coll = qa_client.get_collection("qa_kb")
        except Exception:
            qa_coll = None

    # SDK 反推知识库规模:启动时读一次 manifest,供 /health 展示(读不到则为 0)。
    sdk_records = 0
    sdk_repos = 0
    try:
        _manifest = json.loads(
            (cfg.knowledge_sdk_dir / "SDK_Knowledge_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        sdk_records = int(_manifest.get("record_total", 0))
        sdk_repos = len(_manifest.get("repositories", []))
    except Exception:
        pass

    session_store = SessionStore(ttl_seconds=cfg.session_ttl_seconds)
    attachment_limits = AttachmentLimits(
        ttl_seconds=cfg.attachment_ttl_seconds,
        max_files=cfg.attachment_max_files,
        max_batch_bytes=cfg.attachment_max_batch_bytes,
        storage_capacity_bytes=cfg.attachment_storage_capacity_bytes,
    )
    attachment_store = AttachmentStore(cfg.attachment_storage_dir, attachment_limits)
    attachment_archive_repository = build_attachment_archive_repository(cfg)
    attachment_store.gc_expired(attachment_archive_repository)
    attachment_gc_worker = AttachmentGCWorker(
        attachment_store,
        lifecycle=attachment_archive_repository,
    )
    attachment_service = AttachmentService(attachment_store, attachment_limits)
    attachment_archive_service = AttachmentArchiveService(
        attachment_store,
        enabled=cfg.attachment_archive_enabled,
        handoff_seconds=cfg.attachment_archive_handoff_seconds,
        repository=attachment_archive_repository,
    )
    vision_adapter = build_vision_adapter(vision_config)
    trace_recorder = build_recorder(TraceConfig(
        provider=cfg.trace_provider,  # type: ignore[arg-type]
        sample_rate=cfg.trace_sample_rate,
        log_path=cfg.trace_log_path,
        environment=cfg.trace_environment,
        release=cfg.trace_release,
        langfuse_base_url=cfg.langfuse_base_url,
        langfuse_public_key=cfg.langfuse_public_key,
        langfuse_secret_key=cfg.langfuse_secret_key,
    ))
    data_flywheel_store = build_data_flywheel_store(cfg)
    review_center_store = build_review_center_store(cfg)
    if cfg.llm_provider == "anthropic":
        llm_api_key = cfg.anthropic_api_key
        llm_base_url = cfg.anthropic_base_url
        llm_model_main = cfg.anthropic_model_main
        llm_model_fast = cfg.anthropic_model_fast
        # T4(20260713):Bearer 网关凭证注入 llm_client(schema/clarify/planner
        # 语义面共用);loop adapter 另有自己的 auth_token 参数,两路对齐
        from src.agent.llm_client import set_anthropic_auth_token
        set_anthropic_auth_token(cfg.anthropic_auth_token)
    else:
        llm_api_key = cfg.llm_api_key
        llm_base_url = cfg.llm_base_url
        llm_model_main = cfg.llm_model_main
        llm_model_fast = cfg.llm_model_fast
    orchestrator = Orchestrator(
        qa_collection=qa_coll,
        product_catalog=product_catalog,
        tech_path_rules=TECH_PATH_RULES,
        prompts_dir=cfg.prompts_dir,
        provider=cfg.llm_provider,
        api_key=llm_api_key,
        llm_base_url=llm_base_url,
        model_main=llm_model_main,
        model_fast=llm_model_fast,
        embed_model=cfg.embed_model,
        session_store=session_store,
        openai_api_key=cfg.openai_api_key,
        anthropic_auth_token=cfg.anthropic_auth_token,
        anthropic_main_thinking_mode=cfg.anthropic_main_thinking_mode,
        anthropic_main_effort=cfg.anthropic_main_effort,
        anthropic_main_tool_choice_strategy=cfg.anthropic_main_tool_choice_strategy,
        composition_mode=cfg.composition_mode,
        loop_max_tool_calls=cfg.loop_max_tool_calls,
        loop_max_duration_s=cfg.loop_max_duration_s,
        loop_max_output_tokens=cfg.loop_max_output_tokens,
        loop_provider_retry_attempts=cfg.loop_provider_retry_attempts,
        loop_provider_retry_backoff_s=cfg.loop_provider_retry_backoff_s,
        loop_provider_retry_max_delay_s=cfg.loop_provider_retry_max_delay_s,
        knowledge_dir=cfg.knowledge_dir,
        knowledge_sdk_dir=cfg.knowledge_sdk_dir,
        knowledge_links_dir=cfg.knowledge_links_dir,
        official_link_catalog=official_link_catalog,
        qa_enabled=cfg.enable_qa_kb,
        public_reasoning_enabled=cfg.public_reasoning_enabled,
        public_reasoning_language=cfg.public_reasoning_language,
        public_reasoning_max_steps=cfg.public_reasoning_max_steps,
        fact_store=fact_store,
        model_resolver=model_resolver,
        attachment_store=attachment_store,
        vision_adapter=vision_adapter,
    )

    app.state.orchestrator = orchestrator
    app.state.session_store = session_store
    app.state.trace_recorder = trace_recorder
    app.state.data_flywheel_store = data_flywheel_store
    app.state.review_center_store = review_center_store
    app.state.chat_concurrency_gate = ChatConcurrencyGate(
        cfg.chat_max_concurrent_agent_requests,
    )
    app.state.attachment_store = attachment_store
    app.state.attachment_gc_worker = attachment_gc_worker
    app.state.attachment_service = attachment_service
    app.state.attachment_archive_service = attachment_archive_service
    app.state.attachment_upload_gate = AttachmentUploadGate(
        max_concurrent=cfg.attachment_upload_max_concurrent,
        per_ip_batches_per_minute=cfg.attachment_upload_batches_per_minute,
        trusted_proxy_hosts=cfg.attachment_trusted_proxy_hosts,
    )
    app.state.model_source_coverage = model_source_coverage
    app.state.log_dir = cfg.log_dir
    app.state.build_identity = build_identity
    if cfg.platform_identity_enabled:
        if cfg.platform_enterprise_session_keyring_file is None:
            raise RuntimeError("platform_enterprise_session_keyring_missing")
        if cfg.platform_authenticated_content_keyring_file is None:
            raise RuntimeError("platform_authenticated_content_keyring_missing")
        platform_identity_client = PlatformIdentityClient(
            cfg.platform_identity_base_url, agent_id=cfg.agent_id
        )
        authenticated_session_service = AuthenticatedSessionService(
            agent_id=cfg.agent_id,
            repository=PostgresAuthenticatedSessionRepository(cfg.database_url),
            platform_client=platform_identity_client,
            token_keyring=SessionTokenKeyring.from_file(
                cfg.platform_enterprise_session_keyring_file
            ),
            validation_cache_seconds=(
                cfg.platform_enterprise_validation_cache_seconds
            ),
            idle_ttl_seconds=cfg.platform_enterprise_idle_ttl_seconds,
            absolute_ttl_seconds=cfg.platform_enterprise_absolute_ttl_seconds,
            # Tasks 7/8 wired generic subject ownership (chat continuation,
            # list/detail, Feedback, and flywheel owner columns), so partner
            # subjects can now be issued FAE sessions.
            partner_subject_ownership_ready=True,
        )
        app.state.authenticated_session_service = authenticated_session_service
        app.state.enterprise_session_service = authenticated_session_service
        # Durable generic conversation ownership (Task 7), read by the Task 8
        # owner-scoped routes: chat continuation, conversation list/detail and
        # feedback target resolution all fail closed without it.
        app.state.authenticated_conversation_repository = (
            AuthenticatedConversationRepository(
                cfg.database_url,
                codec=ConversationContentCodec.from_file(
                    cfg.platform_authenticated_content_keyring_file
                ),
            )
        )
        register_platform_identity_routes(
            app,
            service=authenticated_session_service,
            public_origin=cfg.platform_enterprise_public_origin,
            additional_browser_origins=("https://agent.orbbec.com.cn",),
            partner_auth_start_url=cfg.platform_partner_auth_start_url,
        )
        app.router.add_event_handler("shutdown", platform_identity_client.aclose)
    if cfg.platform_task_enabled:
        if cfg.platform_task_content_keyring_file is None:
            raise RuntimeError("platform_task_content_keyring_missing")
        platform_task_codec = TaskContentCodec.from_file(
            cfg.platform_task_content_keyring_file
        )
        platform_task_store = PostgresPlatformTaskStore(
            cfg.database_url,
            codec=platform_task_codec,
        )
        platform_task_verifier = TaskTokenVerifier.from_files(
            dict(cfg.platform_task_public_key_paths),
            audience=cfg.agent_id,
        )
        platform_task_capabilities = PlatformTaskCapabilities.fae_v1(
            capability_version=cfg.platform_task_capability_version,
            agent_id=cfg.agent_id,
        )
        app.state.platform_task_store = platform_task_store
        app.state.platform_task_capabilities = platform_task_capabilities
        platform_task_worker = PlatformTaskWorker(
            store=platform_task_store,
            orchestrator=orchestrator,
            session_store=session_store,
            worker_id=f"{socket.gethostname()}:{os.getpid()}",
        )
        app.state.platform_task_worker = platform_task_worker
        register_platform_task_routes(
            app,
            store=platform_task_store,
            verifier=platform_task_verifier,
            capabilities=platform_task_capabilities,
        )
        app.router.add_event_handler("startup", platform_task_worker.start)
        app.router.add_event_handler("shutdown", platform_task_worker.stop)
    app.router.add_event_handler("startup", attachment_gc_worker.start)
    app.router.add_event_handler("shutdown", attachment_gc_worker.stop)

    @app.get("/health")
    def health():
        try:
            qa_count = qa_coll.count() if qa_coll is not None else 0
        except Exception:
            qa_count = 0
        attachment_status = attachment_store.storage_status()
        archive_health = {
            "enabled": cfg.attachment_archive_enabled,
            "ready": True,
            "pending": 0,
            "failed": 0,
            "oldest_pending_seconds": 0,
            "expired_unarchived_total": 0,
        }
        if attachment_archive_repository is not None:
            try:
                archive_health.update(attachment_archive_repository.health())
            except Exception:
                archive_health["ready"] = False
        model_source_summary = model_source_coverage.summary()
        model_source_summary["freshness_expired"] = len(model_source_freshness)
        build = {
            "available": build_identity is not None,
            "release_name": (
                build_identity.release_name if build_identity is not None else ""
            ),
            "git_sha": build_identity.git_sha if build_identity is not None else "",
            "source": (
                build_identity.source if build_identity is not None else "unavailable"
            ),
        }
        return {
            "status": "ok",
            "build": build,
            "environment": cfg.trace_environment,
            "qa_enabled": cfg.enable_qa_kb,
            "qa_indexed": bool(cfg.enable_qa_kb and qa_coll is not None),
            "qa_count": qa_count,
            "products_loaded": len(product_catalog),
            "fact_matrix_models": sum(
                1 for m in fact_store.known_models if fact_store.rows(m)
            ),
            "fact_fields_source_complete": field_coverage.source_complete_count,
            "model_source_coverage": model_source_summary,
            "sdk_records": sdk_records,
            "sdk_repos": sdk_repos,
            "official_links_verified": official_link_catalog.verified_count,
            "llm_model": llm_model_main,
            "main_model_profile": {
                "thinking_mode": cfg.anthropic_main_thinking_mode,
                "thinking_display": (
                    "omitted"
                    if cfg.anthropic_main_thinking_mode == "adaptive"
                    else None
                ),
                "effort": cfg.anthropic_main_effort,
                "tool_choice_strategy": cfg.anthropic_main_tool_choice_strategy,
            },
            "partner_identity": {
                "login_available": cfg.platform_partner_auth_start_url is not None,
                "reason": cfg.platform_partner_login_reason,
            },
            "composition_mode": cfg.composition_mode,
            "loop_budget": {
                "max_tool_calls": cfg.loop_max_tool_calls,
                "max_duration_s": cfg.loop_max_duration_s,
                "max_output_tokens": cfg.loop_max_output_tokens,
            },
            "attachments": {
                "enabled": True,
                "storage_ready": attachment_status.ready,
                "parser_ready": True,
                "ocr_ready": shutil.which("tesseract") is not None,
                "vision_enabled": vision_adapter is not None,
                "vision_reason": (
                    "ready" if vision_adapter is not None else "disabled_by_config"
                ),
                "vision_config_mode": vision_config.mode,
                "vision_provider": (
                    vision_config.provider if vision_adapter is not None else None
                ),
                "vision_model": vision_config.model if vision_adapter is not None else None,
                "storage_used_bytes": attachment_status.used_bytes,
                "storage_capacity_bytes": attachment_status.capacity_bytes,
                "gc_deleted_total": attachment_gc_worker.deleted_total,
                "gc_failed_total": attachment_gc_worker.failed_total,
            },
            "attachment_archive": archive_health,
        }

    register_attachment_routes(app)
    register_routes(app)
    register_review_routes(app)
    mount_webui(app)

    return app


app = create_app()
