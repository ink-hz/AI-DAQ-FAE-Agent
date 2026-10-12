import pytest

from daq_fae.request_schema import DaqRequestSchema, SchemaExtractionError, extract_request_schema


@pytest.mark.parametrize('question,candidate,field,conditions', [
    ('UMI Finger 的鱼眼 RGB 分辨率是多少？',
     {'field': 'resolution', 'conditions': {'sensor': 'fisheye_rgb'}},
     'rgb_resolution', {'lens_type': 'fisheye'}),
    ('UMI Finger 的双目 Mono 基线是多少？',
     {'field': 'baseline', 'conditions': {'camera_type': 'mono', 'stereo_pair': True}},
     'stereo_mono_baseline', {}),
    ('UMI 视觉模块的鱼眼 RGB 分辨率是多少？',
     {'field': 'fisheye_rgb_resolution',
      'conditions': {'scope': 'module', 'camera_type': 'fisheye_rgb'}},
     'rgb_resolution', {'lens_type': 'fisheye', 'scope': 'module'}),
    ('EGO Pro 的 RGB 最大帧率是多少？',
     {'field': 'rgb_max_frame_rate', 'conditions': {'sensor': 'rgb'}},
     'rgb_max_frame_rate', {}),
    ('UMI Finger 的双目 Mono 基线是多少？',
     {'field': 'stereo_baseline',
      'conditions': {'camera_type': 'mono', 'camera_config': 'stereo'}},
     'stereo_mono_baseline', {}),
])
def test_schema_normalizes_explicit_camera_qualifiers(question, candidate, field, conditions):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                      'entities': ['UMI Finger' if 'Finger' in question
                                                   else 'UMI 视觉模块' if '视觉模块' in question
                                                   else 'EGO Pro'], **candidate}],
    })
    assert schema.goals[0].field == field
    assert schema.goals[0].condition_map() == conditions


def test_schema_rejects_camera_qualifier_absent_from_user_words():
    with pytest.raises(SchemaExtractionError):
        DaqRequestSchema.from_candidate('UMI Finger 的 RGB 分辨率是多少？', {
            'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                          'entities': ['UMI Finger'], 'field': 'resolution',
                                          'conditions': {'sensor': 'fisheye_rgb'}}],
        })


def test_schema_discards_unrequested_device_scope_but_keeps_explicit_system_scope():
    hub = DaqRequestSchema.from_candidate('数采 HUB 的下游端口有几个？', {
        'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                      'entities': ['数采 HUB'], 'field': 'downstream_port_count',
                                      'conditions': {'scope': 'device'}}],
    })
    assert hub.goals[0].condition_map() == {}
    system = DaqRequestSchema.from_candidate('EG-DB 整机深度精度是多少？', {
        'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                      'entities': ['EG-DB'], 'field': 'depth_accuracy',
                                      'conditions': {'scope': 'whole_system'}}],
    })
    assert system.goals[0].condition_map() == {'scope': 'whole_system'}


def test_field_encoded_downstream_port_condition_is_not_a_second_requirement():
    schema = DaqRequestSchema.from_candidate('数采 HUB 的下游端口供电输出规格是多少？', {
        'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                      'entities': ['数采 HUB'],
                                      'field': 'downstream_port_power_output',
                                      'conditions': {'port': 'downstream', 'scope': 'device'}}],
    })
    assert schema.goals[0].condition_map() == {}


def test_unmapped_topology_and_software_conditions_are_explicit_gaps():
    from daq_fae.task_context import prepare_turn
    cases = [
        ('一台 EGO 和两台 WristCam 通过 HUB 连接 PC 后，如何开流、采集并检查保存结果？',
         {'kind': 'product', 'capability': 'inspect_topology',
          'entities': ['EGO', 'WristCam', 'HUB', 'PC'],
          'conditions': {'ego_count': 1, 'wristcam_count': 2,
                         'connection': 'hub_to_pc'}}),
        ('EGO Pro 在 Windows 11 上适用哪个 EgoViewer 版本？',
         {'kind': 'product', 'capability': 'check_software_support',
          'entities': ['EGO Pro', 'EgoViewer'], 'field': 'software_version',
          'conditions': {'os': 'Windows 11', 'software': 'EgoViewer'}}),
    ]
    for question, goal in cases:
        schema = DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [goal],
        })
        assert schema.goals[0].unmapped_condition_keys
        plan = prepare_turn(question, request_schema=schema)
        assert plan.requirements[0]['reason'] == 'unmapped_condition_unverified'


def test_context_statement_can_keep_product_scope_and_a_pure_clarification_goal():
    schema = DaqRequestSchema.from_candidate('我用 EGO 双目 1600×1200 版本', {
        'confidence': 'low', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目'], 'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'unknown', 'capability': 'clarify', 'entities': ['EGO 双目'],
             'conditions': {'resolution_variant': '1600x1200'}, 'field': 'user_intent'},
        ],
    })
    assert schema.goals[1].kind == 'unknown'
    assert schema.goals[1].entities == ()
    assert schema.goals[1].field is None
    assert schema.goals[1].condition_map() == {}


def test_context_statement_can_keep_unmapped_device_counts_without_schema_failure():
    schema = DaqRequestSchema.from_candidate(
        '我使用一台 EGO、两台 WristCam 和 HUB 接 PC 采集。', {
            'confidence': 'low', 'goals': [
                {'kind': 'product', 'capability': 'inspect_topology',
                 'entities': ['EGO', 'WristCam', 'HUB'],
                 'conditions': {'device_counts': {'EGO': 1, 'WristCam': 2, 'HUB': 1},
                                'host': 'PC', 'task': 'capture'}},
                {'kind': 'unknown', 'capability': 'clarify',
                 'entities': ['EGO', 'WristCam', 'HUB'], 'field': 'user_intent'},
            ],
        })
    assert schema.goals[0].unmapped_condition_keys == ('device_counts', 'host', 'task')
    assert schema.goals[1].entities == ()


def test_new_topology_constraint_shape_becomes_unsatisfied_gap_not_schema_error():
    from daq_fae.task_context import prepare_turn
    question = '一台 EGO 和两台 WristCam 通过 HUB 连接 PC 后，如何开流并检查保存结果？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{'kind': 'product',
                                       'capability': 'inspect_topology',
                                       'entities': ['EGO', 'WristCam', 'HUB'],
                                       'conditions': {'combination': {
                                           'devices': [{'name': 'EGO', 'count': 1},
                                                       {'name': 'WristCam', 'count': 2}],
                                           'hub': 'HUB'}}}],
    })
    assert schema.goals[0].unmapped_condition_keys == ('combination',)
    assert prepare_turn(question, request_schema=schema).requirements[0]['reason'] == \
        'unmapped_condition_unverified'


def test_non_spec_goal_does_not_make_an_unprovable_claim_field():
    from daq_fae.task_context import prepare_turn
    question = 'EGO 双目 1920×1200 版本的 RGB 可选分辨率有哪些？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目 1920×1200 版本'], 'field': 'product_variant',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目 1920×1200 版本'], 'field': 'rgb_resolution',
             'conditions': {'resolution_variant': '1920x1200'}},
        ],
    })
    assert schema.goals[0].field is None
    plan = prepare_turn(question, request_schema=schema,
                        grounded_targets={'entities': ['entity:ego-1920'],
                                          'conditions': {'resolution_variant': '1920x1200'}})
    assert 'field' not in plan.requirements[0]


class CandidateAdapter:
    def __init__(self, candidate):
        self.candidate = candidate
        self.calls = []

    def chat(self, messages, tools=None, required_tool=None):
        self.calls.append((messages, tools, required_tool))
        yield {'type': 'tool_call', 'name': 'submit_daq_request_schema',
               'arguments': self.candidate}
        yield {'type': 'stop', 'stop_reason': 'tool_use'}


@pytest.mark.parametrize('question,candidate,kind', [
    ('介绍一下你自己', {'confidence': 'high', 'goals': [
        {'kind': 'service', 'capability': 'service_identity'}]}, 'service'),
    ('Who are you?', {'confidence': 'high', 'goals': [
        {'kind': 'service', 'capability': 'service_identity'}]}, 'service'),
    ('EGO 双目水平视场角是多少？', {'confidence': 'high', 'goals': [
        {'kind': 'product', 'capability': 'lookup_spec', 'entities': ['EGO'],
         'field': 'horizontal_fov'}]}, 'product'),
    ('你是谁？EGO 的视场角是多少？', {'confidence': 'high', 'goals': [
        {'kind': 'service', 'capability': 'service_identity'},
        {'kind': 'product', 'capability': 'lookup_spec', 'entities': ['EGO'],
         'field': 'horizontal_fov'}]}, 'mixed'),
])
def test_model_candidate_produces_validated_composable_goals(question, candidate, kind):
    adapter = CandidateAdapter(candidate)
    schema = extract_request_schema(question, adapter=adapter)
    assert schema.request_kind == kind
    assert schema.original_question == question
    assert schema.effective_question == question
    assert len({goal.id for goal in schema.goals}) == len(schema.goals)
    assert adapter.calls[0][2] == 'submit_daq_request_schema'


def test_unknown_candidate_stays_unknown_without_product_search():
    schema = DaqRequestSchema.from_candidate('不太确定想问什么', {
        'confidence': 'low', 'goals': [{'kind': 'unknown', 'capability': 'clarify'}],
    })
    assert schema.request_kind == 'unknown'
    assert schema.goals[0].capability == 'clarify'


def test_whole_system_scope_is_preserved_as_a_stricter_product_qualifier():
    schema = DaqRequestSchema.from_candidate('EG-DB 整机深度精度是多少？', {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec', 'entities': ['EG-DB'],
            'field': 'depth_accuracy', 'conditions': {'scope': 'whole_system'},
        }],
    })
    assert schema.goals[0].condition_map()['scope'] == 'whole_system'


def test_english_question_requests_english_presentation():
    schema = DaqRequestSchema.from_candidate('Who are you?', {
        'confidence': 'high', 'goals': [{
            'kind': 'service', 'capability': 'service_identity',
        }],
    })
    assert schema.answer_language == 'en'


def test_model_cannot_invent_entity_or_override_original_question():
    with pytest.raises(SchemaExtractionError, match='entity_not_grounded'):
        DaqRequestSchema.from_candidate('这个参数是多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec', 'entities': ['EG-DB'],
                'field': 'accuracy',
            }],
        })


def test_effective_question_uses_only_user_history_not_model_rewrite():
    schema = DaqRequestSchema.from_candidate('它的视场角呢？', {
        'confidence': 'medium', 'effective_question': 'EGO 的视场角是 999°吗？',
        'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                   'entities': ['EGO'], 'field': 'horizontal_fov'}],
    }, prior_user_context='我用 EGO 双目 1600×1200 版本')
    assert 'EGO 双目 1600×1200 版本' in schema.effective_question
    assert '999' not in schema.effective_question


@pytest.mark.parametrize('candidate', [
    {'confidence': 'high', 'goals': []},
    {'confidence': 'high', 'goals': [{'kind': 'service', 'capability': 'lookup_spec'}]},
    {'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'service_identity'}]},
    {'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                      'field': 'horizontal_fov', 'entities': ['EGO'],
                                      'conditions': {'secret': 'injected'}}]},
])
def test_invalid_schema_fails_explicitly(candidate):
    with pytest.raises(SchemaExtractionError):
        DaqRequestSchema.from_candidate('EGO 参数', candidate)


class SequencedSchemaAdapter:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def chat(self, messages, tools=None, required_tool=None):
        self.calls.append((messages, required_tool))
        yield from self.responses[len(self.calls) - 1]


def test_schema_retries_once_when_auto_tool_choice_omits_required_call():
    adapter = SequencedSchemaAdapter([
        [{'type': 'text_delta', 'text': 'I can help.'},
         {'type': 'stop', 'stop_reason': 'end_turn'}],
        [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
          'arguments': {'confidence': 'high', 'goals': [
              {'kind': 'product', 'capability': 'check_software_support',
               'entities': ['EGO Pro', 'EgoViewer']}]}},
         {'type': 'stop', 'stop_reason': 'tool_use'}],
    ])
    schema = extract_request_schema(
        'EGO Pro 在 Windows 11 上适用哪个 EgoViewer 版本？', adapter=adapter)
    assert schema.goals[0].capability == 'check_software_support'
    assert len(adapter.calls) == 2
    assert all(call[1] == 'submit_daq_request_schema' for call in adapter.calls)
    assert '上一次回复没有调用' in adapter.calls[1][0][0]['content']


def test_schema_retry_is_bounded_and_invalid_candidate_is_not_hidden():
    no_tool = [{'type': 'stop', 'stop_reason': 'end_turn'}]
    adapter = SequencedSchemaAdapter([no_tool, no_tool])
    with pytest.raises(SchemaExtractionError, match='model_schema_missing_or_ambiguous'):
        extract_request_schema('EgoViewer 是什么？', adapter=adapter)
    assert len(adapter.calls) == 2

    invalid = SequencedSchemaAdapter([[{'type': 'tool_call',
        'name': 'submit_daq_request_schema',
        'arguments': {'confidence': 'high', 'goals': []}}]])
    with pytest.raises(SchemaExtractionError, match='schema_goals_invalid'):
        extract_request_schema('EgoViewer 是什么？', adapter=invalid)
    assert len(invalid.calls) == 1
