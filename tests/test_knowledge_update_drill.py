"""Rehearsal contracts: real offline graph inputs, private writes, no activation."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from daq_fae.knowledge.records import access_fingerprint, record_fingerprint

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/rehearse_daq_knowledge_update.py'


def drill_module():
    assert SCRIPT.is_file(), 'incremental update rehearsal CLI is missing'
    spec = importlib.util.spec_from_file_location('knowledge_update_drill', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_bundle(tmp_path):
    originals = tmp_path / 'originals'
    originals.mkdir()
    bundle = {'snapshot': {'extractor_version': 'test-v1', 'sources': [], 'chunks': []},
              'records': [], 'sections': [], 'bodies': {}, 'coverage': [], 'questions': [],
              'sku_definitions': {}, 'field_definitions': {}}
    for name in ('alpha', 'beta'):
        content = ('# ' + name + '\nRate | 10 Hz\n').encode()
        (originals / (name + '.md')).write_bytes(content)
        sha = hashlib.sha256(content).hexdigest()
        ref = {'path': name + '.md', 'sha256': sha,
               'locator': {'kind': 'lines', 'start': 1, 'end': 2}}
        source = {'path': ref['path'], 'sha256': sha, 'kind': 'markdown',
                  'impact_scope': {'entity_ids': ['entity:' + name], 'field_ids': ['rate']}}
        bundle['snapshot']['sources'].append(source)
        bundle['snapshot']['chunks'].append({'source_path': ref['path'], 'source_sha256': sha,
                                             'locator': ref['locator']})
        record = {'id': 'claim:' + name, 'kind': 'claim', 'status': 'verified', 'scope': {},
                  'source_refs': [ref], 'data': {'entity_id': 'entity:' + name, 'field': 'rate',
                                               'value': 10, 'unit': 'Hz', 'conditions': {}}}
        record['fact_review'] = {'reviewer': 'fixture-fae', 'reviewed_at': '2026-10-21',
                                 'record_sha256': record_fingerprint(record)}
        record['access_review'] = {'reviewer': 'fixture-owner', 'reviewed_at': '2026-10-21',
                                   'view_roles': ['internal_fae'], 'forward_roles': []}
        record['access_review']['record_sha256'] = access_fingerprint(record)
        bundle['records'].append(record)
        sid = 'section:' + name
        bundle['bodies'][sid] = name + ' rate: 10 Hz.'
        bundle['sections'].append({'section_id': sid, 'source_refs': [deepcopy(ref)],
            'body_sha256': hashlib.sha256(bundle['bodies'][sid].encode()).hexdigest(),
            'view_roles': ['internal_fae'], 'forward_roles': [],
            'dependency_claim_ids': [record['id']], 'record_assertions': [{'id': record['id']}]})
        bundle['coverage'].append({'coverage_id': 'coverage:' + name, 'status': 'verified',
                                   'record_ids': [record['id']], 'section_ids': [sid]})
        bundle['questions'].append({'question_id': 'question:' + name,
                                    'coverage_ids': ['coverage:' + name]})
    bundle['coverage'].append({'coverage_id': 'gap:alpha', 'status': 'audited_no_source',
        'entity_id': 'entity:alpha', 'field_id': 'latency', 'topology_ids': ['topology:alpha'],
        'record_ids': [], 'section_ids': []})
    bundle['coverage'].append({'coverage_id': 'gap:beta', 'status': 'audited_no_source',
        'entity_id': 'entity:beta', 'field_id': 'latency', 'topology_ids': ['topology:beta'],
        'record_ids': [], 'section_ids': []})
    bundle['questions'].append({'question_id': 'question:gap', 'coverage_ids': ['gap:alpha']})
    return bundle, originals


def test_six_scenarios_preserve_inputs_and_trace_precise_closures(tmp_path):
    module = drill_module()
    bundle, originals = fixture_bundle(tmp_path)
    private = tmp_path / 'private'
    private.mkdir()
    before = deepcopy(bundle)
    report = module.rehearse(bundle, originals, private / 'batch', private_root=private)
    assert bundle == before
    assert report['online_eligible'] is False
    scenarios = report['scenarios']
    assert set(scenarios) == {'source_bytes_changed', 'table_value_changed', 'source_removed',
                             'permission_expanded', 'permission_contracted', 'source_added'}
    for name in set(scenarios) - {'source_added'}:
        assert scenarios[name]['plan']['affected']['record_ids'] == ['claim:alpha']
        assert scenarios[name]['plan']['affected']['section_ids'] == ['section:alpha']
        assert scenarios[name]['plan']['reusable_signature_record_ids'] == ['claim:beta']
        assert scenarios[name]['unaffected_record_ids'] == ['claim:beta']
        assert scenarios[name]['unrelated_entities']['entity:beta']['unchanged']
    table = scenarios['table_value_changed']
    assert table['record_fingerprints']['claim:alpha']['old'] != table['record_fingerprints']['claim:alpha']['new']
    assert table['stale_review_findings']['claim:alpha'] == ['fact_review_stale', 'access_review_stale']
    removed = scenarios['source_removed']
    assert removed['proposed_withdrawal_record_ids'] == ['claim:alpha']
    assert removed['runtime_withdrawal_performed'] is False
    added = scenarios['source_added']
    assert added['plan']['affected']['coverage_ids'] == ['gap:alpha']
    assert added['plan']['affected']['question_ids'] == ['question:gap']
    assert added['plan']['affected']['record_ids'] == []
    assert added['candidate']['coverage'] == bundle['coverage']
    assert report['external_bindings']['action'] == 'rebind_and_recheck_required_before_publication'
    assert (private / 'batch/report.json').stat().st_mode & 0o777 == 0o600
    assert json.loads((private / 'batch/report.json').read_text()) == report
    with pytest.raises(ValueError, match='new output'):
        module.rehearse(bundle, originals, private / 'batch', private_root=private)


def test_pointer_hash_guard_covers_success_and_failure(tmp_path):
    module = drill_module()
    bundle, originals = fixture_bundle(tmp_path)
    private = tmp_path / 'private'
    private.mkdir()
    release = tmp_path / 'release'
    release.mkdir()
    pointer = release / 'active.json'
    pointer.write_text('{"release_id":"existing-dev"}\n')
    raw = pointer.read_bytes()
    report = module.rehearse(bundle, originals, private / 'success', release_root=release, private_root=private)
    assert report['pointer_guard']['before'] == report['pointer_guard']['after']
    assert pointer.read_bytes() == raw
    bundle['questions'][0]['coverage_ids'] = ['missing:coverage']
    with pytest.raises(ValueError, match='dependency'):
        module.rehearse(bundle, originals, private / 'failure', release_root=release, private_root=private)
    failure = json.loads((private / 'failure/failure.json').read_text())
    assert failure['pointer_guard']['unchanged'] is True
    assert failure['pointer_guard']['before'] == hashlib.sha256(raw).hexdigest()
    assert pointer.read_bytes() == raw


def test_exact_source_bytes_and_private_output_are_required(tmp_path):
    module = drill_module()
    bundle, originals = fixture_bundle(tmp_path)
    private = tmp_path / 'private'
    private.mkdir()
    with pytest.raises(ValueError, match='private'):
        module.rehearse(bundle, originals, tmp_path / 'outside', private_root=private)
    (originals / 'alpha.md').write_bytes(b'changed outside drill')
    with pytest.raises(ValueError, match='source hash'):
        module.rehearse(bundle, originals, private / 'bad-hash', private_root=private)
    assert not (originals / 'simulated-source.md').exists()


def test_cli_accepts_private_tmp_symlink_in_isolated_worktree(tmp_path, monkeypatch):
    import subprocess
    module = drill_module()
    bundle, originals = fixture_bundle(tmp_path)
    repository = tmp_path / 'repo'
    repository.mkdir()
    subprocess.run(['git', 'init', '-q', str(repository)], check=True)
    (repository / '.gitignore').write_text('/tmp/\n')
    (repository / 'tmp').mkdir()
    worktree = repository / '.worktrees/task'
    worktree.mkdir(parents=True)
    (worktree / 'tmp').symlink_to(repository / 'tmp', target_is_directory=True)
    bundle_path = tmp_path / 'bundle.json'
    bundle_path.write_text(json.dumps(bundle))
    monkeypatch.setattr(module, 'ROOT', worktree)
    assert module.main(['--bundle', str(bundle_path), '--source-root', str(originals),
                        '--output', str(worktree / 'tmp/drill')]) == 0
    assert (repository / 'tmp/drill/report.json').is_file()


def test_dependency_closure_includes_system_but_protects_other_topology(tmp_path):
    module = drill_module()
    bundle, originals = fixture_bundle(tmp_path)
    # The system workflow depends on the parameter without sharing beta's graph.
    system = deepcopy(bundle['records'][0])
    system.update(id='procedure:alpha', kind='procedure', dependency_record_ids=['claim:alpha'])
    system['data'] = {'entity_id': 'entity:alpha', 'topology_id': 'topology:alpha',
                      'steps': ['fixture step'], 'checks': ['fixture check']}
    system['fact_review']['record_sha256'] = record_fingerprint(system)
    system['access_review']['record_sha256'] = access_fingerprint(system)
    bundle['records'].append(system)
    private = tmp_path / 'private'
    private.mkdir()
    report = module.rehearse(bundle, originals, private / 'batch', private_root=private)
    removed = report['scenarios']['source_removed']
    assert removed['proposed_withdrawal_record_ids'] == ['claim:alpha', 'procedure:alpha']
    assert removed['plan']['affected']['record_ids'] == ['claim:alpha', 'procedure:alpha']
    assert removed['plan']['graph']['edges']
    for name in ('permission_expanded', 'permission_contracted'):
        scenario = report['scenarios'][name]
        assert scenario['plan']['affected']['record_ids'] == ['claim:alpha', 'procedure:alpha']
        assert scenario['details']['old_roles'] != scenario['details']['new_roles']
    assert report['scenarios']['source_added']['plan']['affected']['coverage_ids'] == ['gap:alpha']
