#!/usr/bin/env python3
"""Normalize only fixed immutable image assets; fail closed on unsafe trees."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOTS = ('app/daq_fae', 'app/src', 'app/scripts', 'app/prompts', 'app/migrations', 'app/webui/dist')
FENCE_DIRS = ('app', 'app/webui', 'app/data', 'app/data/knowledge', 'run', 'app/deploy')
FENCE_FILES = ('app/upstream-source.json', 'app/deploy/provision_env.py')
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_NODES = 10000
MAX_DEPTH = 64
POLICY = 'daq-immutable-code-permissions-v1'


def _owner_matches(uid, expected):
    return uid == expected


def _metadata(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
            st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _open_path(base, path, directory=False):
    parts = path.split('/')
    if not path or any(p in ('', '.', '..') for p in parts):
        raise ValueError('unsafe path')
    fd = os.dup(base)
    try:
        for i, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(parts) - 1 or directory:
                flags |= os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_hash(fd, st):
    if st.st_size > MAX_FILE:
        raise ValueError('file size limit')
    h = hashlib.sha256()
    size = 0
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        size += len(block)
        if size > MAX_FILE:
            raise ValueError('file size limit')
        h.update(block)
    if size != st.st_size or _metadata(os.fstat(fd)) != _metadata(st):
        raise ValueError('file changed while reading')
    return h.hexdigest()


def _bounded_names(fd, limit):
    names = []
    with os.scandir(fd) as iterator:
        for item in iterator:
            if len(names) >= limit:
                raise ValueError('node limit or directory membership changed')
            names.append(item.name)
    return sorted(names)


def _scan(base, expected_uid):
    entries = {}
    total = 0
    discovered = 0
    def examine(path, fd, mutable, depth, reserved=False):
        nonlocal total, discovered
        if depth > MAX_DEPTH:
            raise ValueError('node/depth limit')
        if not reserved:
            if discovered >= MAX_NODES:
                raise ValueError('node/depth limit')
            discovered += 1
        st = os.fstat(fd)
        mode = stat.S_IMODE(st.st_mode)
        if not _owner_matches(st.st_uid, expected_uid) or mode & 0o7022:
            raise ValueError('unsafe owner or mode')
        directory = stat.S_ISDIR(st.st_mode)
        if not directory and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1):
            raise ValueError('unsafe file type or link')
        if not mutable and (mode & (0o005 if directory else 0o004)) != (0o005 if directory else 0o004):
            raise ValueError('fence is not publicly readable/traversable')
        digest = None
        if not directory:
            total += st.st_size
            if total > MAX_TOTAL:
                raise ValueError('total size limit')
            digest = _read_hash(fd, st)
        entries[path] = {'metadata': _metadata(st), 'digest': digest, 'mutable': mutable, 'dir': directory}
        if directory and mutable:
            names = _bounded_names(fd, MAX_NODES - discovered)
            discovered += len(names)
            for name in names:
                if name in ('.', '..') or '/' in name:
                    raise ValueError('unsafe name')
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    examine(path + '/' + name, child, True, depth + 1, reserved=True)
                finally:
                    os.close(child)
            if _metadata(os.fstat(fd)) != _metadata(st) or _bounded_names(fd, len(names)) != names:
                raise ValueError('directory changed during scan')
    for path in FENCE_DIRS + FENCE_FILES:
        fd = _open_path(base, path, directory=path in FENCE_DIRS)
        try:
            if path in FENCE_FILES and not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('invalid fence file')
            examine(path, fd, False, 0)
        finally:
            os.close(fd)
    for path in ROOTS:
        fd = _open_path(base, path, directory=True)
        try:
            examine(path, fd, True, 0)
        finally:
            os.close(fd)
    return entries, total


def _target(entry):
    if entry['dir']:
        return 0o755
    return 0o755 if entry['metadata'][2] & 0o111 else 0o644


def _apply_one(base, path, entry, expected):
    parts = path.split('/')
    chain = []
    parent = base
    try:
        for i, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(parts) - 1 or entry['dir']:
                flags |= os.O_DIRECTORY
            fd = os.open(part, flags, dir_fd=parent)
            prefix = '/'.join(parts[:i + 1])
            chain.append((prefix, fd))
            parent = fd
            if prefix not in expected or _metadata(os.fstat(fd)) != expected[prefix]:
                raise ValueError('path identity changed before chmod')
        fd = chain[-1][1]
        st = os.fstat(fd)
        if not entry['dir'] and _read_hash(fd, st) != entry['digest']:
            raise ValueError('file content changed before chmod')
        target = _target(entry)
        if stat.S_IMODE(st.st_mode) == target:
            return 0, []
        # Verify the held chain again immediately before the mutation.
        for prefix, held in chain:
            if _metadata(os.fstat(held)) != expected[prefix]:
                raise ValueError('path changed before chmod')
        os.fchmod(fd, target)
        verified = {}
        effects = []
        for prefix, held in chain:
            previous = expected[prefix]
            current = _metadata(os.fstat(held))
            target_mode = (previous[2] & ~0o7777) | target if prefix == path else previous[2]
            if current[:2] != previous[:2] or current[2] != target_mode or current[3:8] != previous[3:8]:
                raise ValueError('unexpected chmod metadata effect')
            reopened = _open_path(base, prefix, directory=(prefix != path or entry['dir']))
            try:
                if _metadata(os.fstat(reopened)) != current or _metadata(os.fstat(held)) != current:
                    raise ValueError('path changed after chmod')
            finally:
                os.close(reopened)
            verified[prefix] = current
            if current[8] != previous[8]:
                effects.append((prefix, previous[8], current[8]))
        expected.update(verified)
        return 1, effects
    finally:
        for _, held in reversed(chain):
            os.close(held)


def _summary(entries, total):
    payload = [(p, e['dir'], e['metadata'][6], e['digest']) for p, e in sorted(entries.items())]
    return {'digest': hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest(),
            'count': len(entries), 'bytes': total}


def normalize(root, *, apply, expected_uid):
    fd = os.open(os.fspath(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before, total = _scan(fd, expected_uid)
        changed = 0
        effect_count = 0
        effect_hash = hashlib.sha256()
        expected = {p: e['metadata'] for p, e in before.items()}
        if not apply and any(e['mutable'] and stat.S_IMODE(e['metadata'][2]) != _target(e) for e in before.values()):
            raise ValueError('unnormalized permissions')
        if apply:
            for path, entry in before.items():
                if entry['mutable']:
                    count, event = _apply_one(fd, path, entry, expected)
                    changed += count
                    for effect in event:
                        effect_hash.update(json.dumps(effect, separators=(',', ':')).encode() + b'\n')
                        effect_count += 1
        after, after_total = _scan(fd, expected_uid)
        if before.keys() != after.keys() or total != after_total:
            raise ValueError('membership changed')
        for path, old in before.items():
            new = after[path]
            a, b = old['metadata'], new['metadata']
            expected_mode = _target(old) if apply and old['mutable'] else stat.S_IMODE(a[2])
            if (a[0:2] != b[0:2] or a[3:8] != b[3:8] or old['digest'] != new['digest']
                    or stat.S_IMODE(b[2]) != expected_mode
                    or b != expected[path]):
                raise ValueError('postflight metadata or content changed')
        return {'policy': POLICY, 'before': _summary(before, total),
                'after': _summary(after, after_total), 'changed': changed,
                'ctime_effects': {'count': effect_count, 'digest': effect_hash.hexdigest()}}
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--apply', action='store_true')
    group.add_argument('--audit', action='store_true')
    args = parser.parse_args()
    if args.apply and os.geteuid() != 0:
        parser.error('--apply requires root')
    try:
        print(json.dumps(normalize('/', apply=args.apply, expected_uid=0), sort_keys=True))
    except ValueError as exc:
        print('permission policy failed: ' + str(exc), file=sys.stderr)
        return 1
    except OSError:
        print('permission policy failed: filesystem operation rejected', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
