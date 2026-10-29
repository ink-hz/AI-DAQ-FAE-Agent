#!/usr/bin/env python3
"""Isolated DAQ native probe. No production env/DSN/keyring/network inputs.

Outer controller uses only Python stdlib and the local Docker daemon. Runtime
subcommands execute the SAME file read-only in the exact source image. They are
not application modules and never modify approved application/knowledge bytes.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import uuid

SOURCE_IMAGE = 'sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7'
PUBLIC_SHA = 'd5e2f0c91431171aba2061c14734c1747eb66886f1a2aa26b99577121617fa38'
PUBLIC_INTERNAL = '/run/daq-secrets/feedback-approval-keys-20261023.json'
MAIN_INTERNAL = '/app/data/knowledge/reviewed-thinking-prefix-20261028'
SDK_INTERNAL = '/app/data/knowledge/sdk-thinking-prefix-20261028'
RUNTIME_RELEASE = 'daq-062f0a7ae744'
OWNER_LABEL = 'org.orbbec.daq.native-probe'
UID = 21081
SUBJECT_A = 'b78b3205-2c86-424a-a217-775382c208bd'
SUBJECT_B = 'd46114bb-e01c-4f69-b67f-ef0d21f62e5e'
BINDING_A = '6dbedcf8-5263-493f-91f5-6324be037d7c'
BINDING_B = '5dc301b1-1f29-4618-832d-74b24355964f'
ORIGIN = 'https://native-probe.invalid'
COOKIE = '__Host-daq_enterprise_session'
CSRF = 'X-DAQ-Enterprise-CSRF'
GATES = tuple(f'V{i:02}' for i in range(1, 12))
FIXTURE_MARKER = 'DAQNATIVE736492'
PROVIDER_EVIDENCE = {'calls': 0, 'reads': []}


class ProbeError(RuntimeError):
    """Only fixed, non-sensitive categories may cross report boundary."""


def error_category(exc):
    if isinstance(exc, ProbeError) and re.fullmatch(r'[a-z_]+', str(exc)):
        return str(exc)
    if isinstance(exc, subprocess.TimeoutExpired):
        return 'command_timeout'
    if isinstance(exc, AssertionError):
        return 'assertion_failed'
    return 'internal_error'


def fresh_report():
    return {'format': 'daq-native-probe/v1', 'gates': {
        gate: {'status': 'NOT_EXECUTED'} for gate in GATES},
        'substitutions': ['synthetic_identity_http', 'synthetic_postgres',
                          'synthetic_credentials', 'synthetic_attachment_data'],
        'real_model_calls': 0, 'input_tokens': 0, 'output_tokens': 0,
        'quality_acceptance': 'NOT_EXECUTED', 'production': 'NOT_EXECUTED',
        'optional_capabilities': {'archive_protection': {'status': 'NOT_EXECUTED',
            'applicability': 'disabled_by_source_configuration'}},
        'transaction_failure_atomicity': {'status': 'NOT_EXECUTED', 'reason': 'normal_success_scenario_only'}}


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ControllerTerminated(BaseException):
    pass


@contextmanager
def controlled_signals():
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
    def terminate(signum, frame):
        # Further termination signals cannot interrupt the reconciliation finally.
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        raise ControllerTerminated()
    try:
        for sig in previous:
            signal.signal(sig, terminate)
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def boot_identity():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return None  # Engineering hosts cannot authorize reboot recovery.


def run_process(command, env, timeout):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=env, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()  # reap the independently sessioned client
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def docker_command(args, timeout=30):
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/nonexistent',
           'DOCKER_HOST': 'unix:///var/run/docker.sock', 'LC_ALL': 'C'}
    return run_process(['docker', *args], env, timeout)


class OwnedDocker:
    """Intent-first ownership journal. Cleanup ambiguity permanently blocks reuse."""
    def __init__(self, root, run=docker_command, *, recover=False):
        self.root = Path(root)
        self.path = self.root / 'journal.json'
        self.run = run
        if self.path.exists():
            if not recover:
                raise ProbeError('existing_attempt')
            self.data = json.loads(self.path.read_text())
        else:
            if recover:
                raise ProbeError('journal_missing')
            self.data = {'attempt': uuid.uuid4().hex, 'state': 'active', 'containers': [],
                         'boot_id': boot_identity(), 'journal_version': 2}
            self.save()

    def save(self):
        atomic_json(self.path, self.data)

    def inspect(self, target):
        result = self.run(['container', 'inspect', target])
        if result.returncode:
            # Explicit supported CLI protocols, including the first native
            # daemon's rc=1 / [] result. A nonzero code alone proves nothing.
            absent_errors = {prefix + target + ending
                for prefix in ('Error: No such container: ', 'Error response from daemon: No such container: ')
                for ending in ('', '\n')}
            if (result.returncode == 1 and result.stdout.strip() in {'', '[]'}
                    and result.stderr in absent_errors):
                return None
            raise ProbeError('inspect_unknown')
        value = json.loads(result.stdout)
        if not isinstance(value, list) or len(value) != 1:
            raise ProbeError('inspect_unknown')
        return value[0]

    def owned(self, item, obj):
        if (obj['Name'] != '/' + item['name'] or obj['Image'] != item['image']
                or obj['Config']['Labels'].get(OWNER_LABEL) != self.data['attempt']
                or obj['Config']['Labels'].get(OWNER_LABEL + '.role') != item['role']
                or (item['id'] is not None and obj['Id'] != item['id'])):
            raise ProbeError('ownership_mismatch')
        if not re.fullmatch(r'[a-f0-9]{64}', obj['Id']):
            raise ProbeError('ownership_mismatch')

    def create(self, role, image, flags, command):
        if self.data['state'] != 'active':
            raise ProbeError('attempt_blocked')
        item = {'role': role, 'name': 'daq-probe-' + self.data['attempt'] + '-' + role,
                'image': image, 'id': None, 'state': 'create_intent', 'create_outcome': 'unknown', 'policy_plan': policy_plan(flags)}
        self.data['containers'].append(item); self.save()
        args = ['create', '--name', item['name'], '--label', OWNER_LABEL + '=' + self.data['attempt'],
                '--label', OWNER_LABEL + '.role=' + role, '--pull', 'never', *flags, image, *command]
        failure = None
        try:
            result = self.run(args)
            item['create_outcome'] = 'unknown' if result.returncode else 'acknowledged'
            self.save()
            if result.returncode:
                failure = ProbeError('create_failed')
        except Exception as exc:
            failure = exc
        # Never create twice, even when CLI timed out after daemon accepted it.
        try:
            obj = self.inspect(item['name'])
            if obj is None:
                raise failure or ProbeError('create_absent')
            self.owned(item, obj)
        except BaseException:
            self.data['state'] = 'blocked'; self.save()
            raise
        item['id'] = obj['Id']; item['state'] = 'created'; item['create_outcome'] = 'materialized'; self.save()
        item['effective_policy'] = audit_plan(obj, item['policy_plan']); self.save()
        return item['id']

    def item(self, cid):
        return next(item for item in self.data['containers'] if item['id'] == cid)

    def start(self, cid):
        item = self.item(cid); obj = self.inspect(cid)
        if obj is None:
            raise ProbeError('owned_container_missing')
        self.owned(item, obj)
        audit_plan(obj, item['policy_plan'])
        if self.run(['start', cid]).returncode:
            raise ProbeError('start_failed')
        observed = self.inspect(cid)
        require(observed is not None, 'started_container_missing')
        self.owned(item, observed)
        item['effective_policy'] = audit_plan(observed, item['policy_plan'])
        item['runtime_state'] = {key: observed['State'].get(key) for key in ('Pid', 'Status', 'StartedAt', 'ExitCode')}
        item['state'] = 'started'; self.save()

    def wait(self, cid, timeout=120):
        result = self.run(['wait', cid], timeout=timeout)
        if result.returncode or result.stdout.strip() != '0':
            raise ProbeError('workload_failed')

    def stop(self, cid):
        item = self.item(cid); obj = self.inspect(cid)
        if obj is None:
            raise ProbeError('owned_container_missing')
        self.owned(item, obj)
        if obj['State']['Running']:
            self.run(['stop', '--time', '10', cid], timeout=20)
        obj = self.inspect(cid)
        if obj is None:
            raise ProbeError('owned_container_missing')
        self.owned(item, obj)
        if obj['State']['Running']:
            raise ProbeError('stop_unconfirmed')
        item['state'] = 'stopped'; self.save()

    def cleanup(self, *, after_reboot=False):
        errors = []
        persistence_errors = []
        self.data['cleanup_errors'] = errors
        self.data['journal_persistence_errors'] = persistence_errors

        def persist(stage, item=None):
            # Evidence IO has a separate failure boundary from already-owned
            # resource termination. A write failure never authorizes release.
            try:
                self.save()
            except BaseException as exc:
                persistence_errors.append({'stage': stage, 'name': item['name'] if item else None,
                    'category': 'journal_persistence_failed', 'exception_type': type(exc).__name__})
                self.data['state'] = 'blocked'

        changed_boot = bool(after_reboot and self.data.get('boot_id') and boot_identity()
                            and self.data['boot_id'] != boot_identity())
        if changed_boot:
            self.data['recovery_evidence'] = {'prior_boot_id': self.data['boot_id'],
                'observed_boot_id': boot_identity(), 'basis': 'kernel_boot_changed'}
            persist('reboot_evidence')
        for item in reversed(self.data['containers']):
            try:
                obj = self.inspect(item['name'])
                if obj is None:
                    if item['id'] is None and not changed_boot:
                        raise ProbeError('unresolved_create')
                    # An exact name absence is sufficient only if there was no
                    # recorded full ID, or that ID also is absent.
                    if item['id'] and self.inspect(item['id']) is not None:
                        raise ProbeError('ownership_mismatch')
                    item['state'] = 'absent'; item['cleanup_status'] = 'absent'; item.pop('cleanup_error', None)
                    persist('absence', item); continue
                self.owned(item, obj)
                cid = obj['Id']
                item['id'] = cid; persist('resolved_identity', item)
                if obj['State']['Running']:
                    self.run(['stop', '--time', '10', cid], timeout=20)
                stopped = self.inspect(cid)
                if stopped is None:
                    raise ProbeError('stop_unconfirmed')
                self.owned(item, stopped)
                if stopped['State']['Running']:
                    raise ProbeError('stop_unconfirmed')
                self.run(['rm', cid])
                if self.inspect(cid) is not None or self.inspect(item['name']) is not None:
                    raise ProbeError('absence_unconfirmed')
                item['state'] = 'absent'; item['cleanup_status'] = 'absent'; item.pop('cleanup_error', None)
                persist('removed_absence', item)
            except BaseException as exc:
                # One ambiguous object blocks release, not independently safe
                # cleanup of earlier owned writers/PG. Never widen ownership.
                item['cleanup_status'] = 'blocked'
                item['cleanup_error'] = error_category(exc)
                errors.append({'name': item['name'], 'category': item['cleanup_error']})
                self.data['state'] = 'blocked'
                persist('object_failure', item)
        self.data['state'] = 'blocked' if errors or persistence_errors else 'released'
        persist('final_state')
        if persistence_errors:
            # Even when every workload stopped, incomplete durable evidence
            # must return failure so the caller cannot release the global lease.
            self.data['state'] = 'blocked'
            raise ProbeError('cleanup_journal_persistence_failed')
        if errors:
            raise ProbeError('cleanup_' + errors[0]['category'])


def security_flags(pg_id):
    if not re.fullmatch(r'[a-f0-9]{64}', pg_id):
        raise ProbeError('network_identity_invalid')
    return ['--user', '21081:21081', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--log-driver', 'none',
            '--network', 'container:' + pg_id,
            '--tmpfs', '/tmp:noexec,nosuid,nodev,size=268435456,mode=0700,uid=21081,gid=21081']



def mount_identity(path):
    st = os.stat(path, follow_symlinks=False)
    require(not stat.S_ISLNK(st.st_mode), 'mount_source_symlink')
    return {'dev': st.st_dev, 'inode': st.st_ino, 'mode': st.st_mode, 'uid': st.st_uid, 'gid': st.st_gid}


def policy_plan(flags):
    def values(key): return [flags[i + 1] for i, value in enumerate(flags) if value == key]
    def first(key, default): return next(iter(values(key)), default)
    mounts = []
    for value in values('--mount'):
        fields = dict(piece.split('=', 1) if '=' in piece else (piece, True) for piece in value.split(','))
        mounts.append({'source': fields['source'], 'target': fields['target'], 'rw': not fields.get('readonly', False),
                       'identity': mount_identity(fields['source'])})
    return {'user': first('--user', ''), 'readonly': '--read-only' in flags, 'cap_drop': values('--cap-drop'),
            'security_opt': values('--security-opt'), 'network': first('--network', 'bridge'),
            'pid': first('--pid', ''), 'log': first('--log-driver', 'json-file'),
            'tmpfs': dict(value.split(':', 1) for value in values('--tmpfs')), 'mounts': mounts}


def audit_plan(obj, plan):
    host = obj['HostConfig']
    require(obj['Config']['User'] == plan['user'] and host['ReadonlyRootfs'] == plan['readonly']
            and (host.get('CapDrop') or []) == plan['cap_drop'] and not host.get('CapAdd')
            and (host.get('SecurityOpt') or []) == plan['security_opt'] and not host.get('GroupAdd')
            and not host.get('Privileged') and not host.get('PortBindings')
            and host['NetworkMode'] == plan['network'] and host.get('PidMode', '') == plan['pid']
            and host['LogConfig']['Type'] == plan['log'], 'effective_daemon_policy_mismatch')
    expected_tmp = {key: sorted(value.split(',')) for key, value in plan['tmpfs'].items()}
    actual_tmp = {key: sorted(value.split(',')) for key, value in (host.get('Tmpfs') or {}).items()}
    require(actual_tmp == expected_tmp, 'effective_tmpfs_policy_mismatch')
    mounts = {item['Destination']: item for item in obj['Mounts'] if item.get('Type') != 'tmpfs'}
    require(set(mounts) == {item['target'] for item in plan['mounts']}, 'effective_mount_set_mismatch')
    for item in plan['mounts']:
        actual = mounts[item['target']]
        require(actual['Source'] == item['source'] and actual['RW'] == item['rw']
                and mount_identity(actual['Source']) == item['identity'], 'effective_mount_source_mismatch')
    return {'user': obj['Config']['User'], 'readonly': host['ReadonlyRootfs'],
            'network': host['NetworkMode'], 'pid': host.get('PidMode', ''),
            'cap_drop': host.get('CapDrop') or [], 'security_opt': host.get('SecurityOpt') or [],
            'log': host['LogConfig']['Type'], 'tmpfs': dict(host.get('Tmpfs') or {}),
            'mounts': [{'source': item['Source'], 'target': item['Destination'], 'rw': item['RW'],
                        'identity': mount_identity(item['Source'])} for item in mounts.values()]}


def audit_candidate(obj, pg_id):
    config = obj['Config']; host = obj['HostConfig']
    require(config['User'] == '21081:21081' and host['ReadonlyRootfs'] is True
            and host['CapDrop'] == ['ALL'] and not host.get('CapAdd') and not host.get('GroupAdd')
            and host['Privileged'] is False and not host.get('PortBindings')
            and host['NetworkMode'] == 'container:' + pg_id
            and host['LogConfig']['Type'] == 'none'
            and host['SecurityOpt'] == ['no-new-privileges:true'], 'daemon_policy_mismatch')
    expected = {MAIN_INTERNAL: False, SDK_INTERNAL: False, '/run/daq-secrets': False,
                '/app/data/attachments': True, '/app/data/daq_traces.jsonl': True, '/probe.py': False}
    actual = {mount['Destination']: mount['RW'] for mount in obj['Mounts'] if mount.get('Type') != 'tmpfs'}
    require(actual == expected, 'daemon_mount_policy_mismatch')


ASSET_HELPER_SHA = '3c513406ae0336ac6e5c0bd8cae6dbc0e0c670b02b307b51726b3afb7190c2a8'
ASSET_HELPER_COMMIT = 'f03e98097e8f44e2ad49b379120710b55af3f066'
ASSET_FILE_LIMIT = 16 * 1024**2
ASSET_TOTAL_LIMIT = 32 * 1024**2
ASSET_NODE_LIMIT = 4096
ASSET_DEPTH_LIMIT = 32
SYNTHETIC_DISK_BUDGET = 256 * 1024**2
DISK_RESERVE = 1024 * 1024**2
TRUSTED_UID = 0
_ASSET_HELPER = None


def asset_helpers():
    global _ASSET_HELPER
    if _ASSET_HELPER is None:
        import types
        path = Path(__file__).with_name('security_assets.py')
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == ASSET_HELPER_SHA, 'asset_helper_identity_mismatch')
        module = types.ModuleType('daq_reviewed_security_assets')
        module.__file__ = str(path)
        exec(compile(raw, str(path), 'exec'), module.__dict__)
        _ASSET_HELPER = module
    return _ASSET_HELPER


def strict_path(value):
    try:
        asset_helpers()._parts(str(value))
    except Exception:
        raise ProbeError('canonical_path_required') from None
    require(',' not in str(value), 'canonical_path_required')
    return Path(value)


class BoundedTree:
    """Stable native handoff v1; reuses reviewed same-checkout copy primitives.

    Root-controlled prepared RO roots; synthetic RW roots require their root-
    controlled parent and stopped writers. Budget/identity/membership preflight
    is complete before any destination publication or Docker creation.
    """
    def __init__(self, path, *, trusted):
        self.path = strict_path(path); self.h = asset_helpers(); self.fd = None
        parent, name = self.h._parent(str(self.path), trusted=True)
        try:
            self.fd = self.h._open_dir(str(self.path), trusted=trusted)
            self.identity = self.h._metadata(os.fstat(self.fd))
            require(self.identity == self.h._metadata(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                    'source_root_identity_changed')
        finally:
            os.close(parent)
        try:
            self.members = self.h._snapshot_target_tree(str(self.path))
            require(self.members[''] == self.identity and len(self.members) <= ASSET_NODE_LIMIT,
                    'asset_budget_or_identity_invalid')
            self.total = 0; self.expected = {}
            for rel, meta in self.members.items():
                require(rel.count('/') <= ASSET_DEPTH_LIMIT, 'asset_depth_budget_exceeded')
                if trusted:
                    require(meta['uid'] == TRUSTED_UID and not meta['mode'] & 0o022,
                            'asset_owner_or_write_access_invalid')
                if rel and not rel.endswith('/'):
                    self.total += meta['size']
                    require(meta['size'] <= ASSET_FILE_LIMIT and self.total <= ASSET_TOTAL_LIMIT,
                            'asset_byte_budget_exceeded')
            # All sizes/types/ownership are validated before any body read.
            for rel, meta in self.members.items():
                if rel and not rel.endswith('/'):
                    raw, st = self.h._read_regular(str(self.path / rel), ASSET_FILE_LIMIT)
                    require(self.h._metadata(st) == meta, 'source_file_identity_changed')
                    self.expected[rel] = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            self.verify()
        except BaseException:
            os.close(self.fd); self.fd = None; raise

    def __enter__(self): return self
    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd); self.fd = None

    def verify(self):
        records = {rel: {**rec, 'source': self.members[rel]} for rel, rec in self.expected.items()}
        dirs = {rel: meta for rel, meta in self.members.items() if not rel or rel.endswith('/')}
        self.h._verify_tree(self.fd, records, directories=dirs)
        require(self.h._snapshot_target_tree(str(self.path)) == self.members, 'source_membership_changed')

    def copy(self, destination, *, owner, mode):
        destination = strict_path(destination)
        self.verify()
        parent, name = self.h._parent(str(destination), trusted=True)
        try:
            # Physical parent ancestry rejects bind aliases as well as names.
            cursor = os.dup(parent)
            try:
                while True:
                    st = os.fstat(cursor)
                    require((st.st_dev, st.st_ino) != (self.identity['dev'], self.identity['inode']),
                            'destination_inside_source')
                    up = os.open('..', self.h.DIR_FLAGS, dir_fd=cursor)
                    same = (st.st_dev, st.st_ino) == (os.fstat(up).st_dev, os.fstat(up).st_ino)
                    os.close(cursor); cursor = up
                    if same: break
            finally: os.close(cursor)
            disk = os.fstatvfs(parent)
            require(disk.f_bavail * disk.f_frsize >= self.total + SYNTHETIC_DISK_BUDGET + DISK_RESERVE,
                    'disk_budget_insufficient')
            out = self.h._mkdir_at(parent, name, owner, UID, 0o750 if owner == 0 else 0o700)
            try:
                records = self.h._tree(self.fd, out, self.expected, owner, UID, mode, [0, 0])
                self.verify()
                identity = self.h._metadata(os.fstat(out))
                require(identity == self.h._metadata(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                        'destination_identity_changed')
            finally: os.close(out)
        finally: os.close(parent)
        return {'source': self.members, 'files': records,
                'destination': self.h._snapshot_target_tree(str(destination))}


def validate_release_handoff(tree, kind):
    # Stable native handoff v1, derived from the fixed current-source inventory
    # in design section 9. A drift needs review; it is never auto-expanded.
    count, size = {'main': (2, 9412265), 'sdk': (15, 1329624)}[kind]
    require(len(tree.expected) == count and tree.total == size, 'release_handoff_inventory_mismatch')
    directories = {'', 'releases/'}
    for path, rec in tree.expected.items():
        if path == 'active.json': continue
        match = re.fullmatch(r'releases/([a-f0-9]{64})/manifest.json', path)
        require(match is not None and rec['sha256'] == match.group(1), 'release_handoff_member_invalid')
        directories.add('releases/' + match.group(1) + '/')
    require({path for path in tree.members if not path or path.endswith('/')} == directories,
            'release_handoff_directory_mismatch')
    raw, _ = tree.h._read_regular(str(tree.path / 'active.json'), 4096)
    pointer = json.loads(raw)
    require(set(pointer) == {'release_id'} and
            'releases/' + str(pointer['release_id']) + '/manifest.json' in tree.expected,
            'release_handoff_active_invalid')
    tree.verify()


def tree_digest(root):
    try:
        with BoundedTree(root, trusted=False) as tree:
            return hashlib.sha256(json.dumps({'members': tree.members, 'files': tree.expected}, sort_keys=True).encode()).hexdigest()
    except Exception:
        raise ProbeError('asset_inventory_invalid') from None


def identity_valid(status):
    return (all(status.get(key, '').split() == [str(UID)] * 4 for key in ('Uid', 'Gid'))
            and all(status.get(key, '').strip('0') == '' for key in ('CapEff', 'CapPrm', 'CapBnd'))
            and all(key in status for key in ('CapEff', 'CapPrm', 'CapBnd'))
            and status.get('NoNewPrivs') == '1'
            and set(status.get('Groups', '').split()) <= {str(UID)})


def require(condition, code):
    if not condition:
        raise ProbeError(code)


def gate_result(checks, not_executed=()):
    return {'status': 'PARTIAL' if not_executed else 'PASS',
            'checks': list(checks), 'not_executed': list(not_executed)}


def bind_mount(source, target, readonly):
    require(not any(c in str(source) + target for c in ',\n\r') and Path(source).is_absolute()
            and target.startswith('/'), 'mount_invalid')
    return ['--mount', f'type=bind,source={source},target={target}' + (',readonly' if readonly else '')]


def synthetic_environment():
    return {
        'PYTHONPATH': '/app', 'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': '/tmp',
        'DAQ_ENVIRONMENT': 'production', 'DAQ_RUNTIME_RELEASE': RUNTIME_RELEASE,
        'DAQ_PROVIDER_MODE': 'anthropic', 'DAQ_ANTHROPIC_API_KEY': 'synthetic-never-real',
        'DAQ_ANTHROPIC_BASE_URL': 'http://127.0.0.1:9',
        'DAQ_ANTHROPIC_MODEL': 'claude-opus-5-5', 'DAQ_VISION_ENABLED': 'true',
        'DAQ_DATABASE_URL': 'postgresql://daq_probe@127.0.0.1:5432/daq_probe',
        'DAQ_PLATFORM_IDENTITY_ENABLED': 'true',
        'DAQ_PLATFORM_IDENTITY_BASE_URL': 'http://127.0.0.1:8082',
        'DAQ_PLATFORM_PUBLIC_ORIGIN': ORIGIN, 'DAQ_PLATFORM_ACCESS_SCOPE': 'enterprise_members',
        'DAQ_PLATFORM_SESSION_KEYRING_FILE': '/run/daq-secrets/session-keyring.json',
        'DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE': '/run/daq-secrets/content-keyring.json',
        'DAQ_KNOWLEDGE_ENTITLEMENTS_FILE': '/run/daq-secrets/entitlements.json',
        'DAQ_KNOWLEDGE_APPROVAL_KEYS_FILE': PUBLIC_INTERNAL,
        'DAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256': PUBLIC_SHA,
        'DAQ_KNOWLEDGE_RELEASE_ROOT': MAIN_INTERNAL, 'DAQ_SDK_RELEASE_ROOT': SDK_INTERNAL,
        'DAQ_TRACE_LOG_PATH': '/app/data/daq_traces.jsonl',
        'DAQ_ATTACHMENT_STORAGE_DIR': '/app/data/attachments',
        'DAQ_PLATFORM_TASK_ENABLED': 'false', 'DAQ_ATTACHMENT_ARCHIVE_ENABLED': 'false',
    }


def identity_server(port=8082):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class IdentityHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != '/probe-metrics':
                self.send_error(404); return
            raw = json.dumps(PROVIDER_EVIDENCE).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

        def do_POST(self):
            code = 403
            body = {}
            try:
                length = int(self.headers.get('Content-Length', '0'))
                require(0 < length < 4096, 'identity_request_invalid')
                request = json.loads(self.rfile.read(length))
                exchange = self.path == '/api/v1/internal/agent-launch/exchange'
                if exchange:
                    require(set(request) == {'code'} and request['code'] in {'a' * 43, 'b' * 43},
                            'identity_request_invalid')
                    first = request['code'] == 'a' * 43
                else:
                    paths = [f'/api/v1/internal/agent-bindings/{binding}/validate'
                             for binding in (BINDING_A, BINDING_B)]
                    require(self.path in paths and request == {'agent_id': 'ai-daq-fae-agent'},
                            'identity_request_invalid')
                    first = self.path == paths[0]
                subject, binding = (SUBJECT_A, BINDING_A) if first else (SUBJECT_B, BINDING_B)
                body = {'contract_version': 'orbbec-fae-identity/v1', 'subject_id': subject,
                        'subject_type': 'enterprise_member', 'internal_user_id': subject,
                        'identity_binding_id': binding, 'agent_id': 'ai-daq-fae-agent',
                        'display_name': 'Synthetic probe', 'partner_display_name': None}
                if not exchange:
                    body['active'] = True
                code = 200
            except Exception:
                pass
            raw = json.dumps(body).encode()
            self.send_response(code); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
    return ThreadingHTTPServer(('127.0.0.1', port), IdentityHandler)


# Everything below runs inside the pinned application image unless explicitly
# named controller. No ASGI test client or in-memory repository is used here.
def runtime_app(scripted=False):
    import threading
    import logging
    import uvicorn
    warning_path = Path('/tmp/app-warnings.json')
    persistent_warning = Path('/app/data/attachments') / ('.probe-' + os.environ['DAQ_NATIVE_PROBE_PHASE'] + '-warnings.json')
    atomic_json(warning_path, {'warning_count': 0})
    atomic_json(persistent_warning, {'warning_count': 0})
    class WarningCapture(logging.Handler):
        def __init__(self):
            super().__init__(logging.WARNING)
            self.count = 0
        def emit(self, record):
            self.count += 1
            atomic_json(warning_path, {'warning_count': self.count})
            atomic_json(persistent_warning, {'warning_count': self.count})
    logging.getLogger().addHandler(WarningCapture())
    server = identity_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        if scripted:
            # Explicit provider boundary only; production create_app and all
            # verifiers/repositories execute unchanged, exactly once.
            from daq_fae.provider_adapter import DaqAnthropicAdapter
            DaqAnthropicAdapter.chat = scripted_chat
        from daq_fae.app import app
        require(app.state.local_state is None, 'local_sqlite_unexpected')
        uvicorn.run(app, host='127.0.0.1', port=8081, log_level='warning', access_log=False)
    finally:
        server.shutdown(); thread.join(timeout=5); server.server_close()


def checked_read_receipt(payload, source_id, locator, expected_text):
    require(isinstance(payload, dict) and payload.get('status') == 'ok', 'read_receipt_not_ok')
    value = payload.get('content', {})
    require(value.get('source_id') == source_id and value.get('locator') == locator
            and value.get('text', '').strip() == expected_text.strip(), 'read_receipt_content_mismatch')
    return {'source_id': source_id, 'locator': locator,
            'text_sha256': hashlib.sha256(expected_text.strip().encode()).hexdigest()}


def scripted_chat(self, messages, tools=None, required_tool=None):
    PROVIDER_EVIDENCE['calls'] += 1
    require(PROVIDER_EVIDENCE['calls'] <= 64, 'synthetic_provider_call_limit')
    if required_tool == 'submit_daq_request_schema':
        name = required_tool
        arguments = {'confidence': 'high', 'attachment_dependency': 'required_for_answer',
                     'goals': [{'kind': 'attachment', 'capability': 'search_attachments'}]}
    else:
        previous = [item for item in messages if item.get('role') == 'tool']
        if not previous:
            name, arguments = 'search_attachments', {'query': FIXTURE_MARKER}
        else:
            search = next((item for item in previous if item.get('tool_call_id') == 'probe-search_attachments'), None)
            require(search is not None, 'scripted_search_receipt_missing')
            payload = json.loads(search['content'])
            require(payload.get('status') == 'ok', 'scripted_search_not_ok')
            hits = payload.get('content', {}).get('hits', [])
            expected_text = 'status=ready\n' + FIXTURE_MARKER
            locator = {'line_start': 1, 'line_end': 2}
            require(len(hits) == 1 and hits[0].get('locator') == locator
                    and hits[0].get('text', '').strip() == expected_text, 'scripted_search_content_mismatch')
            read = next((item for item in previous if item.get('tool_call_id') == 'probe-read'), None)
            if read is None:
                name, arguments = 'read_attachment', {'source_id': hits[0]['source_id'], 'locator': locator}
            else:
                evidence = checked_read_receipt(json.loads(read['content']), hits[0]['source_id'], locator, expected_text)
                PROVIDER_EVIDENCE['reads'].append(evidence)
                name, arguments = 'submit_answer', {
                    'outcome': 'safe_abstained', 'missing': 'Synthetic engineering fixture has no product conclusion.',
                    'next_steps': 'Retain the attachment evidence for the next turn.'}
    yield {'type': 'tool_call', 'id': 'probe-read' if name == 'read_attachment' else 'probe-' + name,
           'name': name, 'arguments': arguments}
    yield {'type': 'stop', 'stop_reason': 'tool_use', 'usage': None}


def http_client(subject='a', saved=None):
    import httpx
    client = httpx.Client(base_url='http://127.0.0.1:8081', timeout=60, trust_env=False)
    if saved is None:
        response = client.post('/enterprise/session', json={'code': subject * 43}, headers={'Origin': ORIGIN})
        require(response.status_code == 201, 'http_launch_failed')
        # Secure __Host cookie remains Secure on the wire. HTTP loopback transport
        # is the isolated equivalent of the reverse proxy's internal HTTP hop;
        # supply exactly the issued cookie, never synthesize or insert DB sessions.
        token = response.cookies.get(COOKIE)
        csrf = response.json()['csrf_token']
        require(bool(token) and 'Secure' in response.headers['set-cookie']
                and 'HttpOnly' in response.headers['set-cookie'], 'cookie_contract_failed')
        saved = {'cookie': token, 'csrf': csrf}
    client.headers.update({'Cookie': COOKIE + '=' + saved['cookie'], 'Origin': ORIGIN, CSRF: saved['csrf']})
    return client, saved


def runtime_startup():
    import httpx
    from daq_fae.knowledge.code_identity import daq_code_sha
    with httpx.Client(base_url='http://127.0.0.1:8081', timeout=3, trust_env=False) as client:
        deadline = time.monotonic() + 45
        while True:
            try:
                response = client.get('/health')
                break
            except httpx.ConnectError:
                if time.monotonic() >= deadline:
                    raise ProbeError('startup_timeout') from None
                time.sleep(.1)  # Bounded readiness wait, never retries a gate/model call.
        require(response.status_code == 200, 'health_http_failed')
        health = response.json()
        require(all(health.get(k) == v for k, v in {
            'status': 'ok', 'environment': 'production', 'runtime_release': RUNTIME_RELEASE,
            'provider_mode': 'anthropic', 'platform_identity_enabled': True,
            'platform_task_enabled': False, 'attachment_archive_enabled': False,
            'session_persistence': 'postgres_daq_authenticated', 'local_dev_only': False,
        }.items()), 'production_health_contract_failed')
        require(bool(health.get('knowledge_release')) and bool(health.get('sdk_knowledge_release'))
                and health['attachment_archive']['ready'] is True, 'signed_startup_failed')
        import psycopg
        with psycopg.connect(os.environ['DAQ_DATABASE_URL']) as connection:
            marker = connection.execute('select agent_id, database_name=current_database() '
                                        'from daq_installation_identity where singleton').fetchone()
            require(marker == ('ai-daq-fae-agent', True), 'database_marker_failed')
        require(not list(Path('/app/data').glob('*.sqlite*')), 'local_sqlite_unexpected')
        require(json.loads(Path('/proc/1/root/tmp/app-warnings.json').read_text())['warning_count'] == 0,
                'application_warning_observed')
    return {'runtime_release': RUNTIME_RELEASE, 'code_sha256': daq_code_sha(),
            'upstream_revision': json.loads(Path('/app/upstream-source.json').read_text())['revision'],
            'knowledge_release': health['knowledge_release'], 'sdk_release': health['sdk_knowledge_release']}


def runtime_auth():
    import httpx
    with httpx.Client(base_url='http://127.0.0.1:8081', trust_env=False) as anon:
        for path in ('/history', '/authenticated/conversations', '/attachments/' + 'a' * 43):
            require(anon.get(path).status_code == 401, 'anonymous_access_allowed')
        require(anon.post('/attachments').status_code == 401, 'anonymous_upload_allowed')
        require(anon.post('/enterprise/session', json={'code': 'a' * 43}).status_code == 403,
                'origin_contract_failed')
        # Fetch every deployed static file by its native URL; compare complete
        # uncompressed body with the exact image bytes, never HTTP 200 alone.
        count = 0
        for path in sorted(Path('/app/webui/dist').rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to('/app/webui/dist').as_posix()
            url = '/daq' if relative == 'index.html' else '/daq/' + relative
            response = anon.get(url)
            require(response.status_code == 200 and response.content == expected_static(relative, path.read_bytes()), 'static_bytes_mismatch')
            count += 1
        require(count > 1, 'static_assets_missing')
    client, _ = http_client()
    with client:
        require(client.get('/enterprise/session').status_code == 200, 'session_http_failed')
        require(client.get('/authenticated/conversations').status_code == 200, 'authenticated_history_failed')
        require(client.post('/attachments', headers={CSRF: 'invalid'}).status_code == 403,
                'csrf_contract_failed')
    return {'static_files_verified': count}


def runtime_filesystem():
    import errno
    code = Path('/app/daq_fae/app.py')
    targets = [code, Path('/app/prompts/daq_schema_extraction.md'),
               Path(MAIN_INTERNAL) / 'active.json', Path(SDK_INTERNAL) / 'active.json',
               Path(PUBLIC_INTERNAL), Path('/run/daq-secrets/session-keyring.json'),
               Path('/run/daq-secrets/content-keyring.json')]
    for target in targets:
        require(target.is_file(), 'readonly_target_missing')
        before = hashlib.sha256(target.read_bytes()).hexdigest()
        operations = [lambda p=target: p.open('ab').close(), lambda p=target: p.unlink(),
                      lambda p=target: p.rename(p.with_name(p.name + '.probe-rename'))]
        for operation in operations:
            try:
                operation()
            except OSError as exc:
                require(exc.errno in {errno.EROFS, errno.EACCES, errno.EPERM}, 'readonly_unexpected_error')
            else:
                raise ProbeError('readonly_write_allowed')
        require(hashlib.sha256(target.read_bytes()).hexdigest() == before, 'readonly_bytes_changed')
    for directory in (Path('/app/data/attachments'), Path('/tmp')):
        file = directory / ('probe-rw-' + uuid.uuid4().hex)
        fd = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(b'synthetic-rw'); stream.flush(); os.fsync(stream.fileno())
        os.chmod(file, 0o600)
        dest = file.with_name(file.name + '-renamed'); file.rename(dest)
        require(dest.stat().st_uid == UID and stat.S_IMODE(dest.stat().st_mode) == 0o600,
                'rw_metadata_failed')
        dest.unlink()
        require(stat.S_IMODE(directory.stat().st_mode) == 0o700, 'rw_directory_mode_failed')
    try:
        Path('/app/probe-forbidden').write_text('synthetic')
    except OSError:
        pass
    else:
        raise ProbeError('rootfs_write_allowed')
    return {'ro_operations': len(targets) * 3, 'rw_locations': 2}



def crypto_seed():
    from src.platform_identity.service import SessionTokenKeyring
    from src.storage.authenticated_conversations import ConversationContentCodec
    session = SessionTokenKeyring.from_file(Path('/run/daq-secrets/session-keyring.json'))
    codec = ConversationContentCodec.from_file(Path('/run/daq-secrets/content-keyring.json'))
    sealed = codec.seal_state('daq:root-key-fixture', {'fixture': 'root-origin'})
    atomic_json(Path('/probe-output/crypto.json'), {
        'ciphertext': base64.b64encode(sealed.ciphertext).decode(), 'key_version': sealed.key_version,
        'state_sha256': base64.b64encode(sealed.state_sha256).decode(),
        'session_digest': base64.b64encode(session.digest('session', 'synthetic-key-token')[1]).decode()})


def crypto_verify():
    from src.platform_identity.service import SessionTokenKeyring
    from src.storage.authenticated_conversations import ConversationContentCodec, SealedConversationState
    data = json.loads(Path('/probe-output/crypto.json').read_text())
    codec = ConversationContentCodec.from_file(Path('/run/daq-secrets/content-keyring.json'))
    session = SessionTokenKeyring.from_file(Path('/run/daq-secrets/session-keyring.json'))
    sealed = SealedConversationState(ciphertext=base64.b64decode(data['ciphertext']),
                                    key_version=data['key_version'],
                                    state_sha256=base64.b64decode(data['state_sha256']))
    require(codec.unseal_state('daq:root-key-fixture', sealed) == {'fixture': 'root-origin'},
            'old_ciphertext_decryption_failed')
    require(base64.b64encode(session.digest('session', 'synthetic-key-token')[1]).decode() == data['session_digest'],
            'old_session_digest_changed')
    return {'old_ciphertext_decrypted': True, 'session_digest_matched': True}


def runtime_keys():
    from src.platform_identity.service import SessionTokenKeyring
    from src.storage.authenticated_conversations import ConversationContentCodec
    from daq_fae.knowledge.entitlements import KnowledgeEntitlements
    SessionTokenKeyring.from_file('/run/daq-secrets/session-keyring.json')
    codec = ConversationContentCodec.from_file('/run/daq-secrets/content-keyring.json')
    sealed = codec.seal_state('daq:synthetic-key-probe', {'fixture': True})
    require(codec.unseal_state('daq:synthetic-key-probe', sealed) == {'fixture': True}, 'key_crypto_failed')
    KnowledgeEntitlements(Path('/run/daq-secrets/entitlements.json'))
    negatives = [(SessionTokenKeyring.from_file, '/probe-negative/' + suffix + '-session.json')
                 for suffix in ('root', 'group')]
    negatives += [(ConversationContentCodec.from_file, '/probe-negative/' + suffix + '-content.json')
                  for suffix in ('root', 'group')]
    negatives.append((KnowledgeEntitlements, Path('/probe-negative/group-entitlements.json')))
    for loader, path in negatives:
        try:
            loader(path)
        except (ValueError, RuntimeError):
            pass
        else:
            raise ProbeError('strict_key_negative_accepted')
    return {'loader_negatives': len(negatives), 'crypto_roundtrip': True, **crypto_verify()}


def runtime_trace(phase, negative=False):
    import logging
    from src.agent.tracing import JsonlTraceSink, TraceSpan
    path = Path('/app/data/daq_traces.jsonl')
    prefix = path.read_bytes()
    warnings = []
    class Capture(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings.append(record.levelno)
    logger = logging.getLogger('src.agent.tracing'); handler = Capture(); logger.addHandler(handler)
    try:
        span = TraceSpan(trace_id='synthetic-native-' + phase, span_id='synthetic-span',
                         parent_span_id=None, node='native_probe', started_at='2026-10-29T00:00:00Z')
        JsonlTraceSink(path).emit_span(span, is_root=True)
    finally:
        logger.removeHandler(handler)
    after = path.read_bytes()
    if negative:
        require(bool(warnings) and after == prefix, 'readonly_trace_failure_hidden')
        return {'status': 'EXPECTED_REFUSAL', 'write_gate': 'FAIL', 'warning_observed': True}
    require(not warnings and after.startswith(prefix) and len(after) > len(prefix), 'trace_append_failed')
    record = json.loads(after[len(prefix):])
    require(record['trace_id'] == span.trace_id and record['type'] == 'root' and record['is_root'],
            'trace_schema_failed')
    return {'prefix_bytes': len(prefix), 'prefix_sha256': hashlib.sha256(prefix).hexdigest(),
            'appended_bytes': len(after) - len(prefix), 'owner': path.stat().st_uid,
            'mode': oct(stat.S_IMODE(path.stat().st_mode))}


def expected_static(relative, content):
    if relative == 'index.html':
        return content.decode().replace('<head>', '<head><base href="/daq/">'
                                       '<meta name="fae-browser-base" content="/daq">'
                                       '<meta name="fae-api-base" content="/daq/api">', 1).encode()
    return content


def upload_fixtures():
    import io
    import fitz
    import docx
    import openpyxl
    from PIL import Image, ImageDraw
    text = ('status=ready\n' + FIXTURE_MARKER + '\n').encode()
    pdf = fitz.open(); page = pdf.new_page(); page.insert_text((72, 72), text.decode())
    pdf_bytes = pdf.tobytes(); pdf.close()
    document = docx.Document(); document.add_paragraph(text.decode()); buffer = io.BytesIO(); document.save(buffer)
    docx_bytes = buffer.getvalue()
    workbook = openpyxl.Workbook(); workbook.active.append(['status', 'ready', FIXTURE_MARKER]); buffer = io.BytesIO(); workbook.save(buffer)
    xlsx_bytes = buffer.getvalue()
    image = Image.new('RGB', (400, 200), 'white'); draw = ImageDraw.Draw(image)
    for y in (20, 60, 100, 140):
        draw.text((20, y), 'NATIVE PROBE READY', fill='black')
    image = image.resize((1600, 800), Image.Resampling.NEAREST)
    buffer = io.BytesIO(); image.save(buffer, format='PNG', compress_level=0)
    require(len(buffer.getvalue()) > 1024 * 1024, 'spool_fixture_too_small')
    return [('probe.txt', text, 'text/plain'), ('probe.csv', ('status,value\nready,' + FIXTURE_MARKER + '\n').encode(), 'text/csv'),
            ('probe.pdf', pdf_bytes, 'application/pdf'),
            ('probe.docx', docx_bytes, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
            ('probe.xlsx', xlsx_bytes, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('probe.png', buffer.getvalue(), 'image/png')]


def fixture_contract(name, content, *, owner=SUBJECT_A):
    extension = name.rsplit('.', 1)[1]
    locator = {'txt': {'line_start': 1, 'line_end': len(content.decode().splitlines())} if extension == 'txt' else {},
               'csv': {'sheet': 'CSV', 'cell_range': 'A1:B2'}, 'pdf': {'page': 1},
               'docx': {'paragraph_range': '1-1'}, 'xlsx': {'sheet': 'Sheet', 'cell_range': 'A1:C1'},
               'png': {'region_label': 'ocr'}}[extension]
    expected = (content.decode().strip() if extension == 'txt' else
                'status\tvalue\nready\t' + FIXTURE_MARKER if extension == 'csv' else
                'A1\tstatus\nB1\tready\nC1\t' + FIXTURE_MARKER if extension == 'xlsx' else
                'NATIVE PROBE READY' if extension == 'png' else 'status=ready\n' + FIXTURE_MARKER)
    return {'name': name, 'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content),
            'owner': owner, 'locator': locator, 'expected_text': expected, 'image': extension == 'png'}


def consume_fixture(store, aid, contract):
    from src.agent.attachment_tools import AttachmentTools
    from src.attachments.models import AttachmentDescriptor
    manifest = store.get(aid)
    raw = store.read_bytes(aid)
    require(manifest.owner_subject_id == contract['owner'] and manifest.status == 'ready'
            and len(raw) == contract['size'] and manifest.sha256 == contract['sha256']
            and hashlib.sha256(raw).hexdigest() == contract['sha256'], 'consumer_original_mismatch')
    chunks = store.read_chunks(aid)
    require(len(chunks) == 1, 'consumer_chunk_count_mismatch')
    chunk = chunks[0]
    require(chunk.source_id == manifest.source_id and chunk.locator.as_dict() == contract['locator']
            and chunk.content_sha256 == hashlib.sha256(chunk.text.encode()).hexdigest(),
            'consumer_chunk_identity_mismatch')
    expected = contract['expected_text']
    require((expected in chunk.text if contract['image'] else chunk.text.strip() == expected),
            'consumer_chunk_text_mismatch')
    if contract['image']:
        import io
        from PIL import Image
        normalized = store.read_normalized_image(aid)
        require(len(normalized) == manifest.normalized_size_bytes and 'ocr_unavailable' not in manifest.warnings,
                'consumer_normalized_size_or_ocr_mismatch')
        try:
            with Image.open(io.BytesIO(normalized)) as image:
                image.load()
                require(image.size == (manifest.normalized_width, manifest.normalized_height)
                        and {'PNG': 'image/png', 'JPEG': 'image/jpeg'}.get(image.format) == manifest.normalized_media_type,
                        'consumer_normalized_metadata_mismatch')
        except (OSError, ValueError):
            raise ProbeError('consumer_normalized_decode_failed') from None
    tools = AttachmentTools([AttachmentDescriptor.from_manifest(manifest)], store)
    receipt = tools.dispatch('read_attachment', {'source_id': manifest.source_id, 'locator': contract['locator']})
    # The real consumer must return exactly the validated chunk; truthy/ready is insufficient.
    read = checked_read_receipt(receipt.to_payload(), manifest.source_id, contract['locator'], chunk.text)
    return {'attachment_id': aid, **read, 'original_sha256': contract['sha256']}


def runtime_store():
    from src.attachments.store import AttachmentStore
    from src.attachments.models import AttachmentLimits
    return AttachmentStore(Path('/app/data/attachments'), AttachmentLimits())


def provider_metrics():
    import httpx
    response = httpx.get('http://127.0.0.1:8082/probe-metrics', timeout=3, trust_env=False)
    require(response.status_code == 200, 'provider_metrics_unavailable')
    return response.json()


def process_record(pid, cmd, status, starttime, namespace):
    role = ('main' if pid == 1 else 'spawn' if b'multiprocessing.spawn' in cmd else 'tesseract')
    if pid == 1:
        require(b'/probe.py' in cmd.split(b'\0') and b'_runtime' in cmd.split(b'\0')
                and ({b'app', b'scripted-app'} & set(cmd.split(b'\0'))), 'main_entry_identity_mismatch')
    require(identity_valid(status), 'process_identity_failed')
    require(starttime.isdigit() and all(re.fullmatch(kind + r':\[\d+\]', namespace[kind]) for kind in ('pid', 'net')),
            'process_namespace_identity_invalid')
    keys = ('Uid', 'Gid', 'Groups', 'CapEff', 'CapPrm', 'CapBnd', 'NoNewPrivs', 'PPid')
    return {'pid': pid, 'ppid': int(status['PPid']), 'starttime': starttime, 'role': role,
            'status': {key: status.get(key, '') for key in keys}, 'namespace': namespace,
            'entrypoint_verified': role == 'main'}


class ProcessObservation:
    def __init__(self):
        import threading
        self.stop_event = threading.Event()
        self.seen = {}
        self.records = {}
        self.error = None
        self.rss_kib = 0
        self.tmp_bytes = 0
        self.thread = threading.Thread(target=self.observe, daemon=True)

    def observe(self):
        while not self.stop_event.is_set():
            try:
                total = 0
                for path in Path('/proc').glob('[0-9]*'):
                    try:
                        cmd = (path / 'cmdline').read_bytes()
                        role = ('main' if path.name == '1' else 'spawn' if b'multiprocessing.spawn' in cmd
                                else 'tesseract' if b'tesseract' in cmd.split(b'\0')[0] else None)
                        if role is None:
                            continue
                        status = dict(line.split(':', 1) for line in (path / 'status').read_text().splitlines() if ':' in line)
                        status = {key: value.strip() for key, value in status.items()}
                        start = (path / 'stat').read_text().rsplit(')', 1)[1].split()[19]
                        ns = {kind: os.readlink(path / 'ns' / kind) for kind in ('pid', 'net')}
                        record = process_record(int(path.name), cmd, status, start, ns)
                        self.records[(int(path.name), start)] = record
                        require(len(self.records) <= 64, 'process_evidence_budget_exceeded')
                        self.seen[role] = True
                        total += int(status.get('VmRSS', '0 kB').split()[0])
                    except (FileNotFoundError, ProcessLookupError):
                        continue  # Process exited between proc directory and read.
                self.rss_kib = max(self.rss_kib, total)
                usage = os.statvfs('/proc/1/root/tmp')
                self.tmp_bytes = max(self.tmp_bytes, (usage.f_blocks - usage.f_bfree) * usage.f_frsize)
            except BaseException as exc:
                self.error = error_category(exc); return
            self.stop_event.wait(.005)

    def __enter__(self):
        self.thread.start(); return self

    def __exit__(self, *_):
        self.stop_event.set(); self.thread.join(timeout=5)
        if Path('/probe-output').is_dir():
            phase = os.environ['DAQ_NATIVE_PROBE_PHASE']
            atomic_json(Path('/probe-output') / ('process-observations-' + phase + '.json'),
                        {'processes': list(self.records.values()), 'roles': sorted(self.seen),
                         'error': self.error, 'rss_peak_sampled_kib': self.rss_kib,
                         'tmp_peak_sampled_bytes': self.tmp_bytes})

    def result(self):
        require(self.error is None, self.error or 'process_observation_failed')
        require(set(self.seen) == {'main', 'spawn', 'tesseract'}, 'process_evidence_missing')
        records = list(self.records.values())
        main = next(item for item in records if item['role'] == 'main')
        spawns = {item['pid'] for item in records if item['role'] == 'spawn'}
        require(all(item['namespace'] == main['namespace'] and
                    (item['pid'] == 1 or (item['role'] == 'spawn' and item['ppid'] == 1) or
                     (item['role'] == 'tesseract' and item['ppid'] in spawns)) for item in records),
                'process_parent_namespace_mismatch')
        return {'roles': sorted(self.seen), 'processes': list(self.records.values()), 'rss_peak_sampled_kib': self.rss_kib,
                'tmp_peak_sampled_bytes': self.tmp_bytes, 'sampling_ms': 5,
                'measurement': 'sampled_lower_bound'}


def runtime_upload():
    client, session = http_client()
    other, _ = http_client('b')
    ids = []; contracts = {}
    store = runtime_store()
    with client, other, ProcessObservation() as observer:
        input_root = Path('/probe-output/inputs')
        input_root.mkdir(mode=0o700)
        inputs = []
        for name, content, media_type in upload_fixtures():
            fixture = input_root / name
            descriptor = os.open(fixture, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            inputs.append({'name': name, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
            atomic_json(Path('/probe-output/input-manifest.json'), inputs)
            response = client.post('/attachments', files=[('files', (name, content, media_type))])
            require(response.status_code == 201, 'multipart_upload_failed')
            manifest = response.json()['results'][0]['attachment']; aid = manifest['attachment_id']
            require(re.fullmatch(r'[A-Za-z0-9_-]{40,64}', aid), 'attachment_id_invalid')
            folder = Path('/app/data/attachments') / aid
            persisted = json.loads((folder / 'manifest.json').read_text())
            require((folder / 'original.bin').read_bytes() == content
                    and persisted['sha256'] == hashlib.sha256(content).hexdigest()
                    and persisted['status'] == 'ready' and persisted['owner_subject_id'] == SUBJECT_A,
                    'attachment_persistence_failed')
            contract = fixture_contract(name, content)
            consume_fixture(store, aid, contract)
            contracts[aid] = contract
            atomic_json(Path('/probe-output/attachment-contracts.json'), contracts)
            chunks = json.loads((folder / 'chunks.json').read_text())
            require(bool(chunks), 'attachment_chunks_missing')
            require('ocr_unavailable' not in persisted.get('warnings', []), 'ocr_unavailable')
            if name.endswith('.png'):
                require((folder / 'normalized-image.bin').stat().st_size > 0
                        and 'NATIVE PROBE READY' in ' '.join(item['text'] for item in chunks).upper(),
                        'ocr_text_missing')
            require(client.get('/attachments/' + aid).status_code == 200, 'owner_attachment_read_failed')
            require(other.get('/attachments/' + aid).status_code == 403, 'other_attachment_read_allowed')
            require(other.delete('/attachments/' + aid).status_code == 403, 'other_attachment_delete_allowed')
            ids.append(aid)
        # Keep the text for continuity; delete a separate newly uploaded CSV.
        require(client.delete('/attachments/' + ids[1]).status_code == 204, 'owner_delete_failed')
    atomic_json(Path('/probe-output/session.json'), session)
    atomic_json(Path('/probe-output/attachment-ids.json'), ids)
    return {'formats': 6, 'ownership_checks': 18, 'identity': observer.result()}


def runtime_seed():
    from src.attachments.models import AttachmentLimits, AttachmentChunk, AttachmentLocator
    from src.attachments.store import AttachmentStore
    from datetime import datetime, timezone, timedelta
    root = Path('/app/data/attachments'); store = AttachmentStore(root, AttachmentLimits())
    contracts = {}
    def seed_text(target, name, text):
        manifest = target.create_pending(name, 'text/plain', text.encode(), kind='text', owner_subject_id=SUBJECT_A)
        chunk = AttachmentChunk(chunk_id='synthetic-' + name, source_id=manifest.source_id,
                                locator=AttachmentLocator(line_start=1, line_end=2), text=text,
                                content_sha256=hashlib.sha256(text.encode()).hexdigest(), token_estimate=10)
        target.commit_ready(manifest.attachment_id, chunks=[chunk], parse_coverage='full',
                            parser_name='synthetic-fixture', parser_version='1')
        contracts[manifest.attachment_id] = fixture_contract(name, text.encode())
        return manifest.attachment_id
    retained = seed_text(store, 'old.txt', 'status=ready\n' + FIXTURE_MARKER + '-legacy')
    disposable = seed_text(store, 'discard.txt', 'status=ready\n' + FIXTURE_MARKER + '-discard')
    old_clock = datetime.now(timezone.utc) - timedelta(seconds=120)
    expired_store = AttachmentStore(root, AttachmentLimits(ttl_seconds=1), clock=lambda: old_clock)
    expired = seed_text(expired_store, 'expired.txt', 'status=ready\n' + FIXTURE_MARKER + '-expired')
    (root / '.partial-synthetic').mkdir(); (root / '.partial-synthetic/incomplete').write_text('fixture')
    (root / '.batch-synthetic').mkdir(); (root / '.batch-synthetic/incomplete').write_text('fixture')
    crypto_seed(); runtime_trace('old-root-seed')
    atomic_json(Path('/probe-output/seed.json'), {'attachment_id': retained, 'disposable_id': disposable,
                                               'expired_id': expired, 'contracts': contracts})
    return {'seeded': 3, 'interrupted_directories': 2}


def runtime_migrated():
    store = runtime_store(); client, _ = http_client(); other, _ = http_client('b')
    seed = json.loads(Path('/probe-output/seed.json').read_text())
    evidence = []
    with client, other:
        for aid in (seed['attachment_id'], seed['disposable_id']):
            evidence.append(consume_fixture(store, aid, seed['contracts'][aid]))
            require(client.get('/attachments/' + aid).status_code == 200, 'migrated_owner_access_failed')
            require(other.get('/attachments/' + aid).status_code == 403 and
                    other.delete('/attachments/' + aid).status_code == 403, 'migrated_other_access_allowed')
        require(client.delete('/attachments/' + seed['disposable_id']).status_code == 204,
                'migrated_owner_delete_failed')
        for aid in (seed['disposable_id'], seed['expired_id']):
            require(client.get('/attachments/' + aid).status_code in {404, 410}
                    and not (store.root / aid).exists(), 'migrated_removed_data_visible')
    atomic_json(Path('/probe-output/deleted-ids.json'), [seed['disposable_id'], seed['expired_id']])
    return {'actual_store_tool_reads': evidence, 'owner_other_delete_verified': True}


def verify_current_consumers(client, other):
    store = runtime_store(); seed = json.loads(Path('/probe-output/seed.json').read_text())
    contracts = json.loads(Path('/probe-output/attachment-contracts.json').read_text())
    ids = json.loads(Path('/probe-output/attachment-ids.json').read_text())
    contracts.pop(ids[1])  # Intentionally deleted nonroot CSV.
    contracts[seed['attachment_id']] = seed['contracts'][seed['attachment_id']]
    root_record = Path('/probe-output/root-attachment.json')
    if root_record.exists():
        record = json.loads(root_record.read_text()); contracts[record['attachment_id']] = record['contract']
    evidence = []
    for aid, contract in contracts.items():
        evidence.append(consume_fixture(store, aid, contract))
        require(client.get('/attachments/' + aid).status_code == 200, 'current_owner_access_failed')
        require(other.get('/attachments/' + aid).status_code == 403 and
                other.delete('/attachments/' + aid).status_code == 403, 'current_other_access_allowed')
    removed = [*json.loads(Path('/probe-output/deleted-ids.json').read_text()), ids[1]]
    for aid in removed:
        require(not (store.root / aid).exists() and client.get('/attachments/' + aid).status_code in {404, 410},
                'deleted_attachment_resurrected')
    return evidence


def task_snapshot(sid, aid):
    from src.storage.authenticated_conversations import AuthenticatedConversationRepository, ConversationContentCodec
    from src.platform_identity.models import PlatformSubject
    from daq_fae.durable_state import DaqDurableState
    codec = ConversationContentCodec.from_file('/run/daq-secrets/content-keyring.json')
    subject = PlatformSubject(subject_id=uuid.UUID(SUBJECT_A), subject_type='enterprise_member',
        internal_user_id=uuid.UUID(SUBJECT_A), identity_binding_id=uuid.UUID(BINDING_A),
        agent_id='ai-daq-fae-agent', active=True)
    database = os.environ['DAQ_DATABASE_URL']
    restored = AuthenticatedConversationRepository(database, codec=codec).load_for_subject(sid, subject.subject_id)
    checkpoint = DaqDurableState(database, codec=codec).load_context(subject, sid)
    require(checkpoint is not None and checkpoint.state.task_context is not None
            and restored.active_attachment_ids == [aid] and restored.owner_subject_id == SUBJECT_A,
            'restored_context_attachment_mismatch')
    manifest = runtime_store().get(aid)
    require(manifest.bound_session_id == sid and manifest.owner_subject_id == SUBJECT_A,
            'restored_attachment_binding_mismatch')
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return {'context_revision': checkpoint.revision, 'context_sha256': digest(checkpoint.state.model_dump()),
            'session_sha256': digest(restored.to_checkpoint()), 'message_count': len(restored.messages),
            'attachment_ids': restored.active_attachment_ids, 'source_id': manifest.source_id}


def runtime_task(phase):
    import psycopg
    session = json.loads(Path('/probe-output/session.json').read_text())
    client, _ = http_client(saved=session)
    ids = json.loads(Path('/probe-output/attachment-ids.json').read_text())
    request = {'message': 'Read this synthetic log: status=ready.', 'attachment_ids': [ids[0]],
               'client_request_id': 'native-probe-first'}
    def events(response):
        require(response.status_code == 200, 'task_http_failed')
        parsed = []
        for block in response.text.strip().split('\n\n'):
            lines = block.splitlines()
            name = next((line[7:] for line in lines if line.startswith('event: ')), None)
            body = next((json.loads(line[6:]) for line in lines if line.startswith('data: ')), None)
            if name and name != 'heartbeat': parsed.append([name, body])
        terminals = [payload for name, payload in parsed if name == 'done']
        require(len(terminals) == 1, 'task_terminal_missing')
        terminal = terminals[0]
        require(terminal['outcome'] == 'safe_abstained' and terminal['fallback_used'] is False,
                'task_outcome_failed')
        return terminal, parsed
    with client:
        before_calls = provider_metrics()
        if phase == 'first':
            terminal, frames = events(client.post('/chat', json=request))
            metrics = provider_metrics()
            require(metrics['calls'] > before_calls['calls'] and len(metrics['reads']) == len(before_calls['reads']) + 1,
                    'first_task_read_not_consumed')
            snapshot = task_snapshot(terminal['session_id'], ids[0])
            require(metrics['reads'][-1]['source_id'] == snapshot['source_id'], 'provider_read_source_binding_failed')
            atomic_json(Path('/probe-output/task.json'), {'terminal': terminal, 'events': frames, 'request': request,
                                                        'snapshot': snapshot, 'provider': metrics})
        else:
            original = json.loads(Path('/probe-output/task.json').read_text())
            require(task_snapshot(original['terminal']['session_id'], ids[0]) == original['snapshot'],
                    'restart_restored_state_changed')
            terminal, frames = events(client.post('/chat', json=original['request']))
            require(frames == original['events'] and terminal == original['terminal'], 'replay_event_identity_changed')
            require(provider_metrics() == before_calls, 'idempotency_replay_called_provider')
            require(task_snapshot(terminal['session_id'], ids[0]) == original['snapshot'], 'replay_state_changed')
        sid = terminal['session_id']
        history = client.get('/authenticated/conversations/' + sid)
        require(history.status_code == 200 and len(history.json()['turns']) == 1
                and history.json()['turns'][0]['trace_id'] == terminal['trace_id'], 'history_continuity_failed')
        with psycopg.connect(os.environ['DAQ_DATABASE_URL']) as connection:
            counts = [connection.execute(f'select count(*) from {table} where external_session_id=%s', (sid,)).fetchone()[0]
                      for table in ('daq_request_ledger', 'daq_context_checkpoints', 'chat_session_checkpoints')]
            require(counts == [1, 1, 1], 'task_successful_persistence_failed')
            require(connection.execute('select state from daq_request_ledger where external_session_id=%s', (sid,)).fetchone()
                    == ('completed',), 'task_ledger_incomplete')
        if phase != 'first':
            prior = task_snapshot(sid, ids[0]); counters = provider_metrics()
            next_turn, _ = events(client.post('/chat', json={'message': '再读取上一轮日志中的status=ready。',
                                  'session_id': sid, 'client_request_id': 'native-probe-second'}))
            restored = task_snapshot(sid, ids[0]); metrics = provider_metrics()
            require(next_turn['session_id'] == sid and restored['context_revision'] == prior['context_revision'] + 1
                    and restored['message_count'] == prior['message_count'] + 2
                    and restored['attachment_ids'] == prior['attachment_ids'], 'next_turn_checkpoint_continuity_failed')
            require(metrics['calls'] > counters['calls'] and len(metrics['reads']) == len(counters['reads']) + 1
                    and metrics['reads'][-1]['source_id'] == prior['source_id'], 'next_turn_read_not_consumed')
            require(len(client.get('/authenticated/conversations/' + sid).json()['turns']) == 2,
                    'next_turn_history_failed')
    return {'phase': phase, 'normal_success_persisted_tables': 3, 'real_model_calls': 0,
            'provider': provider_metrics(), 'restored': task_snapshot(sid, ids[0]),
            'transaction_failure_atomicity': {'status': 'NOT_EXECUTED', 'reason': 'normal_success_scenario_only'}}


def prepare_synthetic_files(root):
    # Elevated metadata setup is exclusively host-side, for newly created test
    # files below a private exclusive workroot. Never chown an input/source.
    for name in ('old-secrets', 'secrets', 'negative', 'old-attachments', 'attachments', 'output'):
        path = root / name; path.mkdir(mode=0o700)
        os.chown(path, UID if name in {'attachments', 'output'} else 0, UID)
        if name in {'secrets', 'negative'}:
            path.chmod(0o750)
    for name in ('session', 'content'):
        data = {'active_version': 1, 'keys': {'1': base64.b64encode(secrets.token_bytes(32)).decode()}}
        for folder, filename, owner, mode in (
            ('old-secrets', name + '-keyring.json', 0, 0o600),
            ('secrets', name + '-keyring.json', UID, 0o600),
            ('negative', 'root-' + name + '.json', 0, 0o600),
            ('negative', 'group-' + name + '.json', UID, 0o640),
        ):
            path = root / folder / filename; atomic_json(path, data)
            os.chown(path, owner, UID); path.chmod(mode)
    entitlements = {'agent_id': 'ai-daq-fae-agent', 'subjects': {}, 'default_enterprise_role': 'internal_fae'}
    for folder, filename, mode in (('old-secrets', 'entitlements.json', 0o640),
                                    ('secrets', 'entitlements.json', 0o640),
                                    ('negative', 'group-entitlements.json', 0o660)):
        path = root / folder / filename; atomic_json(path, entitlements)
        os.chown(path, 0, UID); path.chmod(mode)
    trace = root / 'trace.jsonl'
    fd = os.open(trace, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(b'{"type":"root","trace_id":"synthetic-old-prefix"}\n'); stream.flush(); os.fsync(stream.fileno())
    os.chown(trace, UID, UID)
    copy_private_file(trace, root / 'old-trace.jsonl', uid=0, gid=0)


def runtime_migrate():
    import psycopg
    from scripts.migrate_daq_pg import migrate_connection
    deadline = time.monotonic() + 45
    while True:
        try:
            connection = psycopg.connect(os.environ['DAQ_DATABASE_URL'], autocommit=True, connect_timeout=2)
            break
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise ProbeError('postgres_startup_timeout') from None
            time.sleep(.1)
    with connection:
        migrate_connection(connection)
    return {'real_postgres': True, 'actual_migrations': True}


def runtime_rollback_check():
    client, _ = http_client(); other, _ = http_client('b')
    with client, other:
        before = verify_current_consumers(client, other)
        response = client.post('/attachments', files=[('files', ('root.txt', b'status=ready\nroot current write', 'text/plain'))])
        require(response.status_code == 201, 'rollback_upload_failed')
        aid = response.json()['results'][0]['attachment']['attachment_id']
        require((Path('/app/data/attachments') / aid / 'original.bin').stat().st_uid == 0,
                'rollback_root_owner_unexpected')
        atomic_json(Path('/probe-output/root-attachment.json'), {'attachment_id': aid,
            'contract': fixture_contract('root.txt', b'status=ready\nroot current write')})
        after = verify_current_consumers(client, other)
        history = Path('/probe-output/task.json')
        if history.exists():
            terminal = json.loads(history.read_text())['terminal']
            response = client.get('/authenticated/conversations/' + terminal['session_id'])
            require(response.status_code == 200 and len(response.json()['turns']) == 2,
                    'rollback_history_decryption_failed')
    return {'old_nonroot_new_root_new_consumed': after, 'root_created_uid': 0,
            'trace': runtime_trace('rollback')}


def runtime_reforward_check():
    client, _ = http_client(); other, _ = http_client('b')
    with client, other:
        evidence = verify_current_consumers(client, other)
        for item in evidence:
            require((Path('/app/data/attachments') / item['attachment_id'] / 'original.bin').stat().st_uid == UID,
                    'reforward_owner_failed')
    return {'fresh_copy_actual_consumers': evidence, 'trace': runtime_trace('reforward')}


def runtime_isolation_check():
    import socket
    require(Path('/.dockerenv').is_file() and os.geteuid() in {0, UID}
            and re.fullmatch(r'[a-f0-9]{32}', os.getenv('DAQ_NATIVE_PROBE_ATTEMPT', '')) is not None
            and re.fullmatch(r'[a-z-]+-[0-9]+', os.getenv('DAQ_NATIVE_PROBE_PHASE', '')) is not None
            and all(os.environ.get(key) == value for key, value in synthetic_environment().items())
            and {name for _, name in socket.if_nameindex()} == {'lo'}, 'runtime_isolation_invalid')


def runtime_observation():
    path = Path('/proc/self')
    status = {key: value.strip() for key, value in
              (line.split(':', 1) for line in (path / 'status').read_text().splitlines() if ':' in line)}
    if os.geteuid() == UID:
        require(identity_valid(status), 'worker_process_identity_failed')
    fields = next((line.split() for line in (path / 'mountinfo').read_text().splitlines()
                   if line.split()[4] == '/tmp'), None)
    require(fields is not None, 'tmp_mount_evidence_missing')
    separator = fields.index('-')
    require(fields[separator + 1] == 'tmpfs' and
            {'noexec', 'nosuid', 'nodev'} <= set(fields[5].split(',')), 'tmp_kernel_flags_mismatch')
    tmp = Path('/tmp').stat(); usage = os.statvfs('/tmp')
    require(tmp.st_uid == os.geteuid() and stat.S_IMODE(tmp.st_mode) == 0o700
            and usage.f_blocks * usage.f_frsize == 268435456, 'tmp_kernel_size_owner_mismatch')
    return {'pid': os.getpid(), 'ppid': os.getppid(), 'starttime': (path / 'stat').read_text().rsplit(')', 1)[1].split()[19],
            'namespace': {kind: os.readlink(path / 'ns' / kind) for kind in ('pid', 'net')},
            'status': {key: status.get(key) for key in ('Uid', 'Gid', 'Groups', 'CapEff', 'CapPrm', 'CapBnd', 'NoNewPrivs')},
            'tmpfs': {'bytes': usage.f_blocks * usage.f_frsize, 'uid': tmp.st_uid, 'gid': tmp.st_gid,
                      'mode': oct(stat.S_IMODE(tmp.st_mode)), 'mount_flags': fields[5],
                      'super_flags': fields[separator + 3]}}


def runtime_main(args):
    runtime_isolation_check()
    try:
        if args.phase in {'app', 'scripted-app'}:
            runtime_app(args.phase == 'scripted-app'); return 0
        methods = {'migrate': runtime_migrate, 'seed': runtime_seed, 'migrated': runtime_migrated, 'startup': runtime_startup,
                   'auth': runtime_auth, 'filesystem': runtime_filesystem, 'keys': runtime_keys,
                   'upload': runtime_upload, 'task-first': lambda: runtime_task('first'),
                   'task-replay': lambda: runtime_task('replay'),
                   'rollback': runtime_rollback_check, 'reforward': runtime_reforward_check,
                   'trace-before': lambda: runtime_trace('before'), 'trace-after': lambda: runtime_trace('after'),
                   'trace-negative': lambda: runtime_trace('negative', negative=True)}
        result = methods[args.phase]()
        observation = runtime_observation()
        atomic_json(Path('/probe-output') / (os.environ['DAQ_NATIVE_PROBE_PHASE'] + '.json'), {'status': 'PASS', 'evidence': result, 'runtime_observation': observation})
        return 0
    except BaseException as exc:
        target = Path('/tmp/app-failure.json') if args.phase in {'app', 'scripted-app'} else Path('/probe-output') / (os.environ['DAQ_NATIVE_PROBE_PHASE'] + '.json')
        failure = {'status': 'FAIL', 'category': error_category(exc), 'exception_type': type(exc).__name__}
        if (isinstance(exc, ModuleNotFoundError) and isinstance(exc.name, str)
                and len(exc.name) <= 160
                and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*', exc.name)):
            # Private receipt only: no exception message, paths or traceback.
            failure['missing_module'] = exc.name
        atomic_json(target, failure)
        if args.phase in {'app', 'scripted-app'}:
            atomic_json(Path('/app/data/attachments') / ('.probe-' + os.environ['DAQ_NATIVE_PROBE_PHASE'] + '-failure.json'), failure)
        return 1


def copy_private_file(source, destination, *, uid, gid=UID, mode=0o600, trusted_source=False):
    h = asset_helpers(); source = strict_path(source); destination = strict_path(destination)
    src, name = h._parent(str(source), trusted=trusted_source)
    dst, leaf = h._parent(str(destination), trusted=True)
    try:
        raw, st = h._read_regular(str(source), ASSET_FILE_LIMIT)
        require(not trusted_source or (st.st_uid == 0 and not st.st_mode & 0o022), 'source_file_ownership_invalid')
        return h._copy_file(src, name, dst, leaf,
                            {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}, uid, gid, mode, [0, 0])
    finally:
        os.close(src); os.close(dst)


def set_tree_metadata(root, uid, gid=UID, directory_mode=0o700, file_mode=0o600):
    h = asset_helpers(); parent, name = h._parent(str(strict_path(root)), trusted=True)
    fd = os.open(name, h.DIR_FLAGS, dir_fd=parent)
    nodes = 0
    def visit(current, depth):
        nonlocal nodes
        nodes += 1
        require(nodes <= ASSET_NODE_LIMIT and depth <= ASSET_DEPTH_LIMIT, 'metadata_budget_exceeded')
        for name in h._list_names(current):
            before = os.stat(name, dir_fd=current, follow_symlinks=False)
            flags = h.DIR_FLAGS if stat.S_ISDIR(before.st_mode) else h.FILE_FLAGS
            child = os.open(name, flags, dir_fd=current)
            try:
                require(h._snapshot(before) == h._snapshot(os.fstat(child)) and
                        (stat.S_ISDIR(before.st_mode) or (stat.S_ISREG(before.st_mode) and before.st_nlink == 1)),
                        'metadata_identity_invalid')
                if stat.S_ISDIR(before.st_mode): visit(child, depth + 1)
                else:
                    nodes += 1
                    require(nodes <= ASSET_NODE_LIMIT, 'metadata_budget_exceeded')
                    os.fchown(child, uid, gid); os.fchmod(child, file_mode)
            finally: os.close(child)
        os.fchown(current, uid, gid); os.fchmod(current, directory_mode)
    try: visit(fd, 0)
    finally: os.close(fd); os.close(parent)


def copy_current(source, destination):
    require(not destination.exists(), 'copy_destination_exists')
    # Caller has stopped all known writers. No copytree or path-based chown.
    with BoundedTree(source, trusted=False) as tree:
        tree.copy(destination, owner=UID, mode=0o600)
        return len(tree.expected)


class NativeController:
    def __init__(self, args, manager):
        self.args, self.manager = args, manager
        self.root = Path(args.workroot)
        self.pg = None; self.app = None; self.serial = 0
        self.attachments = self.root / 'attachments'
        self.trace = self.root / 'trace.jsonl'
        self.report = fresh_report()
        self.report['attempt'] = manager.data['attempt']
        self.report['source_image'] = args.source_image
        self.report['pg_image'] = args.pg_image
        self.report['gates_requested'] = args.gate or list(GATES)
        self.current_gate = 'V01'

    def save(self):
        self.verify_workroot()
        atomic_json(self.root / 'report.json', self.report)

    def verify_workroot(self):
        fd = getattr(self.args, 'workroot_fd', None)
        if fd is not None:
            actual = asset_helpers()._open_dir(str(self.root), trusted=True)
            try:
                left, right = os.fstat(fd), os.fstat(actual)
                require((left.st_dev, left.st_ino) == (right.st_dev, right.st_ino), 'workroot_identity_changed')
            finally: os.close(actual)

    def image_check(self, image):
        require(re.fullmatch(r'sha256:[a-f0-9]{64}', image), 'image_id_invalid')
        result = self.manager.run(['image', 'inspect', image])
        require(result.returncode == 0, 'local_image_missing')
        value = json.loads(result.stdout)[0]
        require(value['Id'] == image and value['Architecture'] == 'amd64' and value['Os'] == 'linux',
                'native_image_identity_mismatch')

    def mounts(self, *, root_mode=False, worker=False, trace_ro=False, attachments=None):
        flags = []
        mappings = [(self.root / 'main', MAIN_INTERNAL, True), (self.root / 'sdk', SDK_INTERNAL, True),
                    (self.root / ('old-secrets' if root_mode else 'secrets'), '/run/daq-secrets', True),
                    (attachments or self.attachments, '/app/data/attachments', False),
                    (self.trace, '/app/data/daq_traces.jsonl', trace_ro),
                    (self.root / 'probe.py', '/probe.py', True)]
        if worker:
            mappings += [(self.root / 'negative', '/probe-negative', True),
                         (self.root / 'output', '/probe-output', False)]
        for source, target, ro in mappings:
            flags += bind_mount(source, target, ro)
        for key, value in synthetic_environment().items():
            flags += ['--env', key + '=' + value]
        flags += ['--env', 'DAQ_NATIVE_PROBE_ATTEMPT=' + self.manager.data['attempt']]
        return flags

    def flags(self, root_mode=False):
        if root_mode:
            # Deliberate OLD root baseline: default Docker capabilities and RW
            # rootfs. No false assertion that cap_drop ALL permits DAC bypass.
            return ['--user', '0:0', '--network', 'container:' + self.pg, '--log-driver', 'none',
                    '--tmpfs', '/tmp:noexec,nosuid,nodev,size=268435456,mode=0700']
        return security_flags(self.pg)

    def worker(self, phase, *, root_mode=False, trace_ro=False, attachments=None, share_pid=False):
        self.serial += 1
        phase_id = phase + '-' + str(self.serial)
        self.report['last_phase'] = phase_id; self.save()
        flags = self.flags(root_mode) + self.mounts(root_mode=root_mode, worker=True,
                                                  trace_ro=trace_ro, attachments=attachments)
        flags += ['--env', 'DAQ_NATIVE_PROBE_PHASE=' + phase_id]
        if share_pid:
            flags += ['--pid', 'container:' + self.app]
        cid = self.manager.create('work-' + str(self.serial), self.args.source_image, flags,
                                  ['python', '/probe.py', '_runtime', phase])
        self.manager.start(cid)
        self.manager.wait(cid, timeout=180)
        self.manager.stop(cid)
        evidence = self.root / 'output' / (phase_id + '.json')
        require(evidence.is_file(), 'worker_evidence_missing')
        result = json.loads(evidence.read_text())
        require(result['status'] == 'PASS', 'worker_report_failed')
        self.report.setdefault('worker_observations', {})[phase_id] = result['runtime_observation']; self.save()
        # Only synthetic output, needed after the root seed/rollback workload.
        set_tree_metadata(self.root / 'output', UID)
        return result['evidence']

    def start_app(self, *, root_mode=False, scripted=False):
        self.serial += 1
        self.app_phase = ('root' if root_mode else 'scripted' if scripted else 'app') + '-' + str(self.serial)
        self.report.setdefault('phases', []).append({'id': self.app_phase, 'root_baseline': root_mode,
            'provider_substitution': 'deterministic_adapter' if scripted else 'none',
            'identity_substitution': 'synthetic_http', 'source_image': self.args.source_image})
        if scripted and 'deterministic_provider_adapter_method_v10' not in self.report['substitutions']:
            self.report['substitutions'].append('deterministic_provider_adapter_method_v10')
        self.save()
        flags = self.flags(root_mode) + self.mounts(root_mode=root_mode)
        flags += ['--env', 'DAQ_NATIVE_PROBE_PHASE=' + self.app_phase]
        self.app = self.manager.create('app-' + str(self.serial), self.args.source_image, flags,
                                       ['python', '/probe.py', '_runtime', 'scripted-app' if scripted else 'app'])
        self.manager.start(self.app)
        if not root_mode:
            audit_candidate(self.manager.inspect(self.app), self.pg)
        return self.worker('startup', root_mode=root_mode, share_pid=True)

    def start_initial_app(self):
        startup = self.start_app()
        # startup worker only observes health/PG/proc. Capture maintenance
        # attribution BEFORE any worker that constructs AttachmentStore.
        self.current_gate = 'V08'
        self.verify_workroot()
        seed_raw, _ = asset_helpers()._read_regular(str(self.root / 'output/seed.json'), 65536)
        seed = json.loads(seed_raw)
        snapshot = asset_helpers()._snapshot_target_tree(str(self.attachments))
        interrupted = sorted(name for name in snapshot
                             if name.split('/', 1)[0].startswith(('.partial-', '.batch-')))
        expired_present = seed['expired_id'] + '/' in snapshot
        retained = {seed[key]: seed[key] + '/original.bin' in snapshot
                    for key in ('attachment_id', 'disposable_id')}
        passed = not interrupted and not expired_present and all(retained.values())
        evidence = {'status': 'PASS' if passed else 'FAIL',
                    'phase': 'after_initial_app_before_consumers', 'interrupted': interrupted,
                    'expired_present': expired_present, 'retained_originals': retained,
                    'filesystem_snapshot': snapshot}
        atomic_json(self.root / 'startup-maintenance.json', evidence)
        self.report['startup_maintenance'] = evidence; self.save()
        require(passed, 'startup_maintenance_failed')
        self.current_gate = 'V01'
        return startup

    def stop_app(self):
        self.capture_app_evidence(require_clean=True)
        self.manager.stop(self.app)
        self.capture_app_evidence(require_clean=True)

    def capture_app_evidence(self, *, require_clean=False, removed=False):
        if removed:
            require(self.manager.item(self.app)['state'] == 'absent', 'app_removal_unconfirmed')
        else:
            obj = self.manager.inspect(self.app)
            require(obj is not None, 'app_evidence_target_missing')
            self.manager.owned(self.manager.item(self.app), obj)
        summary = {}
        for kind in ('failure', 'warnings'):
            source = self.attachments / ('.probe-' + self.app_phase + '-' + kind + '.json')
            if source.exists():
                raw, _ = asset_helpers()._read_regular(str(source), 4096)
                destination = self.root / (self.app_phase + '-app-' + kind + '.json')
                atomic_json(destination, json.loads(raw))
                summary['app-' + kind + '.json'] = json.loads(raw)
        self.report.setdefault('app_summaries', {})[self.app_phase] = summary; self.save()
        if require_clean:
            require(summary.get('app-warnings.json', {}).get('warning_count') == 0
                    and 'app-failure.json' not in summary, 'application_stop_warning_or_failure')

    def capture_failure(self):
        if self.app is not None:
            self.capture_app_evidence()

    def complete(self, gate, checks, evidence=None, missing=()):
        self.report['gates'][gate] = {**gate_result(checks, missing), 'evidence': evidence or {}}
        self.save()

    def run(self):
        self.save()
        self.image_check(self.args.source_image); self.image_check(self.args.pg_image)
        prepare_synthetic_files(self.root)
        self.report['asset_dependency'] = {'commit': ASSET_HELPER_COMMIT, 'sha256': ASSET_HELPER_SHA}
        self.report['asset_inventory'] = {}
        for name, tree in self.args.asset_trees.items():
            self.report['asset_inventory'][name] = tree.copy(self.root / name, owner=0, mode=0o640)
        public, public_stat = asset_helpers()._read_regular(self.args.public_approval_key, 65536)
        require(asset_helpers()._metadata(public_stat) == self.args.public_key_snapshot, 'public_key_identity_changed')
        require(hashlib.sha256(public).hexdigest() == PUBLIC_SHA, 'public_key_pin_mismatch')
        for folder in ('old-secrets', 'secrets'):
            path = self.root / folder / Path(PUBLIC_INTERNAL).name
            copy_private_file(self.args.public_approval_key, path, uid=0, mode=0o640, trusted_source=True)
        copy_private_file(__file__, self.root / 'probe.py', uid=0, mode=0o444, trusted_source=True)
        for tree in self.args.asset_trees.values(): tree.verify()
        self.report['source_assets_unchanged'] = True
        require(self.report['source_assets_unchanged'], 'source_assets_changed')
        self.save()
        pg_flags = ['--user', '999:999', '--read-only', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges:true', '--network', 'none', '--log-driver', 'none',
                    '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev,size=536870912,mode=0700,uid=999,gid=999',
                    '--tmpfs', '/var/run/postgresql:rw,nosuid,nodev,size=1048576,mode=0700,uid=999,gid=999',
                    '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=0700,uid=999,gid=999',
                    '--env', 'PGDATA=/var/lib/postgresql/data/pgdata',
                    '--env', 'POSTGRES_USER=daq_probe', '--env', 'POSTGRES_DB=daq_probe',
                    '--env', 'POSTGRES_HOST_AUTH_METHOD=trust']
        self.pg = self.manager.create('postgres', self.args.pg_image, pg_flags, [])
        self.manager.start(self.pg)
        self.worker('migrate')
        self.trace = self.root / 'old-trace.jsonl'
        self.worker('seed', root_mode=True, attachments=self.root / 'old-attachments')
        self.trace = self.root / 'migrated-trace.jsonl'
        copy_private_file(self.root / 'old-trace.jsonl', self.trace, uid=UID)
        require(self.trace.read_bytes() == (self.root / 'old-trace.jsonl').read_bytes(), 'initial_trace_copy_mismatch')
        # Seed workload has stopped; no app exists. Preserve old root snapshot.
        self.attachments.rmdir()
        copied = copy_current(self.root / 'old-attachments', self.attachments)
        startup = self.start_initial_app()
        self.complete('V01', ['exact_local_image', 'production_signed_main_sdk', 'real_pg_migrations_marker',
                              'production_auth_no_sqlite'], startup)
        selected = set(self.args.gate or GATES)
        if selected & {'V03', 'V04'}:
            self.current_gate = 'V03'; fs = self.worker('filesystem')
            self.complete('V03', ['write_delete_rename_denied'], fs)
            self.current_gate = 'V04'
            prefix = self.trace.read_bytes()
            seed = json.loads((self.root / 'output/seed.json').read_text())
            original = (self.attachments / seed['attachment_id'] / 'original.bin').read_bytes()
            self.stop_app(); self.start_app()
            require(self.trace.read_bytes().startswith(prefix) and
                    (self.attachments / seed['attachment_id'] / 'original.bin').read_bytes() == original,
                    'rw_restart_persistence_failed')
            self.complete('V04', ['attachment_tmp_create_fsync_rename_chmod', 'restart_persistent_bytes'], fs)
        if 'V05' in selected:
            self.current_gate = 'V05'; keys = self.worker('keys')
            self.complete('V05', ['actual_strict_loaders', 'same_bytes_synthetic_key_migration', 'crypto_roundtrip'], keys)
        if 'V06' in selected:
            self.current_gate = 'V06'
            self.complete('V06', ['real_http_static_bytes', 'anonymous_origin_csrf', 'synthetic_identity_protocol'], self.worker('auth'))
        if selected & {'V02', 'V07', 'V08', 'V10', 'V11'}:
            self.current_gate = 'V07'; uploads = self.worker('upload', share_pid=True)
            self.complete('V07', ['six_real_multipart_formats', 'manifest_original_chunks', 'normalized_ocr_text'], uploads)
            self.complete('V02', ['actual_main_spawn_tesseract_proc'], uploads['identity'])
            self.current_gate = 'V08'
            require(self.report['startup_maintenance']['status'] == 'PASS', 'startup_maintenance_failed')
            migrated = self.worker('migrated')
            self.report['optional_capabilities'] = {'archive_protection': {
                'status': 'NOT_EXECUTED', 'applicability': 'disabled_by_source_configuration'}}
            self.complete('V08', ['owner_other_http_read_delete', 'nonempty_migration_actual_store_tools',
                                  'startup_partial_batch_cleanup', 'real_startup_expired_gc', 'unexpired_preserved'],
                          {'copied_files': copied, 'consumers': migrated, 'startup': self.report['startup_maintenance']})
        if 'V09' in selected:
            self.current_gate = 'V09'
            first = self.worker('trace-before'); prefix = self.trace.read_bytes()
            self.stop_app(); self.start_app()
            second = self.worker('trace-after')
            require(self.trace.read_bytes().startswith(prefix), 'trace_restart_prefix_changed')
            negative = self.worker('trace-negative', trace_ro=True)
            self.complete('V09', ['real_jsonl_sink', 'restart_prefix_continuity', 'readonly_warning_expected_refusal'],
                          {'before': first, 'after': second, 'negative': negative})
        if 'V10' in selected:
            self.current_gate = 'V10'
            self.stop_app(); self.start_app(scripted=True)
            first = self.worker('task-first')
            self.stop_app(); self.start_app(scripted=True)
            second = self.worker('task-replay')
            self.complete('V10', ['real_http_complete_task', 'normal_success_real_pg_checkpoint_ledger_history',
                                  'same_idempotency_replay_restart', 'next_turn_attachment_read'],
                          {'first': first, 'replay': second})
        if 'V11' in selected:
            self.current_gate = 'V11'; self.stop_app()
            prefix = self.trace.read_bytes()
            self.start_app(root_mode=True)
            rollback = self.worker('rollback', root_mode=True)
            require(self.trace.read_bytes().startswith(prefix), 'rollback_trace_prefix_changed')
            self.stop_app()
            # Every writer is stopped; make a FRESH forward copy. No online chown.
            self.attachments = self.root / 'reforward-attachments'
            copy_current(self.root / 'attachments', self.attachments)
            source_trace = self.trace
            self.trace = self.root / 'reforward-trace.jsonl'
            copy_private_file(source_trace, self.trace, uid=UID)
            require(self.trace.read_bytes() == source_trace.read_bytes(), 'trace_forward_copy_mismatch')
            self.start_app()
            forward = self.worker('reforward')
            self.complete('V11', ['same_image_root_current_data', 'old_new_http_visibility',
                                  'root_new_owner_recorded', 'stopped_fresh_copy_reforward', 'pg_never_restored'],
                          {'rollback': rollback, 'reforward': forward},
                          missing=[] if 'V10' in selected else ['root_history_decryption_requires_vten'])
        self.stop_app()
        self.report['elapsed_seconds'] = round(time.monotonic() - self.started, 3)
        self.save()


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='native Linux amd64 only; creates exclusively synthetic state')
    for name in ('main-root', 'sdk-root', 'public-approval-key', 'expected-public-sha',
                 'source-image', 'pg-image', 'workroot', 'runtime-release', 'main-internal', 'sdk-internal'):
        run.add_argument('--' + name, required=True)
    run.add_argument('--gate', action='append', choices=GATES,
                     help='Select affected gates; prerequisite evidence is explicit in report')
    cleanup = commands.add_parser('cleanup', help='reconcile only owned containers in existing journal')
    cleanup.add_argument('--workroot', required=True)
    cleanup.add_argument('--after-host-reboot', action='store_true',
                         help='Recovery only: kernel boot ID must differ from durable intent')
    runtime = commands.add_parser('_runtime', help=argparse.SUPPRESS)
    runtime.add_argument('phase', choices=['app', 'scripted-app', 'migrate', 'seed', 'migrated', 'startup', 'auth',
                        'filesystem', 'keys', 'upload', 'trace-before', 'trace-after', 'trace-negative',
                        'task-first', 'task-replay', 'rollback', 'reforward'])
    return parser


def validate_inputs(args):
    import platform
    require(sys.platform == 'linux' and os.geteuid() == 0 and platform.machine() in {'x86_64', 'amd64'},
            'native_root_controller_required')
    require(args.source_image == SOURCE_IMAGE and args.expected_public_sha == PUBLIC_SHA
            and args.runtime_release == RUNTIME_RELEASE and args.main_internal == MAIN_INTERNAL
            and args.sdk_internal == SDK_INTERNAL, 'approved_identity_mismatch')
    require(re.fullmatch(r'sha256:[a-f0-9]{64}', args.pg_image), 'postgres_image_id_invalid')
    roots = [strict_path(args.main_root), strict_path(args.sdk_root), strict_path(args.public_approval_key)]
    strict_path(args.workroot)
    for path in roots:
        require(path.is_absolute() and not any(c in str(path) for c in ',\n\r')
                and not any(part.is_symlink() for part in (path, *path.parents)), 'asset_path_invalid')
    for path in roots[:2]:
        require(path.is_dir() and (path / 'active.json').is_file(), 'prepared_release_root_invalid')
    args.asset_trees = {name: BoundedTree(path, trusted=True) for name, path in zip(('main', 'sdk'), roots[:2])}
    require(sum(tree.total for tree in args.asset_trees.values()) <= ASSET_TOTAL_LIMIT, 'combined_asset_budget_exceeded')
    for kind, tree in args.asset_trees.items(): validate_release_handoff(tree, kind)
    key = roots[2]
    require(key.is_file() and key.stat().st_nlink == 1, 'public_key_file_invalid')
    keyparent, _ = asset_helpers()._parent(str(key), trusted=True)
    os.close(keyparent)
    raw, keystat = asset_helpers()._read_regular(str(key), 65536)
    require(keystat.st_uid == 0 and not keystat.st_mode & 0o022, 'public_key_ownership_invalid')
    args.public_key_snapshot = asset_helpers()._metadata(keystat)
    require(hashlib.sha256(raw).hexdigest() == PUBLIC_SHA, 'public_key_pin_mismatch')
    document = json.loads(raw)
    require(set(document) == {'format_version', 'keys'} and
            document['format_version'] == 'daq-knowledge-approval-keys/v1' and
            all(set(item) == {'reviewer', 'public_key_b64'} for item in document['keys']), 'public_key_schema_invalid')
    root = Path(args.workroot)
    require(root.is_absolute() and not root.exists() and root.parent.is_dir()
            and not any(c in str(root) for c in ',\n\r')
            and not any(part.is_symlink() for part in root.parents), 'exclusive_workroot_required')
    require(not any(root.is_relative_to(p) or p.is_relative_to(root) for p in roots), 'workroot_asset_overlap')
    asset_helpers()._ensure_target_outside_sources({'paths': {'main': str(roots[0]), 'sdk': str(roots[1]),
        'secrets': str(roots[2].parent), 'attachments': str(roots[0])}}, str(root))
    parentfd, _ = asset_helpers()._parent(str(root), trusted=True)
    try:
        disk = os.fstatvfs(parentfd)
        require(disk.f_bavail * disk.f_frsize >= ASSET_TOTAL_LIMIT + SYNTHETIC_DISK_BUDGET + DISK_RESERVE,
                'disk_budget_insufficient')
    finally: os.close(parentfd)


def main(argv=None):
    args = argument_parser().parse_args(argv)
    if args.command == '_runtime':
        return runtime_main(args)
    import fcntl
    require(sys.platform == 'linux' and os.geteuid() == 0, 'native_root_controller_required')
    # Global per-host lease persists across process death, workroot names and
    # failed cleanup. Only explicit cleanup can release an existing attempt.
    control = Path('/run/daq-native-probe')
    if not control.exists():
        control.mkdir(mode=0o700)
    require(not control.is_symlink() and control.stat().st_uid == 0
            and stat.S_IMODE(control.stat().st_mode) == 0o700, 'control_directory_invalid')
    lockfd = os.open(control / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lease = control / 'active.json'
        root = strict_path(args.workroot)
        if args.command == 'cleanup':
            trusted_root_fd = asset_helpers()._open_dir(str(root), trusted=True)
            os.close(trusted_root_fd)
            if lease.is_file():
                require(json.loads(lease.read_text())['workroot'] == str(root), 'cleanup_lease_mismatch')
            else:
                require(args.after_host_reboot, 'cleanup_lease_missing')
            manager = OwnedDocker(root, recover=True)
            if args.after_host_reboot:
                require(bool(manager.data.get('boot_id')) and bool(boot_identity()) and
                        manager.data['boot_id'] != boot_identity(), 'reboot_evidence_missing')
            manager.cleanup(after_reboot=args.after_host_reboot)
            atomic_json(lease, {'workroot': str(root), 'released': True})
            print(json.dumps({'cleanup': 'released'})); return 0
        if lease.exists():
            require(json.loads(lease.read_text()).get('released') is True, 'previous_attempt_unreleased')
        validate_inputs(args)
        residue = docker_command(['container', 'ls', '--all', '--filter', 'label=' + OWNER_LABEL, '--quiet'])
        require(residue.returncode == 0 and not residue.stdout.strip(), 'owned_daemon_residue_requires_recovery')
        rootfd = asset_helpers()._start(str(root))
        args.workroot_fd = rootfd
        manager = OwnedDocker(root)
        atomic_json(lease, {'workroot': str(root), 'released': False, 'attempt': manager.data['attempt']})
        controller = NativeController(args, manager); controller.started = time.monotonic()
        failed = False
        try:
            controller.run()
        except BaseException as exc:
            failed = True
            controller.report['gates'][controller.current_gate] = {
                'status': 'FAIL', 'category': error_category(exc)}
            # Runtime failure evidence stays private in output/*.json. Never
            # print model/employee/secret content or full daemon inspect/config.
            controller.report['elapsed_seconds'] = round(time.monotonic() - controller.started, 3)
            try:
                controller.capture_failure()
            except BaseException:
                controller.report['failure_capture'] = 'unavailable'
            controller.save()
        finally:
            try:
                manager.cleanup()
            except BaseException:
                failed = True
                controller.report['cleanup'] = 'BLOCKED'
            else:
                controller.report['cleanup'] = 'released'
                atomic_json(lease, {'workroot': str(root), 'released': True})
                # Failure cleanup can itself stop the last app. Its tmpfs is
                # gone, but phase-specific durable warning evidence remains.
                if controller.app is not None:
                    try:
                        controller.capture_app_evidence(removed=True)
                    except BaseException:
                        failed = True
                        controller.report['failure_capture_after_cleanup'] = 'unavailable'
            controller.save()
        statuses = [row['status'] for row in controller.report['gates'].values()]
        print(json.dumps({'report': str(root / 'report.json'), 'passed': statuses.count('PASS'),
                          'failed': statuses.count('FAIL'), 'partial': statuses.count('PARTIAL'),
                          'not_executed': statuses.count('NOT_EXECUTED'), 'cleanup': controller.report['cleanup']}))
        return 1 if failed or 'PARTIAL' in statuses else 0
    finally:
        for tree in getattr(args, 'asset_trees', {}).values(): tree.__exit__(None, None, None)
        if getattr(args, 'workroot_fd', None) is not None: os.close(args.workroot_fd)
        os.close(lockfd)


def entrypoint(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # Uvicorn owns runtime shutdown signals. Controller cancellation must not
    # turn its graceful SIGTERM shutdown into an application failure receipt.
    if argv and argv[0] == '_runtime':
        return main(argv)
    with controlled_signals():
        return main(argv)


if __name__ == '__main__':
    try:
        raise SystemExit(entrypoint())
    except ProbeError as error:
        print(json.dumps({'status': 'FAIL', 'category': error_category(error)}))
        raise SystemExit(1)
