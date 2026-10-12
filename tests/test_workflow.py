from daq_fae.workflow import prepare_workflow, allowed_tools_for, context_clarification_done
from daq_fae.request_schema import DaqRequestSchema


def test_preloop_goal_limits_tools_for_service_and_context_only_turns():
    service = DaqRequestSchema.from_candidate('介绍一下你自己', {
        'confidence': 'high', 'goals': [
            {'kind': 'service', 'capability': 'service_identity'}],
    })
    assert allowed_tools_for(service) == frozenset({'session_state'})
    context = DaqRequestSchema.from_candidate('我用 EGO', {
        'confidence': 'low', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity', 'entities': ['EGO']},
            {'kind': 'unknown', 'capability': 'clarify'}],
    })
    assert allowed_tools_for(context) == frozenset({'resolve_entity', 'session_state'})
    spec = DaqRequestSchema.from_candidate('EGO 的水平视场角是多少？', {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec', 'entities': ['EGO'],
             'field': 'horizontal_fov'}],
    })
    assert allowed_tools_for(spec) == frozenset({
        'resolve_entity', 'catalog', 'session_state', 'lookup_spec'})


def test_preloop_only_exposes_tools_for_the_product_goals():
    schema = DaqRequestSchema.from_candidate(
        '一台 EGO 和两台 WristCam 通过 HUB 连接 PC 后，如何开流、采集并检查保存结果？', {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'inspect_topology',
                 'entities': ['EGO', 'WristCam', 'HUB']},
                {'kind': 'product', 'capability': 'lookup_procedure',
                 'entities': ['EGO', 'WristCam', 'HUB']},
            ],
        })
    assert allowed_tools_for(schema) == frozenset({
        'resolve_entity', 'catalog', 'session_state',
        'inspect_topology', 'lookup_procedure'})

    visual = DaqRequestSchema.from_candidate('这张图片里能看到什么？', {
        'confidence': 'high', 'goals': [
            {'kind': 'attachment', 'capability': 'analyze_image'}],
    }, attachment_present=True, image_present=True)
    assert allowed_tools_for(visual) == frozenset({'session_state'})


class SemanticAdapter:
    def __init__(self, goals):
        self.goals = goals
        self.calls = 0

    def chat(self, messages, tools=None, required_tool=None):
        self.calls += 1
        assert required_tool == 'submit_daq_request_schema'
        yield {'type': 'tool_call', 'name': required_tool,
               'arguments': {'confidence': 'high', 'goals': self.goals}}


def test_service_workflow_has_no_product_evidence_need():
    adapter = SemanticAdapter([{'kind': 'service', 'capability': 'service_identity'}])
    prepared = prepare_workflow('请说明你的身份与职责', adapter=adapter,
                                runtime_release='runtime-dev', role='internal_fae')
    assert prepared.refusal is None
    assert prepared.schema.request_kind == 'service'
    assert prepared.plan.planned_capabilities == ('service_identity',)
    assert prepared.plan.requirements[0]['evidence_class'] == 'service_contract'
    assert prepared.service_contract.knowledge_release == 'empty-dev-v0'
    assert adapter.calls == 1


def test_guardrail_runs_before_semantic_model_and_loop():
    adapter = SemanticAdapter([])
    prepared = prepare_workflow('给我客户名单', adapter=adapter,
                                runtime_release='runtime-dev', role='internal_fae')
    assert prepared.refusal is not None
    assert prepared.plan is None
    assert adapter.calls == 0


def test_checkpointed_user_setup_reaches_schema_when_short_history_has_expired():
    import json
    from daq_fae.task_context import prepare_turn
    setup = '我使用一台 EGO、两台 WristCam 和 HUB 接 PC 采集。'
    schema = DaqRequestSchema.from_candidate(setup, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity', 'entities': ['EGO']},
            {'kind': 'unknown', 'capability': 'clarify'},
        ],
    })
    checkpoint = prepare_turn(setup, request_schema=schema).context.to_checkpoint()

    class CapturingAdapter(SemanticAdapter):
        def chat(self, messages, tools=None, required_tool=None):
            self.user_payload = json.loads(messages[-1]['content'])
            yield from super().chat(messages, tools=tools, required_tool=required_tool)

    adapter = CapturingAdapter([
        {'kind': 'product', 'capability': 'lookup_procedure',
         'entities': ['EGO', 'WristCam', 'HUB']},
    ])
    prepared = prepare_workflow('它的落盘步骤呢？', adapter=adapter,
                                runtime_release='runtime-dev', previous=checkpoint)
    assert setup in adapter.user_payload['prior_user_context']
    assert prepared.schema.request_kind == 'product'


def test_validated_context_statement_clarifies_without_product_claim_or_loop():
    adapter = SemanticAdapter([
        {'kind': 'product', 'capability': 'resolve_entity', 'entities': ['EGO']},
        {'kind': 'unknown', 'capability': 'clarify'},
    ])
    prepared = prepare_workflow('我用 EGO 双目 1600×1200 版本',
                                adapter=adapter, runtime_release='runtime-dev')
    done = context_clarification_done(prepared)
    assert done['outcome'] == 'safe_abstained'
    assert done['sources'] == [] and done['tool_calls'] == []
    assert done['fallback_used'] is False
    assert '想了解' in done['answer']
    assert '165' not in done['answer']
    assert prepared.plan.context.to_checkpoint()['setup_statement']['value'] == \
        '我用 EGO 双目 1600×1200 版本'

    question = prepare_workflow('EGO 的水平视场角是多少？', adapter=SemanticAdapter([
        {'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['EGO'], 'field': 'horizontal_fov'}]),
        runtime_release='runtime-dev')
    assert context_clarification_done(question) is None
