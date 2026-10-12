"""The DAQ schema can share one PostgreSQL database without touching camera rows."""
import shutil
import subprocess
import tempfile
from pathlib import Path

import psycopg
import pytest


@pytest.fixture
def database():
    if not all(shutil.which(binary) for binary in ('initdb', 'pg_ctl')):
        pytest.skip('isolated PostgreSQL binaries unavailable')
    root = Path(tempfile.mkdtemp(prefix='daq-shared-pg-', dir='/tmp'))
    data, socket = root / 'data', root / 'socket'
    socket.mkdir()
    subprocess.run(['initdb', '-D', str(data), '-U', 'daq_contract_admin', '--auth-local=trust',
                    '--auth-host=reject', '--no-locale', '--encoding=UTF8'], check=True, capture_output=True)
    subprocess.run(['pg_ctl', '-D', str(data), '-l', str(root / 'postgres.log'), '-o',
                    f"-F -k {socket} -h ''", '-w', 'start'], check=True, capture_output=True)
    try:
        with psycopg.connect(host=str(socket), user='daq_contract_admin', dbname='postgres',
                             autocommit=True) as connection:
            connection.execute('create role fae_user login')
            connection.execute('create database fae_database owner fae_user')
        yield f'host={socket} dbname=fae_database user=fae_user'
    finally:
        subprocess.run(['pg_ctl', '-D', str(data), '-m', 'immediate', '-w', 'stop'],
                       check=True, capture_output=True)
        shutil.rmtree(root)


def test_daq_migrations_use_private_schema_in_camera_database(database):
    from scripts.migrate_daq_pg import migrate_connection

    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute('create table public.chat_sessions (external_session_id text primary key)')
        connection.execute("insert into public.chat_sessions values ('camera-session')")
        migrate_connection(connection, isolated_schema=True)
        assert connection.execute('select current_schema()').fetchone()[0] == 'daq_fae'
        assert connection.execute("select to_regclass('daq_fae.daq_installation_identity')").fetchone()[0]
        assert connection.execute("select to_regclass('daq_fae.fae_enterprise_sessions')").fetchone()[0]
        assert connection.execute("select count(*) from public.chat_sessions").fetchone()[0] == 1
        assert connection.execute("select count(*) from daq_fae.chat_sessions").fetchone()[0] == 0
        assert connection.execute("select count(*) from chat_sessions").fetchone()[0] == 0
        migrate_connection(connection, isolated_schema=True)
        assert connection.execute("select count(*) from public.chat_sessions").fetchone()[0] == 1
        constraint = connection.execute(
            "select pg_get_constraintdef(oid) from pg_constraint where conrelid = "
            "'daq_fae.fae_enterprise_sessions'::regclass and conname = "
            "'daq_enterprise_sessions_agent_id_check'"
        ).fetchone()[0]
        assert 'ai-daq-fae-agent' in constraint
    from daq_fae.platform_identity import _verify_identity_database
    from daq_fae.platform_tasks import _verify_task_database
    daq_connection = database + ' options=-csearch_path=daq_fae'
    _verify_identity_database(daq_connection)
    _verify_task_database(daq_connection)
    with psycopg.connect(database, autocommit=True) as camera:
        assert camera.execute('select current_schema()').fetchone()[0] == 'public'
        assert camera.execute("select to_regclass('daq_installation_identity')").fetchone()[0] is None
        assert camera.execute('select external_session_id from chat_sessions').fetchone()[0] == 'camera-session'
