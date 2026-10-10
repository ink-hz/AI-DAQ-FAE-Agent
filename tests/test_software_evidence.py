"""Synthetic C3 exact software evidence and legacy SDK query sentinels."""
from copy import deepcopy

import pytest

from daq_fae.domain_tools import DaqToolBox
from synthetic_release_helpers import load_fixture_active
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from test_reviewed_knowledge import _row, _records, SNAPSHOT, RELEASE_REVIEW
from daq_fae.knowledge.releases import _publish_release as publish_release, _activate_release as activate_release

ARGS = dict(entity='EGO 1600', software='Capture SDK', version='1.2',
            hardware_revision='A', platform='Linux x64', connection_mode='USB',
            capability='record')


def box(tmp_path, **extra):
    data = {k: v for k, v in ARGS.items() if k != 'entity'}
    data.update(entity_id='entity:ego-1600', evidence_level='device_tested', **extra)
    rows = _records() + [_row('software:capture', 'software', data)]
    rid = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, rid)
    return DaqToolBox(knowledge=load_fixture_active(tmp_path), role='internal_fae')


@pytest.mark.parametrize('tool', ['check_software_support', 'sdk_evidence'])
def test_exact_conditions_required_for_both_tools(tmp_path, tool):
    b = box(tmp_path)
    args = dict(ARGS, query='Capture SDK')
    assert b.dispatch(tool, args).status == 'ok'
    for key in ARGS:
        missing = deepcopy(args); missing.pop(key)
        result = b.dispatch(tool, missing)
        assert result.status == 'not_found'
        assert key in result.content['missing_conditions']
        wrong = dict(args, **{key: 'other'})
        assert b.dispatch(tool, wrong).status == 'not_found'


def test_legacy_sdk_query_returns_specific_conditions_gap(tmp_path):
    result = box(tmp_path).dispatch('sdk_evidence', {'query': 'Capture SDK'})
    assert result.status == 'not_found'
    assert result.content['reason'] == 'software_conditions_required'
    assert result.sources == []


@pytest.mark.parametrize('tool', ['check_software_support', 'sdk_evidence'])
def test_dependency_versions_and_conditions_must_match(tmp_path, tool):
    b = box(tmp_path, software_versions={'firmware': '3.0', 'viewer': '4.0'},
            conditions={'stream': 'stereo'})
    args = dict(ARGS, query='Capture SDK')
    assert b.dispatch(tool, args).status == 'not_found'
    args.update(software_versions={'firmware': '3.0', 'viewer': '4.0'},
                conditions={'stream': 'stereo'})
    assert b.dispatch(tool, args).status == 'ok'
    args['software_versions']['firmware'] = '3.1'
    assert b.dispatch(tool, args).status == 'not_found'


@pytest.mark.parametrize('tool', ['check_software_support', 'sdk_evidence'])
def test_single_device_test_cannot_certify_combination(tmp_path, tool):
    b = box(tmp_path, topology_id='topology:combo')
    result = b.dispatch(tool, dict(ARGS, query='Capture SDK', topology_id='topology:combo'))
    assert result.status == 'not_found'
    assert result.content['reason'] == 'end_to_end_test_missing'


def test_unknown_extra_conditions_do_not_get_ignored(tmp_path):
    assert box(tmp_path).dispatch('check_software_support', dict(
        ARGS, conditions={'firmware_version': '3.0'})).status == 'not_found'


def test_sdk_schema_keeps_query_and_adds_optional_exact_selectors():
    schema = next(t['function']['parameters'] for t in DaqToolBox().tool_schemas()
                  if t['function']['name'] == 'sdk_evidence')
    assert schema['required'] == ['query']
    assert set(ARGS) <= set(schema['properties'])


@pytest.mark.parametrize('tier', ['package_present', 'documented', 'source_supported', 'invented'])
def test_unreviewed_or_untested_tiers_never_mean_support(tier):
    from daq_fae.knowledge.software_evidence import support_gap
    row = _row('software:synthetic', 'software', dict(
        {k: v for k, v in ARGS.items() if k != 'entity'},
        entity_id='entity:ego-1600', evidence_level=tier))
    assert support_gap(row, ARGS, {'ego 1600'}) == 'device_test_missing'
    row['status'] = 'candidate'
    assert support_gap(row, ARGS, {'ego 1600'}) == 'reviewed_evidence_unavailable'


def test_private_candidate_matrix_does_not_infer_package_compatibility():
    from daq_fae.knowledge import software_evidence
    builder = getattr(software_evidence, 'candidate_matrix', None)
    assert callable(builder)
    source = {'path': 'Device-A-Linux-SDK-v9-supported.zip', 'sha256': 'a' * 64,
              'locator': {'kind': 'file'}}
    packet = {'cases': [{'id': 'software:package', 'group': 'package_inventory',
                        'question': 'Review the package', 'evidence': [
                            {'source_ref': source, 'evidence_basis': 'asset_metadata_only'}]}]}
    rows = builder(packet)
    assert len(rows) == 1
    assert rows[0]['evidence_level'] == 'package_present'
    assert all(value is None for value in rows[0]['conditions'].values())
    assert rows[0]['status'] == 'candidate'
    assert rows[0]['view_roles'] == rows[0]['forward_roles'] == []
    assert rows[0]['source_refs'] == [source]
    assert rows == builder(packet)


def test_extra_topology_requires_reviewed_combination_evidence(tmp_path):
    b = box(tmp_path)
    assert b.dispatch('check_software_support', dict(ARGS, topology_id='topology:combo')).status == 'not_found'


def test_roles_still_filter_software_before_matching(tmp_path):
    b = box(tmp_path)
    b.role = 'channel'
    r = b.dispatch('sdk_evidence', dict(ARGS, query='Capture SDK'))
    assert r.status == 'not_found'
    assert r.sources == []
    assert r.content['matches'] == []


def test_end_to_end_claim_needs_visible_topology(tmp_path):
    b = box(tmp_path, topology_id='topology:combo')
    # A record claiming a combination cannot invent the missing governed topology.
    # Work on a local copy through the pure matcher; no release mutation.
    row = next(deepcopy(r) for r in b.knowledge._records if r['kind'] == 'software')
    row['data']['evidence_level'] = 'end_to_end_verified'
    assert not b._matches('check_software_support', dict(ARGS, topology_id='topology:combo'),
                          row, {'entity:ego-1600': 'EGO 1600'}, {})


def test_conflicting_reviewed_support_results_fail_closed(tmp_path):
    data = {k: v for k, v in ARGS.items() if k != 'entity'}
    data.update(entity_id='entity:ego-1600', evidence_level='device_tested')
    positive = _row('software:positive', 'software', data)
    negative = _row('software:negative', 'software', deepcopy(data))
    negative['status'] = 'unsupported'
    from daq_fae.knowledge.records import record_fingerprint, access_fingerprint
    negative['fact_review']['record_sha256'] = record_fingerprint(negative)
    negative['access_review']['record_sha256'] = access_fingerprint(negative)
    rid = publish_release(tmp_path, SNAPSHOT, _records()+[positive, negative], None, RELEASE_REVIEW)
    activate_release(tmp_path, rid)
    b = DaqToolBox(knowledge=load_fixture_active(tmp_path), role='internal_fae')
    r = b.dispatch('check_software_support', ARGS)
    assert r.status == 'not_found'
    assert r.content['claim_status'] == 'unknown'
    assert r.content['reason'] == 'reviewed_evidence_unavailable'


def test_end_to_end_exact_combination_passes_pure_matcher():
    from daq_fae.knowledge.software_evidence import support_gap
    data = {k: v for k, v in ARGS.items() if k != 'entity'}
    data.update(entity_id='entity:ego-1600', evidence_level='end_to_end_verified',
                topology_id='topology:combo')
    row = _row('software:combo', 'software', data)
    topology = {'status': 'verified', 'data': {'members': ['entity:ego-1600'],
                                              'platform': 'Linux x64'}}
    assert support_gap(row, dict(ARGS, topology_id='topology:combo'), {'ego 1600'},
                       {'topology:combo': topology}) is None


@pytest.mark.parametrize('tool', ['check_software_support', 'sdk_evidence'])
def test_topology_required_selectors_are_exact_and_consistent(tmp_path, tool):
    topology = _row('topology:combo', 'topology', {
        'members': ['entity:ego-1600'], 'roles': {}, 'connections': [],
        'power': {}, 'platform': 'Linux x64', 'sync_target': 'clock', 'storage': 'disk',
    })
    topology['scope'] = {'cable_revision': 'C1', 'required_selectors': ['cable_revision']}
    from daq_fae.knowledge.records import record_fingerprint, access_fingerprint
    topology['fact_review']['record_sha256'] = record_fingerprint(topology)
    topology['access_review']['record_sha256'] = access_fingerprint(topology)
    data = {k: v for k, v in ARGS.items() if k != 'entity'}
    data.update(entity_id='entity:ego-1600', evidence_level='end_to_end_verified',
                topology_id='topology:combo', conditions={'cable_revision': 'C1'})
    software = _row('software:combo', 'software', data)
    rid = publish_release(tmp_path, SNAPSHOT, _records()+[topology, software], None, RELEASE_REVIEW)
    activate_release(tmp_path, rid)
    b = DaqToolBox(knowledge=load_fixture_active(tmp_path), role='internal_fae')
    args = dict(ARGS, query='Capture SDK', topology_id='topology:combo')
    assert b.dispatch(tool, args).status == 'not_found'
    assert b.dispatch(tool, dict(args, conditions={'cable_revision': 'C1'})).status == 'ok'
    assert b.dispatch(tool, dict(args, conditions={'cable_revision': 'C2'})).status == 'not_found'

    # A legacy software record does not erase a topology's own required selector.
    bare = _row('software:bare', 'software', {k:v for k,v in data.items() if k != 'conditions'})
    rid = publish_release(tmp_path, SNAPSHOT, _records()+[topology, bare], None, RELEASE_REVIEW)
    activate_release(tmp_path, rid)
    b = DaqToolBox(knowledge=load_fixture_active(tmp_path), role='internal_fae')
    missing = b.dispatch(tool, args)
    assert missing.status == 'not_found'
    assert missing.content['reason'] == 'software_topology_conditions_required'

    # Even a reviewed software condition cannot override a contradictory topology.
    contradictory = _row('software:conflicting', 'software', dict(data, conditions={'cable_revision':'C2'}))
    rid = publish_release(tmp_path, SNAPSHOT, _records()+[topology, contradictory], None, RELEASE_REVIEW)
    activate_release(tmp_path, rid)
    b = DaqToolBox(knowledge=load_fixture_active(tmp_path), role='internal_fae')
    conflict = b.dispatch(tool, dict(args, conditions={'cable_revision':'C2'}))
    assert conflict.status == 'not_found'
    assert conflict.content['reason'] == 'software_topology_conditions_required'


@pytest.mark.parametrize('tool', ['check_software_support', 'sdk_evidence'])
def test_exact_dependency_versions_satisfy_requirement_coverage(tmp_path, tool):
    b = box(tmp_path, software_versions={'firmware': '3.0', 'viewer': '4.0'})
    requirement = {'id': 'versions', 'capability': tool, 'software': 'Capture SDK',
                   'entities': ['EGO 1600'], 'conditions': {
                       'platform': 'Linux x64', 'variant': 'A', 'connection': 'USB',
                       'task': ['recording'], 'sdk_version': '1.2',
                       'firmware_version': '3.0', 'viewer_version': '4.0'}}
    b.requirements = [requirement]
    args = dict(ARGS, query='Capture SDK', software_versions={'firmware': '3.0', 'viewer': '4.0'})
    r = b.dispatch(tool, args)
    assert r.status == 'ok'
    assert r.content['matched_requirement_ids'] == ['versions']
    requirement['conditions']['firmware_version'] = '3.1'
    assert b.dispatch(tool, args).content['matched_requirement_ids'] == []


def test_dependency_version_cannot_override_primary_software_version(tmp_path):
    b = box(tmp_path, software_versions={'sdk':'9.0'})
    r = b.dispatch('sdk_evidence', dict(ARGS, query='Capture SDK', software_versions={'sdk':'9.0'}))
    assert r.status == 'not_found'
    assert r.content['reason'] == 'software_versions_unconfirmed'
