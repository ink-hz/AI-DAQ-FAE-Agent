"""DAQ Loop adapter for the shared durable Platform task worker."""
from __future__ import annotations

import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import psycopg

from src.agent.anthropic_transport import AnthropicTransportError
from src.agent.guardrails import categorize_refusal
from src.agent.loop.runtime import LoopRuntime
from src.agent.orchestrator import StreamEvent
from src.agent.protocol import RUNTIME_FAILURE_OUTCOMES
from src.platform_tasks.crypto import TaskContentCodec
from src.platform_tasks.models import TaskStoreError
from src.platform_tasks.identity import TaskTokenVerifier
from src.platform_tasks.postgres_store import PostgresPlatformTaskStore
from src.platform_tasks.routes import PlatformTaskCapabilities, register_platform_task_routes
from src.platform_tasks.worker import PlatformTaskWorker

from daq_fae.authenticated_persistence import _validate_database
from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.domain_tools import DaqToolBox
from daq_fae.empty_knowledge_synthesis import (EMPTY_KNOWLEDGE_RELEASE,
                                                 refine_empty_release_answer)
from daq_fae.platform_identity import AGENT_ID
from daq_fae.provider_errors import anthropic_failure
from daq_fae.task_context import extract_context_hints, prepare_turn


class DaqTaskOrchestrator:
    composition_mode = "loop"

    def __init__(self, *, adapter, session_store, trace_recorder=None,
                 prompt_path: Path | None = None):
        self._adapter = adapter
        self._sessions = session_store
        self._trace_recorder = trace_recorder
        self._prompt_path = prompt_path or Path(__file__).resolve().parents[1] / "prompts" / "empty_knowledge_system.md"
        self._contexts = {}

    def handle_stream(self, *, session_id, user_message,
                      planning_message=None,
                      context_hints=None,
                      required_attachment_source_ids=None,
                      required_image_source_ids=None,
                      continuation_guard=None):
        if required_attachment_source_ids or required_image_source_ids:
            raise ValueError("daq_task_attachments_not_available")
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("daq_task_session_not_found")
        guard = continuation_guard or (lambda: None)
        guard()
        trace = (self._trace_recorder.start_trace("daq_platform_task", {
            "agent_id": AGENT_ID, "session_id": session_id,
            "message_length": len(user_message),
        }) if self._trace_recorder is not None else None)
        try:
            yield StreamEvent("stage", {"stage": "daq_loop", "status": "running"})
            plan = prepare_turn(planning_message or user_message,
                                previous=self._contexts.get(session_id),
                                context_hints=context_hints)
            refusal = categorize_refusal(planning_message or user_message)
            if refusal:
                category, answer = refusal
                done = {"answer": answer, "outcome": "safe_abstained", "sources": [],
                        "planned_capabilities": [], "actual_capabilities": [],
                        "capability_coverage": {}, "refusal_category": category}
            else:
                runtime = LoopRuntime(
                    adapter=self._adapter, toolbox=DaqToolBox(context=plan.context.tool_context()),
                    system_prompt_path=self._prompt_path, evidence_policy=DaqEvidencePolicy(),
                )
                done = None
                try:
                    for event in runtime.run(
                        user_message, history=list(session.messages), entity_note=plan.context_note,
                        evidence_requirements=plan.evidence_requirements(),
                    ):
                        guard()
                        if event.get("type") == "tool_call":
                            yield StreamEvent("stage", {
                                "stage": "tool_call", "status": "running",
                                "message": str(event.get("name") or "DAQ 正在取证"),
                            })
                        elif event.get("type") == "done":
                            done = dict(event)
                    if done is None:
                        raise RuntimeError("daq_task_runtime_missing_terminal")
                except AnthropicTransportError as exc:
                    done = anthropic_failure(exc)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    outcome = ("provider_configuration_error" if status in {400, 401, 403, 404, 422}
                               else "provider_rate_limited" if status == 429
                               else "provider_unavailable" if status >= 500 else "provider_error")
                    done = {"answer": "数采模型请求失败，未生成答案。", "outcome": outcome,
                            "sources": [], "fallback_used": True,
                            "fallback_reason": f"provider_http_{status}"}
                except httpx.TransportError as exc:
                    done = {"answer": "数采模型连接失败，未生成答案。", "outcome": "provider_unavailable",
                            "sources": [], "fallback_used": True,
                            "fallback_reason": type(exc).__name__}
            guard()
            runtime_failure = (done["outcome"] in RUNTIME_FAILURE_OUTCOMES
                               or done["outcome"].startswith("provider_")
                               or done["outcome"] in {"internal_error", "persistence_error"})
            if runtime_failure:
                done["fallback_used"] = True
                done["fallback_reason"] = done.get("fallback_reason") or done["outcome"]
            else:
                done.setdefault("fallback_used", False)
                done.setdefault("fallback_reason", None)
            done.setdefault("planned_capabilities", list(plan.planned_capabilities))
            done.update({"agent_id": AGENT_ID,
                         "knowledge_release": EMPTY_KNOWLEDGE_RELEASE,
                         "session_id": session_id,
                         "trace_id": trace.trace_id if trace is not None else None})
            refine_empty_release_answer(
                done, planned_capabilities=done["planned_capabilities"],
                knowledge_release=EMPTY_KNOWLEDGE_RELEASE,
            )
            self._contexts[session_id] = plan.context
            session.append_message("user", user_message)
            session.append_message("assistant", done["answer"])
            yield StreamEvent("text_delta", {"delta": done["answer"]})
            yield StreamEvent("sources", done.get("sources", []))
            if trace is not None:
                trace.finalize({"outcome": done["outcome"],
                                "fallback_used": done["fallback_used"],
                                "fallback_reason": done["fallback_reason"],
                                "synthesis_mode": done.get("synthesis_mode"),
                                "planned_capabilities": done["planned_capabilities"],
                                "capability_coverage": done.get("capability_coverage", {}),
                                "answer_length": len(done["answer"])})
            yield StreamEvent("done", done)
        except Exception as exc:
            if trace is not None:
                trace.finalize({"outcome": "internal_error", "fallback_used": True,
                                "fallback_reason": "daq_task_execution_failed",
                                "error_type": type(exc).__name__})
            raise

    def handle_platform_task(self, *, spec, session_id, prompt,
                             planning_message, continuation_guard,
                             attachment_refs=()):
        if spec.attachment_refs or attachment_refs:
            raise TaskStoreError("task_attachment_refs_not_supported")
        return self.handle_stream(
            session_id=session_id, user_message=prompt,
            planning_message=planning_message,
            context_hints=extract_context_hints(getattr(spec, 'context_excerpt', ())),
            continuation_guard=continuation_guard,
        )


def _verify_task_database(database_url: str) -> None:
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            row = connection.execute(
                """select agent_id, database_name = current_database() as name_matches
                   from daq_installation_identity where singleton"""
            ).fetchone()
            foreign_sessions = connection.execute(
                "select exists (select 1 from chat_sessions "
                "where external_session_id not like 'daq:%')"
            ).fetchone()[0]
    except psycopg.Error:
        raise ValueError('daq_task_database_identity_unverified') from None
    if row != ('ai-daq-fae-agent', True) or foreign_sessions:
        raise ValueError('daq_task_database_identity_unverified')


def configure_platform_tasks(app, *, adapter, environ=None, task_store=None,
                             task_verifier=None, task_worker=None):
    """Register only DAQ-audience HTTP tasks with a dedicated durable queue."""
    env = os.environ if environ is None else environ
    enabled = env.get("DAQ_PLATFORM_TASK_ENABLED", "false")
    if enabled not in {"true", "false"}:
        raise ValueError("daq_platform_task_enabled_invalid")
    if enabled == "false":
        return None
    if env.get("DAQ_PLATFORM_IDENTITY_ENABLED") != "true":
        raise ValueError("daq_platform_task_requires_authenticated_mode")
    database_url = env.get("DAQ_DATABASE_URL", "")
    _validate_database(database_url, env.get("DATABASE_URL"))
    keyring = Path(env.get("DAQ_PLATFORM_TASK_CONTENT_KEYRING_FILE", ""))
    if not keyring.is_absolute():
        raise ValueError("daq_platform_task_content_keyring_invalid")
    try:
        public_keys = json.loads(env.get("DAQ_PLATFORM_TASK_PUBLIC_KEY_PATHS_JSON", ""))
        if (not isinstance(public_keys, dict) or not public_keys
                or not all(isinstance(kid, str) and isinstance(path, str)
                           and Path(path).is_absolute() for kid, path in public_keys.items())):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("daq_platform_task_public_keys_invalid") from None
    version = int(env.get("DAQ_PLATFORM_TASK_CAPABILITY_VERSION", "2"))
    if version < 1:
        raise ValueError("daq_platform_task_capability_version_invalid")
    codec = TaskContentCodec.from_file(keyring)
    if task_store is None:
        _verify_task_database(database_url)
    store = task_store if task_store is not None else PostgresPlatformTaskStore(database_url, codec=codec)
    verifier = task_verifier if task_verifier is not None else TaskTokenVerifier.from_files(
        public_keys, audience=AGENT_ID,
    )
    capabilities = PlatformTaskCapabilities.fae_v1(capability_version=version, agent_id=AGENT_ID)
    orchestrator = DaqTaskOrchestrator(
        adapter=adapter, session_store=app.state.session_store,
        trace_recorder=app.state.trace_recorder,
    )
    worker = task_worker if task_worker is not None else PlatformTaskWorker(
        store=store, orchestrator=orchestrator, session_store=app.state.session_store,
        worker_id=f"{socket.gethostname()}:{os.getpid()}:daq",
    )
    register_platform_task_routes(app, store=store, verifier=verifier, capabilities=capabilities)
    app.state.platform_task_store = store
    app.state.platform_task_capabilities = capabilities
    app.state.platform_task_worker = worker
    app.state.daq_task_orchestrator = orchestrator
    existing_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def task_lifespan(application):
        async with existing_lifespan(application):
            worker.start()
            try:
                yield
            finally:
                worker.stop()

    app.router.lifespan_context = task_lifespan
    return capabilities
