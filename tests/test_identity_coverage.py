"""Synthetic B1 identities and inventory coverage; no private product facts."""
import copy
import importlib.util
import json
from pathlib import Path
import pytest

MODULE = 'daq_fae.knowledge.identity_coverage'


def api():
    assert importlib.util.find_spec(MODULE) is not None, 'B1 compiler must exist'
    from daq_fae.knowledge import identity_coverage
    return identity_coverage


def inputs():
    ref = {'path': 'spec.pdf', 'sha256': 'a' * 64, 'locator': {'kind': 'page', 'page': 1}}
    def row(identity, kind, data, status='candidate'):
        return {'id': identity, 'kind': kind, 'status': status, 'scope': {'product': 'alpha'}, 'source_refs': [ref], 'data': data}
    rows = [row('entity:alpha', 'entity', {'name': 'Alpha Device', 'entity_type': 'device'}),
            row('entity:beta', 'entity', {'name': 'Alpha Device', 'entity_type': 'variant', 'variant_selector': 'B'}),
            row('claim:alpha:limit', 'claim', {'entity_id': 'entity:alpha', 'field': 'latency', 'value': 2, 'unit': 'ms', 'conditions': {'comparison': '<'}}),
            row('topology:pair', 'topology', {'members': ['entity:alpha', 'entity:beta'], 'roles': {'entity:alpha': 'master'}, 'connections': [], 'power': {}, 'platform': 'lab', 'sync_target': 'pair', 'storage': 'host'})]
    sources = [{'path': 'spec.pdf', 'sha256': 'a' * 64}, {'path': 'unused.pdf', 'sha256': 'b' * 64}]
    config = {'aliases': [{'name': 'Alpha', 'entity_ids': ['entity:alpha'], 'record_ids': ['entity:alpha'], 'status': 'candidate'}],
              'ambiguities': [{'id': 'mapping:pending', 'names': ['Gamma', 'Alpha Plus'], 'entity_ids': ['entity:alpha'], 'record_ids': ['entity:alpha'], 'source_refs': [ref], 'reason': 'SKU mapping pending'}],
              'fields': {'latency': {'name_zh': '延迟', 'name_en': 'latency', 'synonyms': ['delay'], 'unit_aliases': {'ms': ['毫秒']}}}}
    return rows, sources, config


def compile_data():
    rows, sources, config = inputs()
    return api().build_dictionary(rows, sources, [], config)


def test_normalizes_case_width_and_whitespace_without_merging_variants():
    result = compile_data()
    match = api().resolve_name(result, '  ＡＬＰＨＡ   ＤＥＶＩＣＥ ')
    assert match['entity_ids'] == ['entity:alpha', 'entity:beta']
    assert match['status'] == 'ambiguous'
    assert api().resolve_name(result, 'Alpha')['entity_ids'] == ['entity:alpha']
    assert api().resolve_name(result, 'Alpha')['status'] == 'candidate'


def test_external_mapping_remains_ambiguous_even_with_one_local_entity():
    result = compile_data()
    for name in ('Gamma', 'Alpha Plus'):
        assert api().resolve_name(result, name)['status'] == 'ambiguous'
        assert api().resolve_name(result, name)['automatic_resolution'] is False
    assert api().resolve_name(result, 'unknown')['status'] == 'not_found'


def test_preserves_records_conditions_comparators_and_relations():
    rows, sources, config = inputs(); original = copy.deepcopy(rows)
    result = api().build_dictionary(rows, sources, [], config)
    assert rows == original
    assert result['record_inventory'] == sorted(rows, key=lambda r: r['id'])
    assert result['fields'][0]['comparators'] == ['<']
    assert result['fields'][0]['synonyms'] == ['delay']
    assert result['relations'][0]['members'] == ['entity:alpha', 'entity:beta']
    assert result['relations'][0]['status'] == 'candidate'


def test_no_source_is_a_bounded_audit_not_unsupported():
    result = compile_data()
    missing = [c for c in result['coverage'] if c['entity_id'] == 'entity:beta' and c['field_id'] == 'latency']
    assert len(missing) == 1 and missing[0]['status'] == 'audited_no_source'
    assert missing[0]['audit_scope'] == 'supplied_record_inventory_only'
    assert missing[0]['source_refs'] == []
    assert 'unsupported' not in json.dumps(result)


def test_conflict_overrides_candidate_without_picking_value():
    rows, sources, config = inputs()
    conflict = copy.deepcopy(rows[2]); conflict['id'] = 'claim:alpha:conflict'; conflict['status'] = 'conflict'
    conflict['data'].pop('value'); conflict['data']['candidates'] = [{'value': 2, 'source_ref': rows[2]['source_refs'][0]}, {'value': 3, 'source_ref': rows[2]['source_refs'][0]}]
    rows.append(conflict)
    result = api().build_dictionary(rows, sources, [], config)
    cell = next(c for c in result['coverage'] if c['entity_id'] == 'entity:alpha' and c['field_id'] == 'latency')
    assert cell['status'] == 'conflict'
    assert len(cell['record_ids']) == 2
    assert next(r for r in result['record_inventory'] if r['id'] == conflict['id'])['data']['candidates'] == conflict['data']['candidates']


def test_role_coverage_does_not_grant_roles_or_confuse_denial_with_absence():
    result = compile_data()
    assert result['online_eligible'] is False
    for role in result['coverage_by_role']:
        assert role['permission_status'] == 'not_assessed_by_b1'
        assert role['granted_view_records'] == [] and role['granted_forward_records'] == []
        assert role['inventory_status'] == 'candidate'


@pytest.mark.parametrize('bad', ['view_roles', 'forward_roles'])
def test_dictionary_configuration_cannot_grant_access(bad):
    rows, sources, config = inputs(); config[bad] = ['channel']
    with pytest.raises(ValueError, match='permission'):
        api().build_dictionary(rows, sources, [], config)


def test_rejects_broken_source_or_record_references():
    rows, sources, config = inputs(); rows[0]['source_refs'][0]['sha256'] = 'f'*64
    with pytest.raises(ValueError, match='source'):
        api().build_dictionary(rows, sources, [], config)


def test_rejects_alias_to_unknown_entity():
    rows, sources, config = inputs(); config['aliases'][0]['entity_ids'] = ['entity:missing']
    with pytest.raises(ValueError, match='entity'):
        api().build_dictionary(rows, sources, [], config)


def test_deterministic_under_record_source_and_alias_order():
    rows, sources, config = inputs()
    one = api().build_dictionary(rows, sources, [], config)
    two = api().build_dictionary(list(reversed(rows)), list(reversed(sources)), [], config)
    assert one == two


def test_verified_and_conflict_inventory_are_separate_from_permissions():
    rows, sources, config = inputs(); rows[2]['status'] = 'verified'
    result = api().build_dictionary(rows, sources, [], config)
    assert next(c for c in result['coverage'] if c['entity_id'] == 'entity:alpha' and c['field_id'] == 'latency')['status'] == 'verified'
    assert all(r['granted_view_records'] == [] for r in result['coverage_by_role'])


def test_sections_are_linked_without_promoting_candidates_or_filling_fact_gaps():
    rows, sources, config = inputs()
    section = {'section_id': 'section:beta', 'entity_id': 'entity:beta', 'source_refs': rows[0]['source_refs'], 'review_status': 'candidate', 'view_roles': [], 'forward_roles': []}
    result = api().build_dictionary(rows, sources, [section], config)
    assert result['section_coverage'][0]['section_id'] == 'section:beta'
    assert next(c for c in result['coverage'] if c['entity_id'] == 'entity:beta' and c['field_id'] == 'latency')['status'] == 'audited_no_source'


def test_private_writer_requires_ignored_destination_and_rejects_symlinks(tmp_path):
    result = compile_data()
    with pytest.raises(ValueError, match='ignored'):
        api().write_private_dictionary(tmp_path / 'out.json', result)


def test_private_write_is_reproducible_and_restricted(tmp_path):
    import subprocess
    import stat
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / '.gitignore').write_text('/private/\n')
    target = tmp_path / 'private' / 'dictionary.json'
    result = compile_data()
    api().write_private_dictionary(target, result)
    first = target.read_bytes()
    api().write_private_dictionary(target, result)
    assert target.read_bytes() == first
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    link = target.parent / 'link.json'; link.symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        api().write_private_dictionary(link, result)


def test_cli_builds_with_input_hashes(tmp_path):
    import hashlib
    import subprocess
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / '.gitignore').write_text('/private/\n')
    rows, sources, config = inputs()
    payload = {'records': rows, 'sources': sources, 'sections': [], 'config': config}
    source = tmp_path / 'input.json'; source.write_text(json.dumps(payload))
    output = tmp_path / 'private' / 'dictionary.json'
    run = subprocess.run(['python3', 'scripts/daq_identity_coverage.py', '--input', str(source), '--output', str(output)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(output.read_text())['input_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_cross_product_topic_does_not_invent_entity_binding():
    rows, sources, config = inputs()
    section = {'section_id': 'topic:cross', 'knowledge_type': 'topic', 'entity_id': None, 'scope': {'product': 'cross-product'}, 'source_refs': rows[0]['source_refs'], 'review_status': 'candidate'}
    result = api().build_dictionary(rows, sources, [section], config)
    assert result['section_coverage'][0]['entity_ids'] == []
    assert result['section_coverage'][0]['scope'] == {'product': 'cross-product'}


def test_rejects_section_with_unknown_topology_reference():
    rows, sources, config = inputs()
    section = {'section_id': 'section:alpha', 'entity_id': 'entity:alpha',
               'topology_id': 'topology:does-not-exist',
               'source_refs': rows[0]['source_refs'], 'review_status': 'candidate'}
    with pytest.raises(ValueError, match='unknown section topology'):
        api().build_dictionary(rows, sources, [section], config)


@pytest.mark.parametrize('topology_id', ['topology:pair', None, ''])
def test_section_accepts_registered_or_empty_topology(topology_id):
    rows, sources, config = inputs()
    section = {'section_id': 'section:alpha', 'entity_id': 'entity:alpha',
               'topology_id': topology_id,
               'source_refs': rows[0]['source_refs'], 'review_status': 'candidate'}
    result = api().build_dictionary(rows, sources, [section], config)
    assert result['section_coverage'][0]['topology_id'] == topology_id
