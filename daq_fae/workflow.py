"""One DAQ pre-loop domain decision for browser, local Dev and Platform tasks."""
from __future__ import annotations

from dataclasses import dataclass, replace
import time

from src.agent.guardrails import categorize_refusal

from daq_fae.request_schema import DaqRequestSchema, extract_request_schema
from daq_fae.service_contract import ServiceContract
from daq_fae.task_context import DaqTaskContext, TurnPlan, explicit_topic_switch, prepare_turn


@dataclass(frozen=True)
class PreparedWorkflow:
    refusal: tuple[str, str] | None
    schema: DaqRequestSchema | None
    plan: TurnPlan | None
    service_contract: ServiceContract | None
    loop_question: str | None


def context_clarification_done(prepared: PreparedWorkflow | None) -> dict | None:
    """Close a setup-only turn after semantic validation, preserving its context.

    The user has supplied conditions but no question. No product assertion is
    delivered, so a model evidence loop would only repeat the clarification.
    """
    if prepared is None or prepared.schema is None or prepared.plan is None:
        return None
    goals = prepared.schema.goals
    if (not any(goal.kind == 'unknown' and goal.capability == 'clarify'
                for goal in goals)
            or any(goal.kind not in {'unknown', 'product'} for goal in goals)
            or any(goal.kind == 'product' and goal.capability not in {
                'resolve_entity', 'catalog', 'inspect_topology'} for goal in goals)):
        return None
    status = {requirement['id']: 'unknown'
              for requirement in prepared.plan.requirements}
    english = prepared.schema.answer_language == 'en'
    answer = (
        'I have noted the device and setup details you supplied. What would '
        'you like to check: a specification, connection, capture procedure, '
        'or software support?'
        if english else
        '已记录您提供的设备和使用条件。这些是您的描述，尚未作为产品事实核实。'
        '您想了解哪方面：具体规格、连接方式、采集流程，还是软件与 SDK 支持？'
    )
    return {
        'type': 'done', 'answer': answer, 'outcome': 'safe_abstained',
        'sources': [], 'tool_calls': [], 'actual_capabilities': [],
        'planned_capabilities': list(prepared.plan.planned_capabilities),
        'capability_coverage': {capability: 'unknown'
                                for capability in prepared.plan.planned_capabilities},
        'evidence_policy': {'requirement_status': status, 'retry_rounds': 0,
                            'reason_code': None, 'failure_reason': None},
        'synthesis_mode': 'context_clarification',
        'fallback_used': False, 'fallback_reason': None,
    }


def allowed_tools_for(schema: DaqRequestSchema) -> frozenset[str] | None:
    """Expose the evidence tools needed by the request's validated goals."""
    if all(goal.kind in {'service', 'unknown'} for goal in schema.goals):
        return frozenset({'session_state'})
    if all(goal.kind == 'unknown' or (
            goal.kind == 'product' and goal.capability == 'resolve_entity')
            for goal in schema.goals):
        return frozenset({'resolve_entity', 'session_state'})
    if not any(goal.kind == 'product' for goal in schema.goals):
        return frozenset({'session_state'})
    goal_tools = {
        'catalog': {'catalog'},
        'resolve_entity': {'resolve_entity'},
        'lookup_spec': {'lookup_spec'},
        'inspect_topology': {'inspect_topology', 'lookup_procedure'},
        'lookup_procedure': {'inspect_topology', 'lookup_procedure'},
        'check_software_support': {'check_software_support', 'sdk_evidence'},
        'sdk_evidence': {'sdk_evidence', 'check_software_support'},
        'official_links': {'official_links'},
        'experience': {'experience', 'risk'},
        'risk': {'risk', 'experience'},
        'search_knowledge': {'search_knowledge', 'read_doc'},
    }
    # Selection can require facts across several evidence classes. Its broad
    # tool budget remains available until its subgoals are planned separately.
    if any(goal.capability == 'selection' for goal in schema.goals):
        return None
    allowed = {'session_state', 'resolve_entity', 'catalog'}
    for goal in schema.goals:
        if goal.kind == 'product':
            selected = goal_tools.get(goal.capability)
            if selected is None:
                return None
            allowed.update(selected)
    return frozenset(allowed)


class StageLedger:
    """Request-local stage events with stable IDs and closed running intervals."""

    def __init__(self, trace_id: str | None):
        self.trace_id = trace_id
        self.events: list[dict] = []
        self._running: dict[str, tuple[str, float]] = {}

    def start(self, stage: str, **metadata) -> dict:
        if stage in self._running:
            raise ValueError('stage_already_running')
        stage_id = f'{stage}-{len(self.events) + 1}'
        self._running[stage] = (stage_id, time.monotonic())
        event = {'stage': stage, 'stage_id': stage_id, 'status': 'running',
                 'trace_id': self.trace_id, **metadata}
        self.events.append(event)
        return event

    def finish(self, stage: str, *, error: str | None = None, **metadata) -> dict:
        if stage not in self._running:
            raise ValueError('stage_not_running')
        stage_id, started = self._running.pop(stage)
        event = {'stage': stage, 'stage_id': stage_id,
                 'status': 'error' if error else 'completed',
                 'trace_id': self.trace_id,
                 'elapsed_ms': int((time.monotonic() - started) * 1000), **metadata}
        if error:
            event['reason'] = error
        self.events.append(event)
        return event

    def observed(self, stage: str, **metadata) -> dict:
        event = {'stage': stage, 'stage_id': f'{stage}-{len(self.events) + 1}',
                 'status': 'completed', 'trace_id': self.trace_id, **metadata}
        self.events.append(event)
        return event

    def close_errors(self, reason: str) -> list[dict]:
        return [self.finish(stage, error=reason) for stage in tuple(self._running)]


def prepare_workflow(message: str, *, adapter, runtime_release: str,
                     role: str = 'internal_fae', knowledge=None,
                     previous=None, context_hints=None, grounded_targets=None,
                     attachment_source_ids=(), image_source_ids=(),
                     prior_user_context: str = '') -> PreparedWorkflow:
    refusal = categorize_refusal(message)
    if refusal is not None:
        return PreparedWorkflow(refusal, None, None, None, None)
    old = (DaqTaskContext.from_checkpoint(previous) if isinstance(previous, dict)
           else previous if isinstance(previous, DaqTaskContext) else None)
    if old is not None and old.setup_statement is not None \
            and not explicit_topic_switch(message):
        setup = old.setup_statement.value
        if setup not in prior_user_context:
            prior_user_context = (setup + '\n' + prior_user_context)[-1000:]
    schema = extract_request_schema(
        message, adapter=adapter, prior_user_context=prior_user_context,
        attachment_present=bool(attachment_source_ids or image_source_ids),
        image_present=bool(image_source_ids),
    )
    if knowledge is not None and hasattr(knowledge, 'ground_question'):
        effective_grounding = (
            knowledge.ground_question(schema.effective_question)
            if getattr(knowledge, 'is_candidate_preview', False)
            else knowledge.ground_question(schema.effective_question, role=role))
        if effective_grounding.get('entities'):
            grounded_targets = effective_grounding
    contract = ServiceContract.for_release(
        knowledge, role=role, runtime_release=runtime_release)
    plan = prepare_turn(
        message, previous=previous, context_hints=context_hints,
        grounded_targets=grounded_targets, request_schema=schema,
        attachment_source_ids=attachment_source_ids,
        image_source_ids=image_source_ids,
    )
    language_note = (
        'Answer language: English. Every submit_answer field must be written in English; '
        'retain exact product names and evidence limits.\n'
        if schema.answer_language == 'en' else
        '回答语言：中文。submit_answer 的结论、依据、注意、缺口和下一步都用中文；'
        '产品名称、单位和 SDK 名称可保留原文。\n')
    plan = replace(plan, context_note=language_note + contract.prompt_note() + '\n'
                   + plan.context_note)
    return PreparedWorkflow(None, schema, plan, contract, schema.effective_question)
