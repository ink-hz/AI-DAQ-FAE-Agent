"""Synthetic module facts do not establish host-device capabilities."""
import copy
import importlib.util
import subprocess

import pytest


def api():
    assert importlib.util.find_spec('daq_fae.knowledge.module_projection'), 'C5 projection gate missing'
    from daq_fae.knowledge import module_projection
    return module_projection


def approved():
    m = api()
    b = {
        'relation': {'id': 'relation:synthetic', 'status': 'verified',
                     'device_id': 'device:synthetic', 'device_revision': 'A',
                     'module_id': 'module:synthetic', 'module_revision': 'B',
                     'source_refs': [{'source_id': 'source:relation', 'sha256': 'a'*64}]},
        'source': {'repository_id': 'camera-fixture', 'ref': 'refs/tags/fixture',
                   'commit': 'b'*40, 'path': 'facts.json', 'sha256': 'c'*64},
        'facts': [{'id': 'fact:precision', 'status': 'verified', 'scope': 'module',
                   'module_id': 'module:synthetic', 'module_revision': 'B',
                   'field': 'precision', 'value': 7, 'unit': 'mm',
                   'conditions': {'distance': 'synthetic only'},
                   'source_refs': [{'source_id': 'source:camera', 'locator': 'row:1'}]}],
        'view_roles': ['internal_fae'], 'forward_roles': [],
    }
    for kind in ('relation', 'source', 'republication', 'permission'):
        b[kind + '_review'] = {'reviewer': 'Synthetic Reviewer', 'reviewed_at': '2026-10-10',
                              'decision': 'approved', 'sha256': m.projection_fingerprint(b)}
    return b


def test_confirmed_preserves_module_scope_and_audit_without_mutating():
    b = approved(); original = copy.deepcopy(b)
    out = api().project_module_facts(b, role='internal_fae')
    assert out['status'] == 'reviewed_module_projection'
    assert out['facts'] == b['facts']
    assert out['device_claims'] == [] and out['online_eligible'] is False
    assert out['audit']['source'] == b['source']
    assert out['audit']['relation'] == b['relation']
    assert b == original
    out['facts'][0]['value'] = 99
    assert b == original


@pytest.mark.parametrize('kind', ['relation', 'source', 'republication', 'permission'])
def test_unsigned_or_unnamed_review_exposes_no_evidence(kind):
    b = approved(); b[kind + '_review']['reviewer'] = ''
    out = api().project_module_facts(b, role='internal_fae')
    assert out['facts'] == [] and out['audit'] == {}


@pytest.mark.parametrize('change', ['pending', 'revision', 'source', 'value', 'roles'])
def test_drift_invalidates_all_approvals(change):
    b = approved()
    if change == 'pending': b['relation']['status'] = 'candidate'
    if change == 'revision': b['relation']['module_revision'] = 'C'
    if change == 'source': b['source']['commit'] = 'd'*40
    if change == 'value': b['facts'][0]['value'] = 77
    if change == 'roles': b['view_roles'].append('channel')
    assert api().project_module_facts(b, role='internal_fae')['facts'] == []


@pytest.mark.parametrize('role', [None, '', 'channel', 'customer'])
def test_role_filtered_before_facts_or_sources(role):
    out = api().project_module_facts(approved(), role=role)
    assert out['facts'] == [] and out['audit'] == {}


@pytest.mark.parametrize('scope', ['device', 'device_precision', 'synchronization', 'sdk'])
def test_module_facts_never_answer_whole_device_scope(scope):
    out = api().project_module_facts(approved(), role='internal_fae', requested_scope=scope)
    assert out['facts'] == [] and out['device_claims'] == []


@pytest.mark.parametrize('change', ['unknown_revision', 'wrong_module', 'device_fact', 'conflict', 'floating_ref'])
def test_even_resigned_invalid_applicability_is_rejected(change):
    b = approved()
    if change == 'unknown_revision': b['relation']['module_revision'] = ''
    if change == 'wrong_module': b['facts'][0]['module_id'] = 'module:other'
    if change == 'device_fact': b['facts'][0]['scope'] = 'device'
    if change == 'conflict': b['facts'][0]['status'] = 'conflict'
    if change == 'floating_ref': b['source']['ref'] = 'master'
    for kind in ('relation', 'source', 'republication', 'permission'):
        b[kind + '_review']['sha256'] = api().projection_fingerprint(b)
    assert api().project_module_facts(b, role='internal_fae')['facts'] == []


def test_git_snapshot_reads_exact_ref_and_detects_drift(tmp_path):
    m = api()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args]).decode().strip()
    git('init', '-q'); git('config', 'user.email', 'fixture@example.invalid'); git('config', 'user.name', 'Fixture')
    (tmp_path / 'facts.json').write_text('{"synthetic":true}')
    git('add', 'facts.json'); git('commit', '-qm', 'fixture'); git('tag', 'fixture')
    sha = git('rev-parse', 'HEAD')
    raw, source = m.read_git_source(tmp_path, repository_id='fixture', ref='refs/tags/fixture', commit=sha, path='facts.json')
    assert raw == b'{"synthetic":true}' and source['commit'] == sha
    (tmp_path / 'facts.json').write_text('local edits must not be read')
    assert m.read_git_source(tmp_path, repository_id='fixture', ref='refs/tags/fixture', commit=sha, path='facts.json')[0] == raw
    with pytest.raises(ValueError, match='revision'):
        m.read_git_source(tmp_path, repository_id='fixture', ref='refs/tags/fixture', commit='a'*40, path='facts.json')
