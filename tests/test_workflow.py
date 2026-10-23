import pytest

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
        'resolve_entity', 'catalog', 'session_state', 'lookup_spec',
        'search_knowledge', 'read_doc'})


def test_product_goals_can_research_inputs_without_opening_unrelated_software_or_attachments():
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
        'inspect_topology', 'lookup_procedure', 'lookup_spec',
        'search_knowledge', 'read_doc'})

    visual = DaqRequestSchema.from_candidate('这张图片里能看到什么？', {
        'confidence': 'high', 'goals': [
            {'kind': 'attachment', 'capability': 'analyze_image'}],
    }, attachment_present=True, image_present=True)
    assert allowed_tools_for(visual) == frozenset({'session_state'})


def test_standalone_procedure_does_not_open_unasked_topology_goal():
    schema = DaqRequestSchema.from_candidate(
        'EGO Pro 单机 TF 卡录制，从开机到停止的按键和灯态是什么？', {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_procedure',
                 'entities': ['EGO Pro'],
                 'conditions': {'standalone': True, 'recording_mode': 'local_tf_card'}},
            ],
        })
    assert allowed_tools_for(schema) == frozenset({
        'resolve_entity', 'catalog', 'session_state', 'lookup_procedure',
        'lookup_spec', 'search_knowledge', 'read_doc'})


@pytest.mark.parametrize('capability', ['sdk_evidence', 'check_software_support'])
def test_sdk_questions_can_read_release_notes_without_treating_them_as_support(capability):
    schema = DaqRequestSchema.from_candidate(
        'OrbbecSDK 2026-09-21 与 2026-09-23 变更日志如何记载 UMI Finger 磁力计？', {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': capability,
                 'entities': ['UMI Finger']}],
        })
    allowed = allowed_tools_for(schema)
    assert 'search_knowledge' in allowed
    assert 'read_doc' in allowed


class SemanticAdapter:
    def __init__(self, goals):
        self.goals = goals
        self.calls = 0

    def chat(self, messages, tools=None, required_tool=None):
        self.calls += 1
        assert required_tool == 'submit_daq_request_schema'
        yield {'type': 'tool_call', 'name': required_tool,
               'arguments': {'confidence': 'high', 'goals': self.goals}}


def test_generic_family_question_does_not_bind_variant_named_in_negative_clause():
    from daq_fae.knowledge.dev_candidate import DevCandidateKnowledge

    rows = []
    for variant in ('1600x1200', '1920x1200'):
        rows.append({
            'id': 'entity:ego-' + variant[:4], 'kind': 'entity',
            'status': 'verified',
            'scope': {'product': 'ego', 'required_selectors': ['resolution_variant'],
                      'resolution_variant': variant},
            'data': {'name': 'EGO', 'entity_type': 'variant'}, 'source_refs': [],
        })
    knowledge = DevCandidateKnowledge('candidate-dev-' + 'a' * 64, [], rows, {})
    question = '泛称 EGO 是否支持 1600×1300@30？能否因此说它只支持 1600×1200？'
    assert knowledge.ground_question(question)['entities'] == ['entity:ego-1600']
    adapter = SemanticAdapter([{
        'kind': 'product', 'capability': 'lookup_spec', 'entities': ['EGO'],
        'field': 'rgb_max_frame_rate', 'conditions': {'resolution': '1600x1300'},
    }])
    prepared = prepare_workflow(
        question, adapter=adapter, knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev',
        grounded_targets=knowledge.ground_question(question))
    requirement = prepared.plan.requirements[0]
    assert requirement['entities'] == ['EGO']
    assert requirement['conditions'] == {'resolution': '1600x1300'}


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


@pytest.mark.parametrize('also_resolve_current', [False, True])
def test_current_reviewed_product_retries_stale_hub_power_goal(also_resolve_current):
    from pathlib import Path
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge

    release = read_active_release(Path(__file__).resolve().parents[1] /
                                  'data/knowledge/published')
    knowledge = ReviewedKnowledge.from_manifest(release['release_id'],
                                                 release['manifest'])
    question = 'UMI Finger 每口供电是多少？'
    assert knowledge.ground_question(question, role='internal_fae')['entities'] == [
        'entity:umi-finger']

    class RetryingAdapter:
        calls = 0

        def chat(self, messages, tools=None, required_tool=None):
            self.calls += 1
            entity, field = (('数采 HUB', 'downstream_port_power_output')
                             if self.calls == 1 else ('UMI Finger', 'power_output'))
            goals = [{'kind': 'product', 'capability': 'lookup_spec',
                      'entities': [entity], 'field': field}]
            if self.calls == 1 and also_resolve_current:
                goals.insert(0, {'kind': 'product', 'capability': 'resolve_entity',
                                 'entities': ['UMI Finger']})
            yield {'type': 'tool_call', 'name': required_tool,
                   'arguments': {'confidence': 'high', 'goals': goals}}

    adapter = RetryingAdapter()
    prepared = prepare_workflow(
        question, adapter=adapter, knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev',
        prior_user_context='我正在使用数采 HUB。')
    assert adapter.calls == 2
    assert prepared.schema.goals[0].entities == ('UMI Finger',)
    assert prepared.plan.requirements[0]['entities'] == ['entity:umi-finger']


@pytest.mark.parametrize('question', [
    '数采 HUB 接 UMI Finger，UMI Finger 每口供电是多少？',
    '数采 HUB 接 UMI Finger 每口供电是多少？',
])
def test_reviewed_power_target_overrides_hub_topology_background(question):
    from pathlib import Path
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge

    release = read_active_release(Path(__file__).resolve().parents[1] /
                                  'data/knowledge/published')
    knowledge = ReviewedKnowledge.from_manifest(release['release_id'],
                                                 release['manifest'])

    class RetryingAdapter:
        calls = 0

        def chat(self, messages, tools=None, required_tool=None):
            self.calls += 1
            goals = ([
                {'kind': 'product', 'capability': 'resolve_entity',
                 'entities': ['UMI Finger']},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['数采 HUB'],
                 'field': 'downstream_port_power_output'},
            ] if self.calls == 1 else [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['UMI Finger'], 'field': 'power_output',
            }])
            yield {'type': 'tool_call', 'name': required_tool,
                   'arguments': {'confidence': 'high', 'goals': goals}}

    adapter = RetryingAdapter()
    prepared = prepare_workflow(
        question, adapter=adapter, knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev',
        prior_user_context='我正在使用数采 HUB。')
    assert adapter.calls == 2
    assert prepared.schema.goals[0].entities == ('UMI Finger',)


def test_two_named_power_subjects_keep_separate_valid_goals():
    from pathlib import Path
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge

    release = read_active_release(Path(__file__).resolve().parents[1] /
                                  'data/knowledge/published')
    knowledge = ReviewedKnowledge.from_manifest(release['release_id'],
                                                 release['manifest'])
    question = '数采 HUB 每口供电和 UMI Finger 的总功率分别多少？'
    goals = [
        {'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['数采 HUB'], 'field': 'downstream_port_power_output',
         'conditions': {'per_port': True}},
        {'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['UMI Finger'], 'field': 'power_output'},
    ]
    adapter = SemanticAdapter(goals)
    prepared = prepare_workflow(
        question, adapter=adapter, knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev')
    assert adapter.calls == 1
    assert {goal.entities for goal in prepared.schema.goals} == {
        ('数采 HUB',), ('UMI Finger',),
    }


def test_two_named_power_subjects_cannot_replace_second_spec_with_identity():
    from pathlib import Path
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.request_schema import SchemaExtractionError

    release = read_active_release(Path(__file__).resolve().parents[1] /
                                  'data/knowledge/published')
    knowledge = ReviewedKnowledge.from_manifest(release['release_id'],
                                                 release['manifest'])
    adapter = SemanticAdapter([
        {'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['数采 HUB'], 'field': 'downstream_port_power_output',
         'conditions': {'per_port': True}},
        {'kind': 'product', 'capability': 'resolve_entity',
         'entities': ['UMI Finger']},
    ])
    with pytest.raises(SchemaExtractionError, match='schema_missing_explicit_spec_goal'):
        prepare_workflow(
            '数采 HUB 每口供电和 UMI Finger 的总功率分别多少？',
            adapter=adapter, knowledge=knowledge, role='internal_fae',
            runtime_release='runtime-dev')
    assert adapter.calls == 2


def test_plain_power_question_does_not_inherit_hub_spec_from_topology():
    from pathlib import Path
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge

    release = read_active_release(Path(__file__).resolve().parents[1] /
                                  'data/knowledge/published')
    knowledge = ReviewedKnowledge.from_manifest(release['release_id'],
                                                 release['manifest'])

    class RetryingAdapter:
        calls = 0

        def chat(self, messages, tools=None, required_tool=None):
            self.calls += 1
            goals = ([
                {'kind': 'product', 'capability': 'resolve_entity',
                 'entities': ['UMI Finger']},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['数采 HUB'], 'field': 'downstream_port_power_output'},
            ] if self.calls == 1 else [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['UMI Finger'], 'field': 'power_output',
            }])
            yield {'type': 'tool_call', 'name': required_tool,
                   'arguments': {'confidence': 'high', 'goals': goals}}

    adapter = RetryingAdapter()
    prepared = prepare_workflow(
        '数采 HUB 接 UMI Finger，UMI Finger 供电是多少？',
        adapter=adapter, knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev')
    assert adapter.calls == 2
    assert prepared.schema.goals[0].entities == ('UMI Finger',)


def test_coordinated_products_can_share_one_power_question():
    from pathlib import Path
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge

    release = read_active_release(Path(__file__).resolve().parents[1] /
                                  'data/knowledge/published')
    knowledge = ReviewedKnowledge.from_manifest(release['release_id'],
                                                 release['manifest'])
    adapter = SemanticAdapter([
        {'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['数采 HUB'], 'field': 'downstream_port_power_output'},
        {'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['UMI Finger'], 'field': 'power_output'},
    ])
    prepared = prepare_workflow(
        '数采 HUB 和 UMI Finger 的供电输出分别是多少？',
        adapter=adapter, knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev')
    assert adapter.calls == 1
    assert {goal.entities for goal in prepared.schema.goals} == {
        ('数采 HUB',), ('UMI Finger',),
    }


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


def test_followup_variant_change_rebinds_reviewed_entity_before_evidence_plan():
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.task_context import prepare_turn

    rows = []
    for variant in ('1600x1200', '1920x1200'):
        rows.append({
            'id': 'entity:ego-' + variant[:4], 'kind': 'entity',
            'status': 'verified', 'answerable': True,
            'data': {'name': 'EGO', 'aliases': ['EGO 双目'],
                     'entity_type': 'variant'},
            'scope': {'product': 'ego', 'resolution_variant': variant,
                      'required_selectors': ['resolution_variant']},
            'access_review': {'view_roles': ['internal_fae'],
                              'forward_roles': ['internal_fae']},
            'source_refs': [],
        })
    knowledge = ReviewedKnowledge('reviewed-test', {'records': rows, 'sections': []})
    first = '我现在用 EGO 双目 1920×1200 版本，RGB 按原生分辨率采。'
    first_schema = DaqRequestSchema.from_candidate(first, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'resolve_entity',
            'entities': ['EGO 双目'],
            'conditions': {'resolution_variant': '1920x1200'},
        }],
    })
    previous = prepare_turn(
        first, request_schema=first_schema,
        grounded_targets={'entities': ['entity:ego-1920'],
                          'conditions': {'resolution_variant': '1920x1200'}},
    ).context.to_checkpoint()
    question = '如果设备换成 1600×1200 版本，这三项要怎么改？'
    goals = [{
        'kind': 'product', 'capability': 'lookup_spec',
        'entities': ['EGO 双目'], 'field': field,
        'conditions': {'resolution_variant': '1600x1200'},
    } for field in ('horizontal_fov', 'vertical_fov', 'rgb_max_frame_rate')]
    prepared = prepare_workflow(
        question, adapter=SemanticAdapter(goals), knowledge=knowledge,
        role='internal_fae', runtime_release='runtime-dev', previous=previous,
        prior_user_context=first + '\n它的视场角和帧率是多少？',
    )
    assert len(prepared.plan.requirements) == 5
    assert 'rgb_native_resolution' in {
        item['field'] for item in prepared.plan.requirements}
    assert all(item['entities'] == ['entity:ego-1600'] and
               item['conditions']['resolution_variant'] == '1600x1200'
               for item in prepared.plan.requirements)
    assert prepared.plan.context.grounded_scope['entities'] == ['entity:ego-1600']
    followup = prepare_workflow(
        '它的垂直视场角是多少？', adapter=SemanticAdapter([{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目'], 'field': 'vertical_fov',
        }]), knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev',
        previous=prepared.plan.context.to_checkpoint(),
        prior_user_context=first + '\n' + question,
    )
    assert followup.plan.requirements[0]['entities'] == ['entity:ego-1600']
    assert followup.plan.requirements[0]['conditions']['resolution_variant'] == '1600x1200'
    assert followup.plan.context.get('resolution_variant') != '1920x1200'
    noisy_goal = prepare_workflow(
        '它的水平、垂直视场角分别是多少？原生分辨率最高多少帧？',
        adapter=SemanticAdapter([{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目', '1920x1200 版本'],
            'field': 'horizontal_fov',
            'conditions': {'resolution_variant': '1920x1200'},
        }]), knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev', previous=previous,
        prior_user_context=first,
    )
    assert noisy_goal.plan.requirements[0]['entities'] == ['entity:ego-1920']
    previous_with_value = prepare_turn(
        first, request_schema=first_schema,
        updates={'resolution_variant': '1920x1200'},
        grounded_targets={'entities': ['entity:ego-1920'],
                          'conditions': {'resolution_variant': '1920x1200'}},
    ).context.to_checkpoint()
    direct = prepare_workflow(
        'EGO 双目 1600×1200 版的垂直视场角是多少？',
        adapter=SemanticAdapter([{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目'], 'field': 'vertical_fov',
        }]), knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev', previous=previous_with_value,
        prior_user_context=first,
    )
    assert direct.plan.requirements[0]['entities'] == ['entity:ego-1600']
    assert direct.plan.requirements[0]['conditions']['resolution_variant'] == '1600x1200'
    named_with_selector = prepare_workflow(
        'EGO 双目 1600×1200 的水平视场角是多少？',
        adapter=SemanticAdapter([{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目 1600×1200'], 'field': 'horizontal_fov',
            'conditions': {'resolution_variant': '1600x1200'},
        }]), knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev',
    )
    assert named_with_selector.plan.requirements[0]['entities'] == ['entity:ego-1600']

    unknown = prepare_workflow(
        '如果设备换成 2048×1536 版本，这三项要怎么改？',
        adapter=SemanticAdapter([{
            **goal, 'conditions': {'resolution_variant': '2048x1536'}
        } for goal in goals]), knowledge=knowledge,
        role='internal_fae', runtime_release='runtime-dev', previous=previous,
        prior_user_context=first,
    )
    assert unknown.plan.context.grounded_scope == {}
    assert all('entity:ego-1920' not in item['entities']
               for item in unknown.plan.requirements)
    from copy import deepcopy
    incomplete_checkpoint = deepcopy(previous)
    incomplete_checkpoint['grounded_scope']['conditions'] = {}
    unknown_from_incomplete = prepare_workflow(
        '如果设备换成 2048×1536 版本，这三项要怎么改？',
        adapter=SemanticAdapter([{
            **goal, 'conditions': {'resolution_variant': '2048x1536'}
        } for goal in goals]), knowledge=knowledge,
        role='internal_fae', runtime_release='runtime-dev',
        previous=incomplete_checkpoint, prior_user_context=first,
    )
    assert unknown_from_incomplete.plan.context.grounded_scope == {}

    role_changed = prepare_workflow(
        question, adapter=SemanticAdapter(goals), knowledge=knowledge,
        role='channel', runtime_release='runtime-dev', previous=previous,
        prior_user_context=first,
    )
    assert role_changed.plan.context.grounded_scope == {}
    assert all('entity:ego-1600' not in item['entities'] and
               'entity:ego-1920' not in item['entities']
               for item in role_changed.plan.requirements)

    import pytest
    from daq_fae.request_schema import SchemaExtractionError
    with pytest.raises(SchemaExtractionError, match='stale_variant_selector'):
        prepare_workflow(
            '它的水平视场角是多少？', adapter=SemanticAdapter([{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['EGO 双目'], 'field': 'horizontal_fov',
                'conditions': {'resolution_variant': '1600x1200'},
            }]), knowledge=knowledge, role='internal_fae',
            runtime_release='runtime-dev', previous=previous,
            prior_user_context=('之前讨论过 EGO 双目 1600×1200。\n' + first),
        )

    contradictory_id = prepare_workflow(
        '如果 entity:ego-1920 设备换成 1600×1200 版本，这三项要怎么改？',
        adapter=SemanticAdapter([{
            **goal, 'entities': ['entity:ego-1920']
        } for goal in goals]), knowledge=knowledge,
        role='internal_fae', runtime_release='runtime-dev', previous=previous,
        prior_user_context=first,
    )
    assert contradictory_id.plan.context.grounded_scope == {}
    assert all(item['entities'] == ['entity:ego-1920']
               for item in contradictory_id.plan.requirements)

    unscoped = deepcopy(rows)
    for row in unscoped:
        row['scope'].pop('product')
    unscoped_view = ReviewedKnowledge('unscoped-test', {
        'records': unscoped, 'sections': [],
    })
    assert unscoped_view.rebind_variant(
        previous['grounded_scope'], prepared.schema.goals,
        role='internal_fae', current_question=question) == {}


def test_side_mentioned_product_cannot_replace_the_schema_goal_entity():
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.task_context import prepare_turn
    rows = [{
        'id': entity_id, 'kind': 'entity', 'status': 'verified',
        'answerable': True,
        'data': {'name': name, 'entity_type': 'product'},
        'scope': {'product': product, 'required_selectors': []},
        'access_review': {'view_roles': ['internal_fae'],
                          'forward_roles': ['internal_fae']},
        'source_refs': [],
    } for entity_id, name, product in (
        ('entity:umi-finger', 'UMI Finger', 'umi-finger'),
        ('entity:ego-pro', 'EGO Pro', 'ego-pro'),
    )]
    knowledge = ReviewedKnowledge('reviewed-test', {'records': rows, 'sections': []})
    first = '我用 UMI Finger。'
    first_schema = DaqRequestSchema.from_candidate(first, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'resolve_entity',
            'entities': ['UMI Finger'],
        }],
    })
    previous = prepare_turn(first, request_schema=first_schema,
                            grounded_targets={'entities': ['entity:umi-finger'],
                                              'conditions': {}}).context.to_checkpoint()
    question = '与 EGO Pro 的规格相比，前面那台的 RGB 分辨率是多少？'
    assert knowledge.ground_question(question, role='internal_fae')['entities'] == [
        'entity:ego-pro']
    prepared = prepare_workflow(
        question, adapter=SemanticAdapter([{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['UMI Finger'], 'field': 'rgb_resolution',
        }]), knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev', previous=previous,
        prior_user_context=first,
    )
    assert prepared.plan.requirements[0]['entities'] == ['entity:umi-finger']
    pregrounded = prepare_workflow(
        question, adapter=SemanticAdapter([{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['UMI Finger'], 'field': 'rgb_resolution',
        }]), knowledge=knowledge, role='internal_fae',
        runtime_release='runtime-dev', previous=previous,
        prior_user_context=first,
        grounded_targets=knowledge.ground_question(question, role='internal_fae'),
    )
    assert pregrounded.plan.requirements[0]['entities'] == ['entity:umi-finger']
