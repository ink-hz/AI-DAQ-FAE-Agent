"""Platform task knowledge must bind requester, role and release before replay."""
from uuid import uuid4
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg
import pytest

from daq_fae.task_knowledge_binding import (
    BoundTaskStore, DaqTaskVerifier, TaskKnowledgeAccessError, TaskKnowledgeBindings,
)
from scripts.migrate_daq_pg import migrate_connection
from src.platform_tasks.crypto import TaskContentCodec
from src.platform_tasks.identity import TaskIdentityError
from src.platform_tasks.models import PlatformTaskSpec, TaskStoreError
from src.platform_tasks.store import InMemoryPlatformTaskStore

pytest_plugins = ('tests.test_durable_state',)

DAQ_DSN = 'postgresql://daq_user@db.invalid/daq_db?options=-csearch_path%3Ddaq_fae'


@pytest.fixture(scope='module')
def prepared_daq_db(pg_database):
    with psycopg.connect(pg_database, autocommit=True) as connection:
        connection.execute('create table public.camera_task_probe (marker text)')
        connection.execute("insert into public.camera_task_probe values ('camera-task')")
        migrate_connection(connection, isolated_schema=True)
    return pg_database


class Entitlements:
    def __init__(self, requester):
        self.roles = {str(requester): 'internal_fae'}

    def role_for(self, subject):
        role = self.roles.get(str(subject.subject_id))
        if role is None:
            raise ValueError('role_revoked')
        return role


def test_task_binding_is_immutable_and_rechecks_role_release_and_requester(prepared_daq_db):
    pg_database = prepared_daq_db
    requester = uuid4()
    role_policy = Entitlements(requester)
    release = {'id': 'a' * 64}
    def connect(_dsn):
        return psycopg.connect(pg_database, options='-csearch_path=daq_fae')
    bindings = TaskKnowledgeBindings(
        DAQ_DSN, entitlements=role_policy, release_id='a' * 64,
        active_release=lambda: release['id'], connect=connect,
    )
    platform_task_id = uuid4()
    assert bindings.bind(platform_task_id, requester) == 'internal_fae'
    assert bindings.bind(platform_task_id, requester) == 'internal_fae'
    assert bindings.require_current(platform_task_id, requester) == 'internal_fae'
    other_requester = uuid4()
    role_policy.roles[str(other_requester)] = 'internal_fae'
    with pytest.raises(TaskKnowledgeAccessError, match='task_requester_changed'):
        bindings.require_current(platform_task_id, other_requester)
    role_policy.roles[str(requester)] = 'channel'
    with pytest.raises(TaskKnowledgeAccessError, match='task_role_changed'):
        bindings.require_current(platform_task_id, requester)
    role_policy.roles[str(requester)] = 'internal_fae'
    release['id'] = 'b' * 64
    with pytest.raises(TaskKnowledgeAccessError, match='task_release_changed'):
        bindings.require_current(platform_task_id, requester)
    with psycopg.connect(pg_database) as connection:
        assert connection.execute('select marker from public.camera_task_probe').fetchone() == ('camera-task',)


def test_existing_unbound_task_is_not_treated_as_new(prepared_daq_db):
    pg_database = prepared_daq_db
    requester = uuid4()
    role_policy = Entitlements(requester)
    def connect(_dsn):
        return psycopg.connect(pg_database, options='-csearch_path=daq_fae')
    bindings = TaskKnowledgeBindings(
        DAQ_DSN, entitlements=role_policy, release_id='a' * 64,
        active_release=lambda: 'a' * 64, connect=connect,
    )
    unknown_id = uuid4()
    assert bindings.may_create(unknown_id, requester) == 'internal_fae'
    with psycopg.connect(pg_database, autocommit=True) as connection:
        connection.execute(
            """insert into daq_fae.platform_tasks
               (platform_task_id, idempotency_key, request_sha256, request_ciphertext,
                request_key_version, requester_internal_user_id, capability_version,
                authorized_scopes, deadline_at)
               values (%s, 'test-unbound', %s, %s, 1, %s, 2,
                       array['fae.answer'], now() + interval '5 minutes')""",
            (unknown_id, bytes(32), b'ciphertext', requester),
        )
    with pytest.raises(TaskKnowledgeAccessError, match='task_binding_missing'):
        bindings.may_create(unknown_id, requester)


def test_task_store_and_token_verifier_block_replay_after_role_change(prepared_daq_db):
    requester = uuid4()
    policy = Entitlements(requester)
    release = {'id': 'c' * 64}
    bindings = TaskKnowledgeBindings(
        DAQ_DSN, entitlements=policy, release_id=release['id'],
        active_release=lambda: release['id'],
        connect=lambda _: psycopg.connect(prepared_daq_db, options='-csearch_path=daq_fae'),
    )
    base = InMemoryPlatformTaskStore(
        codec=TaskContentCodec(active_key_version=1, keys={1: b't' * 32}),
    )
    store = BoundTaskStore(base, bindings)
    platform_task_id = uuid4()
    identity = SimpleNamespace(
        agent_id='ai-daq-fae-agent', agent_task_id=platform_task_id,
        internal_user_id=requester,
    )
    verifier = DaqTaskVerifier(SimpleNamespace(verify=lambda _: identity), bindings)
    assert verifier.verify('signed-token') is identity
    now = datetime.now(UTC)
    spec = PlatformTaskSpec(
        platform_task_id=platform_task_id, conversation_ref='conversation', turn_ref='turn',
        objective='EGO 规格', context_excerpt=(), constraints=(), attachment_refs=(),
        expected_output='有据回答', capability_version=2, idempotency_key=str(uuid4()),
        deadline_at=now + timedelta(minutes=5), requester_internal_user_id=requester,
        authorized_scopes=('fae.answer',),
    )
    created = store.create_task(spec, now=now)
    assert store.get_task(created.task.task_id).platform_task_id == platform_task_id
    assert store.load_request(created.task.task_id).objective == 'EGO 规格'
    policy.roles[str(requester)] = 'channel'
    with pytest.raises(TaskIdentityError) as denial:
        verifier.verify('signed-token')
    assert denial.value.status_code == 403
    with pytest.raises(TaskStoreError, match='task_role_changed'):
        store.get_task(created.task.task_id)
    release['id'] = 'd' * 64
    with pytest.raises(TaskIdentityError) as changed:
        verifier.verify('signed-token')
    assert changed.value.status_code == 403
