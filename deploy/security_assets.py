#!/usr/bin/env python3
"""Prepare immutable DAQ assets and separately capture quiesced runtime data.

All commands require root. Inputs and output manifests are private metadata; no
key material is accepted in arguments or printed. See SECURITY_NONROOT.md.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import tempfile
import time
import sys
from datetime import datetime, timezone
from pathlib import PurePosixPath

IMAGE = 'sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7'
CANDIDATE_IMAGE = 'sha256:7360ead746ecef1303f27cc85b560ee13807af4a2245c8963f3eef94468586c7'
IMAGE_RECIPE_COMMIT = '9439b21b95ded7530e2f4205bf19a53b667b52ac'
SECRETS = ('session-keyring.json', 'content-keyring.json', 'entitlements.json', 'feedback-approval-keys-20261023.json')
MAX_FILE = 64 * 1024**2
MAX_TOTAL = 256 * 1024**2
MAX_NODES = 4096
DISK_RESERVE = 512 * 1024**2
MAX_DEPTH = 128
MAX_MANIFEST = 16 * 1024 * 1024
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
    if len(data) > MAX_MANIFEST:
        raise AssetError('metadata size limit exceeded')
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
        if budget[0] > MAX_NODES or budget[1] > MAX_TOTAL:
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
            if len(names) > MAX_NODES:
                raise AssetError('directory entry limit exceeded')
    return sorted(names)


def _tree(src, dst, expected, owner, gid, file_mode, budget, selected=None, directories=None):
    found = {}
    def walk(source, target, prefix):
        if prefix.count('/') > MAX_DEPTH:
            raise AssetError('directory depth limit exceeded')
        budget[0] += 1
        if budget[0] > MAX_NODES:
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



def _snapshot_target_tree(path):
    root = _open_dir(path)
    records = {}
    try:
        def visit(fd, prefix):
            if prefix.count('/') > MAX_DEPTH or len(records) >= MAX_NODES:
                raise AssetError('prepared inventory limit exceeded')
            st = os.fstat(fd)
            records[prefix] = _metadata(st)
            for name in _list_names(fd):
                entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                rel = prefix + name
                if stat.S_ISDIR(entry.st_mode):
                    child = os.open(name, DIR_FLAGS, dir_fd=fd)
                    try:
                        if _snapshot(entry) != _snapshot(os.fstat(child)):
                            raise AssetError('prepared directory replaced')
                        visit(child, rel + '/')
                    finally:
                        os.close(child)
                elif stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1:
                    child = os.open(name, FILE_FLAGS, dir_fd=fd)
                    try:
                        if _snapshot(entry) != _snapshot(os.fstat(child)):
                            raise AssetError('prepared file replaced')
                        if len(records) >= MAX_NODES:
                            raise AssetError('prepared inventory limit exceeded')
                        records[rel] = _metadata(entry)
                    finally:
                        os.close(child)
                else:
                    raise AssetError('unsafe prepared entry')
                if _snapshot(entry) != _snapshot(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                    raise AssetError('prepared entry changed')
            if _snapshot(st) != _snapshot(os.fstat(fd)):
                raise AssetError('prepared directory changed')
        visit(root, '')
        parent, name = _parent(path)
        try:
            if _snapshot(os.fstat(root)) != _snapshot(os.stat(name, dir_fd=parent, follow_symlinks=False)):
                raise AssetError('prepared root replaced')
        finally:
            os.close(parent)
        return records
    finally:
        os.close(root)


def _verify_published_tree(path, expected, file_records=None, owner=None, group=None, mode=None, secret=False):
    root = _open_dir(path)
    seen = set()
    try:
        def visit(fd, prefix):
            if prefix.count('/') > MAX_DEPTH:
                raise AssetError('directory depth limit exceeded')
            before = os.fstat(fd)
            if expected.get(prefix) != _metadata(before):
                raise AssetError('prepared directory identity changed')
            if owner is not None and not _owner_mode(before, owner, group, 0o700 if owner else 0o750):
                raise AssetError('prepared directory permissions mismatch')
            seen.add(prefix)
            for name in _list_names(fd):
                rel = prefix + name
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    child = os.open(name, DIR_FLAGS, dir_fd=fd)
                    try:
                        if _snapshot(st) != _snapshot(os.fstat(child)):
                            raise AssetError('prepared directory replaced')
                        visit(child, rel + '/')
                    finally:
                        os.close(child)
                elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                    child = os.open(name, FILE_FLAGS, dir_fd=fd)
                    try:
                        if _snapshot(st) != _snapshot(os.fstat(child)) or expected.get(rel) != _metadata(st):
                            raise AssetError('prepared file identity changed')
                        if file_records is not None:
                            rec = file_records.get(rel)
                            file_owner = 21081 if secret and name in SECRETS[:2] else owner
                            file_mode = 0o600 if secret and name in SECRETS[:2] else mode
                            if not rec or not _owner_mode(st, file_owner, group, file_mode):
                                raise AssetError('prepared file permissions or inventory mismatch')
                            digest = hashlib.sha256()
                            count = 0
                            while True:
                                chunk = os.read(child, 1024 * 1024)
                                if not chunk:
                                    break
                                count += len(chunk)
                                if count > MAX_FILE:
                                    raise AssetError('prepared file limit exceeded')
                                digest.update(chunk)
                            if count != rec['size'] or digest.hexdigest() != rec['sha256']:
                                raise AssetError('prepared file content changed')
                        if _snapshot(st) != _snapshot(os.fstat(child)):
                            raise AssetError('prepared file changed while read')
                        seen.add(rel)
                    finally:
                        os.close(child)
                else:
                    raise AssetError('unsafe prepared entry')
                if _snapshot(st) != _snapshot(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                    raise AssetError('prepared entry changed')
            if _snapshot(before) != _snapshot(os.fstat(fd)):
                raise AssetError('prepared directory changed')
        visit(root, '')
        if seen != set(expected):
            raise AssetError('prepared target inventory changed')
        if file_records is not None and {p for p in seen if p and not p.endswith('/')} != set(file_records):
            raise AssetError('prepared file inventory changed')
        parent, name = _parent(path)
        try:
            if _snapshot(os.fstat(root)) != _snapshot(os.stat(name, dir_fd=parent, follow_symlinks=False)):
                raise AssetError('prepared root replaced')
        finally:
            os.close(parent)
    finally:
        os.close(root)


def _publish_manifest(target_fd, target, output, names):
    fd = os.open('manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target_fd)
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, b'{}\n')
        os.fsync(fd)
        output['target_trees'] = {name: _snapshot_target_tree(str(target) + '/' + name) for name in names}
        os.fchown(target_fd, 0, 21081)
        os.fchmod(target_fd, 0o750)
        os.fsync(target_fd)
        output['target_root'] = _metadata(os.fstat(target_fd))
        data = (json.dumps(output, sort_keys=True, indent=2) + '\n').encode()
        if len(data) > MAX_MANIFEST or len(data) > output['preflight']['manifest_bound']:
            raise AssetError('manifest size limit exceeded')
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        _write_all(fd, data)
        os.fsync(fd)
        os.fsync(target_fd)
    finally:
        os.close(fd)


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



def _preflight(spec, stage, target_fd):
    categories = ('main', 'sdk', 'secrets') if stage == 'ro' else ('attachments', 'trace')
    count = 0
    total = 0
    estimate = 16384
    def account(path, size=0):
        nonlocal count, total, estimate
        count += 1
        total += size
        estimate += 2048 + 24 * len(path.encode('utf-8'))
        if count > MAX_NODES or total > MAX_TOTAL or estimate > MAX_MANIFEST:
            raise AssetError('preflight inventory limit exceeded')
    def check_file(st, rel, expected):
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > MAX_FILE:
            raise AssetError('unsafe or oversized source file')
        value = expected.get(rel)
        if (not isinstance(value, dict) or set(value) != {'size', 'sha256'}
            or type(value['size']) is not int or value['size'] != st.st_size
            or not isinstance(value['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['sha256'])):
            raise AssetError('source inventory identity mismatch')
        account(rel, st.st_size)
    # The batch root and private manifest consume two target entries.
    account('')
    account('manifest.json')
    for category in categories:
        expected = spec['expected'][category]
        if not isinstance(expected, dict) or len(expected) > MAX_NODES:
            raise AssetError('invalid inventory')
        if category == 'secrets' and set(expected) != set(SECRETS):
            raise AssetError('secret inventory mismatch')
        found = set()
        if category == 'trace':
            account('trace/')
            parent, name = _parent(spec['paths']['trace'])
            try:
                st = os.stat(name, dir_fd=parent, follow_symlinks=False)
                check_file(st, 'daq_traces.jsonl', expected)
                found.add('daq_traces.jsonl')
            finally:
                os.close(parent)
        else:
            root = _open_dir(spec['paths'][category])
            try:
                def walk(fd, prefix):
                    if prefix.count('/') > MAX_DEPTH:
                        raise AssetError('directory depth limit exceeded')
                    before = os.fstat(fd)
                    account(prefix)
                    names = sorted(SECRETS) if category == 'secrets' and not prefix else _list_names(fd)
                    for name in names:
                        rel = prefix + name
                        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        if stat.S_ISDIR(st.st_mode):
                            child = os.open(name, DIR_FLAGS, dir_fd=fd)
                            try:
                                if _snapshot(st) != _snapshot(os.fstat(child)):
                                    raise AssetError('source directory replaced')
                                walk(child, rel + '/')
                            finally:
                                os.close(child)
                        else:
                            check_file(st, rel, expected)
                            found.add(rel)
                    if _snapshot(before) != _snapshot(os.fstat(fd)):
                        raise AssetError('source directory changed')
                walk(root, '')
            finally:
                os.close(root)
        if found != set(expected):
            raise AssetError('source inventory mismatch')
    disk = os.fstatvfs(target_fd)
    free = disk.f_bavail * disk.f_frsize
    if free < total + estimate + DISK_RESERVE:
        raise AssetError('insufficient free space with reserve')
    return {'entries': count, 'bytes': total, 'manifest_bound': estimate, 'free_bytes': free, 'reserve_bytes': DISK_RESERVE}


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
        preflight = _preflight(spec, stage, target_fd)
        output = {'version': 1, 'stage': stage, 'matched': True, 'created_at': datetime.now(timezone.utc).isoformat(), 'files': {}, 'preflight': preflight}
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
        _publish_manifest(target_fd, target, output, list(names) + (['trace'] if stage == 'rw' else []))
        for category in output['target_trees']:
            owner = 21081 if stage == 'rw' else 0
            _verify_published_tree(str(target) + '/' + category, output['target_trees'][category], output['files'][category], owner, 21081, 0o600 if owner else 0o640, category == 'secrets')
        if _metadata(os.fstat(target_fd)) != output['target_root']:
            raise AssetError('prepared root changed after publication')
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


def _validate_env(path, expected_hash, approval_hash, effective=None):
    data, _ = _read_regular(path)
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise AssetError('environment identity mismatch')
    if not isinstance(effective, dict):
        raise AssetError('effective compose environment required')
    for key, value in dict(REQUIRED_ENV, DAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=approval_hash).items():
        if effective.get(key) != value:
            raise AssetError('effective environment contract mismatch')


def _check_compose_binary(runtime):
    path = runtime['compose_binary']
    parent, name = _parent(path, trusted=True)
    try:
        fd = os.open(name, FILE_FLAGS, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != 0 or before.st_mode & 0o022 or before.st_size > 256 * 1024**2):
                raise AssetError('untrusted compose executable')
            digest = hashlib.sha256()
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            if (digest.hexdigest() != runtime['compose_sha256'] or _snapshot(before) != _snapshot(os.fstat(fd))
                or _snapshot(before) != _snapshot(os.stat(name, dir_fd=parent, follow_symlinks=False))):
                raise AssetError('compose executable identity mismatch')
            return _metadata(before)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _compose_model(runtime, resolve_env, config_files=None, *, raw=False, project_directory=None, resolve_paths=False):
    before = _check_compose_binary(runtime)
    interpolation, st = _read_regular(runtime['interpolation_file'])
    if not _owner_mode(st, 0, 0, 0o600) or hashlib.sha256(interpolation).hexdigest() != runtime['interpolation_sha256']:
        raise AssetError('interpolation context identity mismatch')
    paths = list(runtime['config_files']) if config_files is None else list(config_files)
    if not paths or len(paths) > 8:
        raise AssetError('unsupported compose file count')
    args = [runtime['compose_binary'], '--project-name', runtime['project'], '--env-file', runtime['interpolation_file']]
    project_directory = project_directory or runtime['project_directory']
    _parts(project_directory)
    args += ['--project-directory', project_directory]
    for path in paths:
        args += ['-f', path]
    args += ['config', '--format', 'json']
    if raw:
        args.append('--no-interpolate')
        if not resolve_paths:
            args.append('--no-path-resolution')
    elif not resolve_env:
        args.append('--no-env-resolution')
    try:
        proc = subprocess.Popen(args, cwd='/', env={}, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ, 'stdout')
        selector.register(proc.stderr, selectors.EVENT_READ, 'stderr')
        output = bytearray()
        deadline = time.monotonic() + 60
        failed = False
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failed = True
                    break
                ready = selector.select(remaining)
                if not ready:
                    failed = True
                    break
                for key, _ in ready:
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                    elif key.data == 'stderr':
                        # Warnings include unset interpolation variables: fail
                        # closed and never relay potentially sensitive text.
                        failed = True
                        break
                    else:
                        output.extend(data)
                        if len(output) > MAX_MANIFEST:
                            failed = True
                            break
                if failed:
                    break
            if failed:
                proc.kill()
            return_code = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        finally:
            selector.close()
            proc.stdout.close()
            proc.stderr.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AssetError('compose parser unavailable') from exc
    if failed or return_code != 0:
        raise AssetError('compose parser rejected configuration')
    after_interpolation, after_st = _read_regular(runtime['interpolation_file'])
    if _snapshot(st) != _snapshot(after_st) or interpolation != after_interpolation:
        raise AssetError('interpolation context changed')
    if _check_compose_binary(runtime) != before:
        raise AssetError('compose executable changed')
    try:
        model = json.loads(output)
    except (ValueError, UnicodeError) as exc:
        raise AssetError('invalid compose model') from exc
    if not isinstance(model, dict):
        raise AssetError('invalid compose model')
    return model



def _compose_source(runtime):
    # ToModel (--no-interpolate) is the reference/explicit layer on both
    # Compose 2.40 and 5.6. ToProject is the complete effective layer.
    raw = _compose_model(runtime, False, raw=True)
    raw_with_paths = _compose_model(runtime, False, raw=True, resolve_paths=True)
    effective = _compose_model(runtime, True)
    service_name = 'ai-daq-fae-backend'
    raw_services = raw.get('services', {})
    effective_services = effective.get('services', {})
    if set(raw_services) != {service_name} or set(effective_services) != {service_name}:
        raise AssetError('unsupported compose service scope')
    raw_service = raw_services[service_name]
    files = raw_service.get('env_file')
    if isinstance(files, (str, dict)):
        files = [files]
    if not isinstance(files, list) or len(files) != 1:
        raise AssetError('unsupported source env file scope')
    references = []
    for item in files:
        if isinstance(item, str):
            item = {'path': item}
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise AssetError('invalid source env file reference')
        if set(item) - {'path', 'required', 'format'}:
            raise AssetError('unreviewed env file reference')
        references.append(copy.deepcopy(item))
    path_services = raw_with_paths.get('services', {})
    if set(path_services) != {service_name}:
        raise AssetError('unsupported resolved source service scope')
    with_paths = path_services[service_name].get('env_file')
    if isinstance(with_paths, (str, dict)):
        with_paths = [with_paths]
    if not isinstance(with_paths, list) or len(with_paths) != len(references):
        raise AssetError('resolved source reference mismatch')
    with_paths = [{'path': item} if isinstance(item, str) else item for item in with_paths]
    if any(not isinstance(item, dict) or not isinstance(item.get('path'), str) for item in with_paths):
        raise AssetError('invalid resolved source reference')
    for original, resolved in zip(references, with_paths):
        if {k: v for k, v in original.items() if k != 'path'} != {k: v for k, v in resolved.items() if k != 'path'}:
            raise AssetError('source reference options changed')
    explicit = raw_service.get('environment')
    if explicit is not None and not isinstance(explicit, dict):
        raise AssetError('invalid explicit environment layer')
    raw_reference = references[0]['path']
    raw_resolved_path = with_paths[0]['path']
    if raw_reference.startswith('/'):
        if os.path.normpath(raw_reference) != raw_resolved_path:
            raise AssetError('source reference origin ambiguous')
        base_directory = runtime['project_directory']
    else:
        suffix = '/' + raw_reference
        if not raw_resolved_path.endswith(suffix):
            raise AssetError('source reference origin ambiguous')
        base_directory = raw_resolved_path[:-len(suffix)] or '/'
    if base_directory != '/':
        origin = _open_dir(base_directory)
        os.close(origin)
    with tempfile.TemporaryDirectory(prefix='daq-compose-refs-') as temp:
        echo_path = os.path.join(temp, 'echo.json')
        echo = {'services': {'reference-echo': {'image': 'synthetic', 'environment': {f'REFERENCE_{i}': item['path'] for i, item in enumerate(references)}}}}
        _write_private_model(echo_path, echo)
        echo_model = _compose_model(runtime, True, [echo_path], project_directory=base_directory)
        echo_env = echo_model.get('services', {}).get('reference-echo', {}).get('environment')
        if not isinstance(echo_env, dict) or set(echo_env) != {f'REFERENCE_{i}' for i in range(len(references))}:
            raise AssetError('invalid resolved env file references')
        for i, item in enumerate(references):
            resolved = echo_env[f'REFERENCE_{i}']
            if not isinstance(resolved, str) or not resolved or '$' in resolved:
                raise AssetError('invalid interpolated env file reference')
            item['path'] = resolved
        resolve_path = os.path.join(temp, 'paths.json')
        paths_model = {'services': {'reference-paths': {'image': 'synthetic', 'env_file': references}}}
        _write_private_model(resolve_path, paths_model)
        paths = _compose_model(runtime, False, [resolve_path], raw=True, project_directory=base_directory, resolve_paths=True)
        references = paths.get('services', {}).get('reference-paths', {}).get('env_file')
        if isinstance(references, (str, dict)):
            references = [references]
        if not isinstance(references, list) or len(references) != 1:
            raise AssetError('invalid resolved env file references')
        references = [{'path': item} if isinstance(item, str) else item for item in references]
        for item in references:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str):
                raise AssetError('invalid resolved env file reference')
            _parts(item['path'])
    source = copy.deepcopy(effective)
    service = source['services'][service_name]
    service.pop('environment', None)
    if explicit is not None:
        service['environment'] = copy.deepcopy(explicit)
    service['env_file'] = references
    return source, effective


def _write_private_model(path, model):
    data = json.dumps(model).encode()
    if len(data) > MAX_MANIFEST:
        raise AssetError('metadata size limit exceeded')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


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



# Explicitly classify normalized Compose service fields. New unreviewed
# fields fail closed instead of inheriting a future execution capability.
_ALLOWED_SERVICE_FIELDS = frozenset({
    'image', 'build', 'pull_policy', 'container_name', 'restart', 'env_file',
    'environment', 'volumes', 'ports', 'networks', 'healthcheck', 'command',
    'entrypoint', 'logging', 'labels', 'working_dir', 'hostname', 'domainname',
    'dns', 'dns_search', 'dns_opt', 'extra_hosts', 'expose', 'platform',
    'init', 'tty', 'stdin_open', 'stop_grace_period', 'stop_signal', 'deploy',
    'mem_limit', 'mem_reservation', 'memswap_limit', 'mem_swappiness',
    'cpus', 'cpuset', 'cpu_count', 'cpu_percent', 'cpu_shares', 'cpu_quota',
    'cpu_period', 'cpu_rt_period', 'cpu_rt_runtime', 'pids_limit', 'ulimits',
    'shm_size', 'oom_score_adj', 'oom_kill_disable', 'user', 'read_only',
    'cap_drop', 'security_opt', 'tmpfs',
})
_FORBIDDEN_EXECUTION_FIELDS = frozenset({
    'volumes_from', 'secrets', 'configs', 'use_api_socket', 'devices',
    'device_cgroup_rules', 'gpus', 'group_add', 'cap_add', 'privileged',
    'pid', 'ipc', 'uts', 'userns_mode', 'network_mode', 'post_start', 'pre_stop',
})


def _execution_boundary(service, phase, *, effective=False):
    if not isinstance(service, dict) or phase not in ('source', 'runtime', 'rollback'):
        raise AssetError('invalid execution policy input')
    if set(service) - _ALLOWED_SERVICE_FIELDS - _FORBIDDEN_EXECUTION_FIELDS:
        raise AssetError('unreviewed compose service field')
    for field in sorted(_FORBIDDEN_EXECUTION_FIELDS):
        if service.get(field):
            raise AssetError('unauthorized compose execution capability: ' + field)
    deploy = service.get('deploy') or {}
    if not isinstance(deploy, dict) or set(deploy) - {'resources', 'placement'} or deploy.get('placement'):
        raise AssetError('unreviewed deploy capability')
    resources = deploy.get('resources') or {}
    if not isinstance(resources, dict) or set(resources) - {'limits', 'reservations'}:
        raise AssetError('unreviewed deploy resources')
    for category in ('limits', 'reservations'):
        values = resources.get(category) or {}
        allowed = {'cpus', 'memory', 'pids'} if category == 'limits' else {'cpus', 'memory'}
        if not isinstance(values, dict):
            raise AssetError('invalid deploy resources')
        if 'devices' in values:
            raise AssetError('unauthorized deploy resource capability: devices')
        if set(values) - allowed:
            raise AssetError('unreviewed deploy resource capability')
    options = service.get('security_opt') or []
    if not isinstance(options, list) or any(option != 'no-new-privileges:true' for option in options):
        raise AssetError('unreviewed security option')
    if phase == 'runtime':
        if (service.get('user') != '21081:21081' or service.get('read_only') is not True
            or service.get('cap_drop') != ['ALL'] or 'no-new-privileges:true' not in options
            or service.get('tmpfs') != ['/tmp:rw,noexec,nosuid,nodev,size=256m,uid=21081,gid=21081,mode=0700']):
            raise AssetError('runtime execution policy mismatch')
    else:
        expected_users = (None, '', '0', '0:0', 'root') if phase == 'source' else ('0:0',)
        if service.get('user') not in expected_users or service.get('read_only') or service.get('cap_drop') or service.get('tmpfs'):
            raise AssetError('original root execution policy mismatch')
    mounts = service.get('volumes') or []
    if not isinstance(mounts, list) or any(not isinstance(item, dict) for item in mounts):
        raise AssetError('invalid explicit mount policy')
    if phase == 'source':
        expected_mounts = {'/app/data': False, '/run/daq-secrets': True}
    else:
        expected_mounts = {
            '/app/data/knowledge/reviewed-thinking-prefix-20261028': True,
            '/app/data/knowledge/sdk-thinking-prefix-20261028': True,
            '/run/daq-secrets': True, '/app/data/attachments': False,
            '/app/data/daq_traces.jsonl': False,
        }
    if len(mounts) != len(expected_mounts) or {item.get('target') for item in mounts} != set(expected_mounts):
        raise AssetError('explicit mount scope mismatch')
    for item in mounts:
        bind = item.get('bind')
        if phase == 'source' and 'bind' not in item:
            bind = {}
        readonly = item.get('read_only', False)
        if (item.get('type') != 'bind' or not isinstance(bind, dict)
            or set(item) - {'type', 'source', 'target', 'read_only', 'bind'}
            or set(bind) - {'create_host_path'}
            or type(readonly) is not bool or readonly != expected_mounts[item['target']]):
            raise AssetError('unreviewed explicit mount capability')
        if phase != 'source':
            if not effective and 'read_only' not in item:
                raise AssetError('author must explicitly declare bind access mode')
            if effective:
                # compose-go 2.9.1 serializes the reviewed false boolean as
                # an omitted field, but retains the long-format bind object.
                if 'create_host_path' in bind and bind['create_host_path'] is not False:
                    raise AssetError('unreviewed effective bind create policy')
            elif bind.get('create_host_path') is not False:
                raise AssetError('author must explicitly disable bind creation')
    networks = service.get('networks') or {}
    if (not isinstance(networks, dict) or set(networks) != {'camera-db', 'platform-edge'}
        or not isinstance(networks.get('platform-edge'), dict)
        or networks['platform-edge'].get('ipv4_address') != '172.31.0.9'):
        raise AssetError('execution network boundary mismatch')
    if set(networks.get('camera-db') or {}) or set(networks['platform-edge']) != {'ipv4_address'}:
        raise AssetError('unreviewed network endpoint capability')
    ports = service.get('ports') or []
    if (len(ports) != 1 or (ports[0].get('host_ip'), str(ports[0].get('published')),
         ports[0].get('target'), ports[0].get('protocol', 'tcp'), ports[0].get('mode', 'ingress'))
        != ('127.0.0.1', '8081', 8081, 'tcp', 'ingress')
        or set(ports[0]) - {'mode', 'host_ip', 'target', 'published', 'protocol'}):
        raise AssetError('execution port boundary mismatch')


def _config(runtime, rollback, source):
    model = copy.deepcopy(source)
    services = model.get('services', {})
    if set(services) != {'ai-daq-fae-backend'}:
        raise AssetError('unsupported compose service scope')
    service = services['ai-daq-fae-backend']
    _execution_boundary(service, 'source')
    old_mounts = service.get('volumes', [])
    mounts_by_target = {v['target']: v for v in old_mounts}
    if (mounts_by_target['/app/data'].get('source') != runtime['old_data_dir']
        or mounts_by_target['/app/data'].get('read_only', False)
        or mounts_by_target['/run/daq-secrets'].get('source') != runtime['old_secrets_dir']
        or not mounts_by_target['/run/daq-secrets'].get('read_only', False)):
        raise AssetError('source mount identity mismatch')
    files = service.get('env_file', [])
    if len(files) != 1 or files[0].get('path') != runtime['env_file']:
        raise AssetError('unsupported source env file scope')
    for key, expected_name in [('camera-db', 'deploy_default'), ('platform-edge', 'orbbec-agent-platform-edge')]:
        network = model.get('networks', {}).get(key, {})
        if network.get('external') is not True or network.get('name') != expected_name:
            raise AssetError('source network identity mismatch')
    health = service.get('healthcheck') or {}
    if (health.get('disable') or service.get('container_name') != 'ai-daq-fae-backend' or service.get('restart') != 'unless-stopped'
        or health.get('test') != ['CMD', 'curl', '-fsS', 'http://127.0.0.1:8081/health']
        or health.get('interval') != '15s' or health.get('timeout') != '5s' or health.get('retries') != 12):
        raise AssetError('source health, name or restart mismatch')
    ro, rw = runtime['ro_dir'], runtime['rw_dir']
    service['volumes'] = [
        _mount(ro + '/main', REQUIRED_ENV['DAQ_KNOWLEDGE_RELEASE_ROOT'], True),
        _mount(ro + '/sdk', REQUIRED_ENV['DAQ_SDK_RELEASE_ROOT'], True),
        _mount(runtime['old_secrets_dir'] if rollback else ro + '/secrets', '/run/daq-secrets', True),
        _mount(rw + '/attachments', '/app/data/attachments', False),
        _mount(rw + '/trace/daq_traces.jsonl', '/app/data/daq_traces.jsonl', False),
    ]
    service.pop('build', None)
    # Compose 5.6 can synthesize an empty placement object while normalizing
    # resource-only deploy settings; writing it back triggers a warning.
    # The private effective-model comparison must still prove equivalence.
    if isinstance(service.get('deploy'), dict) and service['deploy'].get('placement') == {}:
        service['deploy'].pop('placement')
    service.update({'image': IMAGE if rollback else CANDIDATE_IMAGE, 'pull_policy': 'never', 'user': '0:0' if rollback else '21081:21081'})
    if not rollback:
        security = list(service.get('security_opt') or [])
        if 'no-new-privileges:true' not in security:
            security.append('no-new-privileges:true')
        environment = dict(service.get('environment') or {})
        environment.update({'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': '/tmp'})
        service.update({'read_only': True, 'cap_drop': ['ALL'], 'security_opt': security, 'environment': environment, 'tmpfs': ['/tmp:rw,noexec,nosuid,nodev,size=256m,uid=21081,gid=21081,mode=0700']})
    _execution_boundary(service, 'rollback' if rollback else 'runtime')
    return model


def _compare_effective(source, candidate, rollback):
    delta = {'image', 'build', 'pull_policy', 'user', 'read_only', 'cap_drop', 'security_opt', 'tmpfs', 'volumes', 'environment'}
    if {k: v for k, v in source.items() if k != 'services'} != {k: v for k, v in candidate.items() if k != 'services'}:
        raise AssetError('compose top-level semantics changed')
    a = source['services']['ai-daq-fae-backend']
    b = candidate['services']['ai-daq-fae-backend']
    if {k: v for k, v in a.items() if k not in delta} != {k: v for k, v in b.items() if k not in delta}:
        raise AssetError('compose service semantics changed')
    expected_env = dict(a.get('environment') or {})
    if not rollback:
        expected_env.update({'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': '/tmp'})
    if expected_env != (b.get('environment') or {}):
        raise AssetError('effective environment changed')



def _check_effective_policy(runtime, effective, rollback, desired):
    phase = 'rollback' if rollback else 'runtime'
    expected = desired['services']['ai-daq-fae-backend']
    # The effective omitted-false interpretation is valid only when paired
    # with this explicitly authored, independently reviewed long bind model.
    _execution_boundary(expected, phase)
    service = effective['services']['ai-daq-fae-backend']
    _execution_boundary(service, phase, effective=True)
    for key, expected_name in [('camera-db', 'deploy_default'), ('platform-edge', 'orbbec-agent-platform-edge')]:
        network = effective.get('networks', {}).get(key, {})
        if network.get('external') is not True or network.get('name') != expected_name:
            raise AssetError('effective network identity mismatch')
    if (service.get('image') != (IMAGE if rollback else CANDIDATE_IMAGE) or service.get('pull_policy') != 'never'
        or service.get('user') != ('0:0' if rollback else '21081:21081') or service.get('build')):
        raise AssetError('effective image or user policy mismatch')
    actual_mounts = service.get('volumes', [])
    wanted = expected['volumes']
    approved_sources = {
        REQUIRED_ENV['DAQ_KNOWLEDGE_RELEASE_ROOT']: runtime['ro_dir'] + '/main',
        REQUIRED_ENV['DAQ_SDK_RELEASE_ROOT']: runtime['ro_dir'] + '/sdk',
        '/run/daq-secrets': runtime['old_secrets_dir'] if rollback else runtime['ro_dir'] + '/secrets',
        '/app/data/attachments': runtime['rw_dir'] + '/attachments',
        '/app/data/daq_traces.jsonl': runtime['rw_dir'] + '/trace/daq_traces.jsonl',
    }
    if any(entry.get('source') != approved_sources[entry['target']] for entry in wanted):
        raise AssetError('author mount source identity mismatch')
    if len(actual_mounts) != len(wanted):
        raise AssetError('effective mount count mismatch')
    actual_by_target = {entry.get('target'): entry for entry in actual_mounts}
    for entry in wanted:
        actual = actual_by_target.get(entry['target'], {})
        if (actual.get('type') != 'bind' or actual.get('source') != entry['source']
            or actual.get('read_only', False) != entry['read_only']
            or actual['bind'].get('create_host_path', False) is not False):
            raise AssetError('effective mount policy mismatch')
    if not rollback:
        if (service.get('read_only') is not True or service.get('cap_drop') != ['ALL']
            or service.get('security_opt', []) != expected.get('security_opt', [])
            or 'no-new-privileges:true' not in service.get('security_opt', [])
            or service.get('tmpfs') != ['/tmp:rw,noexec,nosuid,nodev,size=256m,uid=21081,gid=21081,mode=0700']):
            raise AssetError('effective security policy mismatch')
    elif (service.get('read_only') or service.get('cap_drop') or service.get('tmpfs')
          or service.get('security_opt', []) != expected.get('security_opt', [])):
        raise AssetError('effective rollback policy mismatch')


def _verify_models(runtime, source, candidate, rollback):
    with tempfile.TemporaryDirectory(prefix='daq-config-') as temp:
        for name, model, is_rollback in [('runtime', candidate, False), ('rollback', rollback, True)]:
            path = os.path.join(temp, name + '.json')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                _write_all(fd, json.dumps(model).encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            effective = _compose_model(runtime, True, [path])
            _compare_effective(source, effective, is_rollback)
            _check_effective_policy(runtime, effective, is_rollback, model)


def _validate_deployment(runtime):
    raw, st = _read_regular(runtime['labels_file'])
    if not _owner_mode(st, 0, 0, 0o600):
        raise AssetError('label evidence must be private')
    labels = json.loads(raw)
    if not isinstance(labels, dict) or labels.get('com.docker.compose.project') != runtime['project'] or labels.get('com.docker.compose.service') != 'ai-daq-fae-backend':
        raise AssetError('compose labels mismatch')
    project_directory = runtime.get('project_directory')
    _parts(project_directory)
    if labels.get('com.docker.compose.project.working_dir') != project_directory:
        raise AssetError('compose project directory identity mismatch')
    directory = _open_dir(project_directory)
    os.close(directory)
    files = runtime['config_files']
    if not isinstance(files, dict) or not files:
        raise AssetError('config file identities required')
    if labels.get('com.docker.compose.project.config_files', '').split(',') != list(files):
        raise AssetError('compose config labels mismatch')
    total_size = 0
    for path, expected in files.items():
        _parts(path)
        raw_config, _ = _read_regular(path)
        total_size += len(raw_config)
        if total_size > MAX_MANIFEST:
            raise AssetError('compose input size limit exceeded')
        if hashlib.sha256(raw_config).hexdigest() != expected:
            raise AssetError('compose config identity mismatch')
    return 'sha256:' + hashlib.sha256(raw).hexdigest()



def _input_fingerprint(runtime):
    paths = list(runtime['config_files']) + [runtime['labels_file'], runtime['env_file'], runtime['interpolation_file']]
    result = []
    for path in paths:
        data, st = _read_regular(path)
        result.append((_metadata(st), hashlib.sha256(data).hexdigest()))
    return result


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
    input_fingerprint = _input_fingerprint(runtime)
    data_root = runtime['old_data_dir']
    _parts(data_root)
    expected_paths = {'main': data_root + '/knowledge/reviewed-thinking-prefix-20261028', 'sdk': data_root + '/knowledge/sdk-thinking-prefix-20261028', 'attachments': data_root + '/attachments', 'trace': data_root + '/daq_traces.jsonl'}
    if any(spec['paths'][key] != path for key, path in expected_paths.items()):
        raise AssetError('source data mount identity mismatch')
    source, effective_source = _compose_source(runtime)
    _execution_boundary(effective_source.get('services', {}).get('ai-daq-fae-backend'), 'source')
    effective_environment = effective_source.get('services', {}).get('ai-daq-fae-backend', {}).get('environment')
    _validate_env(runtime['env_file'], runtime['env_sha256'], spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256'], effective_environment)
    candidate = _config(runtime, False, source)
    rollback = _config(runtime, True, source)
    _verify_models(runtime, effective_source, candidate, rollback)
    if _validate_deployment(runtime) != runtime['config_identity'] or _input_fingerprint(runtime) != input_fingerprint:
        raise AssetError('deployment input changed during parsing')
    _validate_env(runtime['env_file'], runtime['env_sha256'], spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256'], effective_environment)
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
        reopened = _open_dir(root)
        try:
            parent, name = _parent(root)
            try:
                root_stat = os.fstat(reopened)
                if _snapshot(root_stat) != _snapshot(os.stat(name, dir_fd=parent, follow_symlinks=False)) or _metadata(root_stat) != manifest.get('target_root'):
                    raise AssetError('prepared root identity changed')
            finally:
                os.close(parent)
        finally:
            os.close(reopened)
        for category in expected_names:
            owner = 21081 if stage == 'rw' else 0
            _verify_published_tree(root + '/' + category, manifest['target_trees'][category], manifest['files'][category], owner, 21081, 0o600 if owner else 0o640, category == 'secrets')
        manifests[stage] = manifest
    if runtime['old_secrets_dir'] != spec['paths']['secrets']:
        raise AssetError('rollback secrets must be the original directory')
    for category, stage in [('main', 'ro'), ('sdk', 'ro'), ('secrets', 'ro'), ('attachments', 'rw')]:
        source = _open_dir(spec['paths'][category])
        try:
            if _metadata(os.fstat(source)) != manifests[stage]['source_roots'][category]:
                raise AssetError('source root changed after staging')
            _verify_tree(source, manifests[stage]['files'][category], SECRETS if category == 'secrets' else None, manifests[stage]['source_directories'][category])
        finally:
            os.close(source)
    trace_parent, trace_name = _parent(spec['paths']['trace'])
    try:
        _verify_tree(trace_parent, {trace_name: manifests['rw']['files']['trace']['daq_traces.jsonl']}, (trace_name,))
    finally:
        os.close(trace_parent)
    return runtime, candidate, rollback


def _render_receipt(runtime):
    return {'version': 1, 'stage': 'render', 'config_identity_controller_asserted': runtime['config_identity'],
            'project': runtime['project'], 'environment_sha256': runtime['env_sha256'], 'matched': True,
            'image_declarations': {'source_rollback': IMAGE, 'candidate': CANDIDATE_IMAGE,
                                   'recipe_commit': IMAGE_RECIPE_COMMIT}}


def render(spec, target):
    _check_spec(spec)
    _ensure_target_outside_sources(spec, target, include_prepared=True)
    target_fd = None
    try:
        target_fd = _start(target)
        runtime, candidate, rollback = _validate_runtime(spec)
        _write_json(target_fd, 'runtime.json', candidate)
        _write_json(target_fd, 'rollback.json', rollback)
        _write_json(target_fd, 'manifest.json', _render_receipt(runtime))
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
        runtime, candidate, rollback = _validate_runtime(spec)
        root = _open_dir(str(target))
        try:
            if not _owner_mode(os.fstat(root), 0, 0, 0o700) or set(os.listdir(root)) != {'runtime.json', 'rollback.json', 'manifest.json'}:
                raise AssetError('config directory invalid')
        finally:
            os.close(root)
        if _private_json(str(target) + '/runtime.json') != candidate:
            raise AssetError('runtime configuration drift')
        if _private_json(str(target) + '/rollback.json') != rollback:
            raise AssetError('rollback configuration drift')
        receipt = _private_json(str(target) + '/manifest.json')
        if receipt != _render_receipt(runtime):
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
