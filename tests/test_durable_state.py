"""Durable execution/context contracts against an isolated Unix-socket Postgres."""
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from src.api.stream import sse_event
from src.platform_identity.models import PlatformSubject
from src.storage.authenticated_conversations import ConversationContentCodec

SUBJECT = PlatformSubject(subject_id=UUID('b78b3205-2c86-424a-a217-775382c208bd'),
                          internal_user_id=UUID('b78b3205-2c86-424a-a217-775382c208bd'),
                          identity_binding_id=UUID('6dbedcf8-5263-493f-91f5-6324be037d7c'),
                          subject_type='enterprise_member', agent_id='ai-daq-fae-agent', active=True)


@pytest.fixture(scope='module')
def pg_database():
    if not all(shutil.which(binary) for binary in ('initdb', 'pg_ctl')):
        pytest.skip('isolated Postgres contract requires initdb and pg_ctl')
    directory = Path(tempfile.mkdtemp(prefix='daq-contract-pg-', dir='/tmp'))
    data, socket = directory / 'data', directory / 'socket'
    socket.mkdir()
    subprocess.run(['initdb', '-D', str(data), '-U', 'daq_contract_admin', '--auth-local=trust',
                    '--auth-host=reject', '--no-locale', '--encoding=UTF8'], check=True, capture_output=True)
    subprocess.run(['pg_ctl', '-D', str(data), '-l', str(directory / 'postgres.log'), '-o',
                    f"-F -k {socket} -h ''", '-w', 'start'], check=True, capture_output=True)
    try:
        with psycopg.connect(host=str(socket), user='daq_contract_admin', dbname='postgres', autocommit=True) as connection:
            connection.execute('create role daq_contract_user login')
            connection.execute('create database daq_contract owner daq_contract_user')
        yield f'host={socket} dbname=daq_contract user=daq_contract_user'
    finally:
        subprocess.run(['pg_ctl', '-D', str(data), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)
        shutil.rmtree(directory)


def store(pg_database):
    from daq_fae.durable_state import DaqDurableState
    migration = Path(__file__).resolve().parents[1] / 'migrations/daq/001_durable_request_context.sql'
    with psycopg.connect(pg_database) as connection:
        connection.execute(migration.read_text())
    return DaqDurableState(pg_database, codec=ConversationContentCodec(active_key_version=1, keys={1: b'c' * 32}))


def reserve(state, *, subject=SUBJECT, client_id=None, session_id=None, payload=None):
    return state.reserve(subject, client_id or str(uuid4()), payload or {'message': '设备版本', 'session_id': None},
                         session_id or f'daq:{uuid4()}')


def completed(reservation):
    return [sse_event('session', {'session_id': reservation.session_id}),
            sse_event('text_delta', {'delta': '确认设备版本后再核查'}),
            sse_event('done', {'agent_id': 'ai-daq-fae-agent', 'session_id': reservation.session_id,
                               'trace_id': 'trace-1', 'outcome': 'safe_abstained',
                               'fallback_used': False, 'fallback_reason': None})]


def test_reservation_replay_survives_new_store_instance_and_events_are_sealed(pg_database):
    state = store(pg_database)
    first = reserve(state)
    assert first.status == 'execute' and first.execution_token
    assert state.reserve(SUBJECT, first.client_request_id, {'message': '设备版本', 'session_id': None},
                         f'daq:{uuid4()}').status == 'in_progress'
    state.finish(SUBJECT, first, completed(first))
    restored = store(pg_database).reserve(SUBJECT, first.client_request_id,
                                          {'message': '设备版本', 'session_id': None}, f'daq:{uuid4()}')
    assert restored.status == 'replay' and restored.session_id == first.session_id
    assert restored.events == tuple(completed(first)) and restored.execution_token is None
    with psycopg.connect(pg_database, row_factory=dict_row) as connection:
        row = connection.execute('select terminal_ciphertext from daq_request_ledger where client_request_id=%s',
                                 (first.client_request_id,)).fetchone()
    assert '确认设备版本'.encode() not in bytes(row['terminal_ciphertext'])


def test_concurrent_duplicate_reservation_executes_once(pg_database):
    state = store(pg_database)
    key, session = str(uuid4()), f'daq:{uuid4()}'
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: reserve(state, client_id=key, session_id=session), range(4)))
    assert [item.status for item in results].count('execute') == 1
    assert [item.status for item in results].count('in_progress') == 3


def test_payload_conflict_and_other_owner_are_not_replayed(pg_database):
    from dataclasses import replace
    from daq_fae.durable_state import RequestConflict
    state = store(pg_database)
    first = reserve(state)
    with pytest.raises(RequestConflict):
        reserve(state, client_id=first.client_request_id, payload={'message': '不同问题'})
    other_id = UUID('d46114bb-e01c-4f69-b67f-ef0d21f62e5e')
    other = replace(SUBJECT, subject_id=other_id, internal_user_id=other_id)
    assert reserve(state, subject=other, client_id=first.client_request_id).status == 'execute'


def test_foreign_agent_missing_owner_and_foreign_session_fail_closed(pg_database):
    from dataclasses import replace
    from daq_fae.durable_state import DurableStateError
    from src.platform_identity.models import PlatformIdentityError
    state = store(pg_database)
    with pytest.raises(PlatformIdentityError):
        reserve(state, subject=replace(SUBJECT, agent_id='ai-fae-agent'))
    with pytest.raises(PlatformIdentityError):
        reserve(state, subject=None)
    with pytest.raises(DurableStateError):
        reserve(state, session_id='fae-session')


def test_expired_execution_and_interruption_never_automatically_rerun(pg_database):
    from daq_fae.durable_state import RequestInterrupted
    state = store(pg_database)
    first = reserve(state)
    with psycopg.connect(pg_database) as connection:
        connection.execute("update daq_request_ledger set lease_expires_at=clock_timestamp()-interval '1 second' where client_request_id=%s", (first.client_request_id,))
    next_attempt = reserve(state, client_id=first.client_request_id, session_id=first.session_id)
    assert next_attempt.status == 'interrupted'
    with pytest.raises(RequestInterrupted):
        state.finish(SUBJECT, first, completed(first))
    second = reserve(state)
    state.interrupt(SUBJECT, second, reason='client_disconnected')
    assert reserve(state, client_id=second.client_request_id).status == 'interrupted'


def test_session_busy_and_invalid_terminal_cannot_be_marked_complete(pg_database):
    from daq_fae.durable_state import DurableStateError, SessionBusy
    state = store(pg_database)
    first = reserve(state)
    with pytest.raises(SessionBusy):
        reserve(state, session_id=first.session_id)
    with pytest.raises(DurableStateError, match='daq_terminal_invalid'):
        state.finish(SUBJECT, first, completed(first)[:-1])
    assert reserve(state, client_id=first.client_request_id).status == 'in_progress'


def test_context_revision_restart_owner_boundary_and_atomic_finish(pg_database):
    from dataclasses import replace
    from daq_fae.durable_state import ContextConflict, DaqContextState
    state = store(pg_database)
    first = reserve(state)
    context = DaqContextState(acquisition_task='录制', platform='Linux', viewer_version='1.2',
                              device_variants={'EGO': '1920'}, tried_steps=['检查连接'],
                              requirements=[{'id': 'version', 'capability': 'check_software_support',
                                             'critical': True, 'status': 'missing'}])
    assert state.save_context(SUBJECT, first.session_id, context, expected_revision=0) == 1
    with pytest.raises(ContextConflict):
        state.save_context(SUBJECT, first.session_id, context, expected_revision=0)
    state.finish(SUBJECT, first, completed(first), context=context, expected_context_revision=1)
    checkpoint = store(pg_database).load_context(SUBJECT, first.session_id)
    assert checkpoint.revision == 2 and checkpoint.state == context
    other_id = UUID('d46114bb-e01c-4f69-b67f-ef0d21f62e5e')
    other = replace(SUBJECT, subject_id=other_id, internal_user_id=other_id)
    assert state.load_context(other, first.session_id) is None
    assert state.load_context(SUBJECT, f'daq:{uuid4()}') is None


def test_context_conflict_rolls_back_request_completion(pg_database):
    from daq_fae.durable_state import ContextConflict, DaqContextState
    state = store(pg_database)
    first = reserve(state)
    context = DaqContextState(acquisition_task='录制')
    state.save_context(SUBJECT, first.session_id, context, expected_revision=0)
    with pytest.raises(ContextConflict):
        state.finish(SUBJECT, first, completed(first), context=context, expected_context_revision=0)
    assert reserve(state, client_id=first.client_request_id).status == 'in_progress'


def test_terminal_must_have_exactly_one_frame_and_correct_agent(pg_database):
    from daq_fae.durable_state import DurableStateError
    state = store(pg_database)
    first = reserve(state)
    extra = completed(first)
    extra[-1] += sse_event('text_delta', {'delta': 'post-terminal garbage'})
    with pytest.raises(DurableStateError, match='daq_terminal_invalid'):
        state.finish(SUBJECT, first, extra)
    wrong = completed(first)
    wrong[-1] = wrong[-1].replace('ai-daq-fae-agent', 'ai-fae-agent')
    with pytest.raises(DurableStateError, match='daq_terminal_invalid'):
        state.finish(SUBJECT, first, wrong)


def test_lease_renew_foreign_execution_and_same_session_binding(pg_database):
    from dataclasses import replace
    from daq_fae.durable_state import DurableStateError, RequestInterrupted
    state = store(pg_database)
    first = reserve(state)
    assert state.renew(SUBJECT, first, lease_seconds=60) is True
    other_id = UUID('d46114bb-e01c-4f69-b67f-ef0d21f62e5e')
    other = replace(SUBJECT, subject_id=other_id, internal_user_id=other_id)
    with pytest.raises(RequestInterrupted):
        state.finish(other, first, completed(first))
    with pytest.raises(DurableStateError, match='daq_session_access_denied'):
        reserve(state, subject=other, session_id=first.session_id)
    state.finish(SUBJECT, first, completed(first))
    with pytest.raises(RequestInterrupted):
        state.renew(SUBJECT, first)


def test_context_closed_schema_and_tampered_ciphertext_fail(pg_database):
    from daq_fae.durable_state import DaqContextState, DurableStateError
    from pydantic import ValidationError
    state = store(pg_database)
    first = reserve(state)
    with pytest.raises(ValidationError):
        DaqContextState.model_validate({'schema_version': 2})
    with pytest.raises(ValidationError):
        DaqContextState.model_validate({'unreviewed_knowledge': 'must not persist'})
    state.save_context(SUBJECT, first.session_id, DaqContextState(platform='Linux'), expected_revision=0)
    with psycopg.connect(pg_database) as connection:
        connection.execute('update daq_context_checkpoints set state_ciphertext=%s where external_session_id=%s',
                           (b'invalid ciphertext', first.session_id))
    with pytest.raises(DurableStateError, match='daq_state_checkpoint_invalid'):
        state.load_context(SUBJECT, first.session_id)


def test_missing_durable_config_fails_closed():
    from daq_fae.durable_state import DurableStateError, configure_durable_state
    from fastapi import FastAPI
    with pytest.raises(DurableStateError, match='daq_durable_configuration_missing'):
        configure_durable_state(FastAPI(), environ={})


def test_task_context_checkpoint_round_trip_preserves_multiturn_user_provenance(pg_database):
    from daq_fae.durable_state import DaqContextState
    from daq_fae.task_context import prepare_turn
    state = store(pg_database)
    first = reserve(state)
    plan = prepare_turn('设备是 EGO + WristCam；平台是 Linux；SDK 版本 1.2.0；已经检查连接，录制仍报错',
                        updates={'variant': {'value': '1920', 'certainty': 'hypothesis'}})
    raw = plan.context.to_checkpoint()
    context = DaqContextState(task_context=raw)
    state.finish(SUBJECT, first, completed(first), context=context, expected_context_revision=0)
    loaded = store(pg_database).load_context(SUBJECT, first.session_id)
    assert loaded.state.task_context == raw
    followup = prepare_turn('那下一步呢？', previous=loaded.state.task_context)
    assert followup.context.get('equipment') == ['EGO', 'WristCam']
    assert followup.context.get('sdk_version') == '1.2.0'
    assert followup.context.values['variant'].certainty == 'hypothesis'
    assert followup.context.values['variant'].authority == 'user_supplied'
    assert followup.context.values['variant'].origin_turn == 1
    assert followup.context.turn == 2 and followup.context.topic_id == raw['topic_id']
    assert set(raw['active_capabilities']) <= set(followup.context.active_capabilities)
    assert followup.context.attempted_steps == tuple(raw['attempted_steps'])
    switched = prepare_turn('换个场景，设备是 UMI', previous=loaded.state.task_context)
    assert switched.context.get('equipment') == ['UMI']
    assert switched.context.get('sdk_version') is None


@pytest.mark.parametrize('mutation', [
    lambda raw: raw.update(version=2),
    lambda raw: raw.update(verified_facts={'frame_rate': 120}),
    lambda raw: raw['values']['equipment'].update(authority='official_fact'),
    lambda raw: raw.update(turn='1'),
])
def test_task_context_checkpoint_rejects_unknown_or_upgraded_authority(mutation):
    from daq_fae.durable_state import DaqContextState
    from daq_fae.task_context import prepare_turn
    from pydantic import ValidationError
    raw = prepare_turn('设备是 EGO').context.to_checkpoint()
    mutation(raw)
    with pytest.raises(ValidationError, match="daq_task_context_checkpoint_invalid"):
        DaqContextState(task_context=raw)
