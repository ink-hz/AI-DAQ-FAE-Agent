#!/usr/bin/env python3
"""Root-reviewed literal, single-shot argv runner; no cutover policy or recovery.

ACK means only the CLI terminated. Parent owns exclusivity, authorization,
quiescence, material/PG/FD/writer/start-policy proof and physical daemon checks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid

ROOT_UID = 0
MAX_JSON = 65536
NAME = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')
HEX32 = re.compile(r'[0-9a-f]{32}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
KINDS = {'stop', 'rm', 'create', 'start', 'listener_fence', 'listener_rearm'}
ROLES = {'source', 'candidate', 'rollback', 'observer', 'listener'}


class Rejected(ValueError):
    """Fixed safe failure, never raw child output or input contents."""


def require(condition):
    if not condition:
        raise Rejected('invalid_or_unsafe_record')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def checked_path(value, directory=False, private=False):
    require(type(value) is str and value.startswith('/') and '\x00' not in value)
    path = Path(value)
    require(str(path) == value and not any(p in {'.', '..'} for p in value.split('/')))
    for part in [*reversed(path.parents), path]:
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode))
        require(info.st_uid in {0, ROOT_UID} and not info.st_mode & 0o022)
        if part != path:
            require(stat.S_ISDIR(info.st_mode))
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    if private:
        require(info.st_uid == ROOT_UID and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600))
    if not directory:
        require(info.st_nlink == 1)
    return path


def read_json(path):
    checked_path(str(path), private=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == ROOT_UID
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= MAX_JSON)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(MAX_JSON + 1)
        require(len(data) <= MAX_JSON)
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=lambda _: require(False))
        require(type(value) is dict)
        return value
    finally:
        os.close(fd)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(directory, name, value):
    """Exclusive link publish: no destination overwrite, even under contention.

    Partial unique temps remain evidence; no cleanup or reuse on failure.
    """
    data = canonical(value)
    require(len(data) <= MAX_JSON)
    temp = directory / ('.partial-' + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    os.link(temp, directory / name, follow_symlinks=False)
    os.unlink(temp)
    sync_dir(directory)


def validate_intent(value, attempt, operation):
    require(set(value) == {'version', 'run_attempt', 'operation', 'role', 'kind', 'argv', 'expected'})
    require(type(value['version']) is int and value['version'] == 1)
    require(value['run_attempt'] == attempt and value['operation'] == operation)
    require(type(value['role']) is str and value['role'] in ROLES)
    require(type(value['kind']) is str and value['kind'] in KINDS)
    argv = value['argv']
    require(type(argv) is list and 1 <= len(argv) <= 256)
    require(all(type(a) is str and 0 < len(a) <= 8192 and '\x00' not in a for a in argv))
    require(sum(len(a) for a in argv) <= 32768)
    checked_path(argv[0])
    expected = value['expected']
    require(type(expected) is dict)
    kind = value['kind']
    if kind in {'stop', 'rm', 'start'}:
        require(set(expected) == {'target_cid'})
        require(type(expected['target_cid']) is str and HEX64.fullmatch(expected['target_cid']))
    elif kind == 'create':
        require(set(expected) == {'pending_name', 'image_id', 'config_identity'})
        require(type(expected['pending_name']) is str and NAME.fullmatch(expected['pending_name']))
        require(type(expected['image_id']) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', expected['image_id']))
    else:
        require(set(expected) == {'config_identity'})
    if 'config_identity' in expected:
        require(type(expected['config_identity']) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', expected['config_identity']))
    return value


def binding(intent):
    return {key: intent[key] for key in ('run_attempt', 'operation', 'role', 'kind', 'expected')} | {
        'intent_sha256': digest(intent), 'argv_sha256': digest(intent['argv'])}


def run_directory(root, attempt, operation):
    require(os.geteuid() == ROOT_UID)
    require(type(attempt) is str and HEX32.fullmatch(attempt))
    require(type(operation) is str and NAME.fullmatch(operation))
    require(operation not in {'status', 'submit', 'intent', 'claim', 'ack', 'may-have-occurred'})
    return checked_path(root, directory=True, private=True)


def status(root, attempt, operation):
    """Read-only. No state inference from PID, no ACK creation or repair."""
    root = run_directory(root, attempt, operation)
    op = root / operation
    try:
        op.lstat()
    except FileNotFoundError:
        return {'state': 'ABSENT'}
    try:
        checked_path(str(op), directory=True, private=True)
        require(set(p.name for p in op.iterdir()) == {'intent.json', 'may_have_occurred.json', 'claim.json', 'ack.json'})
        record = read_json(op / 'intent.json')
        require(set(record) == {'intent', 'sha256'})
        intent = validate_intent(record['intent'], attempt, operation)
        require(record['sha256'] == digest(intent))
        bound = binding(intent)
        marker = read_json(op / 'may_have_occurred.json')
        require(marker == bound)
        claim = read_json(op / 'claim.json')
        require(set(claim) == set(bound) | {'supervisor'})
        require(all(claim[k] == v for k, v in bound.items()))
        identity = claim['supervisor']
        require(type(identity) is dict and set(identity) == {'pid', 'nonce', 'boot_id', 'proc_start'})
        require(type(identity['pid']) is int and identity['pid'] > 0)
        require(type(identity['nonce']) is str and HEX32.fullmatch(identity['nonce']))
        for key in ('boot_id', 'proc_start'):
            require(identity[key] is None or (type(identity[key]) is str and 0 < len(identity[key]) < 128))
        ack = read_json(op / 'ack.json')
        require(set(ack) == set(claim) | {'returncode', 'stdout', 'stderr'})
        require(all(ack[k] == v for k, v in claim.items()))
        require(type(ack['returncode']) is int and -255 <= ack['returncode'] <= 255)
        for stream in ('stdout', 'stderr'):
            metric = ack[stream]
            require(type(metric) is dict and set(metric) == {'bytes', 'sha256'})
            require(type(metric['bytes']) is int and metric['bytes'] >= 0)
            require(type(metric['sha256']) is str and HEX64.fullmatch(metric['sha256']))
        return {'state': 'ACK', 'ack': ack}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return {'state': 'UNKNOWN'}


def supervisor_identity():
    identity = {'pid': os.getpid(), 'nonce': uuid.uuid4().hex, 'boot_id': None, 'proc_start': None}
    if sys.platform.startswith('linux'):
        identity['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        identity['proc_start'] = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
    return identity


def supervise(root, op, intent, previous_mask=None):
    interrupted = [False]
    def interrupted_signal(_number, _frame):
        interrupted[0] = True
    for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(number, interrupted_signal)
    if previous_mask is not None:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    child = None
    try:
        os.setsid()
        os.chdir(root)
        with open(os.devnull, 'r+b', buffering=0) as null:
            for fd in (0, 1, 2):
                os.dup2(null.fileno(), fd)
        claim = binding(intent) | {'supervisor': supervisor_identity()}
        # Only an exclusive durable claim permits child launch. Save failure stops here.
        publish(op, 'claim.json', claim)
        sync_dir(root)
        if interrupted[0]:
            return
        child = subprocess.Popen(intent['argv'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, shell=False, env={}, cwd=root, close_fds=True)
        totals = {name: {'bytes': 0, 'hash': hashlib.sha256()} for name in ('stdout', 'stderr')}
        with selectors.DefaultSelector() as selector:
            for name in totals:
                selector.register(getattr(child, name), selectors.EVENT_READ, name)
            while selector.get_map():
                for key, _events in selector.select():
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if chunk:
                        totals[key.data]['bytes'] += len(chunk)
                        totals[key.data]['hash'].update(chunk)
                    else:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
        code = child.wait()
        # A signal is uncertainty, never a new writer or a synthetic cancellation.
        if interrupted[0]:
            return
        ack = claim | {'returncode': code}
        ack.update({name: {'bytes': metric['bytes'], 'sha256': metric['hash'].hexdigest()}
                    for name, metric in totals.items()})
        publish(op, 'ack.json', ack)
        sync_dir(root)
        if not interrupted[0]:
            # ACK is already durable. An unpersisted unlink can only regress
            # ACK to UNKNOWN after crash, never manufacture a durable ACK.
            os.unlink(op / 'ack_pending.json')
    except BaseException:
        # Persistence/reporting failure never kills a known source child.
        # Pipes must still be drained if an unrelated exception interrupted capture.
        if child is not None:
            try:
                with selectors.DefaultSelector() as selector:
                    for pipe in (child.stdout, child.stderr):
                        if not pipe.closed:
                            selector.register(pipe, selectors.EVENT_READ)
                    while selector.get_map():
                        for key, _events in selector.select():
                            if not os.read(key.fileobj.fileno(), 65536):
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
            finally:
                child.wait()


def submit(root, intent_path, attempt, operation):
    root = run_directory(root, attempt, operation)
    intent = validate_intent(read_json(Path(intent_path)), attempt, operation)
    op = root / operation
    try:
        op.mkdir(mode=0o700)
    except FileExistsError:
        raise Rejected('operation_already_allocated') from None
    # Every exception after mkdir leaves an irreversible UNKNOWN allocation.
    sync_dir(root)
    publish(op, 'intent.json', {'intent': intent, 'sha256': digest(intent)})
    sync_dir(root)
    publish(op, 'ack_pending.json', binding(intent))
    publish(op, 'may_have_occurred.json', binding(intent))
    sync_dir(op)
    sync_dir(root)
    # Block signals across fork until the child installs its own handlers.
    numbers = {signal.SIGHUP, signal.SIGINT, signal.SIGTERM}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, numbers)
    try:
        pid = os.fork()
        if pid == 0:
            try:
                supervise(root, op, intent, previous)
            finally:
                os._exit(0)
        return pid
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('submit', 'status'))
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--intent')
    parser.add_argument('--wait', type=float, default=0)
    args = parser.parse_args()
    try:
        require(0 <= args.wait <= 60)
        if args.command == 'submit':
            require(args.intent is not None)
            submit(args.run_dir, args.intent, args.attempt, args.operation)
            until = time.monotonic() + args.wait
            while time.monotonic() < until:
                result = status(args.run_dir, args.attempt, args.operation)
                if result['state'] == 'ACK':
                    break
                time.sleep(min(.05, max(0, until - time.monotonic())))
        else:
            require(args.intent is None and args.wait == 0)
        result = status(args.run_dir, args.attempt, args.operation)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['state'] == 'ACK' and result['ack']['returncode'] == 0 else 2
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('{"state":"UNKNOWN","error":"rejected_or_persistence_failure"}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
