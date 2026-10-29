#!/usr/bin/env python3
"""Controller metadata observer; OBSERVED is never whole-service quiescence."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import signal
import stat
import sys
import time

import psycopg

CONTROL_DIR = '/observer-control'
OUTPUT_DIR = '/observer-output'
CONTROL_UID = 0
OBSERVER_UID = 21081
OBSERVER_GID = 21081
AGENT = 'ai-daq-fae-agent'
RELATIONS = ('daq_request_ledger', 'daq_installation_identity')


class ObserverError(Exception):
    pass


def require(condition, category):
    if not condition:
        raise ObserverError(category)


def read_identity(connection):
    row = connection.execute('''SELECT pg_catalog.current_database(), current_user,
        pg_catalog.current_schema(), pg_catalog.current_setting('search_path')''').fetchone()
    identity = dict(zip(('database', 'user', 'schema', 'search_path'), row))
    relations = {}
    for name in RELATIONS:
        row = connection.execute('''SELECT n.nspname,c.oid,c.relkind
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE c.oid=pg_catalog.to_regclass(%s)''', (name,)).fetchone()
        require(row is not None and row[2] == 'r', 'relation_mismatch')
        relations[name] = dict(zip(('schema', 'oid', 'kind'), row))
    identity['relations'] = relations
    rows = connection.execute('SELECT agent_id,database_name,singleton FROM daq_installation_identity LIMIT 2').fetchall()
    require(len(rows) == 1 and rows[0] == (AGENT, identity['database'], True), 'marker_mismatch')
    identity['marker'] = {'agent_id': rows[0][0], 'database_name': rows[0][1]}
    return identity


def validate_identity(identity):
    require(type(identity) is dict and set(identity) == {'database', 'user', 'schema', 'search_path', 'relations', 'marker'}, 'identity_invalid')
    for name in ('database', 'user', 'schema', 'search_path'):
        require(type(identity[name]) is str and 0 < len(identity[name]) <= 1024 and '\0' not in identity[name], 'identity_invalid')
    require(type(identity['relations']) is dict and set(identity['relations']) == set(RELATIONS), 'identity_invalid')
    for value in identity['relations'].values():
        require(type(value) is dict and set(value) == {'schema', 'oid', 'kind'}, 'identity_invalid')
        require(type(value['schema']) is str and 0 < len(value['schema']) <= 1024 and '\0' not in value['schema'] and value['kind'] == 'r' and type(value['oid']) is int and 0 < value['oid'] < 2**32, 'identity_invalid')
    require(identity['marker'] == {'agent_id': AGENT, 'database_name': identity['database']}, 'identity_invalid')


def check_transaction(connection):
    row = connection.execute("SELECT pg_catalog.current_setting('transaction_read_only'),pg_catalog.current_setting('statement_timeout'),pg_catalog.current_setting('lock_timeout')").fetchone()
    require(row == ('on', '3s', '500ms'), 'transaction_mismatch')


@contextmanager
def read_transaction(dsn, expected, attempt):
    validate_identity(expected)
    require(re.fullmatch('[0-9a-f]{32}', attempt) is not None, 'attempt_invalid')
    # Preserve every original DSN option (including search_path); no options override.
    with psycopg.connect(dsn, connect_timeout=3, application_name='daq-observer-' + attempt, autocommit=True) as connection:
        connection.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        try:
            connection.execute("SET LOCAL statement_timeout='3s'")
            connection.execute("SET LOCAL lock_timeout='500ms'")
            connection.execute('LOCK TABLE daq_request_ledger,daq_installation_identity IN ACCESS SHARE MODE')
            identity = read_identity(connection)
            require(identity == expected, 'identity_mismatch')
            check_transaction(connection)
            yield connection, identity
            require(read_identity(connection) == expected, 'identity_mismatch')
            check_transaction(connection)
            connection.execute('COMMIT')
        except BaseException:
            # Closing always rolls back; avoid issuing more SQL on error or deadline.
            connection.close()
            raise


def observe(dsn, expected, attempt):
    with read_transaction(dsn, expected, attempt) as (connection, identity):
        row = connection.execute('''SELECT
            count(*) FILTER (WHERE state='running'),
            count(*) FILTER (WHERE state='running' AND lease_expires_at>CURRENT_TIMESTAMP),
            count(*) FILTER (WHERE state='running' AND lease_expires_at<=CURRENT_TIMESTAMP),
            count(*) FILTER (WHERE state='completed'), count(*) FILTER (WHERE state='interrupted'),
            max(updated_at), CURRENT_TIMESTAMP, pg_catalog.pg_backend_pid()
            FROM daq_request_ledger WHERE agent_id=%s''', (AGENT,)).fetchone()
        require(all(type(n) is int and n >= 0 for n in row[:5]) and row[0] == row[1] + row[2], 'aggregate_invalid')
        result = {'status': 'OBSERVED', 'query_ok': True, 'identity': identity,
                  'transaction_read_only': True, 'observer_backend_pid': row[7],
                  'observed_at': row[6].isoformat(), 'last_update': row[5].isoformat() if row[5] else None,
                  'counts': dict(zip(('running', 'live_lease', 'expired_running', 'completed', 'interrupted'), row[:5]))}
    return result


def safe_error(error):
    # Never render exception messages, dynamic class names, tracebacks or arguments.
    categories = {'identity_invalid', 'attempt_invalid', 'identity_mismatch', 'marker_mismatch',
                  'relation_mismatch', 'transaction_mismatch', 'aggregate_invalid', 'ipc_invalid',
                  'duplicate_changed', 'request_out_of_order', 'deadline_exceeded', 'signal_interrupted',
                  'configuration_invalid', 'arguments_invalid'}
    category = error.args[0] if type(error) is ObserverError and len(error.args) == 1 and type(error.args[0]) is str and error.args[0] in categories else 'internal_error'
    result = {'status': 'FAILED', 'query_ok': False, 'category': category}
    if isinstance(error, psycopg.Error):
        result['category'] = 'database_error'
        code = error.sqlstate
        if type(code) is str and re.fullmatch('[0-9A-Z]{5}', code):
            result['sqlstate'] = code
    return result


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def open_directory(path, uid, mode):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if not (info.st_uid == uid and info.st_gid == OBSERVER_GID and stat.S_IMODE(info.st_mode) == mode):
        os.close(fd)
        raise ObserverError('ipc_invalid')
    return fd


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'ipc_invalid')
        result[key] = value
    return result


def read_control(fd, name, limit):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(handle)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == CONTROL_UID
                and before.st_gid == OBSERVER_GID and stat.S_IMODE(before.st_mode) == 0o640
                and 0 < before.st_size <= limit, 'ipc_invalid')
        data = b''
        while len(data) <= limit:
            block = os.read(handle, limit + 1 - len(data))
            if not block:
                break
            data += block
        require(len(data) == before.st_size and fingerprint(before) == fingerprint(os.fstat(handle)), 'ipc_invalid')
        require(fingerprint(before) == fingerprint(os.stat(name, dir_fd=fd, follow_symlinks=False)), 'ipc_invalid')
        value = json.loads(data, object_pairs_hook=unique_object)
        return value, (fingerprint(before), hashlib.sha256(data).hexdigest())
    finally:
        os.close(handle)


def publish(fd, name, value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode() + b'\n'
    require(len(data) <= 8192, 'ipc_invalid')
    temporary = '.' + name
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
    finally:
        os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)


class Inbox:
    def __init__(self, attempt):
        self.attempt = attempt
        self.control = open_directory(CONTROL_DIR, CONTROL_UID, 0o750)
        self.output = None
        try:
            self.output = open_directory(OUTPUT_DIR, OBSERVER_UID, 0o700)
            with os.scandir(self.output) as entries:
                require(next(entries, None) is None, 'ipc_invalid')
            expected, self.expected_fingerprint = read_control(self.control, 'expected.json', 8192)
            require(type(expected) is dict and set(expected) == {'version', 'attempt', 'identity'}
                    and type(expected['version']) is int and expected['version'] == 1 and expected['attempt'] == attempt, 'ipc_invalid')
            validate_identity(expected['identity'])
            self.identity = expected['identity']
            self.seen = {}
            publish(self.output, 'ready.json', {'status': 'READY', 'attempt': attempt, 'pid': os.getpid()})
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.output is not None:
            os.close(self.output); self.output = None
        if self.control is not None:
            os.close(self.control); self.control = None

    def next_request(self):
        _, current = read_control(self.control, 'expected.json', 8192)
        require(current == self.expected_fingerprint, 'duplicate_changed')
        names = set()
        with os.scandir(self.control) as entries:
            for entry in entries:
                require(len(names) < 17, 'ipc_invalid')
                names.add(entry.name)
        allowed = {'expected.json'} | {f'request-{i:02d}.json' for i in range(1, 17)}
        require(names <= allowed, 'ipc_invalid')
        for name, previous in self.seen.items():
            require(name in names, 'duplicate_changed')
            _, current = read_control(self.control, name, 1024)
            require(current == previous, 'duplicate_changed')
        new = names - {'expected.json'} - set(self.seen)
        if not new:
            return None
        index = len(self.seen) + 1
        name = f'request-{index:02d}.json'
        require(new == {name}, 'request_out_of_order')
        request, signature = read_control(self.control, name, 1024)
        require(type(request) is dict and set(request) == {'attempt', 'id', 'phase'}
                and request['attempt'] == self.attempt and type(request['id']) is int and request['id'] == index
                and type(request['phase']) is str and request['phase'] in {'pre', 'post', 'final', 'stop'}, 'ipc_invalid')
        publish(self.output, f'claim-{index:02d}.json', request)
        self.seen[name] = signature
        return request


@contextmanager
def deadline(seconds):
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    def expired(signum, frame):
        raise ObserverError('deadline_exceeded')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, min(seconds, previous_timer[0]) if previous_timer[0] else seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(.000001, previous_timer[0] - (time.monotonic() - started)), previous_timer[1])


def serve(attempt, lifetime):
    inbox = None
    request = None
    try:
        require(type(lifetime) is int and 1 <= lifetime <= 300 and re.fullmatch('[0-9a-f]{32}', attempt) is not None, 'arguments_invalid')
        require(os.getuid() == OBSERVER_UID and os.getgid() == OBSERVER_GID, 'configuration_invalid')
        dsn = os.environ.get('DAQ_DATABASE_URL')
        require(type(dsn) is str and 0 < len(dsn) <= 16384, 'configuration_invalid')
        with deadline(lifetime):
            inbox = Inbox(attempt)
            while True:
                request = inbox.next_request()
                if request is None:
                    time.sleep(.05)
                    continue
                if request['phase'] == 'stop':
                    result = {'status': 'STOPPED', 'query_ok': False}
                else:
                    with deadline(15):
                        result = observe(dsn, inbox.identity, attempt)
                result.update(request)
                publish(inbox.output, f"result-{request['id']:02d}.json", result)
                if request['phase'] == 'stop':
                    return 0
                request = None
    except BaseException as error:
        result = safe_error(error)
        result['attempt'] = attempt if type(attempt) is str and re.fullmatch('[0-9a-f]{32}', attempt) else None
        if request is not None:
            result.update(request)
        if inbox is not None:
            try:
                publish(inbox.output, 'error.json', result)
            except BaseException:
                pass  # Parent treats missing receipt/process loss as missing proof.
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        if inbox is not None:
            inbox.close()


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ObserverError('arguments_invalid')


def main():
    def interrupted(signum, frame):
        raise ObserverError('signal_interrupted')
    for kind in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(kind, interrupted)
    try:
        parser = SafeParser(description=__doc__, allow_abbrev=False)
        parser.add_argument('--attempt', required=True)
        parser.add_argument('--lifetime', type=int, default=300)
        args = parser.parse_args()
    except ObserverError as error:
        print(json.dumps(safe_error(error)), file=sys.stderr)
        return 1
    return serve(args.attempt, args.lifetime)


if __name__ == '__main__':
    sys.exit(main())
