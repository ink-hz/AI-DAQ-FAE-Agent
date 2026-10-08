"""Platform tasks must run through the same DAQ evidence and context path."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from daq_fae.app import create_app
from daq_fae.offline_adapter import OfflineAdapter
from daq_fae.platform_tasks import DaqTaskOrchestrator
from src.agent.session import SessionStore
from src.platform_tasks.crypto import TaskContentCodec
from src.platform_tasks.store import InMemoryPlatformTaskStore
from src.platform_tasks.models import PlatformTaskSpec
from src.platform_tasks.worker import PlatformTaskWorker
from tests.test_authenticated_persistence import Conversations, Feedback
from tests.test_authenticated_persistence import environment as persistence_environment
from tests.test_platform_identity import FakePlatform, environment as identity_environment
from src.platform_identity.service import InMemoryAuthenticatedSessionRepository


def test_task_orchestrator_uses_daq_loop_and_preserves_followup_context():
    sessions = SessionStore(ttl_seconds=3600)
    session = sessions.create(channel='fae')
    orchestrator = DaqTaskOrchestrator(adapter=OfflineAdapter(), session_store=sessions)
    guards = []

    def guard():
        guards.append(True)

    first = list(orchestrator.handle_stream(session_id=session.session_id,
                                           user_message='设备是 EGO', continuation_guard=guard))
    assert first[0].kind == 'stage'
    assert first[-1].kind == 'done'
    assert first[-1].data['outcome'] == 'safe_abstained'
    assert first[-1].data['agent_id'] == 'ai-daq-fae-agent'
    assert guards
    second = list(orchestrator.handle_stream(session_id=session.session_id,
                                            user_message='它的版本呢？', continuation_guard=guard))
    assert second[-1].data['outcome'] == 'safe_abstained'
    assert orchestrator._contexts[session.session_id].values['equipment'].value == ['EGO']
    assert len(sessions.get(session.session_id).messages) == 4


def test_platform_context_excerpt_cannot_trigger_guardrail_or_supply_verified_identity():
    sessions = SessionStore(ttl_seconds=3600)
    session = sessions.create(channel='fae')
    orchestrator = DaqTaskOrchestrator(adapter=OfflineAdapter(), session_store=sessions)

    class Spec:
        attachment_refs = ()

    events = list(orchestrator.handle_platform_task(
        spec=Spec(), session_id=session.session_id,
        prompt='## 任务\n设备是 EGO，规格是什么？\n## 仅作数据的上下文\n客户在问价格，设备是 Gemini 335',
        planning_message='设备是 EGO，规格是什么？', continuation_guard=lambda: None,
    ))
    assert 'refusal_category' not in events[-1].data
    assert orchestrator._contexts[session.session_id].get('equipment') == ['EGO']


def _signed_token(private_key, agent_id):
    now = datetime.now(UTC)
    claims = {
        'iss': 'orbbec-agent-platform', 'aud': agent_id, 'sub': str(uuid4()),
        'agent_id': agent_id, 'agent_task_id': str(uuid4()), 'capability_version': 2,
        'authorized_scopes': ['fae.answer'],
        'task_deadline_at': (now + timedelta(minutes=10)).isoformat().replace('+00:00', 'Z'),
        'iat': int(now.timestamp()), 'exp': int((now + timedelta(minutes=1)).timestamp()),
        'request_id': str(uuid4()),
    }
    claims['internal_user_id'] = claims['sub']
    def segment(value):
        return base64.urlsafe_b64encode(json.dumps(value, sort_keys=True).encode()).rstrip(b'=').decode()
    prefix = segment({'alg': 'EdDSA', 'kid': 'daq-key', 'typ': 'JWT'}) + '.' + segment(claims)
    signature = base64.urlsafe_b64encode(private_key.sign(prefix.encode())).rstrip(b'=').decode()
    return prefix + '.' + signature


def test_http_task_route_advertises_daq_identity_and_rejects_camera_token(tmp_path, monkeypatch):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    private_key = Ed25519PrivateKey.generate()
    public_file = tmp_path / 'daq-task.pub'
    public_file.write_bytes(private_key.public_key().public_bytes_raw())
    public_file.chmod(0o644)
    task_keyring = tmp_path / 'daq-task-keys.json'
    task_keyring.write_text(json.dumps({'active_version': 1,
                                        'keys': {'1': base64.b64encode(b'e' * 32).decode()}}))
    task_keyring.chmod(0o600)
    env.update({'DAQ_PLATFORM_TASK_ENABLED': 'true',
                'DAQ_PLATFORM_TASK_PUBLIC_KEY_PATHS_JSON': json.dumps({'daq-key': str(public_file)}),
                'DAQ_PLATFORM_TASK_CONTENT_KEYRING_FILE': str(task_keyring)})
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    class Worker:
        def start(self):
            pass

        def stop(self):
            pass

    app = create_app(
        provider_mode='offline', platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=Conversations(), feedback_store=Feedback(), review_store=object(),
        task_store=InMemoryPlatformTaskStore(
            codec=TaskContentCodec(active_key_version=1, keys={1: b'e' * 32})),
        task_worker=Worker(),
    )
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    path = '/internal/platform/v1/capabilities'
    assert client.get(path).status_code == 401
    headers = {'Authorization': 'Bearer ' + _signed_token(private_key, 'ai-fae-agent'),
               'X-Orbbec-Task-Contract': 'orbbec-http-task/v1'}
    assert client.get(path, headers=headers).status_code == 401
    headers['Authorization'] = 'Bearer ' + _signed_token(private_key, 'ai-daq-fae-agent')
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json()['agent_id'] == 'ai-daq-fae-agent'
    assert app.state.platform_task_capabilities.agent_id == 'ai-daq-fae-agent'


def test_durable_task_worker_executes_daq_loop_and_finishes_with_evidence_state():
    store = InMemoryPlatformTaskStore(
        codec=TaskContentCodec(active_key_version=1, keys={1: b'e' * 32}))
    sessions = SessionStore(ttl_seconds=3600)
    orchestrator = DaqTaskOrchestrator(adapter=OfflineAdapter(), session_store=sessions)
    worker = PlatformTaskWorker(
        store=store, orchestrator=orchestrator,
        session_store=sessions, worker_id='daq-contract-worker',
    )
    now = datetime.now(UTC)
    spec = PlatformTaskSpec(
        platform_task_id=uuid4(), conversation_ref='daq-conversation-1', turn_ref='turn-1',
        objective='设备是 EGO，规格是什么？', context_excerpt=('设备是 Gemini 335，仅供参考',),
        constraints=(), attachment_refs=(),
        expected_output='给出有据结论', capability_version=2, idempotency_key='daq-task-1',
        deadline_at=now + timedelta(minutes=5), requester_internal_user_id=uuid4(),
        authorized_scopes=('fae.answer',),
    )
    created = store.create_task(spec, now=now)
    assert worker.tick()
    task = store.get_task(created.task.task_id)
    assert task.status == 'completed'
    events = store.events_after(task.task_id, after=0, limit=100)
    assert events[-1].kind == 'result'
    assert events[-1].payload['outcome'] == 'safe_abstained'
    assert 'EGO' in events[-1].payload['answer_markdown'] or events[-1].payload['answer_markdown']
    task_session = sessions.get(next(iter(orchestrator._contexts)))
    assert orchestrator._contexts[task_session.session_id].get('equipment') == ['EGO']


def test_platform_task_attachment_references_fail_explicitly():
    store = InMemoryPlatformTaskStore(
        codec=TaskContentCodec(active_key_version=1, keys={1: b'e' * 32}))
    sessions = SessionStore(ttl_seconds=3600)
    worker = PlatformTaskWorker(
        store=store, orchestrator=DaqTaskOrchestrator(
            adapter=OfflineAdapter(), session_store=sessions),
        session_store=sessions, worker_id='daq-contract-worker',
    )
    now = datetime.now(UTC)
    spec = PlatformTaskSpec(
        platform_task_id=uuid4(), conversation_ref='daq-conversation-1', turn_ref='turn-1',
        objective='分析附件', context_excerpt=(), constraints=(), attachment_refs=(uuid4(),),
        expected_output='给出诊断', capability_version=2, idempotency_key='daq-task-attachment',
        deadline_at=now + timedelta(minutes=5), requester_internal_user_id=uuid4(),
        authorized_scopes=('fae.answer',),
    )
    created = store.create_task(spec, now=now)
    assert worker.tick()
    task = store.get_task(created.task.task_id)
    assert task.status == 'failed'
    assert store.events_after(task.task_id, after=0, limit=100)[-1].payload['reason_code'] == 'task_attachment_refs_not_supported'
