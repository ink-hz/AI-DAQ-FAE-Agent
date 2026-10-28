"""DAQ Loop adapter for the shared durable Platform task worker."""
from __future__ import annotations

import json
import copy
import os
import socket
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import httpx
import psycopg

from src.agent.anthropic_transport import AnthropicTransportError
from src.agent.guardrails import categorize_refusal
from daq_fae.thinking_prefix import DaqLoopRuntime as LoopRuntime
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
from daq_fae.provider_errors import provider_exception_done
from daq_fae.task_context import extract_context_hints, prepare_turn
from daq_fae.offline_adapter import OfflineAdapter
from daq_fae.request_schema import SchemaExtractionError
from daq_fae.workflow import (StageLedger, prepare_workflow, allowed_tools_for,
                              context_clarification_done)
from daq_fae.response_language import localize_answer
from daq_fae.task_knowledge_binding import BoundTaskStore, DaqTaskVerifier, TaskKnowledgeBindings
from daq_fae.knowledge.releases import read_active_release


class DaqTaskOrchestrator:
    composition_mode = "loop"

    def __init__(self, *, adapter, session_store, trace_recorder=None,
                 prompt_path: Path | None = None, knowledge=None,
                 task_bindings=None, runtime_release: str | None = None):
        self._adapter = adapter
        self._sessions = session_store
        self._trace_recorder = trace_recorder
        if knowledge is not None and task_bindings is None:
            raise ValueError('daq_task_knowledge_binding_required')
        self._knowledge = knowledge
        self._task_bindings = task_bindings
        self._runtime_release = runtime_release
        prompt_name = ('reviewed_knowledge_system.md' if knowledge is not None
                       else 'empty_knowledge_system.md')
        self._prompt_path = prompt_path or Path(__file__).resolve().parents[1] / 'prompts' / prompt_name
        self._contexts = {}
        self._trusted_questions = {}

    def _trusted_history(self, session_id, messages):
        """Replay only user text supplied as a planning question, never Task hints."""
        questions = self._trusted_questions.get(session_id, ())
        history = []
        turn_index = 0
        include_answer = False
        for item in messages:
            if item.get('role') == 'user':
                include_answer = turn_index < len(questions)
                if include_answer:
                    history.append({'role': 'user', 'content': questions[turn_index]})
                turn_index += 1
            elif item.get('role') == 'assistant' and include_answer:
                history.append(item)
                include_answer = False
        return history

    def handle_stream(self, *, session_id, user_message,
                      planning_message=None,
                      context_hints=None,
                      required_attachment_source_ids=None,
                      required_image_source_ids=None,
                      continuation_guard=None, knowledge_role=None):
        if required_attachment_source_ids or required_image_source_ids:
            raise ValueError("daq_task_attachments_not_available")
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("daq_task_session_not_found")
        guard = continuation_guard or (lambda: None)
        if self._knowledge is not None and not knowledge_role:
            raise ValueError('daq_task_knowledge_role_required')
        guard()
        question = planning_message or user_message
        trusted_history = self._trusted_history(session_id, session.messages)
        trace = (self._trace_recorder.start_trace("daq_platform_task", {
            "agent_id": AGENT_ID, "session_id": session_id,
            "message_length": len(user_message),
        }) if self._trace_recorder is not None else None)
        stages = StageLedger(trace.trace_id if trace is not None else None)
        try:
            prepared = None
            answer_policy = None
            yield StreamEvent('stage', stages.start('guardrail'))
            refusal = categorize_refusal(question)
            yield StreamEvent('stage', stages.finish('guardrail', refusal=bool(refusal)))
            plan = None
            if refusal:
                category, answer = refusal
                done = {"answer": answer, "outcome": "safe_abstained", "sources": [],
                        "planned_capabilities": [], "actual_capabilities": [],
                        "capability_coverage": {}, "refusal_category": category}
            else:
                yield StreamEvent('stage', stages.start('schema'))
                if isinstance(self._adapter, OfflineAdapter):
                    plan = prepare_turn(question,
                                        previous=self._contexts.get(session_id),
                                        context_hints=context_hints,
                                        grounded_targets=(self._knowledge.ground_question(
                                            question, role=knowledge_role)
                                            if self._knowledge is not None else None))
                    prepared = None
                else:
                    try:
                        prepared = prepare_workflow(
                            question, adapter=self._adapter,
                            runtime_release=self._runtime_release or 'runtime-dev',
                            previous=self._contexts.get(session_id),
                            context_hints=context_hints,
                            knowledge=self._knowledge, role=knowledge_role or 'internal_fae',
                            prior_user_context='\n'.join(
                                item['content'] for item in trusted_history[-16:]
                                if item.get('role') == 'user'),
                            grounded_targets=(self._knowledge.ground_question(
                                question, role=knowledge_role) if self._knowledge is not None else None),
                        )
                    except (SchemaExtractionError, AnthropicTransportError,
                            httpx.HTTPStatusError, httpx.TransportError) as exc:
                        done = (provider_exception_done(exc) if not isinstance(
                            exc, SchemaExtractionError) else {
                                'answer': '本轮请求理解失败，未生成可靠答案。',
                                'outcome': 'schema_error', 'sources': [],
                                'fallback_used': True, 'fallback_reason': str(exc),
                            })
                        yield StreamEvent('stage', stages.finish('schema', error=done['outcome']))
                        guard()
                        done.update({
                            'agent_id': AGENT_ID,
                            'knowledge_release': (self._knowledge.release_id if self._knowledge
                                                  is not None else EMPTY_KNOWLEDGE_RELEASE),
                            'runtime_release': self._runtime_release,
                            'session_id': session_id,
                            'trace_id': trace.trace_id if trace is not None else None,
                            'planned_capabilities': [], 'actual_capabilities': [],
                            'capability_coverage': {},
                        })
                        if trace is not None:
                            trace.finalize({'outcome': done['outcome'],
                                            'fallback_used': True,
                                            'fallback_reason': done['fallback_reason']})
                        yield StreamEvent('text_delta', {'delta': done['answer']})
                        yield StreamEvent('sources', [])
                        yield StreamEvent('done', done)
                        return
                    plan = prepared.plan
                yield StreamEvent('stage', stages.finish(
                    'schema', request_kind=prepared.schema.request_kind if prepared
                    else 'offline_synthetic',
                    **({'goal_ids': [goal.id for goal in prepared.schema.goals],
                        'original_question': prepared.schema.original_question,
                        'effective_question': prepared.schema.effective_question,
                        'entity_scope': sorted({entity for goal in prepared.schema.goals
                                                for entity in goal.entities}),
                        'attachment_dependency': prepared.schema.attachment_dependency,
                        'confidence': prepared.schema.confidence,
                        'field_lineage': plan.field_lineage(),
                        'goal_field_lineage': plan.goal_field_lineage(),
                        'invalidated_fields': list(plan.invalidated_fields)}
                       if prepared else {})))
                allowed_tools = (allowed_tools_for(prepared.schema)
                                 if prepared else None)
                answer_policy = DaqEvidencePolicy(
                    knowledge=self._knowledge, role=knowledge_role,
                    allow_dev_source_check=(getattr(self._knowledge, 'sdk_catalog', None) is not None
                                            and not getattr(self._knowledge.sdk_catalog, 'published', False)),
                    service_contract=prepared.service_contract if prepared else None,
                    question=prepared.schema.effective_question if prepared else user_message,
                    answer_language=prepared.schema.answer_language if prepared else 'zh',
                    allowed_tools=allowed_tools,
                    allowed_chinese_terms=tuple({entity for goal in prepared.schema.goals
                                                 for entity in goal.entities
                                                 if any('\u3400' <= char <= '\u9fff'
                                                        for char in entity)})
                    if prepared else ())
                runtime = LoopRuntime(
                    adapter=self._adapter,
                    toolbox=DaqToolBox(context=plan.context.tool_context(),
                                       current_question=(prepared.schema.original_question
                                                         if prepared else question),
                                       knowledge=self._knowledge, role=knowledge_role,
                                       requirements=plan.requirements, access_guard=guard,
                                       allowed_tools=allowed_tools),
                    system_prompt_path=self._prompt_path,
                    evidence_policy=answer_policy,
                )
                done = None
                clarification = context_clarification_done(prepared)
                if clarification is not None:
                    yield StreamEvent('stage', stages.observed(
                        'clarification', reason='user_intent_missing'))
                else:
                    yield StreamEvent('stage', stages.start('loop'))
                try:
                    events = iter((clarification,)) if clarification is not None else runtime.run(
                        user_message, history=trusted_history, entity_note=plan.context_note,
                        evidence_requirements=plan.evidence_requirements(),
                    )
                    for event in events:
                        guard()
                        if event.get("type") == "tool_call":
                            yield StreamEvent('stage', stages.observed(
                                'tool_call', tool=event.get('tool') or event.get('name'),
                                tool_status=event.get('status')))
                        elif event.get("type") == "done":
                            done = dict(event)
                    if done is None:
                        raise RuntimeError("daq_task_runtime_missing_terminal")
                    if clarification is None:
                        yield StreamEvent('stage', stages.finish('loop', outcome=done.get('outcome')))
                except AnthropicTransportError as exc:
                    done = anthropic_failure(exc)
                    yield StreamEvent('stage', stages.finish('loop', error=done['outcome']))
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    outcome = ("provider_configuration_error" if status in {400, 401, 403, 404, 422}
                               else "provider_rate_limited" if status == 429
                               else "provider_unavailable" if status >= 500 else "provider_error")
                    done = {"answer": "数采模型请求失败，未生成答案。", "outcome": outcome,
                            "sources": [], "fallback_used": True,
                            "fallback_reason": f"provider_http_{status}"}
                    yield StreamEvent('stage', stages.finish('loop', error=outcome))
                except httpx.TransportError as exc:
                    done = {"answer": "数采模型连接失败，未生成答案。", "outcome": "provider_unavailable",
                            "sources": [], "fallback_used": True,
                            "fallback_reason": type(exc).__name__}
                    yield StreamEvent('stage', stages.finish('loop', error='provider_unavailable'))
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
            done.setdefault("planned_capabilities", list(plan.planned_capabilities) if plan else [])
            done.update({"agent_id": AGENT_ID,
                         "knowledge_release": (self._knowledge.release_id if self._knowledge
                                               is not None else EMPTY_KNOWLEDGE_RELEASE),
                         "session_id": session_id,
                         "trace_id": trace.trace_id if trace is not None else None})
            if self._runtime_release is not None:
                done['runtime_release'] = self._runtime_release
            if self._knowledge is None:
                refine_empty_release_answer(
                    done, planned_capabilities=done["planned_capabilities"],
                    knowledge_release=EMPTY_KNOWLEDGE_RELEASE,
                )
            if prepared is not None:
                localize_answer(done, prepared.schema.answer_language,
                                submission=answer_policy.last_submission if answer_policy else None)
                done['request_kind'] = prepared.schema.request_kind
                done['request_schema'] = asdict(prepared.schema)
                done['evidence_requirements'] = copy.deepcopy(plan.requirements)
                requirement_status = (done.get('evidence_policy') or {}).get(
                    'requirement_status', {})
                done['goal_status'] = {
                    requirement['goal_id']: requirement_status.get(requirement['id'], 'unknown')
                    for requirement in plan.requirements if 'goal_id' in requirement
                }
            yield StreamEvent('stage', stages.observed(
                'coverage', requirement_status=(done.get('evidence_policy') or {}).get(
                    'requirement_status', {}), goal_status=done.get('goal_status', {})))
            yield StreamEvent('stage', stages.observed(
                'outcome', outcome=done['outcome'], fallback_used=done['fallback_used']))
            if plan is not None:
                self._contexts[session_id] = plan.context
            session.append_message("user", user_message)
            session.append_message("assistant", done["answer"])
            self._trusted_questions.setdefault(session_id, []).append(question)
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
        knowledge_role = None
        guard = continuation_guard
        if self._task_bindings is not None:
            knowledge_role = self._task_bindings.require_current(
                spec.platform_task_id, spec.requester_internal_user_id)

            def guard():
                self._task_bindings.require_current(
                    spec.platform_task_id, spec.requester_internal_user_id)
                continuation_guard()
        return self.handle_stream(
            session_id=session_id, user_message=prompt,
            planning_message=planning_message,
            context_hints=extract_context_hints(getattr(spec, 'context_excerpt', ())),
            continuation_guard=guard, knowledge_role=knowledge_role,
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
                             task_verifier=None, task_worker=None, knowledge=None,
                             entitlements=None, release_root=None,
                             runtime_release: str | None = None,
                             task_binding_connect=psycopg.connect):
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
    if knowledge is not None:
        if entitlements is None or release_root is None:
            raise ValueError('daq_knowledge_task_role_contract_missing')
        bindings = TaskKnowledgeBindings(
            database_url, entitlements=entitlements, release_id=knowledge.release_id,
            active_release=lambda: (read_active_release(release_root) or {}).get('release_id'),
            connect=task_binding_connect,
        )
        bindings.assert_ready()
        store = BoundTaskStore(store, bindings)
        verifier = DaqTaskVerifier(verifier, bindings)
        app.state.daq_task_knowledge_bindings = bindings
    capabilities = PlatformTaskCapabilities.fae_v1(capability_version=version, agent_id=AGENT_ID)
    orchestrator = DaqTaskOrchestrator(
        adapter=adapter, session_store=app.state.session_store,
        trace_recorder=app.state.trace_recorder,
        knowledge=knowledge, task_bindings=(bindings if knowledge is not None else None),
        runtime_release=runtime_release,
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
