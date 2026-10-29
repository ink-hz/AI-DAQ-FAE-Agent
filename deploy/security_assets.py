#!/usr/bin/env python3
"""Prepare immutable DAQ assets and separately capture quiesced runtime data.

All commands require root. Inputs and output manifests are private metadata; no
key material is accepted in arguments or printed. See SECURITY_NONROOT.md.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import PurePosixPath

IMAGE = 'sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7'
SECRETS = ('session-keyring.json', 'content-keyring.json', 'entitlements.json', 'feedback-approval-keys-20261023.json')
MAX_FILE = 10 * 1024**3
MAX_TOTAL = 1024**4
MAX_FILES = 1000000
MAX_DEPTH = 128
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class AssetError(Exception):
    pass


def _parts(path):
    if not isinstance(path, str) or not path.startswith('/') or '$' in path or any(ord(char) < 32 for char in path):
        raise AssetError('absolute path required')
    parts = path.split('/')[1:]
    if not parts or any(p in ('', '.', '..') for p in parts):
        raise AssetError('invalid path')
    return parts


def _open_dir(path, trusted=False):
    fd = os.open('/', DIR_FLAGS)
    try:
        if trusted and not _trusted_parent(os.fstat(fd)):
            raise AssetError('untrusted ancestor')
        for part in _parts(path):
            new = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = new
            if trusted and not _trusted_parent(os.fstat(fd)):
                raise AssetError('untrusted ancestor')
        return fd
    except Exception:
        os.close(fd)
        raise


def _parent(path, trusted=False):
    parts = _parts(str(path))
    parent = '/' + '/'.join(parts[:-1])
    return (_open_dir(parent, trusted=trusted) if parts[:-1] else os.open('/', DIR_FLAGS), parts[-1])


def _snapshot(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid, st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _metadata(st):
    return {'dev': st.st_dev, 'inode': st.st_ino, 'mode': st.st_mode, 'uid': st.st_uid, 'gid': st.st_gid, 'nlink': st.st_nlink, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns}


def _mkdir_at(parent, name, uid, gid, mode):
    os.mkdir(name, 0o700, dir_fd=parent)
    fd = os.open(name, DIR_FLAGS, dir_fd=parent)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        os.fsync(fd)
        os.fsync(parent)
        return fd
    except Exception:
        os.close(fd)
        raise


def _write_json(parent, name, obj):
    data = (json.dumps(obj, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(parent)


def _write_all(fd, data):
    while data:
        written = os.write(fd, data)
        if written <= 0:
            raise AssetError('short write')
        data = data[written:]


def _copy_file(src_parent, name, dst_parent, dst_name, expected, owner, gid, mode, budget):
    fd = os.open(name, FILE_FLAGS, dir_fd=src_parent)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE:
            raise AssetError('unsafe source file')
        budget[0] += 1
        budget[1] += before.st_size
        if budget[0] > MAX_FILES or budget[1] > MAX_TOTAL:
            raise AssetError('copy limit exceeded')
        out = os.open(dst_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dst_parent)
        try:
            digest = hashlib.sha256()
            count = 0
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > before.st_size or count > MAX_FILE:
                    raise AssetError('source size changed')
                digest.update(chunk)
                _write_all(out, chunk)
            if _snapshot(before) != _snapshot(os.fstat(fd)):
                raise AssetError('source changed')
            again = os.stat(name, dir_fd=src_parent, follow_symlinks=False)
            if _snapshot(before) != _snapshot(again):
                raise AssetError('source replaced')
            actual = {'size': count, 'sha256': digest.hexdigest()}
            if actual != expected:
                raise AssetError('source identity mismatch')
            os.fchown(out, owner, gid)
            os.fchmod(out, mode)
            os.fsync(out)
        finally:
            os.close(out)
        os.fsync(dst_parent)
        return dict(actual, source=_metadata(before), target_uid=owner, target_gid=gid, target_mode=oct(mode))
    finally:
        os.close(fd)



def _list_names(fd):
    names = []
    with os.scandir(fd) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > MAX_FILES:
                raise AssetError('directory entry limit exceeded')
    return sorted(names)


def _tree(src, dst, expected, owner, gid, file_mode, budget, selected=None, directories=None):
    found = {}
    def walk(source, target, prefix):
        if prefix.count('/') > MAX_DEPTH:
            raise AssetError('directory depth limit exceeded')
        budget[0] += 1
        if budget[0] > MAX_FILES:
            raise AssetError('entry limit exceeded')
        before = os.fstat(source)
        if directories is not None:
            directories[prefix] = _metadata(before)
        names = _list_names(source)
        if selected is not None and not prefix:
            names = sorted(selected)
        for name in names:
            if name in ('.', '..') or '/' in name:
                raise AssetError('invalid name')
            rel = prefix + name
            st = os.stat(name, dir_fd=source, follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                child = os.open(name, DIR_FLAGS, dir_fd=source)
                try:
                    if _snapshot(st) != _snapshot(os.fstat(child)):
                        raise AssetError('source directory replaced')
                    target_child = _mkdir_at(target, name, owner, gid, 0o700 if owner else 0o750)
                    try:
                        walk(child, target_child, rel + '/')
                    finally:
                        os.close(target_child)
                    if _snapshot(os.stat(name, dir_fd=source, follow_symlinks=False)) != _snapshot(st):
                        raise AssetError('source directory changed')
                finally:
                    os.close(child)
            elif stat.S_ISREG(st.st_mode):
                if rel not in expected:
                    raise AssetError('unexpected source file')
                mode = 0o600 if selected and name in SECRETS[:2] else file_mode
                file_owner = 21081 if selected and name in SECRETS[:2] else owner
                found[rel] = _copy_file(source, name, target, name, expected[rel], file_owner, gid, mode, budget)
            else:
                raise AssetError('unsafe source entry')
        if _snapshot(before) != _snapshot(os.fstat(source)):
            raise AssetError('source directory changed')
    walk(src, dst, '')
    if set(found) != set(expected):
        raise AssetError('source inventory mismatch')
    return found



def _verify_tree(source, records, selected=None, directories=None):
    found = set()
    def walk(fd, prefix):
        if prefix.count('/') > MAX_DEPTH:
            raise AssetError('directory depth limit exceeded')
        before = os.fstat(fd)
        if directories is not None and directories.get(prefix) != _metadata(before):
            raise AssetError('source directory metadata changed')
        names = _list_names(fd) if prefix or selected is None else sorted(selected)
        for name in names:
            rel = prefix + name
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                child = os.open(name, DIR_FLAGS, dir_fd=fd)
                try:
                    if _snapshot(st) != _snapshot(os.fstat(child)):
                        raise AssetError('directory replaced')
                    walk(child, rel + '/')
                    if _snapshot(st) != _snapshot(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                        raise AssetError('directory changed')
                finally:
                    os.close(child)
            elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and rel in records:
                child = os.open(name, FILE_FLAGS, dir_fd=fd)
                try:
                    before_file = os.fstat(child)
                    digest = hashlib.sha256()
                    count = 0
                    while True:
                        chunk = os.read(child, 1024 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > MAX_FILE:
                            raise AssetError('file limit exceeded')
                        digest.update(chunk)
                    rec = records[rel]
                    if (_snapshot(st) != _snapshot(before_file) or _snapshot(st) != _snapshot(os.fstat(child))
                        or _snapshot(st) != _snapshot(os.stat(name, dir_fd=fd, follow_symlinks=False))
                        or _metadata(st) != rec['source'] or count != rec['size'] or digest.hexdigest() != rec['sha256']):
                        raise AssetError('source changed after copy')
                    found.add(rel)
                finally:
                    os.close(child)
            else:
                raise AssetError('source inventory changed')
        if _snapshot(before) != _snapshot(os.fstat(fd)):
            raise AssetError('source directory changed')
    walk(source, '')
    if found != set(records):
        raise AssetError('source inventory changed')


def _is_root():
    return os.geteuid() == 0


def _trusted_parent(st):
    return st.st_uid == 0 and not st.st_mode & 0o022


def _check_spec(spec):
    if not _is_root():
        raise AssetError('root required')
    if spec.get('uid') != 21081 or spec.get('gid') != 21081:
        raise AssetError('unexpected uid or gid')
    if not isinstance(spec.get('paths'), dict) or not isinstance(spec.get('expected'), dict):
        raise AssetError('invalid spec')


def _start(target):
    parent, name = _parent(str(target), trusted=True)
    try:
        # A writable/unowned parent makes exclusive publication vulnerable to substitution.
        st = os.fstat(parent)
        if not _trusted_parent(st):
            raise AssetError('target parent must be private and root-owned')
        return _mkdir_at(parent, name, 0, 0, 0o700)
    finally:
        os.close(parent)



def _ensure_target_outside_sources(spec, target, include_prepared=False):
    # Compare directory identities, not merely strings: bind aliases can name
    # the same tree. Open all components without following symbolic links.
    roots = []
    try:
        for key in ('main', 'sdk', 'secrets', 'attachments'):
            source_path = PurePosixPath(spec['paths'][key])
            target_path = PurePosixPath(str(target))
            if target_path == source_path or source_path in target_path.parents:
                raise AssetError('target within source tree')
            try:
                roots.append(_open_dir(spec['paths'][key]))
            except FileNotFoundError:
                # A different stage may not yet have its inputs, but lexical
                # containment is still forbidden. Actual stage inputs are
                # opened and required by the copy operation itself.
                continue
        for key in ('ro_dir', 'rw_dir'):
            if include_prepared and spec.get('runtime', {}).get(key):
                roots.append(_open_dir(spec['runtime'][key]))
        identities = {(os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in roots}
        parent, _ = _parent(str(target), trusted=True)
        try:
            while True:
                st = os.fstat(parent)
                if (st.st_dev, st.st_ino) in identities:
                    raise AssetError('target within source tree')
                up = os.open('..', DIR_FLAGS, dir_fd=parent)
                up_stat = os.fstat(up)
                if (st.st_dev, st.st_ino) == (up_stat.st_dev, up_stat.st_ino):
                    os.close(up)
                    break
                os.close(parent)
                parent = up
        finally:
            os.close(parent)
    except OSError as exc:
        raise AssetError('unsafe source or target path') from exc
    finally:
        for fd in roots:
            os.close(fd)


def _run(spec, target, stage, evidence=None):
    _check_spec(spec)
    _ensure_target_outside_sources(spec, target)
    target_fd = None
    try:
        target_fd = _start(target)
        if stage == 'rw':
            required = ('container_id', 'image_id', 'stopped_at', 'quiescence_evidence')
            if (not isinstance(evidence, dict) or set(evidence) != set(required)
                or any(not isinstance(evidence.get(k), str) for k in required)
                or not re.fullmatch(r'[0-9a-f]{64}', evidence['container_id'])
                or evidence['image_id'] != IMAGE
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', evidence['quiescence_evidence'])):
                raise AssetError('controller stopped evidence required')
            try:
                stopped = datetime.fromisoformat(evidence['stopped_at'].replace('Z', '+00:00'))
                if stopped.utcoffset() is None:
                    raise ValueError('timezone required')
            except ValueError as exc:
                raise AssetError('invalid stopped time') from exc
        output = {'version': 1, 'stage': stage, 'matched': True, 'created_at': datetime.now(timezone.utc).isoformat(), 'files': {}}
        budget = [0, 0]
        roots = {}
        source_dirs = {}
        names = ('main', 'sdk', 'secrets') if stage == 'ro' else ('attachments',)
        for name in names:
            expected = spec['expected'][name]
            if name == 'secrets' and set(expected) != set(SECRETS):
                raise AssetError('secret inventory mismatch')
            source = _open_dir(spec['paths'][name])
            try:
                roots[name] = _metadata(os.fstat(source))
                owner = 21081 if name == 'attachments' else 0
                dst = _mkdir_at(target_fd, name, owner, 21081, 0o700 if owner else 0o750)
                try:
                    source_dirs[name] = {}
                    output['files'][name] = _tree(source, dst, expected, owner, 21081, 0o600 if owner else 0o640, budget, SECRETS if name == 'secrets' else None, source_dirs[name])
                finally:
                    os.close(dst)
            finally:
                os.close(source)
        if stage == 'rw':
            source, name = _parent(spec['paths']['trace'])
            dst = _mkdir_at(target_fd, 'trace', 21081, 21081, 0o700)
            try:
                expected = spec['expected']['trace']
                if set(expected) != {'daq_traces.jsonl'}:
                    raise AssetError('trace inventory mismatch')
                output['files']['trace'] = {'daq_traces.jsonl': _copy_file(source, name, dst, 'daq_traces.jsonl', expected['daq_traces.jsonl'], 21081, 21081, 0o600, budget)}
            finally:
                os.close(source)
                os.close(dst)
            output['stopped_evidence'] = {k: evidence[k] for k in required}
            output['stopped_evidence']['verification'] = 'controller_asserted_not_verified'
        # Reopen every root and rescan all selected files after copying. A
        # stable descriptor alone would not detect replacing the path itself.
        for name in names:
            source = _open_dir(spec['paths'][name])
            try:
                if _metadata(os.fstat(source)) != roots[name]:
                    raise AssetError('source root changed')
                _verify_tree(source, output['files'][name], SECRETS if name == 'secrets' else None, source_dirs[name])
            finally:
                os.close(source)
        if stage == 'rw':
            trace_parent, trace_name = _parent(spec['paths']['trace'])
            try:
                _verify_tree(trace_parent, {trace_name: output['files']['trace']['daq_traces.jsonl']}, (trace_name,))
            finally:
                os.close(trace_parent)
        for name in names:
            owner = 21081 if name == 'attachments' else 0
            _validate_tree(str(target) + '/' + name, output['files'][name], owner, 21081, 0o600 if owner else 0o640, name == 'secrets')
        if stage == 'rw':
            _validate_tree(str(target) + '/trace', output['files']['trace'], 21081, 21081, 0o600)
        output['source_roots'] = roots
        output['source_directories'] = source_dirs
        _write_json(target_fd, 'manifest.json', output)
        os.fchown(target_fd, 0, 21081)
        os.fchmod(target_fd, 0o750)
        os.fsync(target_fd)
        return output
    except Exception as exc:
        if target_fd is not None:
            try:
                _write_json(target_fd, 'failure.json', {'version': 1, 'stage': stage, 'status': 'failed', 'category': type(exc).__name__, 'reason': str(exc) if isinstance(exc, AssetError) else type(exc).__name__})
            except Exception:
                pass
        raise AssetError('preparation failed; inspect private receipt') from exc
    finally:
        if target_fd is not None:
            os.close(target_fd)


def stage_ro(spec, target):
    return _run(spec, target, 'ro')


def capture_rw(spec, target, evidence):
    return _run(spec, target, 'rw', evidence)

REQUIRED_ENV = {
    'DAQ_ENVIRONMENT': 'production',
    'DAQ_PLATFORM_IDENTITY_ENABLED': 'true',
    'DAQ_PLATFORM_SESSION_KEYRING_FILE': '/run/daq-secrets/session-keyring.json',
    'DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE': '/run/daq-secrets/content-keyring.json',
    'DAQ_KNOWLEDGE_ENTITLEMENTS_FILE': '/run/daq-secrets/entitlements.json',
    'DAQ_KNOWLEDGE_APPROVAL_KEYS_FILE': '/run/daq-secrets/feedback-approval-keys-20261023.json',
    'DAQ_KNOWLEDGE_RELEASE_ROOT': '/app/data/knowledge/reviewed-thinking-prefix-20261028',
    'DAQ_SDK_RELEASE_ROOT': '/app/data/knowledge/sdk-thinking-prefix-20261028',
    'DAQ_ATTACHMENT_STORAGE_DIR': '/app/data/attachments',
    'DAQ_TRACE_LOG_PATH': '/app/data/daq_traces.jsonl',
}


def _owner_mode(st, uid, gid, mode):
    return st.st_uid == uid and st.st_gid == gid and stat.S_IMODE(st.st_mode) == mode


def _read_regular(path, limit=16 * 1024 * 1024):
    parent, name = _parent(path)
    try:
        fd = os.open(name, FILE_FLAGS, dir_fd=parent)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
                raise AssetError('invalid metadata file')
            data = bytearray()
            while True:
                part = os.read(fd, min(1024 * 1024, limit + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
                if len(data) > limit:
                    raise AssetError('metadata file too large')
            if _snapshot(st) != _snapshot(os.fstat(fd)) or _snapshot(st) != _snapshot(os.stat(name, dir_fd=parent, follow_symlinks=False)):
                raise AssetError('metadata changed')
            return bytes(data), st
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _validate_env(path, expected_hash, approval_hash):
    data, _ = _read_regular(path)
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise AssetError('environment identity mismatch')
    values = {}
    for line in data.decode('utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise AssetError('invalid environment file')
        key, value = line.split('=', 1)
        key = key.strip()
        if key in REQUIRED_ENV or key == 'DAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256':
            if key in values:
                raise AssetError('duplicate environment field')
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            values[key] = value
    if values != dict(REQUIRED_ENV, DAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=approval_hash):
        raise AssetError('environment contract mismatch')


def _validate_tree(root, records, owner, group, mode, secret=False):
    fd = _open_dir(root)
    try:
        def walk(parent, prefix):
            if prefix.count('/') > MAX_DEPTH:
                raise AssetError('directory depth limit exceeded')
            before = os.fstat(parent)
            for name in _list_names(parent):
                rel = prefix + name
                st = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    if not _owner_mode(st, owner, group, 0o700 if owner else 0o750):
                        raise AssetError('prepared directory permissions mismatch')
                    child = os.open(name, DIR_FLAGS, dir_fd=parent)
                    try:
                        if _snapshot(st) != _snapshot(os.fstat(child)):
                            raise AssetError('prepared directory replaced')
                        walk(child, rel + '/')
                    finally:
                        os.close(child)
                elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and rel in records:
                    rec = records[rel]
                    wanted_owner = 21081 if secret and name in SECRETS[:2] else owner
                    wanted_mode = 0o600 if secret and name in SECRETS[:2] else mode
                    if not _owner_mode(st, wanted_owner, group, wanted_mode):
                        raise AssetError('prepared file permissions mismatch')
                    child = os.open(name, FILE_FLAGS, dir_fd=parent)
                    try:
                        digest = hashlib.sha256()
                        count = 0
                        while True:
                            part = os.read(child, 1024 * 1024)
                            if not part:
                                break
                            count += len(part)
                            if count > MAX_FILE:
                                raise AssetError('prepared file too large')
                            digest.update(part)
                        if (_snapshot(st) != _snapshot(os.fstat(child)) or _snapshot(st) != _snapshot(os.stat(name, dir_fd=parent, follow_symlinks=False)) or count != rec['size'] or digest.hexdigest() != rec['sha256']):
                            raise AssetError('prepared file changed')
                    finally:
                        os.close(child)
                    seen.add(rel)
                else:
                    raise AssetError('unexpected prepared entry')
            if _snapshot(before) != _snapshot(os.fstat(parent)):
                raise AssetError('prepared directory changed')
        if not _owner_mode(os.fstat(fd), owner, group, 0o700 if owner else 0o750):
            raise AssetError('prepared root permissions mismatch')
        seen = set()
        walk(fd, '')
        if seen != set(records):
            raise AssetError('prepared inventory mismatch')
    finally:
        os.close(fd)


def _mount(source, target, readonly):
    return {'type': 'bind', 'source': source, 'target': target, 'read_only': readonly, 'bind': {'create_host_path': False}}


def _config(runtime, rollback):
    ro, rw = runtime['ro_dir'], runtime['rw_dir']
    mounts = [
        _mount(ro + '/main', REQUIRED_ENV['DAQ_KNOWLEDGE_RELEASE_ROOT'], True),
        _mount(ro + '/sdk', REQUIRED_ENV['DAQ_SDK_RELEASE_ROOT'], True),
        _mount(runtime['old_secrets_dir'] if rollback else ro + '/secrets', '/run/daq-secrets', True),
        _mount(rw + '/attachments', '/app/data/attachments', False),
        _mount(rw + '/trace/daq_traces.jsonl', '/app/data/daq_traces.jsonl', False),
    ]
    service = {'image': IMAGE, 'pull_policy': 'never', 'container_name': 'ai-daq-fae-backend', 'restart': 'unless-stopped', 'env_file': [runtime['env_file']], 'volumes': mounts, 'ports': ['127.0.0.1:8081:8081'], 'networks': {'camera-db': None, 'platform-edge': {'ipv4_address': '172.31.0.9'}}, 'healthcheck': {'test': ['CMD', 'curl', '-fsS', 'http://127.0.0.1:8081/health'], 'interval': '15s', 'timeout': '5s', 'retries': 12}}
    if rollback:
        service['user'] = '0:0'
    else:
        service.update({'user': '21081:21081', 'read_only': True, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'], 'environment': {'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': '/tmp'}, 'tmpfs': ['/tmp:rw,noexec,nosuid,nodev,size=256m,uid=21081,gid=21081,mode=0700']})
    return {'name': runtime['project'], 'services': {'ai-daq-fae-backend': service}, 'networks': {'camera-db': {'external': True, 'name': 'deploy_default'}, 'platform-edge': {'external': True, 'name': 'orbbec-agent-platform-edge'}}}



def _validate_deployment(runtime):
    raw, st = _read_regular(runtime['labels_file'])
    if not _owner_mode(st, 0, 0, 0o600):
        raise AssetError('label evidence must be private')
    labels = json.loads(raw)
    if not isinstance(labels, dict) or labels.get('com.docker.compose.project') != runtime['project'] or labels.get('com.docker.compose.service') != 'ai-daq-fae-backend':
        raise AssetError('compose labels mismatch')
    files = runtime['config_files']
    if not isinstance(files, dict) or not files:
        raise AssetError('config file identities required')
    if labels.get('com.docker.compose.project.config_files', '').split(',') != list(files):
        raise AssetError('compose config labels mismatch')
    for path, expected in files.items():
        _parts(path)
        raw_config, _ = _read_regular(path)
        if hashlib.sha256(raw_config).hexdigest() != expected:
            raise AssetError('compose config identity mismatch')
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def _validate_runtime(spec):
    runtime = spec['runtime']
    for key in ('ro_dir', 'rw_dir', 'env_file', 'old_secrets_dir'):
        _parts(runtime[key])
    if not isinstance(runtime.get('project'), str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', runtime.get('config_identity', '')):
        raise AssetError('deployment identity required')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', runtime['project']):
        raise AssetError('invalid compose project')
    if _validate_deployment(runtime) != runtime['config_identity']:
        raise AssetError('label evidence identity mismatch')
    _validate_env(runtime['env_file'], runtime['env_sha256'], spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256'])
    manifests = {}
    for name, stage in [('ro_dir', 'ro'), ('rw_dir', 'rw')]:
        root = runtime[name]
        root_fd = _open_dir(root)
        try:
            if not _owner_mode(os.fstat(root_fd), 0, 21081, 0o750):
                raise AssetError('prepared parent permissions mismatch')
            expected_entries = {'main', 'sdk', 'secrets', 'manifest.json'} if stage == 'ro' else {'attachments', 'trace', 'manifest.json'}
            if set(os.listdir(root_fd)) != expected_entries:
                raise AssetError('prepared root is incomplete')
        finally:
            os.close(root_fd)
        raw, st = _read_regular(root + '/manifest.json')
        if not _owner_mode(st, 0, 0, 0o600):
            raise AssetError('manifest permissions mismatch')
        manifest = json.loads(raw)
        if manifest.get('stage') != stage or manifest.get('matched') is not True:
            raise AssetError('prepared manifest invalid')
        expected_names = ('main', 'sdk', 'secrets') if stage == 'ro' else ('attachments', 'trace')
        if set(manifest.get('files', {})) != set(expected_names):
            raise AssetError('manifest scope mismatch')
        for category in expected_names:
            records = manifest['files'][category]
            expected = spec['expected'][category]
            if set(records) != set(expected) or any({'size': rec['size'], 'sha256': rec['sha256']} != expected[path] for path, rec in records.items()):
                raise AssetError('manifest identity mismatch')
        if stage == 'rw' and manifest.get('stopped_evidence', {}).get('verification') != 'controller_asserted_not_verified':
            raise AssetError('stopped evidence absent')
        manifests[stage] = manifest
    for name in ('main', 'sdk', 'secrets'):
        _validate_tree(runtime['ro_dir'] + '/' + name, manifests['ro']['files'][name], 0, 21081, 0o640, name == 'secrets')
    _validate_tree(runtime['rw_dir'] + '/attachments', manifests['rw']['files']['attachments'], 21081, 21081, 0o600)
    _validate_tree(runtime['rw_dir'] + '/trace', manifests['rw']['files']['trace'], 21081, 21081, 0o600)
    if runtime['old_secrets_dir'] != spec['paths']['secrets']:
        raise AssetError('rollback secrets must be the original directory')
    old = _open_dir(runtime['old_secrets_dir'])
    try:
        _verify_tree(old, manifests['ro']['files']['secrets'], SECRETS, manifests['ro']['source_directories']['secrets'])
    finally:
        os.close(old)
    return runtime


def render(spec, target):
    _check_spec(spec)
    _ensure_target_outside_sources(spec, target, include_prepared=True)
    target_fd = None
    try:
        target_fd = _start(target)
        runtime = _validate_runtime(spec)
        _write_json(target_fd, 'runtime.json', _config(runtime, False))
        _write_json(target_fd, 'rollback.json', _config(runtime, True))
        _write_json(target_fd, 'manifest.json', {'version': 1, 'stage': 'render', 'config_identity_controller_asserted': runtime['config_identity'], 'project': runtime['project'], 'environment_sha256': runtime['env_sha256'], 'matched': True})
        return {'matched': True}
    except Exception as exc:
        if target_fd is not None:
            try:
                _write_json(target_fd, 'failure.json', {'version': 1, 'stage': 'render', 'status': 'failed', 'category': type(exc).__name__, 'reason': str(exc) if isinstance(exc, AssetError) else type(exc).__name__})
            except Exception:
                pass
        raise AssetError('render failed; inspect private receipt') from exc
    finally:
        if target_fd is not None:
            os.close(target_fd)



def verify(spec, target):
    _check_spec(spec)
    try:
        runtime = _validate_runtime(spec)
        root = _open_dir(str(target))
        try:
            if not _owner_mode(os.fstat(root), 0, 0, 0o700) or set(os.listdir(root)) != {'runtime.json', 'rollback.json', 'manifest.json'}:
                raise AssetError('config directory invalid')
        finally:
            os.close(root)
        if _private_json(str(target) + '/runtime.json') != _config(runtime, False):
            raise AssetError('runtime configuration drift')
        if _private_json(str(target) + '/rollback.json') != _config(runtime, True):
            raise AssetError('rollback configuration drift')
        receipt = _private_json(str(target) + '/manifest.json')
        if receipt != {'version': 1, 'stage': 'render', 'config_identity_controller_asserted': runtime['config_identity'], 'project': runtime['project'], 'environment_sha256': runtime['env_sha256'], 'matched': True}:
            raise AssetError('config manifest mismatch')
        return {'matched': True}
    except Exception as exc:
        raise AssetError('verification failed') from exc


def _private_json(path):
    data, st = _read_regular(path)
    if not _owner_mode(st, 0, 0, 0o600):
        raise AssetError('private root-owned input required')
    obj = json.loads(data)
    if not isinstance(obj, dict):
        raise AssetError('object input required')
    return obj


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepare DAQ security assets without printing sensitive data')
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('stage-ro', 'capture-rw', 'render', 'verify'):
        sub = commands.add_parser(command)
        sub.add_argument('--spec', required=True)
        sub.add_argument('--target', required=True)
        if command == 'capture-rw':
            sub.add_argument('--stopped-evidence', required=True)
    args = parser.parse_args(argv)
    try:
        if not _is_root():
            raise AssetError('root required')
        spec = _private_json(args.spec)
        if args.command == 'stage-ro':
            stage_ro(spec, args.target)
        elif args.command == 'capture-rw':
            capture_rw(spec, args.target, _private_json(args.stopped_evidence))
        elif args.command == 'render':
            render(spec, args.target)
        else:
            verify(spec, args.target)
        print(json.dumps({'stage': args.command, 'matched': True}))
        return 0
    except (AssetError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'stage': args.command, 'matched': False}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
