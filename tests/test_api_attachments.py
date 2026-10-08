from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from daq_fae.app import create_app
from src.attachments.models import AttachmentLimits
from tests.test_api_transport import terminal


def upload(client, text='device log\nstatus=ready'):
    response = client.post('/attachments', files=[('files', ('log.txt', text.encode(), 'text/plain'))])
    assert response.status_code == 201, response.text
    return response.json()['results'][0]['attachment']


def test_upload_status_delete_and_no_download_or_private_paths(tmp_path):
    app = create_app(attachment_dir=tmp_path / 'daq')
    with TestClient(app) as client:
        manifest = upload(client)
        aid = manifest['attachment_id']
        status = client.get(f'/attachments/{aid}')
        assert status.status_code == 200
        assert status.json()['status'] == 'ready'
        assert 'device log' not in status.text
        assert str(tmp_path) not in status.text
        assert 'sha256' not in status.json()
        assert client.get(f'/attachments/{aid}/download').status_code == 404
        assert client.delete(f'/attachments/{aid}').status_code == 204
        assert client.delete(f'/attachments/{aid}').status_code == 204
        assert client.get(f'/attachments/{aid}').status_code == 410
        assert not (tmp_path / 'daq' / aid).exists()


def test_expiry_and_gc_reject_stale_chat_binding(tmp_path):
    now = [datetime.now(timezone.utc)]
    app = create_app(attachment_dir=tmp_path / 'daq', attachment_clock=lambda: now[0],
                     attachment_limits=AttachmentLimits(ttl_seconds=1))
    client = TestClient(app)
    aid = upload(client)['attachment_id']
    now[0] += timedelta(seconds=2)
    assert client.get(f'/attachments/{aid}').status_code == 410
    response = client.post('/chat', json={'message': '看日志', 'attachment_ids': [aid]})
    assert response.status_code == 410
    assert app.state.attachment_store.gc_expired().deleted_count == 1
    assert not (tmp_path / 'daq' / aid).exists()


def test_batch_limits_and_rate_gate_are_enforced(tmp_path):
    app = create_app(attachment_dir=tmp_path / 'daq', attachment_limits=AttachmentLimits(max_files=1, max_batch_bytes=10))
    client = TestClient(app)
    files = [('files', ('a.txt', b'a', 'text/plain')), ('files', ('b.txt', b'b', 'text/plain'))]
    assert client.post('/attachments', files=files).status_code == 413
    assert client.post('/attachments', files=[('files', ('a.txt', b'x' * 11, 'text/plain'))]).status_code == 413
    app.state.attachment_upload_gate._rate = 1
    # Failed batch attempts also consume upload rate budget.
    assert client.post('/attachments', files=files[:1]).status_code == 429
    assert app.state.attachment_store.storage_status().used_bytes == 0


def test_attachment_session_binding_is_exclusive_and_model_use_is_explicit(tmp_path):
    class ProbeAdapter:
        model = 'attachment-contract'
        tool_choice_strategy = 'submit_only_auto'

        def chat(self, messages, tools=None, required_tool=None):
            tool_messages = [item for item in messages if item.get('role') == 'tool']
            names = {item['function']['name'] for item in tools}
            if 'search_attachments' in names and not tool_messages:
                name, arguments = 'search_attachments', {'query': 'status=ready'}
            elif not any('empty_knowledge_release' in str(item.get('content')) for item in tool_messages):
                name, arguments = 'search_knowledge', {'query': 'EGO status'}
            else:
                name, arguments = 'submit_answer', {
                    'outcome': 'safe_abstained', 'missing': '缺少已审核的产品事实资料。',
                }
            yield {'type': 'tool_call', 'id': name, 'name': name, 'arguments': arguments}
            yield {'type': 'stop', 'stop_reason': 'tool_use', 'usage': None}

    app = create_app(attachment_dir=tmp_path / 'daq', adapter=ProbeAdapter())
    client = TestClient(app)
    aid = upload(client)['attachment_id']
    first = terminal(client.post('/chat', json={'message': '看日志', 'attachment_ids': [aid]}))
    sid = first['session_id']
    assert app.state.attachment_store.get(aid).bound_session_id == sid
    assert first['outcome'] == 'safe_abstained'
    assert first['fallback_used'] is False
    assert first['sources'][0]['type'] == 'user_attachment'
    assert first['attachment_coverage'] == 'full'
    assert first['actual_capabilities'] == ['search_attachments', 'search_knowledge', 'user_attachment']
    second_sid = terminal(client.post('/chat', json={'message': '新会话'}))['session_id']
    denied = client.post('/chat', json={'session_id': second_sid, 'message': '看日志', 'attachment_ids': [aid]})
    assert denied.status_code == 409
    assert 'attachment' in denied.json()['detail']
    assert app.state.attachment_store.get(aid).bound_session_id == sid
    assert client.get('/health').json()['attachment_capability'] == 'session_bound_tools'


def test_binding_failure_is_atomic_and_upload_identity_does_not_come_from_headers(tmp_path):
    app = create_app(attachment_dir=tmp_path / 'daq')
    client = TestClient(app)
    bound = upload(client)['attachment_id']
    first_sid = terminal(client.post('/chat', json={'message': '附件', 'attachment_ids': [bound]}))['session_id']
    free = upload(client)['attachment_id']
    second_sid = terminal(client.post('/chat', json={'message': '另一会话'}))['session_id']
    denied = client.post('/chat', json={'session_id': second_sid, 'message': '两个附件', 'attachment_ids': [free, bound]})
    assert denied.status_code == 409
    assert app.state.attachment_store.get(free).bound_session_id is None
    assert app.state.attachment_store.get(bound).bound_session_id == first_sid
    assert app.state.chat_concurrency_gate.snapshot().active == 0
    assert app.state.session_store.get(second_sid).active_attachment_ids == []
    uploaded = client.post('/attachments', files=[('files', ('a.txt', b'log', 'text/plain'))],
                           headers={'x-platform-subject-id': 'forged-user'})
    manifest = uploaded.json()['results'][0]['attachment']
    assert app.state.attachment_store.get(manifest['attachment_id']).owner_subject_id is None


def test_capacity_type_checks_local_guard_and_gc_lifecycle(tmp_path):
    app = create_app(attachment_dir=tmp_path / 'daq',
                     attachment_limits=AttachmentLimits(storage_capacity_bytes=10))
    with TestClient(app) as client:
        assert app.state.attachment_gc_worker._thread.is_alive()
        invalid = client.post('/attachments', files=[('files', ('a.exe', b'MZbinary', 'application/octet-stream'))])
        assert invalid.status_code == 207
        assert invalid.json()['results'][0]['ok'] is False
        full = client.post('/attachments', files=[('files', ('a.txt', b'x' * 20, 'text/plain'))])
        assert full.status_code == 207
        assert full.json()['results'][0]['error']['code'] == 'attachment_capacity_exceeded'
        assert app.state.attachment_store.storage_status().used_bytes == 0
    assert app.state.attachment_gc_worker._thread is None
    remote = TestClient(app, client=('192.0.2.1', 1234))
    assert remote.post('/attachments', files=[('files', ('a.txt', b'log', 'text/plain'))]).status_code == 403
