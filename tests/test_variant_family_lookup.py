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
                 'scope': scope, 'data': {'name': 'EGO'}, 'source_refs': []},
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
