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
