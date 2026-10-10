"""C6 synthetic role contract; no external identity/model/release activation."""
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from daq_fae.app import create_app
from daq_fae.knowledge.releases import _activate_release as activate_release
from src.platform_identity.models import PlatformIdentityError
from src.platform_identity.service import InMemoryAuthenticatedSessionRepository
from tests.test_platform_identity import CODE, FakePlatform, environment as identity_environment
from tests.test_authenticated_persistence import Conversations, Feedback, environment as persistence_environment
from tests.test_durable_state import store
from tests.test_api_transport import terminal
from test_section_tools import make_view
from synthetic_release_helpers import approve_fixture_for_app

pytest_plugins = ('tests.test_durable_state',)


def grants(path, role):
    path.write_text(json.dumps({'agent_id': 'ai-daq-fae-agent', 'subjects': {
        str(FakePlatform().subject.subject_id): role} if role else {}}))
    path.chmod(0o600)


def test_entitlement_file_rechecks_revocation_expansion_and_agent(tmp_path):
    from daq_fae.knowledge.entitlements import KnowledgeEntitlements
    path = tmp_path / 'entitlements.json'
    grants(path, 'channel')
    policy = KnowledgeEntitlements(path)
    subject = FakePlatform().subject
    assert policy.role_for(subject) == 'channel'
    grants(path, 'internal_fae')
    assert policy.role_for(subject) == 'internal_fae'
    grants(path, None)
    with pytest.raises(PlatformIdentityError):
        policy.role_for(subject)
    grants(path, 'internal_fae')
    with pytest.raises(PlatformIdentityError):
        policy.role_for(replace(subject, agent_id='ai-fae-agent'))
    path.chmod(0o666)
    with pytest.raises(PlatformIdentityError):
        policy.role_for(subject)


def test_authenticated_release_roles_history_and_replay(tmp_path, monkeypatch, pg_database):
    root = tmp_path / 'release'
    view = make_view(root)
    from tests.test_reviewed_knowledge import _records, SNAPSHOT, RELEASE_REVIEW
    from daq_fae.knowledge.releases import _publish_release as publish_release
    manifest = view.manifest
    release_id = publish_release(root, SNAPSHOT,
        manifest['records'] + [_records(link_forward=('internal_fae',))[-1]], None, RELEASE_REVIEW,
        sections=[{k: v for k, v in section.items() if k != 'body'} for section in manifest['sections']],
        bodies={section['section_id']: section['body'] for section in manifest['sections']})
    activate_release(root, release_id)
    path = tmp_path / 'entitlements.json'
    grants(path, 'internal_fae')
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path),
           'DAQ_KNOWLEDGE_ENTITLEMENTS_FILE': str(path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    calls = []
    def run(runtime, *args, **kwargs):
        box = runtime.toolbox if hasattr(runtime, 'toolbox') else runtime._toolbox
        assert all(isinstance(requirement, dict) for requirement in box.requirements)
        results = [box.dispatch(name, arguments) for name, arguments in [
            ('search_knowledge', {'query': '采集'}),
            ('read_doc', {'section_id': 'section:recording'}),
            ('lookup_spec', {'entity': 'EGO 1600', 'field': 'resolution'}),
            ('official_links', {'query': 'EGO'}),
        ]]
        calls.append(results)
        yield {'type': 'done', 'answer': '合成私有答案' if results[0].status == 'ok' else '缺证',
               'outcome': 'safe_abstained', 'sources': results[0].sources, 'tool_calls': [],
               'capability_coverage': {}}
    monkeypatch.setattr('daq_fae.authenticated_chat.LoopRuntime.run', run)
    _, verifier = approve_fixture_for_app(root)
    app = create_app(provider_mode='offline', knowledge_release_root=root, knowledge_approval_verifier=verifier,
                     platform_client=FakePlatform(),
                     identity_repository=InMemoryAuthenticatedSessionRepository(),
                     conversation_repository=Conversations(), feedback_store=Feedback(),
                     review_store=object(), durable_state=store(pg_database))
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    body = {'message': '请介绍采集器', 'client_request_id': 'c6-role-first'}
    first = terminal(client.post('/chat', json=body, headers=headers))
    assert first['answer'] == '合成私有答案'
    assert all(result.status == 'ok' for result in calls[0])
    session = first['session_id']
    assert client.get('/history', params={'session_id': session}).status_code == 200
    grants(path, 'channel')
    assert client.get('/history', params={'session_id': session}).status_code == 404
    assert client.get(f'/authenticated/conversations/{session}').status_code == 404
    assert client.post('/chat', json=body, headers=headers).status_code == 409
    second = terminal(client.post('/chat', json={**body, 'client_request_id': 'c6-channel'}, headers=headers))
    assert second['answer'] == '缺证' and second['sources'] == []
    assert all(result.status == 'not_found' and not result.sources for result in calls[-1])
    assert client.get('/review/sessions').status_code == 403
    grants(path, 'tmall_support')
    third = terminal(client.post('/chat', json={**body, 'client_request_id': 'c6-tmall'}, headers=headers))
    assert third['answer'] == '缺证'
    grants(path, 'internal_fae')
    expanded = terminal(client.post('/chat', json={**body, 'client_request_id': 'c6-expanded'}, headers=headers))
    assert expanded['answer'] == '合成私有答案'
    grants(path, None)
    assert client.get('/history', params={'session_id': session}).status_code == 403
    assert client.post('/chat', json=body, headers=headers).status_code == 403


def test_conflict_notice_needs_separate_status_scope_and_role_review(tmp_path):
    from copy import deepcopy
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.knowledge.conflict_visibility import conflict_notice_fingerprint
    view = make_view(tmp_path)
    manifest = deepcopy(view.manifest)
    row = next(r for r in manifest['records'] if r['id'] == 'claim:conflict')
    row['conflict_notice_review'] = {
        'reviewer': 'synthetic-permission-owner', 'reviewed_at': '2026-10-10',
        'view_roles': ['internal_fae'], 'statement': 'conflict_exists',
    }
    row['conflict_notice_review']['sha256'] = conflict_notice_fingerprint(row)
    reviewed = ReviewedKnowledge.from_manifest(view.release_id, manifest)
    args = {'entity': 'EGO 1600', 'field': 'conflict_only'}
    result = DaqToolBox(knowledge=reviewed, role='internal_fae').dispatch('lookup_spec', args)
    assert result.content['claim_status'] == 'conflict'
    assert '冲突机密' not in str(result) and not result.sources
    result = DaqToolBox(knowledge=reviewed, role='channel').dispatch('lookup_spec', args)
    assert result.content['claim_status'] == 'unknown'
    row['conflict_notice_review']['view_roles'].append('channel')
    stale = ReviewedKnowledge.from_manifest(view.release_id, manifest)
    result = DaqToolBox(knowledge=stale, role='internal_fae').dispatch('lookup_spec', args)
    assert result.content['claim_status'] == 'unknown'


def test_tool_guard_rechecks_revoked_access_before_returning_evidence(tmp_path):
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.entitlements import KnowledgeEntitlements
    path = tmp_path / 'roles.json'
    grants(path, 'internal_fae')
    policy = KnowledgeEntitlements(path)
    subject = FakePlatform().subject
    def guard():
        if policy.role_for(subject) != 'internal_fae':
            raise PlatformIdentityError('daq_knowledge_role_changed', status_code=403)
    box = DaqToolBox(knowledge=make_view(tmp_path / 'release'), role='internal_fae', access_guard=guard)
    assert box.dispatch('read_doc', {'section_id': 'section:recording'}).status == 'ok'
    grants(path, 'channel')
    with pytest.raises(PlatformIdentityError):
        box.with_request_context('录制').dispatch('read_doc', {'section_id': 'section:recording'})


def test_nonempty_task_mode_rejects_missing_role_bound_result_contract(tmp_path, monkeypatch):
    view = make_view(tmp_path / 'release')
    activate_release(tmp_path / 'release', view.release_id)
    path = tmp_path / 'roles.json'
    grants(path, 'internal_fae')
    monkeypatch.setenv('DAQ_PLATFORM_IDENTITY_ENABLED', 'true')
    monkeypatch.setenv('DAQ_PLATFORM_TASK_ENABLED', 'true')
    monkeypatch.setenv('DAQ_DATABASE_URL', 'postgresql://synthetic/daq')
    monkeypatch.setenv('DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE', str(tmp_path / 'unused'))
    monkeypatch.setenv('DAQ_KNOWLEDGE_ENTITLEMENTS_FILE', str(path))
    _, verifier = approve_fixture_for_app(tmp_path / 'release')
    with pytest.raises(ValueError, match='daq_knowledge_task_role_replay_contract_missing'):
        create_app(provider_mode='offline', knowledge_release_root=tmp_path / 'release', knowledge_approval_verifier=verifier)


@pytest.mark.parametrize('window', ['before_write', 'after_write', 'after_commit',
                                    'actual_send', 'replay_reserve', 'replay_frame'])
def test_revocation_at_commit_or_frame_boundary_never_sends_private_terminal(
    tmp_path, monkeypatch, pg_database, window,
):
    view = make_view(tmp_path / 'release')
    activate_release(tmp_path / 'release', view.release_id)
    path = tmp_path / 'roles.json'
    grants(path, 'internal_fae')
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path),
           'DAQ_KNOWLEDGE_ENTITLEMENTS_FILE': str(path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    def run(runtime, *args, **kwargs):
        yield {'type': 'done', 'answer': 'PRIVATE_TERMINAL_SENTINEL',
               'outcome': 'safe_abstained', 'sources': [{'source_id': 'PRIVATE_SOURCE_SENTINEL'}],
               'tool_calls': [], 'capability_coverage': {}}
    monkeypatch.setattr('daq_fae.authenticated_chat.LoopRuntime.run', run)
    durable = store(pg_database)
    conversations = Conversations()
    _, verifier = approve_fixture_for_app(tmp_path / 'release')
    app = create_app(provider_mode='offline', knowledge_release_root=tmp_path / 'release', knowledge_approval_verifier=verifier,
                     platform_client=FakePlatform(), identity_repository=InMemoryAuthenticatedSessionRepository(),
                     conversation_repository=conversations, feedback_store=Feedback(),
                     review_store=object(), durable_state=durable)
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    body = {'message': '采集', 'client_request_id': 'boundary-' + window}
    if window.startswith('replay'):
        initial = client.post('/chat', json=body, headers=headers)
        assert terminal(initial)['answer'] == 'PRIVATE_TERMINAL_SENTINEL'
        assert client.post('/chat', json=body, headers=headers).text == initial.text
        original = durable.reserve
        def reserve(*args, **kwargs):
            reservation = original(*args, **kwargs)
            if reservation.status == 'replay':
                if window == 'replay_reserve':
                    grants(path, None)
                else:
                    def frames():
                        for frame in reservation.events:
                            if frame.startswith('event: text_delta'):
                                grants(path, None)
                            yield frame
                    return replace(reservation, events=frames())
            return reservation
        monkeypatch.setattr(durable, 'reserve', reserve)
    elif window == 'actual_send':
        from daq_fae.authenticated_chat import iter_with_heartbeat
        def heartbeat(*args, **kwargs):
            for item in iter_with_heartbeat(*args, **kwargs):
                if isinstance(item, str) and item.startswith('event: text_delta'):
                    grants(path, None)
                yield item
        monkeypatch.setattr('daq_fae.authenticated_chat.iter_with_heartbeat', heartbeat)
    else:
        original = durable.complete_turn
        def complete(*args, **kwargs):
            writer = kwargs['write_turn']
            def write(connection):
                if window == 'before_write':
                    grants(path, None)
                result = writer(connection)
                if window == 'after_write':
                    grants(path, None)
                return result
            kwargs['write_turn'] = write
            result = original(*args, **kwargs)
            if window == 'after_commit':
                grants(path, None)
            return result
        monkeypatch.setattr(durable, 'complete_turn', complete)
    response = client.post('/chat', json=body, headers=headers)
    assert 'PRIVATE_TERMINAL_SENTINEL' not in response.text
    assert 'PRIVATE_SOURCE_SENTINEL' not in response.text
    assert 'event: done' not in response.text
    assert '"stage": "authorization"' in response.text
    if window == 'before_write':
        assert not conversations.writes


def test_tool_revocation_during_section_read_withholds_body(tmp_path, monkeypatch):
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.entitlements import KnowledgeEntitlements
    path = tmp_path / 'roles.json'
    grants(path, 'internal_fae')
    policy = KnowledgeEntitlements(path)
    view = make_view(tmp_path / 'release')
    original = view.sections_for
    def sections(*args, **kwargs):
        result = original(*args, **kwargs)
        grants(path, None)
        return result
    monkeypatch.setattr(view, 'sections_for', sections)
    box = DaqToolBox(knowledge=view, role='internal_fae',
                     access_guard=lambda: policy.role_for(FakePlatform().subject))
    with pytest.raises(PlatformIdentityError):
        box.with_request_context('录制').dispatch('read_doc', {'section_id': 'section:recording'})
