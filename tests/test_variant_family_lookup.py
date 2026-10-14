import pytest

from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.domain_tools import DaqToolBox


class FamilyKnowledge:
    release_id = 'a' * 64
    manifest = {'format_version': 1}

    def records_for(self, role):
        assert role == 'internal_fae'
        rows = []
        for variant, value in [('1600x1200', 165), ('1920x1200', 149)]:
            identity = 'entity:ego-' + variant[:4]
            scope = {'product': 'ego', 'resolution_variant': variant,
                     'required_selectors': ['resolution_variant']}
            rows += [
                {'id': identity, 'kind': 'entity', 'status': 'verified',
                 'scope': scope, 'data': {'name': 'EGO', 'aliases': ['EGO 双目']},
                 'source_refs': []},
                {'id': 'claim:fov-' + variant[:4], 'kind': 'claim', 'status': 'verified',
                 'scope': scope, 'data': {'entity_id': identity,
                                        'field': 'rgb_horizontal_fov', 'value': value,
                                        'unit': 'degree', 'conditions': {}}, 'source_refs': []},
            ]
        rows.append({'id': 'entity:ego-pro', 'kind': 'entity', 'status': 'verified',
                     'scope': {'product': 'ego-pro'}, 'data': {'name': 'EGO Pro'},
                     'source_refs': []})
        return rows

    def ground_question(self, question, *, role):
        return {}

    def resolve_field(self, field, entity_id=None):
        return 'rgb_horizontal_fov' if field in {'horizontal_fov', 'rgb_horizontal_fov'} else None

    def verified_variant_scopes(self, entity, field, *, role):
        return {'resolution_variant': ['1600x1200', '1920x1200']}

    def conflict_notices_for(self, role):
        return []


def test_family_phrase_can_query_each_explicit_variant_without_crossing_to_pro():
    box = DaqToolBox(question='EGO 双目的水平视场角是多少？',
                     knowledge=FamilyKnowledge(), role='internal_fae')
    for variant, expected in [('1600x1200', 165), ('1920x1200', 149)]:
        result = box.dispatch('lookup_spec', {
            'entity': 'EGO 双目', 'field': 'horizontal_fov',
            'conditions': {'resolution_variant': variant},
        })
        assert result.status == 'ok'
        assert [row['data']['value'] for row in result.content['matches']] == [expected]
    unresolved = box.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'horizontal_fov', 'conditions': {},
    })
    assert unresolved.status == 'not_found'
    assert unresolved.content['reason'] == 'variant_selector_required'


def test_comparison_identity_evidence_is_required_for_each_visible_variant():
    from daq_fae.request_schema import DaqRequestSchema
    from daq_fae.task_context import prepare_turn

    question = 'EGO 双目 1600×1200 和 1920×1200 两个版本怎么比较？'
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
    requirements = prepare_turn(question, request_schema=schema).requirements
    identity_ids = {item['id'] for item in requirements
                    if item['capability'] == 'resolve_entity'}
    assert len(identity_ids) == 2
    full = DaqToolBox(question=question, knowledge=FamilyKnowledge(),
                      role='internal_fae', requirements=requirements)
    # Specification retrieval alone cannot satisfy an identity obligation.
    spec = full.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'horizontal_fov',
        'conditions': {'resolution_variant': '1600x1200'},
    })
    assert not identity_ids.intersection(spec.content['matched_requirement_ids'])
    both = full.dispatch('resolve_entity', {'text': 'EGO 双目'})
    assert identity_ids <= set(both.content['matched_requirement_ids'])

    class OneVisibleVariant(FamilyKnowledge):
        def records_for(self, role):
            return [row for row in super().records_for(role)
                    if row['scope'].get('resolution_variant') != '1920x1200']

    limited = DaqToolBox(question=question, knowledge=OneVisibleVariant(),
                         role='internal_fae', requirements=requirements)
    one = limited.dispatch('resolve_entity', {'text': 'EGO 双目'})
    assert len(identity_ids.intersection(one.content['matched_requirement_ids'])) == 1


def test_missing_spec_attribution_uses_exact_product_key_and_matching_selectors():
    requirement = {'id': 'req1600', 'capability': 'lookup_spec',
                   'entities': ['EGO 双目'], 'field': 'depth_accuracy',
                   'conditions': {'resolution_variant': '1600x1200'}}
    box = DaqToolBox(question='EGO 双目 1600×1200 深度精度是多少？',
                     knowledge=FamilyKnowledge(), role='internal_fae',
                     requirements=[requirement])
    matching = box.dispatch('lookup_spec', {
        'entity': 'ego', 'field': 'depth_accuracy',
        'conditions': {'resolution_variant': '1600x1200'},
    })
    assert matching.status == 'not_found'
    assert matching.content['matched_requirement_ids'] == ['req1600']
    omitted = box.dispatch('lookup_spec', {
        'entity': 'ego', 'field': 'depth_accuracy',
    })
    assert omitted.content['matched_requirement_ids'] == []
    conflicting = box.dispatch('lookup_spec', {
        'entity': 'ego', 'field': 'depth_accuracy',
        'conditions': {'resolution_variant': '1920x1200'},
    })
    assert conflicting.content['matched_requirement_ids'] == []
    overfiltered = box.dispatch('lookup_spec', {
        'entity': 'ego', 'field': 'depth_accuracy',
        'conditions': {'resolution_variant': '1600x1200',
                       'resolution': '1920x1080'},
    })
    assert overfiltered.content['matched_requirement_ids'] == []


def test_conflicting_selector_cannot_mark_answerable_canonical_spec_missing():
    requirement = {'id': 'req1600', 'capability': 'lookup_spec',
                   'entities': ['EGO 双目'], 'field': 'rgb_horizontal_fov',
                   'conditions': {'resolution_variant': '1600x1200'}}
    box = DaqToolBox(question='EGO 双目 1600×1200 水平视场角是多少？',
                     knowledge=FamilyKnowledge(), role='internal_fae',
                     requirements=[requirement])
    wrong = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'rgb_horizontal_fov',
        'conditions': {'resolution_variant': '1920x1200'},
    })
    assert wrong.status == 'not_found'
    assert wrong.content['matched_requirement_ids'] == []
    extra = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'rgb_horizontal_fov',
        'conditions': {'resolution_variant': '1600x1200',
                       'resolution': '1920x1080'},
    })
    assert extra.status == 'not_found'
    assert extra.content['matched_requirement_ids'] == []
    right = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'rgb_horizontal_fov',
        'conditions': {'resolution_variant': '1600x1200'},
    })
    assert right.status == 'ok'
    assert right.content['matched_requirement_ids'] == ['req1600']


def test_one_measurement_mode_cannot_cover_a_broad_multi_mode_fps_goal():
    class MultiModeKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            for mode, fps in [('1920x1200', 30), ('1920x1080', 60)]:
                rows.append({
                    'id': 'claim:fps-' + mode, 'kind': 'claim',
                    'status': 'verified',
                    'scope': {'product': 'ego',
                              'resolution_variant': '1920x1200',
                              'required_selectors': ['resolution_variant']},
                    'data': {'entity_id': 'entity:ego-1920',
                             'field': 'rgb_max_fps', 'value': fps,
                             'unit': 'fps', 'conditions': {'resolution': mode}},
                    'source_refs': [],
                })
            return rows

        def resolve_field(self, field, entity_id=None):
            return 'rgb_max_fps' if field in {
                'rgb_max_fps', 'rgb_max_frame_rate'} else super().resolve_field(field, entity_id)

    broad = {'id': 'broad-fps', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200'}}
    box = DaqToolBox(question='EGO 双目 1920×1200 版 RGB 最大帧率是多少？',
                     knowledge=MultiModeKnowledge(), role='internal_fae',
                     requirements=[broad])
    narrowed = box.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'rgb_max_fps',
        'conditions': {'resolution_variant': '1920x1200',
                       'resolution': '1920x1200'},
    })
    assert narrowed.status == 'ok'
    assert narrowed.content['matched_requirement_ids'] == []
    unresolved = box.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'rgb_max_fps',
        'conditions': {'resolution_variant': '1920x1200'},
    })
    assert unresolved.status == 'not_found'
    assert unresolved.content['reason'] == 'claim_conditions_required'
    assert unresolved.content['matched_requirement_ids'] == []

    exact = {**broad, 'id': 'exact-fps',
             'conditions': {'resolution_variant': '1920x1200',
                            'resolution': '1920x1200'}}
    exact_box = DaqToolBox(question='EGO 双目 1920×1200 版在 1920×1200 下 RGB 帧率？',
                           knowledge=MultiModeKnowledge(), role='internal_fae',
                           requirements=[exact])
    selected = exact_box.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'rgb_max_fps',
        'conditions': exact['conditions'],
    })
    assert selected.content['matched_requirement_ids'] == ['exact-fps']


def test_one_verified_mode_cannot_hide_visible_conflicting_mode():
    class ConflictModeKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            rows.append({
                'id': 'claim:fps-native', 'kind': 'claim', 'status': 'verified',
                'scope': {'product': 'ego',
                          'resolution_variant': '1920x1200',
                          'required_selectors': ['resolution_variant']},
                'data': {'entity_id': 'entity:ego-1920', 'field': 'rgb_max_fps',
                         'value': 30, 'unit': 'fps',
                         'conditions': {'resolution': '1920x1200'}},
                'source_refs': [],
            })
            return rows

        def conflict_notices_for(self, role):
            return [{
                'id': 'conflict:fps-1080', 'kind': 'claim', 'status': 'conflict',
                'scope': {'product': 'ego',
                          'resolution_variant': '1920x1200',
                          'required_selectors': ['resolution_variant']},
                'data': {'entity_id': 'entity:ego-1920', 'field': 'rgb_max_fps',
                         'conditions': {'resolution': '1920x1080'}},
                'source_refs': [],
            }]

        def resolve_field(self, field, entity_id=None):
            return 'rgb_max_fps' if field in {
                'rgb_max_fps', 'rgb_max_frame_rate'} else super().resolve_field(field, entity_id)

    requirement = {'id': 'broad-fps', 'capability': 'lookup_spec',
                   'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                   'conditions': {'resolution_variant': '1920x1200'}}
    box = DaqToolBox(question='EGO 双目 1920×1200 版 RGB 最大帧率是多少？',
                     knowledge=ConflictModeKnowledge(), role='internal_fae',
                     requirements=[requirement])
    native = box.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'rgb_max_fps',
        'conditions': {'resolution_variant': '1920x1200',
                       'resolution': '1920x1200'},
    })
    assert native.status == 'ok'
    assert native.content['matched_requirement_ids'] == []
    conflicted = box.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'rgb_max_fps',
        'conditions': {'resolution_variant': '1920x1200',
                       'resolution': '1920x1080'},
    })
    assert conflicted.status == 'not_found'
    assert conflicted.content['reason'] == 'reviewed_conflict_notice'
    assert conflicted.content['matched_requirement_ids'] == ['broad-fps']


def test_overlapping_visible_conflict_takes_priority_over_verified_spec():
    class OverlappingConflictKnowledge(FamilyKnowledge):
        def conflict_notices_for(self, role):
            return [{
                'id': 'conflict:fov-revision-a', 'kind': 'claim',
                'status': 'conflict',
                'scope': {'product': 'ego', 'resolution_variant': '1920x1200',
                          'hardware_revision': 'A',
                          'required_selectors': ['resolution_variant']},
                'data': {'entity_id': 'entity:ego-1920',
                         'field': 'rgb_horizontal_fov', 'conditions': {}},
                'source_refs': [],
            }]

    requirement = {'id': 'fov1920', 'capability': 'lookup_spec',
                   'entities': ['EGO 双目'], 'field': 'rgb_horizontal_fov',
                   'conditions': {'resolution_variant': '1920x1200'}}
    box = DaqToolBox(question='EGO 双目 1920×1200 版水平视角是多少？',
                     knowledge=OverlappingConflictKnowledge(),
                     role='internal_fae', requirements=[requirement])
    result = box.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'rgb_horizontal_fov',
        'conditions': {'resolution_variant': '1920x1200'},
    })
    assert result.status == 'not_found'
    assert result.content['reason'] == 'reviewed_conflict_notice'
    assert result.content['matched_requirement_ids'] == ['fov1920']
    session = DaqEvidencePolicy().begin({'requirements': [requirement]})
    session.observe('lookup_spec', result)
    assert session.snapshot().requirement_status['fov1920'] == 'conflict'


def test_reviewed_family_name_with_exact_variant_suffix_resolves_to_one_entity():
    box = DaqToolBox(question='EGO 双目 1920×1200 和 1600×1200 版本比较',
                     knowledge=FamilyKnowledge(), role='internal_fae')
    result = box.dispatch('lookup_spec', {
        'entity': 'EGO 双目 1920x1200', 'field': 'horizontal_fov',
        'conditions': {'resolution_variant': '1920x1200'},
    })
    assert result.status == 'ok'
    assert result.content['matches'][0]['id'] == 'claim:fov-1920'


def test_verified_claim_exposes_missing_measurement_condition_without_value():
    class ConditionKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            for row in rows:
                if row['kind'] == 'claim':
                    row['data']['field'] = 'rgb_max_fps'
                    row['data']['conditions'] = {
                        'resolution': row['scope']['resolution_variant']}
            return rows

        def resolve_field(self, field, entity_id=None):
            return 'rgb_max_fps' if field in {'rgb_max_fps', 'rgb_frame_rate'} else None

    box = DaqToolBox(question='EGO 双目 1600×1200 版本最大帧率是多少？',
                     knowledge=ConditionKnowledge(), role='internal_fae')
    first = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'rgb_max_fps',
        'conditions': {'resolution_variant': '1600x1200'},
    })
    assert first.status == 'ok'
    assert first.content['matches'][0]['data']['conditions'] == {'resolution': '1600x1200'}
    assert first.content['query_conditions_completed_from_unique_claim'] == {
        'resolution': '1600x1200'}
    exact = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'rgb_max_fps',
        'conditions': {'resolution_variant': '1600x1200', 'resolution': '1600x1200'},
    })
    assert exact.status == 'ok'
    assert exact.content['matches'][0]['data']['value'] == 165


def test_user_baseline_target_does_not_filter_verified_baseline_claim():
    class BaselineKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            for row in rows:
                if row['kind'] == 'claim':
                    row['data'].update(field='stereo_mono_baseline', value=50,
                                       unit='mm', conditions={})
            return rows

        def resolve_field(self, field, entity_id=None):
            return 'stereo_mono_baseline' if field == 'stereo_mono_baseline' else None

    box = DaqToolBox(
        question='EGO 双目 1600×1200 版的双目 Mono 基线是 50 mm 吗？',
        knowledge=BaselineKnowledge(), role='internal_fae',
        requirements=[{
            'id': 'req-baseline', 'capability': 'lookup_spec',
            'field': 'stereo_mono_baseline', 'entities': ['entity:ego-1600'],
            'conditions': {'resolution_variant': '1600x1200'},
            'question_constraints': {'baseline_value_mm': 50},
        }],
    )
    for conditions in ({'resolution_variant': '1600x1200', 'baseline_value_mm': 50},
                       "{'resolution_variant': '1600x1200', 'baseline_value_mm': 50}"):
        result = box.dispatch('lookup_spec', {
            'entity': 'entity:ego-1600', 'field': 'stereo_mono_baseline',
            'conditions': conditions,
        })
        assert result.status == 'ok'
        assert result.content['matches'][0]['data']['value'] == 50
        assert result.content['question_targets_not_claim_conditions'] == {
            'baseline_value_mm': 50,
        }


def test_unparseable_conditions_cannot_look_like_absent_claim():
    box = DaqToolBox(question='EGO 双目 1600×1200 版的视场角？',
                     knowledge=FamilyKnowledge(), role='internal_fae')
    result = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'horizontal_fov',
        'conditions': "{'resolution_variant': unknown}",
    })
    assert result.status == 'tool_error'
    assert result.content['error'] == 'conditions_must_be_object'


def test_future_measurement_condition_is_not_removed_as_question_target():
    class AccuracyKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            rows = [row for row in rows if row['kind'] == 'entity']
            for distance, accuracy in [(1, 5), (2, 15)]:
                rows.append({
                    'id': f'claim:accuracy-{distance}m', 'kind': 'claim',
                    'status': 'verified',
                    'scope': {'product': 'ego', 'resolution_variant': '1600x1200',
                              'required_selectors': ['resolution_variant']},
                    'data': {'entity_id': 'entity:ego-1600',
                             'field': 'depth_accuracy', 'value': accuracy,
                             'unit': 'mm',
                             'conditions': {'measurement_distance_m': distance}},
                    'source_refs': [],
                })
            return rows

        def resolve_field(self, field, entity_id=None):
            return field if field == 'depth_accuracy' else None

    box = DaqToolBox(
        question='EGO 双目 1600×1200 版在 1 m 距离的深度精度是多少？',
        knowledge=AccuracyKnowledge(), role='internal_fae',
        requirements=[{
            'id': 'req-accuracy', 'capability': 'lookup_spec',
            'field': 'depth_accuracy', 'entities': ['entity:ego-1600'],
            'conditions': {'resolution_variant': '1600x1200'},
            'question_constraints': {'measurement_distance_m': 1},
        }],
    )
    result = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'depth_accuracy',
        'conditions': {'resolution_variant': '1600x1200',
                       'measurement_distance_m': 1},
    })
    assert result.status == 'ok'
    assert [row['data']['value'] for row in result.content['matches']] == [5]
    assert 'question_targets_not_claim_conditions' not in result.content


def test_conflicted_measurement_condition_is_not_hidden_as_question_target():
    class ConflictKnowledge(FamilyKnowledge):
        def records_for(self, role):
            return [row for row in super().records_for(role) if row['kind'] == 'entity']

        def conflict_notices_for(self, role):
            return [{
                'id': 'claim:accuracy-conflict', 'kind': 'claim', 'status': 'conflict',
                'scope': {'product': 'ego', 'resolution_variant': '1600x1200',
                          'required_selectors': ['resolution_variant']},
                'data': {'entity_id': 'entity:ego-1600',
                         'field': 'depth_accuracy',
                         'conditions': {'measurement_distance_m': 1}},
            }]

        def resolve_field(self, field, entity_id=None):
            return field if field == 'depth_accuracy' else None

    box = DaqToolBox(
        question='EGO 双目 1600×1200 版在 1 m 距离的深度精度是多少？',
        knowledge=ConflictKnowledge(), role='internal_fae',
        requirements=[{
            'id': 'req-accuracy', 'capability': 'lookup_spec',
            'field': 'depth_accuracy', 'entities': ['entity:ego-1600'],
            'conditions': {'resolution_variant': '1600x1200'},
            'question_constraints': {'measurement_distance_m': 1},
        }],
    )
    result = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'depth_accuracy',
        'conditions': {'resolution_variant': '1600x1200',
                       'measurement_distance_m': 1},
    })
    assert result.status == 'not_found'
    assert result.content['reason'] == 'reviewed_conflict_notice'
    assert result.content['claim_status'] == 'conflict'


def test_output_resolution_does_not_strip_verified_measurement_resolution():
    class ResolutionKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = [row for row in super().records_for(role) if row['kind'] == 'entity']
            rows.append({
                'id': 'claim:depth-resolution', 'kind': 'claim', 'status': 'verified',
                'scope': {'product': 'ego', 'resolution_variant': '1600x1200',
                          'required_selectors': ['resolution_variant']},
                'data': {'entity_id': 'entity:ego-1600',
                         'field': 'depth_resolution', 'value': '320x240',
                         'unit': 'px', 'conditions': {'resolution': '640x480'}},
                'source_refs': [],
            })
            return rows

        def resolve_field(self, field, entity_id=None):
            return field if field == 'depth_resolution' else None

    box = DaqToolBox(question='EGO 双目 1600×1200 版在 640×480 模式下的深度分辨率？',
                     knowledge=ResolutionKnowledge(), role='internal_fae')
    result = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'depth_resolution',
        'conditions': {'resolution_variant': '1600x1200', 'resolution': '640x480'},
    })
    assert result.status == 'ok'
    assert result.content['matches'][0]['data']['value'] == '320x240'


def test_absent_scoped_field_attributes_attempt_to_single_named_entity_goal():
    class UnscopedKnowledge(FamilyKnowledge):
        def verified_variant_scopes(self, entity, field, *, role):
            return {}

    box = DaqToolBox(
        question='EGO Pro 的深度精度是多少？',
        knowledge=UnscopedKnowledge(), role='internal_fae',
        requirements=[{
            'id': 'req-ego-pro-accuracy', 'capability': 'lookup_spec',
            'field': 'depth_accuracy', 'entities': ['EGO Pro'],
            'conditions': {},
        }],
    )
    result = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-pro', 'field': 'depth_accuracy',
    })
    assert result.status == 'not_found'
    assert result.content['reason'] == 'reviewed_field_unavailable'
    assert result.content['matched_requirement_ids'] == ['req-ego-pro-accuracy']


def test_two_entity_comparison_coverage_tracks_each_claim_and_absence():
    from daq_fae.domain_evidence import DaqEvidencePolicy
    from daq_fae.request_schema import DaqRequestSchema
    from daq_fae.task_context import prepare_turn

    class ComparisonKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = []
            for entity_id, name in [('entity:umi-module', 'UMI 视觉模块'),
                                    ('entity:umi-finger', 'UMI Finger')]:
                rows.append({'id': entity_id, 'kind': 'entity', 'status': 'verified',
                             'scope': {'product': name},
                             'data': {'name': name}, 'source_refs': []})
                rows.append({'id': f'claim:{entity_id}-resolution', 'kind': 'claim',
                             'status': 'verified', 'scope': {'product': name},
                             'data': {'entity_id': entity_id,
                                      'field': 'stereo_mono_resolution',
                                      'value': '1280x960', 'unit': 'px',
                                      'conditions': {}}, 'source_refs': []})
            rows.append({'id': 'claim:finger-baseline', 'kind': 'claim',
                         'status': 'verified', 'scope': {'product': 'UMI Finger'},
                         'data': {'entity_id': 'entity:umi-finger',
                                  'field': 'stereo_mono_baseline',
                                  'value': 50, 'unit': 'mm', 'conditions': {}},
                         'source_refs': []})
            return rows

        def resolve_field(self, field, entity_id=None):
            return field

        def verified_variant_scopes(self, entity, field, *, role):
            return {}

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
    plan = prepare_turn(question, request_schema=schema)
    box = DaqToolBox(question=question, knowledge=ComparisonKnowledge(),
                     role='internal_fae', requirements=plan.requirements)
    policy = DaqEvidencePolicy().begin(plan.evidence_requirements())
    for entity_id in ('entity:umi-module', 'entity:umi-finger'):
        for field in ('stereo_mono_resolution', 'stereo_mono_baseline'):
            policy.observe('lookup_spec', box.dispatch('lookup_spec', {
                'entity': entity_id, 'field': field,
            }))
    statuses = {
        (req['entities'][0], req['field']): policy.snapshot().requirement_status[req['id']]
        for req in plan.requirements
    }
    assert statuses == {
        ('UMI 视觉模块', 'stereo_mono_resolution'): 'satisfied',
        ('UMI Finger', 'stereo_mono_resolution'): 'satisfied',
        ('UMI 视觉模块', 'stereo_mono_baseline'): 'missing',
        ('UMI Finger', 'stereo_mono_baseline'): 'satisfied',
    }


def test_missing_entity_attempt_does_not_mark_other_identity_goal_missing():
    box = DaqToolBox(
        question='EGO Pro 和 WristCam 分别是什么产品？',
        knowledge=FamilyKnowledge(), role='internal_fae',
        requirements=[
            {'id': 'req-ego', 'capability': 'resolve_entity',
             'entities': ['EGO Pro'], 'conditions': {}},
            {'id': 'req-wrist', 'capability': 'resolve_entity',
             'entities': ['WristCam'], 'conditions': {}},
        ],
    )
    missing = box.dispatch('resolve_entity', {'text': 'WristCam'})
    assert missing.status == 'not_found'
    assert missing.content['matched_requirement_ids'] == ['req-wrist']


def test_field02_scoped_resolution_alias_for_explicit_1920_variant():
    class ResolutionKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            for row in rows:
                if row['kind'] == 'claim' and row['scope']['resolution_variant'] == '1920x1200':
                    row['data']['field'] = 'rgb_resolution_options'
                    row['data']['value'] = ['1920x1200', '1920x1080']
            return rows

        def resolve_field(self, field, entity_id=None):
            if field == 'rgb_resolution' and entity_id == 'entity:ego-1920':
                return 'rgb_resolution_options'
            return super().resolve_field(field, entity_id)

    result = DaqToolBox(question='EGO 双目两个版本的 RGB 分辨率',
                        knowledge=ResolutionKnowledge(), role='internal_fae').dispatch(
                            'lookup_spec', {'entity': 'EGO 双目', 'field': 'rgb_resolution',
                                            'conditions': {'resolution_variant': '1920x1200'}})
    assert result.status == 'ok'
    assert result.content['matches'][0]['data']['value'] == ['1920x1200', '1920x1080']


def test_field02_resolution_target_and_measurement_do_not_cross_variants():
    class MixedKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            for row in tuple(rows):
                if row['kind'] != 'claim':
                    continue
                variant = row['scope']['resolution_variant']
                row['data'].update(
                    field=('rgb_resolution_options' if variant == '1920x1200'
                           else 'rgb_resolution'),
                    value=([variant, '1920x1080'] if variant == '1920x1200' else variant),
                )
                rows.append({**row, 'id': 'claim:fps-' + variant[:4],
                             'data': {**row['data'], 'field': 'rgb_max_fps',
                                      'value': 30 if variant == '1920x1200' else 60,
                                      'conditions': {'resolution': variant}}})
            return rows

        def resolve_field(self, field, entity_id=None):
            if field == 'rgb_resolution' and entity_id == 'entity:ego-1920':
                return 'rgb_resolution_options'
            if field == 'rgb_max_frame_rate':
                return 'rgb_max_fps'
            return field

    requirements = [
        {'id': 'req-res-1920', 'capability': 'lookup_spec',
         'field': 'rgb_resolution', 'entities': ['entity:ego-1920'],
         'conditions': {'resolution_variant': '1920x1200', 'resolution': '1920x1200'}},
        {'id': 'req-res-1600', 'capability': 'lookup_spec',
         'field': 'rgb_resolution', 'entities': ['entity:ego-1600'],
         'conditions': {'resolution_variant': '1600x1200'}},
        {'id': 'req-fps-1920', 'capability': 'lookup_spec',
         'field': 'rgb_max_frame_rate', 'entities': ['entity:ego-1920'],
         'conditions': {'resolution_variant': '1920x1200', 'resolution': '1920x1200'}},
        {'id': 'req-fps-1600', 'capability': 'lookup_spec',
         'field': 'rgb_max_frame_rate', 'entities': ['entity:ego-1600'],
         'conditions': {'resolution_variant': '1600x1200'}},
    ]
    box = DaqToolBox(question='EGO 双目 1920×1200 版和 1600×1200 版对比',
                     knowledge=MixedKnowledge(), role='internal_fae',
                     requirements=requirements)
    for variant, expected in [('1920x1200', ['1920x1200', '1920x1080']),
                              ('1600x1200', '1600x1200')]:
        result = box.dispatch('lookup_spec', {
            'entity': 'EGO 双目', 'field': 'rgb_resolution',
            'conditions': {'resolution_variant': variant,
                           'resolution': variant},
        })
        assert result.status == 'ok'
        assert result.content['matches'][0]['data']['value'] == expected
    fps = box.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'rgb_max_frame_rate',
        'conditions': {'resolution_variant': '1600x1200'},
    })
    assert fps.status == 'ok'
    assert fps.content['matches'][0]['data']['value'] == 60


def test_other_product_requirement_cannot_inject_measurement_resolution():
    class FpsKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            for row in rows:
                if row['kind'] == 'claim':
                    row['data'].update(field='rgb_max_fps',
                                       conditions={'resolution': row['scope']['resolution_variant']})
            return rows

        def resolve_field(self, field, entity_id=None):
            return 'rgb_max_fps' if field in {'rgb_max_fps', 'rgb_max_frame_rate'} else field

    box = DaqToolBox(
        question='比较 EGO Pro 和 EGO 双目 1920×1200 版',
        knowledge=FpsKnowledge(), role='internal_fae',
        requirements=[{
            'id': 'req-ego-pro', 'capability': 'lookup_spec',
            'field': 'rgb_max_frame_rate', 'entities': ['entity:ego-pro'],
            'conditions': {'resolution': '1600x1200'},
        }],
    )
    result = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1920', 'field': 'rgb_max_fps',
        'conditions': {'resolution_variant': '1920x1200'},
    })
    assert result.status == 'ok'
    assert result.content['matches'][0]['id'] == 'claim:fov-1920'


def test_family_requirement_without_variant_cannot_inject_other_variant_resolution():
    class FpsKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            for row in rows:
                if row['kind'] == 'claim':
                    row['data'].update(field='rgb_max_fps',
                                       conditions={'resolution': row['scope']['resolution_variant']})
            return rows

        def resolve_field(self, field, entity_id=None):
            return 'rgb_max_fps' if field in {'rgb_max_fps', 'rgb_max_frame_rate'} else field

    box = DaqToolBox(
        question='EGO 双目两种版本的帧率', knowledge=FpsKnowledge(),
        role='internal_fae', requirements=[{
            'id': 'req-family', 'capability': 'lookup_spec',
            'field': 'rgb_max_fps', 'entities': ['EGO 双目'],
            'conditions': {'resolution': '1920x1200'},
        }],
    )
    result = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'rgb_max_fps',
        'conditions': {'resolution_variant': '1600x1200'},
    })
    assert result.status == 'ok'
    assert result.content['matches'][0]['id'] == 'claim:fov-1600'


def test_multiple_measurement_modes_still_require_explicit_resolution():
    class MultiModeKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            rows = [row for row in rows if row['scope'].get('resolution_variant') != '1920x1200']
            claim = next(row for row in rows if row['kind'] == 'claim')
            claim['data'].update(field='rgb_max_fps', value=60,
                                 conditions={'resolution': '1600x1200'})
            alternate = {**claim, 'id': 'claim:fps-alternate',
                         'data': {**claim['data'], 'value': 30,
                                  'conditions': {'resolution': '1280x720'}}}
            rows.append(alternate)
            return rows

        def resolve_field(self, field, entity_id=None):
            return 'rgb_max_fps' if field == 'rgb_max_frame_rate' else None

    result = DaqToolBox(question='EGO 1600×1200 最大 RGB 帧率',
                        knowledge=MultiModeKnowledge(), role='internal_fae').dispatch(
                            'lookup_spec', {'entity': 'EGO', 'field': 'rgb_max_frame_rate',
                                            'conditions': {'resolution_variant': '1600x1200'}})
    assert result.status == 'not_found'
    assert result.content['reason'] == 'claim_conditions_required'
    assert sorted(result.content['available_claim_conditions']['resolution']) == [
        '1280x720', '1600x1200']


def test_hub_public_alias_resolves_to_unique_reviewed_product():
    class HubKnowledge(FamilyKnowledge):
        def records_for(self, role):
            return [
                {'id': 'entity:hub', 'kind': 'entity', 'status': 'verified',
                 'scope': {'product': 'hub'},
                 'data': {'name': 'ORBBEC 数采系列 HUB'}, 'source_refs': []},
                {'id': 'claim:hub-ports', 'kind': 'claim', 'status': 'verified',
                 'scope': {'product': 'hub'},
                 'data': {'entity_id': 'entity:hub', 'field': 'downstream_port_count',
                          'value': 4, 'unit': 'port', 'conditions': {}},
                 'source_refs': []},
            ]

        def resolve_field(self, field, entity_id=None):
            return field

    result = DaqToolBox(question='数采 HUB 有几个下游端口？',
                        knowledge=HubKnowledge(), role='internal_fae').dispatch(
                            'lookup_spec', {'entity': 'HUB', 'field': 'downstream_port_count'})
    assert result.status == 'ok'
    assert result.content['matches'][0]['data']['value'] == 4


@pytest.mark.parametrize('count_location', ['conditions', 'scope'])
def test_three_port_total_claim_cannot_satisfy_unspecified_total_goal(count_location):
    class HubKnowledge(FamilyKnowledge):
        def records_for(self, role):
            return [
                {'id': 'entity:hub', 'kind': 'entity', 'status': 'verified',
                 'scope': {'product': 'hub'},
                 'data': {'name': '数采 HUB'}, 'source_refs': []},
                {'id': 'claim:hub-total-three', 'kind': 'claim',
                 'status': 'verified', 'scope': {
                     'product': 'hub',
                     **({'simultaneous_port_count': 3}
                        if count_location == 'scope' else {})},
                 'data': {'entity_id': 'entity:hub',
                          'field': 'total_downstream_port_power_output',
                          'value': 30, 'unit': 'W',
                          'conditions': ({'simultaneous_port_count': 3}
                                         if count_location == 'conditions' else {})},
                 'source_refs': []},
            ]

        def resolve_field(self, field, entity_id=None):
            return field

    requirement = {'id': 'req-total', 'capability': 'lookup_spec',
                   'field': 'total_downstream_port_power_output',
                   'entities': ['数采 HUB'], 'conditions': {}}
    box = DaqToolBox(question='数采 HUB 下游口总供电输出多少？',
                     knowledge=HubKnowledge(), role='internal_fae',
                     requirements=[requirement])
    result = box.dispatch('lookup_spec', {
        'entity': '数采 HUB', 'field': 'total_downstream_port_power_output',
        'conditions': {'simultaneous_port_count': 3},
    })
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == []
    session = DaqEvidencePolicy().begin({'requirements': [requirement]})
    session.observe('lookup_spec', result)
    assert session.snapshot().requirement_status['req-total'] != 'satisfied'
    unqualified = box.dispatch('lookup_spec', {
        'entity': '数采 HUB', 'field': 'total_downstream_port_power_output',
    })
    if unqualified.status == 'ok':
        assert unqualified.content['matched_requirement_ids'] == []
        session.observe('lookup_spec', unqualified)
        assert session.snapshot().requirement_status['req-total'] != 'satisfied'


def test_total_claim_cannot_satisfy_per_port_power_requirement():
    class HubKnowledge(FamilyKnowledge):
        def records_for(self, role):
            return [
                {'id': 'entity:hub', 'kind': 'entity', 'status': 'verified',
                 'scope': {'product': 'hub'},
                 'data': {'name': '数采 HUB'}, 'source_refs': []},
                {'id': 'claim:hub-total-three', 'kind': 'claim',
                 'status': 'verified', 'scope': {'product': 'hub'},
                 'data': {'entity_id': 'entity:hub',
                          'field': 'total_downstream_port_power_output',
                          'value': 30, 'unit': 'W',
                          'conditions': {'simultaneous_port_count': 3}},
                 'source_refs': []},
            ]

        def resolve_field(self, field, entity_id=None):
            return field

    requirement = {'id': 'req-per-port', 'capability': 'lookup_spec',
                   'field': 'downstream_port_power_output',
                   'entities': ['数采 HUB'], 'conditions': {}}
    result = DaqToolBox(question='数采 HUB 每口供电多少？',
                        knowledge=HubKnowledge(), role='internal_fae',
                        requirements=[requirement]).dispatch('lookup_spec', {
                            'entity': '数采 HUB',
                            'field': 'total_downstream_port_power_output',
                            'conditions': {'simultaneous_port_count': 3},
                        })
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == []


def test_field14_entity_resolution_exposes_reviewed_field_coverage_without_values():
    class HubKnowledge(FamilyKnowledge):
        def records_for(self, role):
            return [
                {'id': 'entity:hub', 'kind': 'entity', 'status': 'verified',
                 'scope': {'product': 'hub'},
                 'data': {'name': 'ORBBEC 数采系列 HUB'}, 'source_refs': []},
                {'id': 'claim:hub-ports', 'kind': 'claim', 'status': 'verified',
                 'scope': {'product': 'hub'},
                 'data': {'entity_id': 'entity:hub', 'field': 'downstream_port_count',
                          'value': 4, 'unit': 'port', 'conditions': {}},
                 'source_refs': []},
            ]

    result = DaqToolBox(question='HUB wiring', knowledge=HubKnowledge(),
                        role='internal_fae').dispatch('resolve_entity', {'text': 'HUB'})
    assert result.status == 'ok'
    assert result.content['available_spec_fields'] == {
        'entity:hub': ['downstream_port_count']}
    assert '4' not in str(result.content['available_spec_fields'])


def test_field01_reviewed_family_name_satisfies_matching_variant_spec_goal():
    from daq_fae.request_schema import DaqRequestSchema
    from daq_fae.task_context import prepare_turn

    question = '做手部动作采集，EGO 双目 1600×1200 的水平视场角是多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目'], 'field': 'horizontal_fov',
            'conditions': {'resolution_variant': '1600x1200'},
        }],
    })
    plan = prepare_turn(question, request_schema=schema)
    result = DaqToolBox(question=question, knowledge=FamilyKnowledge(),
                        role='internal_fae', requirements=plan.requirements).dispatch(
                            'lookup_spec', {'entity': 'EGO 双目', 'field': 'horizontal_fov',
                                            'conditions': {'resolution_variant': '1600x1200'}})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == [plan.requirements[0]['id']]


def test_field02_wrong_measurement_condition_reports_variant_modes():
    class FrameRateKnowledge(FamilyKnowledge):
        def records_for(self, role):
            rows = super().records_for(role)
            for row in rows:
                if row['kind'] == 'claim':
                    row['data']['field'] = 'rgb_max_fps'
                    row['data']['conditions'] = {
                        'resolution': row['scope']['resolution_variant']}
            return rows

        def resolve_field(self, field, entity_id=None):
            return 'rgb_max_fps' if field == 'rgb_max_frame_rate' else None

    result = DaqToolBox(question='EGO 双目两版 RGB 原生帧率',
                        knowledge=FrameRateKnowledge(), role='internal_fae').dispatch(
                            'lookup_spec', {'entity': 'EGO 双目',
                                            'field': 'rgb_max_frame_rate',
                                            'conditions': {'resolution_variant': '1600x1200',
                                                           'resolution': '1920x1200'}})
    assert result.status == 'not_found'
    assert result.content['reason'] == 'claim_conditions_mismatch'
    assert result.content['available_claim_conditions'] == {'resolution': ['1600x1200']}
    assert result.sources == []
