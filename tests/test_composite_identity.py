from copy import deepcopy
from types import SimpleNamespace

import pytest
from daq_fae.domain_tools import DaqToolBox
from daq_fae.request_schema import DaqRequestSchema, SchemaExtractionError
from test_catalog_collection_contract import _directory


def candidate():
    return {'confidence': 'high', 'goals': [
        {'kind': 'product', 'capability': 'resolve_entity', 'entities': ['NOVA 960']},
        {'kind': 'product', 'capability': 'lookup_procedure', 'entities': ['NOVA 960'],
         'conditions': {'platform': 'android', 'connection': 'wifi'}}]}


def test_unique_reviewed_composite_name_is_one_identity_across_schema_and_tool(tmp_path):
    view = _directory(tmp_path)
    schema = DaqRequestSchema.from_candidate('Use the NOVA 960 Android guide.', candidate(), knowledge=view)
    for goal in schema.goals:
        assert goal.entities == ('NOVA',)
        assert goal.condition_map()['resolution_variant'] == '960x720'
        assert 'resolution_variant' not in goal.unmapped_condition_keys
    r = DaqToolBox(knowledge=view, role='internal_fae').dispatch('resolve_entity', {'text': 'NOVA 960'})
    assert [row['id'] for row in r.content['matches']] == ['entity:nova-small']


def test_composite_identity_never_overrides_an_explicit_conflicting_selector(tmp_path):
    c = candidate();c['goals'][0]['conditions'] = {'resolution_variant': '1280x960'}
    with pytest.raises(SchemaExtractionError, match='variant_alias_conflict'):
        DaqRequestSchema.from_candidate('NOVA 960 on Android.', c, knowledge=_directory(tmp_path))


def test_same_width_with_two_heights_remains_ambiguous(tmp_path):
    from daq_fae.knowledge.identity_selectors import composite_identity
    rows = _directory(tmp_path).records_for('internal_fae')
    peer = deepcopy(next(r for r in rows if r['id'] == 'entity:nova-small'))
    peer['id'] = 'entity:another';peer['scope']['resolution_variant'] = '960x800'
    assert composite_identity('NOVA 960', rows + [peer]) is None
    assert composite_identity('NOVA 960x720', rows + [peer], allow_full=True)['id'] == 'entity:nova-small'


def test_declared_product_name_and_unreviewed_variant_cannot_be_stolen(tmp_path):
    from daq_fae.knowledge.identity_selectors import composite_identity
    rows = _directory(tmp_path).records_for('internal_fae')
    peer = deepcopy(rows[-1]);peer['id'] = 'entity:literal-name';peer['data']['name'] = 'NOVA 960'
    assert composite_identity('NOVA 960', rows + [peer]) is None
    for r in rows:
        if r['kind'] == 'entity':r['status'] = 'candidate'
    assert composite_identity('NOVA 960', rows) is None


def test_output_resolution_does_not_become_an_explicit_variant_by_text_scan(tmp_path):
    from daq_fae.knowledge.identity_selectors import composite_versions_in_text
    rows = _directory(tmp_path).records_for('internal_fae')
    assert composite_versions_in_text('NOVA 960 over WiFi', rows) == {'960x720'}
    assert composite_versions_in_text('NOVA 9600 over WiFi', rows) == set()
    assert composite_versions_in_text('NOVA 960x720 output at 30 fps', rows) == set()


def test_a_model_cannot_promote_an_output_mode_embedded_in_entities(tmp_path):
    c = {'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['NOVA 960x720'], 'field': 'rgb_max_frame_rate',
         'conditions': {'resolution': '960x720'}}]}
    for question in ('NOVA 960x720只是输出模式，不指定硬件版本，RGB能到30fps吗？',
                     'Can NOVA 960x720 output at 30fps?'):
        with pytest.raises(SchemaExtractionError, match='resolution_variant_not_user_variant'):
            DaqRequestSchema.from_candidate(question, c, knowledge=_directory(tmp_path / str(len(question))))


def test_an_explicit_full_variant_marker_in_either_language_is_preserved(tmp_path):
    for suffix in ('版本', ' variant'):
        c = candidate()
        for goal in c['goals']:goal['entities'] = ['NOVA 960x720']
        s = DaqRequestSchema.from_candidate('NOVA 960x720'+suffix+' on Android guide.', c,
                                            knowledge=_directory(tmp_path / str(len(suffix))))
        assert all(g.condition_map()['resolution_variant'] == '960x720' for g in s.goals)


def test_short_pixel_width_is_not_a_hardware_selector(tmp_path):
    from daq_fae.knowledge.identity_selectors import composite_versions_in_text
    view = _directory(tmp_path)
    for q in ('Can NOVA 960 output pixels be captured?',
              'NOVA 960只是输出像素宽度，不是硬件版本，RGB能到30fps吗？'):
        assert composite_versions_in_text(q, view.records_for('internal_fae')) == set()
        with pytest.raises(SchemaExtractionError, match='resolution_variant_not_user_variant'):
            DaqRequestSchema.from_candidate(q, candidate(), knowledge=view)


def test_equivalent_selector_glyphs_are_not_a_conflict(tmp_path):
    c = candidate()
    for goal in c['goals']:
        goal['entities'] = ['NOVA 960×720']
        goal['conditions'] = {'resolution_variant': ' 960×720 '}
    s = DaqRequestSchema.from_candidate('NOVA 960×720版本 on Android.', c,
                                        knowledge=_directory(tmp_path))
    assert all(g.condition_map()['resolution_variant'] == '960x720' for g in s.goals)


def test_followup_cannot_promote_prior_measurement_name_into_hardware(tmp_path):
    with pytest.raises(SchemaExtractionError, match='resolution_variant_not_user_variant'):
        DaqRequestSchema.from_candidate('该模式还能采集IMU吗？', candidate(),
            prior_user_context='NOVA 960只是输出像素宽度，不是硬件版本，RGB能到30fps吗？',
            knowledge=_directory(tmp_path))


def test_new_explicit_variant_overrides_prior_measurement_context(tmp_path):
    s = DaqRequestSchema.from_candidate('现在确认用NOVA 960版本，按Android说明操作。', candidate(),
        prior_user_context='NOVA 960只是输出像素宽度，不是硬件版本。', knowledge=_directory(tmp_path))
    assert all(g.condition_map()['resolution_variant'] == '960x720' for g in s.goals)


def test_topic_switch_does_not_authorize_an_old_composite_name(tmp_path):
    c = {**candidate(), 'topic_switch': True}
    with pytest.raises(SchemaExtractionError, match='entity_not_grounded'):
        DaqRequestSchema.from_candidate('Switch to Probe on Android.', c,
            prior_user_context='NOVA 960版本', knowledge=_directory(tmp_path))


def test_a_shorter_numeric_prefix_is_not_the_users_model(tmp_path):
    with pytest.raises(SchemaExtractionError, match='entity_not_grounded'):
        DaqRequestSchema.from_candidate('Use the NOVA 9600 Android guide.', candidate(),
                                        knowledge=_directory(tmp_path))
