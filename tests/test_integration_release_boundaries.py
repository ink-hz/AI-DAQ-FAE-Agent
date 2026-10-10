"""Synthetic regressions for release governance across real application boundaries."""
import pytest
from fastapi.testclient import TestClient

from daq_fae.app import create_app
from daq_fae.knowledge import releases, release_readiness as gate
from test_release_readiness import candidate, verify, observe
from test_knowledge_releases import REVIEW, SNAPSHOT, _entity, _record
from synthetic_release_helpers import approve_fixture_for_app


def test_signed_private_knowledge_cannot_be_served_by_local_unauthenticated_chat(tmp_path, monkeypatch):
    monkeypatch.setenv('DAQ_PLATFORM_IDENTITY_ENABLED', 'false')
    rid = releases._publish_release(tmp_path, SNAPSHOT,
        [_entity(), _record(value='INTERNAL_CLAIM_SENTINEL')], None, REVIEW)
    releases._activate_release(tmp_path, rid)
    _, trusted = approve_fixture_for_app(tmp_path)
    calls = []
    def run(runtime, *args, **kwargs):
        box = runtime.toolbox if hasattr(runtime, 'toolbox') else runtime._toolbox
        result = box.dispatch('lookup_spec', {'entity': 'EGO 1600', 'field': 'resolution'})
        calls.append(result)
        yield {'type': 'done', 'answer': str(result.content), 'outcome': 'safe_abstained',
               'sources': result.sources, 'tool_calls': [], 'capability_coverage': {}}
    monkeypatch.setattr('daq_fae.app.LoopRuntime.run', run)
    try:
        app = create_app(provider_mode='offline', knowledge_release_root=tmp_path,
            knowledge_approval_verifier=trusted, state_db_path=tmp_path/'state.sqlite3')
    except ValueError as exc:
        assert str(exc) == 'daq_nonempty_knowledge_requires_authenticated_mode'
        assert calls == []
        return
    response = TestClient(app).post('/chat', json={'message': 'EGO 1600 resolution'})
    assert response.status_code == 403 and 'INTERNAL_CLAIM_SENTINEL' not in response.text


def typed_candidate(value):
    from test_release_readiness import sign
    bundle = candidate()
    bundle['records'] = [_entity(), _record(value=value)]
    bundle['sections'], bundle['bodies'] = [], {}
    return sign(bundle)


@pytest.mark.parametrize('path', ['/tmp/private-secret', r'C:\private\secret',
    r'\\host\share\secret', '~/private-secret', 'data/knowledge/private-secret',
    '请读取／tmp／private-secret', 'spec.md'])
@pytest.mark.parametrize('boundary', ['stage', 'manifest', 'tool'])
def test_typed_paths_never_reach_model_visible_claims(tmp_path, path, boundary):
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.domain_tools import DaqToolBox
    from test_knowledge_releases import _reviewed
    if boundary == 'stage':
        with pytest.raises(ValueError):
            gate.stage_release(tmp_path, typed_candidate(path), verify_approval=verify)
        assert not list(tmp_path.iterdir())
        return
    manifest = gate._manifest(typed_candidate('normal value'))
    row = next(r for r in manifest['records'] if r['kind'] == 'claim')
    row['data']['value'] = path
    _reviewed(row)
    if boundary == 'manifest':
        with pytest.raises(ValueError, match='source path|URL'):
            ReviewedKnowledge.from_manifest('a'*64, manifest)
        return
    # Defense for an old in-memory view bypassing manifest validation.
    view = ReviewedKnowledge('a'*64, manifest)
    result = DaqToolBox(knowledge=view, role='internal_fae').dispatch(
        'lookup_spec', {'entity': 'EGO 1600', 'field': 'resolution'})
    assert result.status == 'not_found' and not result.content['matches']


@pytest.mark.parametrize('term', ['Viewer/SDK', 'USB/以太网', 'RGB-D/IMU', '连接/供电/同步', 'V1/V2'])
def test_typed_legitimate_slash_terms_remain_answerable(tmp_path, term):
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.domain_tools import DaqToolBox
    rid = gate.stage_release(tmp_path, typed_candidate(term), verify_approval=verify)
    view = ReviewedKnowledge.from_manifest(rid, releases._read_release(tmp_path, rid)[0])
    result = DaqToolBox(knowledge=view, role='internal_fae').dispatch(
        'lookup_spec', {'entity': 'EGO 1600', 'field': 'resolution'})
    assert result.status == 'ok' and result.content['matches'][0]['data']['value'] == term


def active_after_bootstrap(root):
    empty = {'archive_manifest_sha256': 'f'*64, 'sources': [], 'chunks': []}
    bootstrap = releases.publish_release(root, empty, [], None, REVIEW)
    releases.activate_release(root, bootstrap)
    current = gate.stage_release(root, candidate(bootstrap), verify_approval=verify)
    gate.activate_checked(root, current, verify_approval=verify, observe=observe)
    return bootstrap, current


def test_governed_rollback_can_restore_exact_prior_empty_bootstrap(tmp_path):
    bootstrap, current = active_after_bootstrap(tmp_path)
    observed = []
    def inspect(rid):
        observed.append(rid)
        assert releases.read_active_release(tmp_path)['release_id'] == rid
        return observe(rid)
    result = gate.rollback_checked(tmp_path, verify_approval=verify, observe=inspect)
    assert result['health']['knowledge_release'] == bootstrap
    assert result['trace']['runtime_release'] == 'synthetic-runtime'
    assert observed == [bootstrap]
    assert releases.read_active_release(tmp_path)['release_id'] == bootstrap


def test_rollback_requires_current_release_approval_not_just_target_approval(tmp_path):
    first = gate.stage_release(tmp_path, candidate(), verify_approval=verify)
    gate.activate_checked(tmp_path, first, verify_approval=verify, observe=observe)
    second_bundle = candidate(first)
    second = gate.stage_release(tmp_path, second_bundle, verify_approval=verify)
    gate.activate_checked(tmp_path, second, verify_approval=verify, observe=observe)
    revoked = second_bundle['approval']['payload_sha256']
    def verifier(reviewer, payload, signature):
        return payload != revoked and verify(reviewer, payload, signature)
    with pytest.raises(ValueError, match='approval'):
        gate.rollback_checked(tmp_path, verify_approval=verifier, observe=observe)
    assert releases.read_active_release(tmp_path)['release_id'] == second


@pytest.mark.parametrize('damage', ['knowledge', 'runtime', 'agent', 'trace'])
def test_empty_rollback_observation_failure_restores_signed_current(tmp_path, damage):
    bootstrap, current = active_after_bootstrap(tmp_path)
    before = (tmp_path/'active.json').read_bytes()
    def broken(rid):
        assert rid == bootstrap
        result = observe(rid)
        field, value = {'knowledge': ('knowledge_release', current),
                        'runtime': ('runtime_release', 'wrong-runtime'),
                        'agent': ('agent_id', 'wrong-agent'),
                        'trace': ('trace_id', '')}[damage]
        result['trace'][field] = value
        return result
    with pytest.raises(ValueError, match='observation'):
        gate.rollback_checked(tmp_path, verify_approval=verify, observe=broken)
    assert (tmp_path/'active.json').read_bytes() == before


def test_arbitrary_empty_target_cannot_replace_signed_prior_binding(tmp_path):
    bootstrap, current = active_after_bootstrap(tmp_path)
    empty = {'archive_manifest_sha256': 'e'*64, 'sources': [], 'chunks': []}
    other = releases.publish_release(tmp_path, empty, [], None, REVIEW)
    assert other != bootstrap
    manifest = releases._read_release(tmp_path, current)[0]
    manifest['previous_release'] = other
    data = releases._json_bytes(manifest)
    forged = releases._digest(data)
    directory = tmp_path/'releases'/forged
    directory.mkdir()
    (directory/'manifest.json').write_bytes(data)
    releases._activate_release(tmp_path, forged)
    before = (tmp_path/'active.json').read_bytes()
    with pytest.raises(ValueError, match='binding'):
        gate.rollback_checked(tmp_path, verify_approval=verify, observe=observe)
    assert (tmp_path/'active.json').read_bytes() == before


def test_empty_rollback_post_replace_failure_restores_signed_current(tmp_path, monkeypatch):
    _, current = active_after_bootstrap(tmp_path)
    fsync = releases.os.fsync
    calls = 0
    def failing(fd):
        nonlocal calls
        calls += 1
        if calls == 2: raise OSError('synthetic post-replace failure')
        return fsync(fd)
    monkeypatch.setattr(releases.os, 'fsync', failing)
    with pytest.raises(ValueError, match='pointer transition'):
        gate.rollback_checked(tmp_path, verify_approval=verify, observe=observe)
    assert releases.read_active_release(tmp_path)['release_id'] == current


@pytest.mark.parametrize('expired_side', ['current', 'target'])
def test_rollback_expiry_rules_distinguish_current_from_target(tmp_path, monkeypatch, expired_side):
    from test_reviewed_knowledge import _records
    from test_release_readiness import sign
    from test_official_link_gate import advance_past_link_expiry
    def with_link(previous):
        b = candidate(previous)
        b['records'] = _records(link_forward=('internal_fae',))
        b['sections'], b['bodies'] = [], {}
        b['scope']['answerable_ids'] = [r['id'] for r in b['records']]
        b['scope']['excluded_topics'].remove('links')
        b['scope']['topic_reviews'] = {'links': 'a'*64}
        return sign(b)
    if expired_side == 'current':
        empty = {'archive_manifest_sha256': 'f'*64, 'sources': [], 'chunks': []}
        target = releases.publish_release(tmp_path, empty, [], None, REVIEW)
        releases.activate_release(tmp_path, target)
        current = gate.stage_release(tmp_path, with_link(target), verify_approval=verify)
    else:
        target = gate.stage_release(tmp_path, with_link(None), verify_approval=verify)
        gate.activate_checked(tmp_path, target, verify_approval=verify, observe=observe)
        current = gate.stage_release(tmp_path, candidate(target), verify_approval=verify)
    gate.activate_checked(tmp_path, current, verify_approval=verify, observe=observe)
    advance_past_link_expiry(monkeypatch)
    if expired_side == 'current':
        gate.rollback_checked(tmp_path, verify_approval=verify, observe=observe)
        assert releases.read_active_release(tmp_path)['release_id'] == target
    else:
        before = (tmp_path/'active.json').read_bytes()
        with pytest.raises(ValueError, match='content_contract'):
            gate.rollback_checked(tmp_path, verify_approval=verify, observe=observe)
        assert (tmp_path/'active.json').read_bytes() == before
