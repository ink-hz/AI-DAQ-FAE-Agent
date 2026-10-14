import pytest

from daq_fae.request_schema import DaqRequestSchema, SchemaExtractionError, extract_request_schema


def test_repeated_software_goal_does_not_fail_entire_request():
    question = ('EGO Pro 接 Windows 11 时，哪一版 EgoViewer、SDK 和固件能保证四路录制？'
                '请发可转给客户的官方下载链接。')
    software = {'kind': 'product', 'capability': 'check_software_support',
                'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}}
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            software, dict(software),
            {'kind': 'product', 'capability': 'sdk_evidence',
             'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}},
            {'kind': 'product', 'capability': 'official_links',
             'entities': ['EGO Pro'], 'conditions': {}},
        ],
    })
    assert [goal.capability for goal in schema.goals] == [
        'check_software_support', 'sdk_evidence', 'official_links']
    assert len({goal.id for goal in schema.goals}) == 3


def test_identity_goal_rejects_task_context_as_an_evidence_condition():
    question = 'UMI Finger 做采集时这是什么产品？'
    with pytest.raises(SchemaExtractionError, match='schema_identity_context_condition'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'medium', 'goals': [{
                'kind': 'product', 'capability': 'resolve_entity',
                'entities': ['UMI Finger'],
                'conditions': {'usage_context': 'data_collection'},
            }],
        })


def test_identity_selector_stays_on_identity_but_software_context_does_not():
    question = 'EGO 双目 1600×1200 版在 Windows 11 上能用 EgoViewer 吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目'],
             'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['EGO 双目', 'EgoViewer'],
             'conditions': {'platform': 'Windows 11',
                            'resolution_variant': '1600x1200'}},
        ],
    })
    assert schema.goals[0].condition_map() == {'resolution_variant': '1600x1200'}
    assert schema.goals[1].condition_map()['platform'] == 'Windows 11'


def test_normalization_collision_cannot_erase_distinct_software_fields():
    question = 'EGO Pro 在 Windows 11 的 EgoViewer、SDK 版本能保证录制吗？'
    shared = {'kind': 'product', 'capability': 'check_software_support',
              'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}}
    with pytest.raises(SchemaExtractionError, match='normalization_collision'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {**shared, 'field': 'viewer_version'},
                {**shared, 'field': 'sdk_version'},
            ],
        })


def test_normalization_collision_cannot_erase_unknown_goal_detail():
    question = 'EGO Pro 在 Windows 11 的 EgoViewer、SDK 版本能保证录制吗？'
    shared = {'kind': 'product', 'capability': 'check_software_support',
              'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}}
    with pytest.raises(SchemaExtractionError, match='normalization_collision'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {**shared, 'software': 'EgoViewer'},
                {**shared, 'software': 'SDK'},
            ],
        })


@pytest.mark.parametrize('broad_first', [True, False])
def test_normalization_collision_cannot_erase_unmapped_variant_condition(broad_first):
    question = 'EGO 双目 1600×1200 版的水平视角是多少？'
    shared = {'kind': 'product', 'capability': 'lookup_spec',
              'entities': ['EGO 双目'], 'field': 'horizontal_fov'}
    broad = {**shared, 'conditions': {'resolution_variant':
                                     ['1600x1200', '3200x2400']}}
    exact = {**shared, 'conditions': {'resolution_variant': '1600x1200'}}
    with pytest.raises(SchemaExtractionError, match='normalization_collision'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high',
            'goals': [broad, exact] if broad_first else [exact, broad],
        })


def test_field12_variant_phrase_is_a_condition_not_a_second_product():
    question = '它的水平、垂直视场角分别是多少？原生分辨率最高多少帧？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目', '1920x1200 版本'],
            'field': 'horizontal_fov',
            'conditions': {'resolution_variant': '1920x1200'},
        }],
    }, prior_user_context='我现在用 EGO 双目 1920×1200 版本。')
    assert schema.goals[0].entities == ('EGO 双目',)
    assert schema.goals[0].condition_map()['resolution_variant'] == '1920x1200'


def test_followup_goals_share_one_explicit_variant_without_sharing_measurement_mode():
    question = '它的水平、垂直视场角分别是多少？原生分辨率最高多少帧？'
    prior = '我现在用 EGO 双目 1920×1200 版本，RGB 按原生分辨率采。'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': field, 'conditions': {}}
            for field in ('horizontal_fov', 'vertical_fov')
        ] + [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': field,
             'conditions': {'resolution_variant': '1920x1200'}}
            for field in ('rgb_resolution', 'rgb_max_frame_rate')
        ],
    }, prior_user_context=prior)
    assert len(schema.goals) == 4
    assert all(goal.condition_map() == {'resolution_variant': '1920x1200'}
               for goal in schema.goals)


def test_followup_scoped_fov_goals_match_reviewed_evidence():
    from pathlib import Path
    from daq_fae.domain_evidence import DaqEvidencePolicy
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.task_context import prepare_turn

    question = '它的水平、垂直视场角分别是多少？原生分辨率最高多少帧？'
    prior = '我现在用 EGO 1920×1200 版本，RGB 按原生分辨率采。'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': field, 'conditions': {}}
            for field in ('horizontal_fov', 'vertical_fov')
        ] + [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': field,
             'conditions': {'resolution_variant': '1920x1200'}}
            for field in ('rgb_resolution', 'rgb_max_frame_rate')
        ],
    }, prior_user_context=prior)
    active = read_active_release(Path(__file__).resolve().parents[1] /
                                 'data/knowledge/published')
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    plan = prepare_turn(question, request_schema=schema)
    box = DaqToolBox(question=question, knowledge=view, role='internal_fae',
                     requirements=plan.requirements)
    session = DaqEvidencePolicy().begin({'requirements': plan.requirements})
    for field in ('horizontal_fov', 'vertical_fov', 'rgb_resolution',
                  'rgb_max_frame_rate'):
        result = box.dispatch('lookup_spec', {
            'entity': 'EGO', 'field': field,
            'conditions': {'resolution_variant': '1920x1200'},
        })
        assert result.status == 'ok'
        session.observe('lookup_spec', result)
    assert set(session.snapshot().requirement_status.values()) == {'satisfied'}


def test_peer_variant_is_not_copied_to_other_product_or_comparison():
    question = ('EGO 双目 1600×1200 和 1920×1200 两版的视角，'
                '以及 UMI Finger 的双目 Mono 分辨率是多少？')
    with pytest.raises(SchemaExtractionError, match='schema_missing_variant_scope'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'horizontal_fov',
                 'conditions': {'resolution_variant': '1920x1200'}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'vertical_fov',
                 'conditions': {}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['UMI Finger'], 'field': 'stereo_mono_resolution',
                 'conditions': {}},
            ],
        })


def test_single_ego_variant_does_not_scope_unrelated_umi_goal():
    question = ('EGO 双目 1920×1200 版的水平视角和帧率是多少？'
                'UMI Finger 的双目 Mono 分辨率呢？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'horizontal_fov',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['UMI Finger'], 'field': 'stereo_mono_resolution',
             'conditions': {}},
        ],
    })
    assert schema.goals[0].condition_map() == {'resolution_variant': '1920x1200'}
    assert schema.goals[2].condition_map() == {}


def test_new_variant_cannot_inherit_old_peer_selector():
    question = '如果换成 EGO 双目 3200×2400 版，它的视角和帧率怎样？'
    prior = '原来使用 EGO 双目 1920×1200 版本。'
    with pytest.raises(SchemaExtractionError, match='schema_missing_variant_scope'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'horizontal_fov',
                 'conditions': {}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1920x1200'}},
            ],
        }, prior_user_context=prior)


def test_old_variant_pronoun_does_not_borrow_new_variant_from_later_clause():
    question = ('它的水平视角是多少？如果换成 EGO 双目 1600×1200 版本，'
                'RGB 最大帧率又是多少？')
    prior = '我现在用 EGO 双目 1920×1200 版本。'
    with pytest.raises(SchemaExtractionError, match='schema_missing_variant_scope'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'horizontal_fov',
                 'conditions': {}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1600x1200'}},
            ],
        }, prior_user_context=prior)


def test_variant_list_drops_only_matching_selector_phrases_not_other_products():
    question = 'EGO 双目 1600×1200 版本与 1920×1200 版本和 WristCam 的区别？'
    with pytest.raises(SchemaExtractionError, match='multi_entity_selector'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['EGO 双目', '1600x1200 版本', '1920x1200 版本', 'WristCam'],
                'field': 'horizontal_fov',
                'conditions': {'resolution_variant': ['1600x1200', '1920x1200']},
            }],
        })
    unresolved = DaqRequestSchema.from_candidate('1920×1200 版本怎么样？', {
        'confidence': 'low', 'goals': [{
            'kind': 'product', 'capability': 'resolve_entity',
            'entities': ['1920x1200 版本'],
            'conditions': {'resolution_variant': '1920x1200'},
        }],
    })
    assert unresolved.goals[0].entities == ('1920x1200 版本',)


def test_multi_entity_spec_goals_expand_to_independent_evidence_needs():
    from daq_fae.task_context import prepare_turn

    question = ('UMI 视觉模块和 UMI Finger 的双目 Mono 分辨率一样吗？'
                '视觉模块的基线也能按 Finger 的 50 mm 填吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['UMI 视觉模块', 'UMI Finger'],
             'field': field, 'conditions': {}}
            for field in ('stereo_mono_resolution', 'stereo_mono_baseline')
        ],
    })
    assert len(schema.goals) == 4
    assert {(goal.entities, goal.field) for goal in schema.goals} == {
        ((entity,), field)
        for entity in ('UMI 视觉模块', 'UMI Finger')
        for field in ('stereo_mono_resolution', 'stereo_mono_baseline')
    }
    plan = prepare_turn(question, request_schema=schema)
    assert len(plan.requirements) == 4
    assert len({req['goal_id'] for req in plan.requirements}) == 4
    assert all(len(req['entities']) == 1 for req in plan.requirements)


def test_multi_entity_spec_cannot_copy_component_scope_to_device():
    question = 'UMI 视觉模块（组件）和 UMI Finger（设备）的双目 Mono 分辨率各是多少？'
    with pytest.raises(SchemaExtractionError, match='multi_entity_conditions'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['UMI 视觉模块', 'UMI Finger'],
                'field': 'stereo_mono_resolution',
                'conditions': {'scope': 'component'},
            }],
        })


def test_reviewed_claim_scope_uses_each_entities_verified_type():
    from pathlib import Path
    from daq_fae.domain_evidence import DaqEvidencePolicy
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.task_context import prepare_turn

    question = 'UMI 视觉模块（组件）和 UMI Finger（设备）的双目 Mono 分辨率各是多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': [entity], 'field': 'stereo_mono_resolution',
             'conditions': {'scope': scope}}
            for entity, scope in [('UMI 视觉模块', 'component'),
                                  ('UMI Finger', 'device')]
        ],
    })
    requirements = prepare_turn(question, request_schema=schema).requirements
    active = read_active_release(Path(__file__).resolve().parents[1] /
                                 'data/knowledge/published')
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    box = DaqToolBox(question=question, knowledge=view, role='internal_fae',
                     requirements=requirements)
    session = DaqEvidencePolicy().begin({'requirements': requirements})
    for entity, scope, claim in [
            ('UMI 视觉模块', 'component', 'claim:umi-vision-module-mono-resolution'),
            ('UMI Finger', 'device', 'claim:umi-finger-mono-resolution')]:
        result = box.dispatch('lookup_spec', {'entity': entity,
                                               'field': 'stereo_mono_resolution',
                                               'conditions': {'scope': scope}})
        assert result.status == 'ok'
        assert [row['id'] for row in result.content['matches']] == [claim]
        session.observe('lookup_spec', result)
    assert set(session.snapshot().requirement_status.values()) == {'satisfied'}
    wrong = box.dispatch('lookup_spec', {'entity': 'UMI Finger',
                                          'field': 'stereo_mono_resolution',
                                          'conditions': {'scope': 'component'}})
    assert wrong.status == 'not_found'
    hidden = DaqToolBox(question=question, knowledge=view, role='channel',
                        requirements=requirements).dispatch('lookup_spec', {
                            'entity': 'UMI Finger',
                            'field': 'stereo_mono_resolution',
                            'conditions': {'scope': 'device'},
                        })
    assert hidden.status == 'not_found'


def test_simultaneous_hub_power_needs_its_own_reviewed_condition():
    from pathlib import Path
    from daq_fae.domain_evidence import DaqEvidencePolicy
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.task_context import prepare_turn

    question = '数采 HUB 有几个下游端口？三个口同时用时总供电输出多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'downstream_port_count'},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'downstream_port_power_output',
             'conditions': {'simultaneous_port_count': 3}},
        ],
    })
    requirements = prepare_turn(question, request_schema=schema).requirements
    power = next(req for req in requirements if req['field'] ==
                 'total_downstream_port_power_output')
    assert power['conditions'] == {'simultaneous_port_count': 3}
    active = read_active_release(Path(__file__).resolve().parents[1] /
                                 'data/knowledge/published')
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    box = DaqToolBox(question=question, knowledge=view, role='internal_fae',
                     requirements=requirements)
    session = DaqEvidencePolicy().begin({'requirements': requirements})
    single = box.dispatch('lookup_spec', {'entity': '数采 HUB',
                                           'field': 'downstream_port_power_output',
                                           'conditions': {'per_port': True}})
    assert single.status == 'ok'
    session.observe('lookup_spec', single)
    assert session.snapshot().requirement_status[power['id']] != 'satisfied'
    total = box.dispatch('lookup_spec', {'entity': '数采 HUB',
                                          'field': 'total_downstream_port_power_output',
                                          'conditions': {'simultaneous_port_count': 3}})
    assert total.status == 'not_found'
    session.observe('lookup_spec', total)
    assert session.snapshot().requirement_status[power['id']] == 'missing'


@pytest.mark.parametrize('model_conditions', [
    {}, {'port_count': 3}, {'simultaneous_port_count': 3},
])
def test_explicit_three_port_total_normalizes_single_power_goal(model_conditions):
    question = '数采 HUB 有几个下游端口？三个口同时用时总供电输出多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'downstream_port_count'},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'downstream_port_power_output',
             'conditions': model_conditions},
        ],
    })
    power = next(goal for goal in schema.goals if goal.field and
                 'power_output' in goal.field)
    assert power.field == 'total_downstream_port_power_output'
    assert power.condition_map() == {'simultaneous_port_count': 3}


@pytest.mark.parametrize('model_conditions', [
    {'port_count': 2}, {'simultaneous_port_count': 2}, {'per_port': True},
])
def test_explicit_three_port_total_rejects_wrong_or_single_port_scope(model_conditions):
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power'):
        DaqRequestSchema.from_candidate(
            '数采 HUB 三个口同时用时总供电输出多少？', {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'lookup_spec',
                    'entities': ['数采 HUB'],
                    'field': 'downstream_port_power_output',
                    'conditions': model_conditions,
                }],
            })


def test_total_power_and_single_port_power_need_distinct_goals():
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power'):
        DaqRequestSchema.from_candidate(
            '数采 HUB 每口供电多少？三个口同时总供电输出多少？', {
                'confidence': 'high', 'goals': [
                    {'kind': 'product', 'capability': 'lookup_spec',
                     'entities': ['数采 HUB'],
                     'field': 'downstream_port_power_output'},
                    {'kind': 'product', 'capability': 'lookup_spec',
                     'entities': ['数采 HUB'],
                     'field': 'downstream_port_power_output',
                     'conditions': {'per_port': True}},
                ],
            })


def test_aggregate_total_cannot_swallow_explicit_per_port_power_goal():
    question = '数采 HUB 每口供电多少？三个口同时总供电输出多少？'
    for only_goal in [
        {'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['数采 HUB'], 'field': 'downstream_port_power_output'},
        {'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['数采 HUB'],
         'field': 'total_downstream_port_power_output',
         'conditions': {'simultaneous_port_count': 3}},
    ]:
        with pytest.raises(SchemaExtractionError, match='schema_aggregate_power_goal_missing'):
            DaqRequestSchema.from_candidate(question, {
                'confidence': 'high', 'goals': [only_goal],
            })


def test_port_count_question_does_not_create_aggregate_power_goal():
    schema = DaqRequestSchema.from_candidate(
        '一台 EGO、两台 WristCam、数采 HUB 四个下游口够用吗？每口供电多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'downstream_port_power_output',
                'conditions': {'per_port': True},
            }],
        })
    assert schema.goals[0].field == 'downstream_port_power_output'
    assert schema.goals[0].condition_map() == {'per_port': True}


@pytest.mark.parametrize('conditions', [
    {'simultaneous_port_count': 3},
    {'per_port': True, 'simultaneous_port_count': 3},
])
def test_field05_single_port_power_rejects_aggregate_context(conditions):
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power'):
        DaqRequestSchema.from_candidate(
            '一台 EGO、两台 WristCam 和数采 HUB，四个下游口够用吗、每口供电多少？'
            '能否直接三路同步采集？', {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'lookup_spec',
                    'entities': ['数采 HUB'],
                    'field': 'downstream_port_power_output',
                    'conditions': conditions,
                }],
            })


def test_explicit_per_port_power_fills_omitted_model_condition():
    schema = DaqRequestSchema.from_candidate(
        '数采 HUB 每口供电多少？', {'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['数采 HUB'], 'field': 'downstream_port_power_output',
            'conditions': {},
        }]})
    assert schema.goals[0].field == 'downstream_port_power_output'
    assert schema.goals[0].condition_map() == {'per_port': True}


@pytest.mark.parametrize('prior_user_context', ['', '我用数采 HUB 的三个下游口。'])
def test_other_product_per_port_question_does_not_require_hub_goal(prior_user_context):
    schema = DaqRequestSchema.from_candidate(
        'UMI Finger 每口供电是多少？', {'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['UMI Finger'], 'field': 'power_output',
        }]}, prior_user_context=prior_user_context)
    assert schema.goals[0].field == 'power_output'


def test_unknown_other_product_per_port_question_does_not_inherit_hub_goal():
    schema = DaqRequestSchema.from_candidate(
        'UMI Finger 每口供电是多少？', {'confidence': 'low', 'goals': [{
            'kind': 'unknown', 'capability': 'clarify',
        }]}, prior_user_context='我正在使用数采 HUB。')
    assert schema.goals[0].capability == 'clarify'


def test_aggregate_power_cannot_be_omitted_behind_port_count():
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power_goal_missing'):
        DaqRequestSchema.from_candidate(
            '数采 HUB 有几个下游口？三个口同时用时总供电输出多少？', {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'lookup_spec',
                    'entities': ['数采 HUB'], 'field': 'downstream_port_count',
                }],
            })


def test_aggregate_power_short_followup_uses_explicit_current_port_count():
    schema = DaqRequestSchema.from_candidate(
        '这三个口同时用时总供电多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'total_downstream_port_power_output',
                'conditions': {'simultaneous_port_count': 3},
            }],
        }, prior_user_context='我在用数采 HUB。')
    assert schema.goals[0].field == 'total_downstream_port_power_output'
    assert schema.goals[0].condition_map() == {'simultaneous_port_count': 3}


def test_two_explicit_aggregate_counts_require_two_separate_goals():
    question = '数采 HUB 三口与四口同时使用时总供电分别多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['数采 HUB'],
            'field': 'total_downstream_port_power_output',
            'conditions': {'simultaneous_port_count': count},
        } for count in (3, 4)],
    })
    assert {goal.condition_map()['simultaneous_port_count'] for goal in schema.goals} == {
        3, 4,
    }
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power_goal_missing'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'total_downstream_port_power_output',
                'conditions': {'simultaneous_port_count': 3},
            }],
        })


def test_other_product_total_power_does_not_create_hub_total_goal():
    schema = DaqRequestSchema.from_candidate(
        '数采 HUB 有几个下游口？UMI Finger 的总供电输出是多少？', {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['数采 HUB'], 'field': 'downstream_port_count'},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['UMI Finger'], 'field': 'power_output'},
            ],
        })
    assert [goal.field for goal in schema.goals] == [
        'downstream_port_count', 'power_output',
    ]


def test_aggregate_power_goal_must_target_hub_not_another_product():
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power_goal_missing'):
        DaqRequestSchema.from_candidate(
            '数采 HUB 三个口同时总供电多少？UMI Finger 是什么产品？', {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'lookup_spec',
                    'entities': ['UMI Finger'],
                    'field': 'total_downstream_port_power_output',
                    'conditions': {'simultaneous_port_count': 3},
                }],
            })


def test_other_product_total_power_in_same_sentence_does_not_create_hub_goal():
    schema = DaqRequestSchema.from_candidate(
        '数采 HUB 三口接 UMI Finger，UMI Finger 的总功率是多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['UMI Finger'], 'field': 'power_output',
            }],
        })
    assert schema.goals[0].field == 'power_output'


def test_aggregate_power_short_followup_inherits_unique_user_port_count():
    schema = DaqRequestSchema.from_candidate(
        '那总供电输出多少？', {'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['数采 HUB'],
            'field': 'total_downstream_port_power_output',
            'conditions': {'simultaneous_port_count': 3},
        }]}, prior_user_context='我用数采 HUB 的三个下游口。')
    assert schema.goals[0].condition_map()['simultaneous_port_count'] == 3


def test_english_three_ports_requires_three_port_total_goal():
    question = ('For the DAQ HUB, what is the total power output '
                'when three ports are used simultaneously?')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['DAQ HUB'],
            'field': 'total_downstream_port_power_output',
            'conditions': {'simultaneous_port_count': 3},
        }],
    })
    assert schema.goals[0].condition_map()['simultaneous_port_count'] == 3


def test_hub_capacity_four_is_not_another_simultaneous_total_goal():
    schema = DaqRequestSchema.from_candidate(
        '数采 HUB 四个下游口中三个口同时用时总供电输出多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'total_downstream_port_power_output',
                'conditions': {'simultaneous_port_count': 3},
            }],
        })
    assert schema.goals[0].condition_map()['simultaneous_port_count'] == 3


def test_comma_followup_total_inherits_current_clause_simultaneous_count():
    schema = DaqRequestSchema.from_candidate(
        '数采 HUB 四口中三个口同时使用，总供电输出多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'total_downstream_port_power_output',
                'conditions': {'simultaneous_port_count': 3},
            }],
        })
    assert schema.goals[0].condition_map()['simultaneous_port_count'] == 3


def test_explicit_new_product_total_overrides_prior_hub_topic():
    schema = DaqRequestSchema.from_candidate(
        '那 UMI Finger 的总供电输出多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['UMI Finger'], 'field': 'power_output',
            }],
        }, prior_user_context='我用数采 HUB 的三个下游口。')
    assert schema.goals[0].field == 'power_output'


@pytest.mark.parametrize('explicit_location', ['scope', 'conditions'])
def test_explicit_claim_scope_overrides_entity_device_type(explicit_location):
    from daq_fae.domain_tools import DaqToolBox
    row = {'kind': 'claim', 'scope': {'product': 'umi-finger'},
           'data': {'entity_id': 'entity:umi-finger', 'conditions': {}}}
    (row['scope'] if explicit_location == 'scope' else
     row['data']['conditions'])['scope'] = 'component'
    entities = {'entity:umi-finger': {'kind': 'entity', 'data': {
        'name': 'UMI Finger', 'entity_type': 'device'}}}
    assert not DaqToolBox._condition_supported(row, 'scope', 'device', entities)
    assert DaqToolBox._condition_supported(row, 'scope', 'component', entities)


def test_multi_entity_identity_goals_expand_without_sharing_status():
    question = 'EGO Pro 和 WristCam 分别是什么产品？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'resolve_entity',
            'entities': ['EGO Pro', 'WristCam'], 'conditions': {},
        }],
    })
    assert len(schema.goals) == 2
    assert {goal.entities for goal in schema.goals} == {
        ('EGO Pro',), ('WristCam',),
    }


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


def test_field02_fps_target_is_not_a_claim_measurement_condition():
    from daq_fae.task_context import prepare_turn
    question = 'EGO 双目 1920×1200 原生 RGB 能跑 60 fps 吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
            'conditions': {'resolution_variant': '1920x1200',
                           'rgb_resolution': '1920x1200',
                           'rgb_max_frame_rate': 60},
        }],
    })
    requirement = prepare_turn(question, request_schema=schema).requirements[0]
    assert requirement['conditions'] == {'resolution_variant': '1920x1200'}
    assert requirement.get('reason') != 'unmapped_condition_unverified'
    assert requirement['question_constraints'] == {
        'rgb_resolution': '1920x1200', 'rgb_max_frame_rate': 60,
    }


def test_field01_capture_task_does_not_filter_static_fov_claim():
    from daq_fae.task_context import prepare_turn
    question = 'EGO 双目 1600×1200 版本做手部动作采集，水平视场角是多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目'], 'field': 'horizontal_fov',
            'conditions': {'resolution_variant': '1600x1200'},
        }],
    })
    requirement = prepare_turn(question, request_schema=schema).requirements[0]
    assert requirement['conditions'] == {'resolution_variant': '1600x1200'}


def test_field01_comparison_expands_each_variant_to_an_independent_spec_goal():
    from daq_fae.task_context import prepare_turn
    question = 'EGO 双目 1600×1200 和 1920×1200 两个版本的水平视角怎么比？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目'], 'field': 'horizontal_fov',
            'conditions': {'resolution_variant': ['1600x1200', '1920x1200']},
        }],
    })
    assert len(schema.goals) == 2
    assert [goal.condition_map()['resolution_variant'] for goal in schema.goals] == [
        '1600x1200', '1920x1200']
    assert [item['conditions']['resolution_variant'] for item in
            prepare_turn(question, request_schema=schema).requirements] == [
                '1600x1200', '1920x1200']


def test_field01_generic_identity_goal_does_not_block_reviewed_variant_comparison():
    from daq_fae.task_context import prepare_turn
    question = ('做手部动作采集，优先要宽视野和 60 fps，在 EGO 双目 '
                '1600×1200 和 1920×1200 两个版本里怎么选？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目']},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'horizontal_fov',
             'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'horizontal_fov',
             'conditions': {'resolution_variant': '1920x1200'}},
        ],
    })
    assert [goal.capability for goal in schema.goals] == [
        'resolve_entity', 'resolve_entity', 'lookup_spec', 'lookup_spec']
    assert [goal.condition_map().get('resolution_variant') for goal in
            schema.goals[:2]] == ['1600x1200', '1920x1200']
    assert [item['conditions'].get('resolution_variant') for item in
            prepare_turn(question, request_schema=schema).requirements[:2]] == [
                '1600x1200', '1920x1200']


def test_field13_rgb_mode_question_cannot_become_stereo_mono_resolution():
    question = ('EGO 双目 1920×1200 版除了原生分辨率还能选 '
                '1920×1080 吗？切到 1080 后 RGB 能到 60 fps 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': 'stereo_mono_resolution',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200',
                            'resolution': '1920x1080'}},
        ],
    })
    assert schema.goals[0].field == 'rgb_resolution'


def test_field13_output_mode_cannot_become_product_variant():
    question = ('EGO 双目 1920×1200 版除了原生分辨率还能选 '
                '1920×1080 吗？切到 1080 后 RGB 能到 60 fps 吗？')
    with pytest.raises(SchemaExtractionError, match='resolution_variant_not_user_variant'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_resolution',
                 'conditions': {'resolution_variant': '1920x1080'}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1920x1080'}},
            ],
        })


def test_field13_output_mode_remains_measurement_condition_on_base_variant():
    from daq_fae.task_context import prepare_turn
    question = ('EGO 双目 1920×1200 版除了原生分辨率还能选 '
                '1920×1080 吗？切到 1080 后 RGB 能到 60 fps 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_resolution',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200',
                            'resolution': '1920x1080'}},
        ],
    })
    goals = [goal for goal in schema.goals if goal.field == 'rgb_max_frame_rate']
    assert len(goals) == 1
    assert goals[0].condition_map() == {
        'resolution_variant': '1920x1200', 'resolution': '1920x1080',
    }
    requirement = [item for item in prepare_turn(question, request_schema=schema).requirements
                   if item.get('field') == 'rgb_max_frame_rate'][0]
    assert requirement['conditions'] == {
        'resolution_variant': '1920x1200', 'resolution': '1920x1080',
    }


def test_field13_explicit_mode_requires_a_matching_fps_measurement_goal():
    question = ('EGO 双目 1920×1200 版除了原生分辨率还能选 '
                '1920×1080 吗？切到 1080 后 RGB 能到 60 fps 吗？')
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_resolution',
                 'conditions': {'resolution_variant': '1920x1200'}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1920x1200'}},
            ],
        })


@pytest.mark.parametrize('question', [
    'EGO 双目 1920×1200 版在 1920×1080 下 RGB 最高多少帧？',
    'EGO 双目 1920×1200 版改用 1920×1080，RGB 帧率能到 60 吗？',
    'EGO 双目 1600×1200 版本和 1920×1200 版本各是多少帧？前者在 1280×720 下又是多少？',
])
def test_explicit_mode_fps_query_requires_the_mode_condition(question):
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_frame_rate',
                 'conditions': {'resolution_variant': '1920x1200'}},
            ],
        })


def test_mode_change_for_recording_does_not_rewrite_native_fps_question():
    question = ('EGO 双目 1920×1200 版原生 RGB 帧率是多少？'
                '切到 1920×1080 后还能正常录像吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_procedure',
             'entities': ['EGO 双目'], 'conditions': {'resolution': '1920x1080'}},
        ],
    })
    assert schema.goals[0].condition_map() == {'resolution_variant': '1920x1200'}


def test_mode_fps_goal_must_bind_the_requested_variant_and_rgb_channel():
    question = ('EGO 双目 1600×1200 版与 1920×1200 版对比；'
                '1920×1200 版切到 1920×1080 后 RGB 最高多少帧？')
    for field, variant in [('rgb_max_frame_rate', '1600x1200'),
                           ('depth_frame_rate', '1920x1200')]:
        with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
            DaqRequestSchema.from_candidate(question, {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'lookup_spec',
                    'entities': ['EGO 双目'], 'field': field,
                    'conditions': {'resolution_variant': variant,
                                   'resolution': '1920x1080'},
                }],
            })


def test_two_output_modes_each_require_a_fps_goal():
    question = ('EGO 双目 1920×1200 版的两种输出模式 '
                '1920×1080 与 1280×720 各是多少帧？')
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                'conditions': {'resolution_variant': '1920x1200',
                               'resolution': '1920x1080'},
            }],
        })


@pytest.mark.parametrize('question,capability,field', [
    ('EGO 双目 1920×1200 版原生已知 30 fps，切到 1920×1080 后 SDK 能用吗？',
     'check_software_support', None),
    ('EGO 双目 1920×1200 版原生 RGB 是 30 fps，切到 1920×1080 后怎么录制？',
     'lookup_procedure', None),
    ('EGO 双目 1920×1200 版原生 30 fps，切到 1920×1080 后能否选这个模式？',
     'lookup_spec', 'rgb_resolution'),
])
def test_background_fps_does_not_force_mode_frame_rate_goal(question, capability, field):
    goal = {'kind': 'product', 'capability': capability, 'entities': ['EGO 双目'],
            'conditions': {'resolution_variant': '1920x1200'}}
    if field:
        goal['field'] = field
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [goal],
    })
    assert len(schema.goals) == 1


def test_rgb_mode_does_not_apply_to_following_mono_fps_question():
    question = ('EGO 双目 1920×1200 版在 1920×1080 下 RGB 最高多少帧，'
                'Mono 最高多少帧？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200',
                            'resolution': '1920x1080'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'mono_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200'}},
        ],
    })
    assert schema.goals[1].condition_map() == {'resolution_variant': '1920x1200'}


def test_return_to_native_fps_requires_a_second_mode_goal():
    question = ('EGO 双目 1920×1200 版在 1920×1080 下 RGB 多少帧，'
                '回到原生分辨率又是多少帧？')
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1920x1200',
                                'resolution': '1920x1080'}},
            ],
        })


def test_followup_mode_uses_single_explicit_prior_variant():
    question = '切到 1920×1080 后 RGB 最高多少帧？'
    prior = '我在用 EGO 双目 1920×1200 版。'
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1600x1200',
                                'resolution': '1920x1080'}},
            ],
        }, prior_user_context=prior)


def test_same_mode_explicit_rgb_and_mono_each_require_fps_goal():
    question = 'EGO 双目 1920×1200 版的 RGB 和 Mono 在 1920×1080 下各是多少帧？'
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1920x1200',
                                'resolution': '1920x1080'}},
            ],
        })


def test_comparison_of_two_explicit_variants_is_not_a_mode_change():
    question = 'EGO 双目 1600×1200 版本和 1920×1200 版本的 RGB 帧率分别是多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': ['1600x1200', '1920x1200']}},
        ],
    })
    assert {goal.condition_map()['resolution_variant'] for goal in schema.goals} == {
        '1600x1200', '1920x1200',
    }


def test_field13_model_schema_retries_with_version_and_mode_distinguished():
    question = ('EGO 双目 1920×1200 版除了原生分辨率还能选 '
                '1920×1080 吗？切到 1080 后 RGB 能到 60 fps 吗？')

    class SequenceAdapter:
        def __init__(self):
            self.calls = []

        def chat(self, messages, tools=None, required_tool=None):
            self.calls.append(messages)
            condition = ({'resolution_variant': '1920x1080'} if len(self.calls) == 1 else
                         {'resolution_variant': '1920x1200', 'resolution': '1920x1080'})
            yield {'type': 'tool_call', 'name': 'submit_daq_request_schema',
                   'arguments': {'confidence': 'high', 'goals': [
                       {'kind': 'product', 'capability': 'lookup_spec',
                        'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                        'conditions': condition},
                   ]}}

    adapter = SequenceAdapter()
    schema = extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 2
    assert schema.goals[0].condition_map() == {
        'resolution_variant': '1920x1200', 'resolution': '1920x1080',
    }
    assert '产品版本' in adapter.calls[1][0]['content']


def test_field12_native_fps_followup_requires_native_rgb_resolution_evidence():
    question = '如果设备换成 1600×1200 版本，这三项要怎么改？'
    prior = ('我现在用 EGO 双目 1920×1200 版本，RGB 按原生分辨率采。\n'
             '它的水平、垂直视场角分别是多少？原生分辨率最高多少帧？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'horizontal_fov',
             'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'vertical_fov',
             'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1600x1200'}},
        ],
    }, prior_user_context=prior)
    assert any(goal.capability == 'lookup_spec' and goal.field == 'rgb_resolution'
               and goal.condition_map() == {'resolution_variant': '1600x1200'}
               for goal in schema.goals)


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


def test_schema_retries_once_when_model_invents_an_entity():
    class SequenceAdapter:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None, required_tool=None):
            self.calls += 1
            entity = 'invented-model' if self.calls == 1 else 'EGO Pro'
            yield {'type': 'tool_call', 'name': 'submit_daq_request_schema',
                   'arguments': {'confidence': 'medium', 'goals': [{
                       'kind': 'product', 'capability': 'resolve_entity',
                       'entities': [entity],
                   }]}}

    adapter = SequenceAdapter()
    schema = extract_request_schema('EGO Pro 的规格是多少？', adapter=adapter)
    assert adapter.calls == 2
    assert schema.goals[0].entities == ('EGO Pro',)


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


def test_selected_answer_language_overrides_model_and_question_language():
    candidate = {'confidence': 'high', 'answer_language': 'zh', 'goals': [
        {'kind': 'service', 'capability': 'service_identity'}]}
    assert DaqRequestSchema.from_candidate(
        '介绍一下你自己', candidate, selected_answer_language='en',
    ).answer_language == 'en'
    assert DaqRequestSchema.from_candidate(
        'Who are you?', {**candidate, 'answer_language': 'en'},
        selected_answer_language='zh',
    ).answer_language == 'zh'


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


def test_identity_context_condition_retries_without_losing_the_identity_goal():
    from pathlib import Path
    from daq_fae.domain_evidence import DaqEvidencePolicy
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.task_context import prepare_turn
    question = 'UMI Finger 做采集时这是什么产品？'
    def event(conditions):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'medium', 'goals': [{
                     'kind': 'product', 'capability': 'resolve_entity',
                     'entities': ['UMI Finger'], 'conditions': conditions,
                 }]}}]
    adapter = SequencedSchemaAdapter([
        event({'usage_context': 'data_collection'}), event({}),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 2
    assert 'schema_identity_context_condition' not in str(schema)
    assert '采集任务' in adapter.calls[1][0][0]['content']
    assert schema.goals[0].condition_map() == {}
    requirement = prepare_turn(question, request_schema=schema).requirements[0]
    assert requirement['conditions'] == {}
    assert requirement.get('reason') != 'unmapped_condition_unverified'
    active = read_active_release(Path(__file__).resolve().parents[1] /
                                 'data/knowledge/published')
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    for role, expected_status in [('internal_fae', 'satisfied'),
                                  ('channel', 'missing')]:
        box = DaqToolBox(question=question, knowledge=view, role=role,
                         requirements=[requirement])
        result = box.dispatch('resolve_entity', {'text': 'UMI Finger'})
        session = DaqEvidencePolicy().begin({'requirements': [requirement]})
        session.observe('resolve_entity', result)
        assert session.snapshot().requirement_status[requirement['id']] == expected_status


def test_unknown_identity_qualifier_cannot_be_silently_dropped():
    with pytest.raises(SchemaExtractionError, match='schema_identity_context_condition'):
        DaqRequestSchema.from_candidate('UMI Finger Rev B 是什么产品？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'resolve_entity',
                'entities': ['UMI Finger'], 'conditions': {'model_variant': 'Rev B'},
            }],
        })


def test_identity_retry_preserves_separate_software_question():
    from daq_fae.task_context import prepare_turn
    question = 'UMI Finger 是什么产品？在 Windows 11 上能用 EgoViewer 采集吗？'
    def event(goals):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': goals}}]
    identity = {'kind': 'product', 'capability': 'resolve_entity',
                'entities': ['UMI Finger']}
    software = {'kind': 'product', 'capability': 'check_software_support',
                'entities': ['UMI Finger', 'EgoViewer'],
                'conditions': {'platform': 'Windows 11'}}
    adapter = SequencedSchemaAdapter([
        event([{**identity, 'conditions': {'platform': 'Windows 11'}}, software]),
        event([identity, software]),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    requirements = prepare_turn(question, request_schema=schema).requirements
    assert len(adapter.calls) == 2
    assert {item['capability'] for item in requirements} == {
        'resolve_entity', 'check_software_support',
    }
    assert next(item for item in requirements if item['capability'] ==
                'resolve_entity')['conditions'] == {}
    assert next(item for item in requirements if item['capability'] ==
                'check_software_support')['conditions']['platform'] == 'Windows 11'


def test_schema_retry_explains_normalization_collision_for_software_components():
    question = ('EGO Pro 接 Windows 11 时，哪一版 EgoViewer、SDK 和固件能保证四路录制？'
                '请发官方下载链接。')
    def event(goals):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': goals}}]
    shared = {'kind': 'product', 'capability': 'check_software_support',
              'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}}
    adapter = SequencedSchemaAdapter([
        event([{**shared, 'field': 'viewer_version'},
               {**shared, 'field': 'sdk_version'}]),
        event([shared,
               {'kind': 'product', 'capability': 'sdk_evidence',
                'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}},
               {'kind': 'product', 'capability': 'official_links',
                'entities': ['EGO Pro']}]),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    assert [goal.capability for goal in schema.goals] == [
        'check_software_support', 'sdk_evidence', 'official_links']
    assert len(adapter.calls) == 2
    assert '非规格目标不要填写 field' in adapter.calls[1][0][0]['content']


def test_schema_retries_when_explicit_spec_question_was_reduced_to_identity():
    question = ('客户手上是 EGO 双目 1920×1200 版，要求 RGB 在 1920×1200 '
                '原生分辨率跑 60 fps。这个指标能确认吗？如果换 1600×1200 版呢？')
    def call(goals):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': goals}}]
    adapter = SequencedSchemaAdapter([
        call([{'kind': 'product', 'capability': 'resolve_entity',
               'entities': ['EGO 双目']}]),
        call([{'kind': 'product', 'capability': 'lookup_spec',
               'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
               'conditions': {'resolution_variant': ['1920x1200', '1600x1200']}},
              {'kind': 'product', 'capability': 'lookup_spec',
               'entities': ['EGO 双目'], 'field': 'rgb_resolution',
               'conditions': {'resolution_variant': ['1920x1200', '1600x1200']}}]),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 2
    assert 'schema_missing_explicit_spec_goal' in adapter.calls[1][0][0]['content']
    assert len([goal for goal in schema.goals if goal.capability == 'lookup_spec']) == 4
    from daq_fae.workflow import allowed_tools_for
    assert 'lookup_spec' in allowed_tools_for(schema)


def test_schema_rejects_repeated_identity_only_answer_to_explicit_spec_question():
    question = 'EGO Pro 的 RGB 最大帧率是多少？'
    event = [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
              'arguments': {'confidence': 'high', 'goals': [
                  {'kind': 'product', 'capability': 'resolve_entity',
                   'entities': ['EGO Pro']} ]}}]
    adapter = SequencedSchemaAdapter([event, event])
    with pytest.raises(SchemaExtractionError, match='schema_missing_explicit_spec_goal'):
        extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 2


@pytest.mark.parametrize('question,goals', [
    ('我现在用 EGO 双目 1920×1200 版本，RGB 按原生分辨率采。', [
        {'kind': 'product', 'capability': 'resolve_entity',
         'entities': ['EGO 双目']}]),
    ('EGO 双目 1920×1200 版 RGB 按原生分辨率采，请按步骤说怎么接线和录制', [
        {'kind': 'product', 'capability': 'lookup_procedure',
         'entities': ['EGO 双目']}]),
    ('你能查询 EGO 双目的 RGB 分辨率吗？', [
        {'kind': 'service', 'capability': 'service_capabilities'},
        {'kind': 'product', 'capability': 'resolve_entity',
         'entities': ['EGO 双目']}]),
    ('EGO 双目 RGB 分辨率在 EgoViewer 哪一版可用？', [
        {'kind': 'product', 'capability': 'check_software_support',
         'entities': ['EGO 双目', 'EgoViewer']}]),
])
def test_schema_completeness_guard_preserves_non_value_requests(question, goals):
    response = [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': goals}}]
    adapter = SequencedSchemaAdapter([response])
    schema = extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 1
    assert all(goal.capability != 'lookup_spec' for goal in schema.goals)


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

    invalid_response = [{'type': 'tool_call',
        'name': 'submit_daq_request_schema',
        'arguments': {'confidence': 'high', 'goals': []}}]
    invalid = SequencedSchemaAdapter([invalid_response, invalid_response])
    with pytest.raises(SchemaExtractionError, match='schema_goals_invalid'):
        extract_request_schema('EgoViewer 是什么？', adapter=invalid)
    assert len(invalid.calls) == 2
