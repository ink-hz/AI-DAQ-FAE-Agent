"""Owned local PostgreSQL17 + file IPC tests; never a production quiescence claim."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import subprocess
import time
import tempfile

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'deploy/observe_daq_quiescence.py'
ATTEMPT = 'a' * 32


def module():
    spec = importlib.util.spec_from_file_location('quiescence_observer', SCRIPT)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


@pytest.fixture(scope='module')
def local_pg17(tmp_path_factory):
    binaries = Path('/opt/homebrew/opt/postgresql@17/bin')
    if not (binaries / 'postgres').is_file(): pytest.fail('owned local PostgreSQL17 unavailable; no fallback/pull')
    version = subprocess.check_output([str(binaries / 'postgres'), '--version'], text=True).strip()
    assert 'PostgreSQL) 17.' in version
    root = tmp_path_factory.mktemp('daq_observer_pg17'); root.chmod(0o700)
    data = root / 'data'; socket_owner = tempfile.TemporaryDirectory(prefix='dqo-', dir='/tmp')
    sockets = Path(socket_owner.name)
    env = {'PATH': str(binaries) + ':/usr/bin:/bin', 'LC_ALL': 'C'}
    subprocess.run([str(binaries / 'initdb'), '-D', str(data), '-U', 'fixture_admin', '--auth-local=trust',
                    '--auth-host=scram-sha-256', '--no-locale', '--encoding=UTF8'], env=env, check=True, capture_output=True)
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); port = bound.getsockname()[1]
    subprocess.run([str(binaries / 'pg_ctl'), '-D', str(data), '-l', str(root / 'server.log'), '-o',
                    f'-h 127.0.0.1 -p {port} -k {sockets}', '-w', 'start'], env=env, check=True, capture_output=True)
    admin = make_conninfo(host=str(sockets), port=port, user='fixture_admin', dbname='postgres')
    try:
        with psycopg.connect(admin, autocommit=True) as connection:
            assert connection.execute('show server_version_num').fetchone()[0].startswith('17')
        yield {'admin': admin, 'port': port, 'version': version}
    finally:
        subprocess.run([str(binaries / 'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'],
                       env=env, check=True, capture_output=True)
        socket_owner.cleanup()


@pytest.fixture
def database(local_pg17):
    suffix = secrets.token_hex(6); name = 'observer_' + suffix; role = 'reader_' + suffix
    password = secrets.token_urlsafe(24)
    with psycopg.connect(local_pg17['admin'], autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    admin_dsn = make_conninfo(local_pg17['admin'], dbname=name)
    dsn = make_conninfo(host='127.0.0.1', port=local_pg17['port'], dbname=name, user=role,
                        password=password, options='-csearch_path=daq_fae')
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute('CREATE SCHEMA daq_fae')
            marker = (ROOT / 'migrations/daq/002_installation_identity.sql').read_text()
            marker = 'create table if not exists daq_installation_identity' + marker.split('create table if not exists daq_installation_identity', 1)[1].split('do $guard$', 1)[0]
            for schema in ('daq_fae', 'public'):
                admin.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
                admin.execute((ROOT / 'migrations/daq/001_durable_request_context.sql').read_text())
                admin.execute(marker)
                admin.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
                admin.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
            admin.execute('SET search_path TO daq_fae')
            admin.execute('CREATE TABLE writable_fixture (n integer)')
            admin.execute(sql.SQL('GRANT SELECT,INSERT,DELETE ON writable_fixture TO {}').format(sql.Identifier(role)))
            owner = 'b78b3205-2c86-424a-a217-775382c208bd'
            for index, (state, delta) in enumerate((('running', 600), ('running', -600), ('completed', 600), ('interrupted', 600))):
                sid = f'daq:fixture-{index}'
                admin.execute('INSERT INTO daq_state_sessions VALUES (%s,%s,%s)', ('ai-daq-fae-agent', sid, owner))
                terminal = b'fixture' if state == 'completed' else None
                admin.execute('''INSERT INTO daq_request_ledger
                    (agent_id,owner_subject_id,client_request_id,external_session_id,payload_sha256,execution_token,state,
                     lease_expires_at,terminal_ciphertext,terminal_key_version,terminal_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+make_interval(secs=>%s),%s,%s,%s)''',
                    ('ai-daq-fae-agent', owner, str(index), sid, b'fixture', owner, state, delta, terminal,
                     1 if terminal else None, terminal))
        # Source-derived baseline in this synthetic DB, from the actual normal role.
        m = module()
        with psycopg.connect(dsn, autocommit=True) as source:
            source.execute('BEGIN READ ONLY')
            identity = m.read_identity(source)
            source.execute('COMMIT')
            assert source.execute('select rolsuper from pg_roles where rolname=current_user').fetchone() == (False,)
        yield {'dsn': dsn, 'admin': admin_dsn, 'identity': identity, 'role': role}
    finally:
        with psycopg.connect(local_pg17['admin'], autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
            admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_pg17_normal_role_preserves_options_and_honest_nonzero_counts(database):
    m = module(); result = m.observe(database['dsn'], database['identity'], ATTEMPT)
    assert result['status'] == 'OBSERVED' and result['query_ok'] is True
    assert result['counts'] == {'running': 2, 'live_lease': 1, 'expired_running': 1, 'completed': 1, 'interrupted': 1}
    assert result['identity'] == database['identity'] and result['transaction_read_only'] is True
    assert result['observed_at'] and result['last_update']
    with psycopg.connect(database['admin']) as admin:
        assert admin.execute('select count(*) from pg_stat_activity where application_name=%s', ('daq-observer-' + ATTEMPT,)).fetchone() == (0,)


def test_pg17_accessible_empty_wrong_schema_is_refused(database):
    m = module(); wrong = make_conninfo(database['dsn'], options='-csearch_path=public')
    with psycopg.connect(wrong) as connection:
        assert connection.execute('select count(*) from daq_request_ledger').fetchone() == (0,)
    with pytest.raises(m.ObserverError, match='identity_mismatch'):
        m.observe(wrong, database['identity'], ATTEMPT)


def test_pg17_readonly_boundary_rejects_otherwise_authorized_write(database):
    m = module()
    with psycopg.connect(database['dsn']) as connection:
        connection.execute('insert into writable_fixture values (1)')
        connection.execute('delete from writable_fixture')
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction) as failure:
        with m.read_transaction(database['dsn'], database['identity'], ATTEMPT) as (connection, _):
            connection.execute('insert into writable_fixture values (2)')
    assert failure.value.sqlstate == '25006'


@pytest.mark.parametrize('change', ['marker', 'oid'])
def test_pg17_marker_or_relation_identity_drift_refused(database, change):
    m = module()
    with psycopg.connect(database['admin'], autocommit=True) as admin:
        if change == 'marker': admin.execute("update daq_fae.daq_installation_identity set database_name='wrong_database'")
        else:
            admin.execute('alter table daq_fae.daq_request_ledger rename to original_ledger')
            admin.execute('create table daq_fae.daq_request_ledger (like daq_fae.original_ledger including all)')
            admin.execute(sql.SQL('grant select on daq_fae.daq_request_ledger to {}').format(sql.Identifier(database['role'])))
    with pytest.raises(m.ObserverError, match='identity_mismatch|marker_mismatch'):
        m.observe(database['dsn'], database['identity'], ATTEMPT)


def write_control(path, value):
    pending = path.parent.parent / ('pending-' + secrets.token_hex(8))
    pending.write_text(json.dumps(value)); pending.chmod(0o640)
    os.replace(pending, path)


@pytest.fixture
def ipc(tmp_path, monkeypatch, database):
    m = module(); control = tmp_path / 'control'; output = tmp_path / 'output'
    control.mkdir(mode=0o750); output.mkdir(mode=0o700)
    # Only OS root/21081 ownership is substituted; all FD/mode/inode checks are real.
    monkeypatch.setattr(m, 'CONTROL_UID', os.getuid())
    monkeypatch.setattr(m, 'OBSERVER_UID', os.getuid())
    monkeypatch.setattr(m, 'OBSERVER_GID', os.getgid())
    monkeypatch.setattr(m, 'CONTROL_DIR', str(control))
    monkeypatch.setattr(m, 'OUTPUT_DIR', str(output))
    write_control(control / 'expected.json', {'version': 1, 'attempt': ATTEMPT, 'identity': database['identity']})
    return m, control, output


def request(control, index=1, phase='pre', **fields):
    value = {'attempt': ATTEMPT, 'id': index, 'phase': phase}; value.update(fields)
    write_control(control / f'request-{index:02d}.json', value)


def test_ipc_claim_once_immutable_expected_and_duplicate(ipc):
    m, control, output = ipc
    inbox = m.Inbox(ATTEMPT)
    try:
        assert inbox.next_request() is None
        request(control)
        assert inbox.next_request()['id'] == 1
        assert inbox.next_request() is None
        assert json.loads((output / 'claim-01.json').read_text())['id'] == 1
        request(control, phase='post')
        with pytest.raises(m.ObserverError, match='duplicate_changed'): inbox.next_request()
    finally: inbox.close()
    # No restart can replay a claimed query using the same output evidence directory.
    with pytest.raises(m.ObserverError): m.Inbox(ATTEMPT)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'mode', 'owner', 'oversize', 'duplicate_key', 'out_of_order', 'unknown_field', 'bool_id', 'expected_changed', 'unknown_name', 'removed_duplicate'])
def test_ipc_invalid_inputs_are_refused(ipc, kind, monkeypatch, tmp_path):
    m, control, output = ipc; inbox = m.Inbox(ATTEMPT)
    target = control / 'request-01.json'; request(control)
    try:
        if kind == 'symlink':
            target.unlink(); target.symlink_to(control / 'expected.json')
        elif kind == 'hardlink': os.link(target, tmp_path / 'alias')
        elif kind == 'fifo': target.unlink(); os.mkfifo(target, 0o640)
        elif kind == 'mode': target.chmod(0o644)
        elif kind == 'owner': monkeypatch.setattr(m, 'CONTROL_UID', os.getuid() + 1)
        elif kind == 'oversize': target.write_text(' ' * 1025)
        elif kind == 'duplicate_key': target.write_text('{"id":1,"id":1}')
        elif kind == 'out_of_order': target.rename(control / 'request-02.json')
        elif kind == 'unknown_field': request(control, sql='SELECT sensitive_content')
        elif kind == 'bool_id': request(control, id=True)
        elif kind == 'expected_changed':
            existing = (control / 'expected.json').read_text()
            (control / 'expected.json').write_text(existing)
        elif kind == 'unknown_name': (control / 'arbitrary.json').write_text('{}')
        elif kind == 'removed_duplicate': inbox.next_request(); target.unlink()
        with pytest.raises((m.ObserverError, OSError)): inbox.next_request()
        if kind != 'removed_duplicate': assert not (output / 'claim-01.json').exists()
    finally: inbox.close()


def test_publish_is_atomic_bounded_and_never_overwrites(ipc):
    m, _, output = ipc; fd = m.open_directory(str(output), os.getuid(), 0o700)
    try:
        m.publish(fd, 'one.json', {'status': 'first'})
        with pytest.raises(FileExistsError): m.publish(fd, 'one.json', {'status': 'second'})
        assert json.loads((output / 'one.json').read_text()) == {'status': 'first'}
        assert (output / 'one.json').stat().st_mode & 0o777 == 0o600
        with pytest.raises(m.ObserverError): m.publish(fd, 'huge.json', {'x': 'x' * 8192})
        assert set(p.name for p in output.iterdir()) == {'one.json'}
    finally: os.close(fd)


@pytest.mark.parametrize('error', [RuntimeError('secret postgres://credential'), ValueError({'password': 'secret'}), psycopg.OperationalError('secret DSN'), None])
def test_safe_errors_never_render_untrusted_exception_data(error):
    m = module()
    if error is None: error = m.ObserverError({'secret': 'password'})
    result = m.safe_error(error)
    rendered = json.dumps(result)
    assert 'secret' not in rendered and 'password' not in rendered and 'postgres' not in rendered
    assert result['status'] == 'FAILED' and result['query_ok'] is False
    assert 'counts' not in result


def test_pg17_lock_timeout_is_bounded_and_connection_closed(database):
    m = module()
    with psycopg.connect(database['admin']) as locker:
        locker.execute('LOCK TABLE daq_fae.daq_request_ledger IN ACCESS EXCLUSIVE MODE')
        before = time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable) as failure:
            m.observe(database['dsn'], database['identity'], ATTEMPT)
        assert .4 <= time.monotonic() - before < 2
        assert m.safe_error(failure.value)['sqlstate'] == '55P03'
    with psycopg.connect(database['admin']) as admin:
        assert admin.execute('select count(*) from pg_stat_activity where application_name=%s', ('daq-observer-' + ATTEMPT,)).fetchone() == (0,)


def child_observer(ipc, database, lifetime=10):
    _, control, output = ipc
    # Production has fixed mountpoints/UIDs; this local child substitutes only that OS boundary.
    wrapper = '''import importlib.util,os,sys
spec=importlib.util.spec_from_file_location('observer',sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.CONTROL_UID=os.getuid(); m.OBSERVER_UID=os.getuid(); m.OBSERVER_GID=os.getgid()
m.CONTROL_DIR=sys.argv[2]; m.OUTPUT_DIR=sys.argv[3]
sys.argv=['observer','--attempt',sys.argv[4],'--lifetime',sys.argv[5]]
raise SystemExit(m.main())
'''
    return subprocess.Popen([sys.executable, '-c', wrapper,
                             str(SCRIPT), str(control), str(output), ATTEMPT, str(lifetime)],
                            env={'PATH': '/usr/bin:/bin', 'DAQ_DATABASE_URL': database['dsn']},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def await_file(path, child):
    end = time.monotonic() + 5
    while time.monotonic() < end and not path.exists() and child.poll() is None: time.sleep(.02)
    assert path.exists(), 'owned observer exited or did not provide bounded receipt'
    return json.loads(path.read_text())


def test_real_process_ipc_two_phases_then_stop(database, ipc):
    _, control, output = ipc; child = child_observer(ipc, database)
    try:
        await_file(output / 'ready.json', child)
        request(control)
        first = await_file(output / 'result-01.json', child)
        assert first['counts']['running'] == 2
        time.sleep(.15)
        assert sorted(p.name for p in output.iterdir()) == ['claim-01.json', 'ready.json', 'result-01.json']
        request(control, 2, 'post')
        second = await_file(output / 'result-02.json', child)
        assert second['identity'] == first['identity'] and second['observer_backend_pid'] != first['observer_backend_pid']
        request(control, 3, 'stop')
        assert await_file(output / 'result-03.json', child)['status'] == 'STOPPED'
        stdout, stderr = child.communicate(timeout=3)
        assert child.returncode == 0 and stdout == stderr == ''
    finally:
        if child.poll() is None: child.kill(); child.communicate()


@pytest.mark.parametrize('finish', ['deadline', 'sigterm', 'sighup'])
def test_real_process_loss_is_failure_without_zero_counts(database, ipc, finish):
    import signal
    _, _, output = ipc; child = child_observer(ipc, database, 1 if finish == 'deadline' else 10)
    try:
        await_file(output / 'ready.json', child)
        if finish != 'deadline': child.send_signal(signal.SIGTERM if finish == 'sigterm' else signal.SIGHUP)
        stdout, stderr = child.communicate(timeout=4)
        assert child.returncode == 1 and stdout == ''
        result = json.loads(stderr)
        assert result['category'] == ('deadline_exceeded' if finish == 'deadline' else 'signal_interrupted')
        assert 'counts' not in result and database['dsn'] not in stderr
        assert json.loads((output / 'error.json').read_text()) == result
    finally:
        if child.poll() is None: child.kill(); child.communicate()


def test_pg17_source_identity_can_resolve_relations_after_current_schema(database):
    m = module(); dsn = make_conninfo(database['dsn'], options='-csearch_path=observer_empty,daq_fae')
    with psycopg.connect(database['admin'], autocommit=True) as admin:
        admin.execute('CREATE SCHEMA observer_empty')
        admin.execute(sql.SQL('GRANT USAGE ON SCHEMA observer_empty TO {}').format(sql.Identifier(database['role'])))
    with psycopg.connect(dsn) as source: expected = m.read_identity(source)
    assert expected['schema'] == 'observer_empty'
    assert expected['relations']['daq_request_ledger']['schema'] == 'daq_fae'
    assert m.observe(dsn, expected, ATTEMPT)['counts']['running'] == 2


def test_pg17_scram_rejects_wrong_password_and_statement_timeout_applies(database):
    m = module()
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(make_conninfo(database['dsn'], password='deliberately-wrong'), connect_timeout=2)
    with psycopg.connect(database['admin']) as admin:
        assert admin.execute('SELECT rolpassword LIKE %s FROM pg_authid WHERE rolname=%s', ('SCRAM-SHA-256$%', database['role'])).fetchone() == (True,)
    before = time.monotonic()
    with pytest.raises(psycopg.errors.QueryCanceled) as failure:
        with m.read_transaction(database['dsn'], database['identity'], ATTEMPT) as (connection, _):
            connection.execute('SELECT pg_catalog.pg_sleep(4)')
    assert 2.8 < time.monotonic() - before < 4
    assert m.safe_error(failure.value)['sqlstate'] == '57014'


@pytest.mark.parametrize('kind', ['control_symlink', 'output_symlink', 'control_mode', 'output_mode'])
def test_ipc_directory_boundary_refuses_alias_and_permissions(ipc, kind, monkeypatch, tmp_path):
    m, control, output = ipc
    if kind.endswith('symlink'):
        alias = tmp_path / 'alias'; alias.symlink_to(control if kind.startswith('control') else output)
        monkeypatch.setattr(m, 'CONTROL_DIR' if kind.startswith('control') else 'OUTPUT_DIR', str(alias))
    else: (control if kind.startswith('control') else output).chmod(0o777)
    with pytest.raises((m.ObserverError, OSError)): m.Inbox(ATTEMPT)


def test_cli_rejects_arbitrary_path_without_echoing_arguments():
    child = subprocess.run([sys.executable, str(SCRIPT), '--attempt', ATTEMPT,
                            '--control-dir', 'secret-password-location'], text=True, capture_output=True)
    assert child.returncode == 1 and child.stdout == ''
    assert json.loads(child.stderr)['category'] == 'arguments_invalid'
    assert 'secret-password' not in child.stderr
