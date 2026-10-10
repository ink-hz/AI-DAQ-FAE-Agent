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
