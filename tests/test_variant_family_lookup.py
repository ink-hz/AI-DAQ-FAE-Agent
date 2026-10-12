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
    assert first.status == 'not_found'
    assert first.content['reason'] == 'claim_conditions_required'
    assert first.content['available_claim_conditions'] == {'resolution': ['1600x1200']}
    assert first.sources == []
    exact = box.dispatch('lookup_spec', {
        'entity': 'entity:ego-1600', 'field': 'rgb_max_fps',
        'conditions': {'resolution_variant': '1600x1200', 'resolution': '1600x1200'},
    })
    assert exact.status == 'ok'
    assert exact.content['matches'][0]['data']['value'] == 165
