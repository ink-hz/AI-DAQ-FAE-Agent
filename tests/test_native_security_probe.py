"""Engineering tests: fake daemon + real filesystem; no native gate claims."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

MODULE = Path(__file__).parents[1] / 'deploy/probe_nonroot_runtime.py'


def module():
    assert MODULE.exists(), 'native probe implementation missing'
    spec = importlib.util.spec_from_file_location('native_probe', MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _policy_object(plan):
    return {'Config': {'User': plan['user']}, 'HostConfig': {
        'ReadonlyRootfs': plan['readonly'], 'CapDrop': plan['cap_drop'], 'CapAdd': [],
        'SecurityOpt': plan['security_opt'], 'GroupAdd': [], 'Privileged': False, 'PortBindings': {},
        'NetworkMode': plan['network'], 'PidMode': plan['pid'], 'LogConfig': {'Type': plan['log']},
        'Tmpfs': dict(plan['tmpfs'])}, 'Mounts': [
            {'Source': item['source'], 'Destination': item['target'], 'RW': item['rw'], 'Type': 'bind'}
            for item in plan['mounts']]}


def fake_root_metadata(monkeypatch, mod):
    # Engineering substitution only: descriptor operations stay real/no-follow;
    # this macOS test process cannot own uid0/21081 or root-controlled ancestors.
    helper = mod.asset_helpers()
    monkeypatch.setattr(helper, '_trusted_parent', lambda st: True)
    monkeypatch.setattr(mod.os, 'fchown', lambda *args: None)


class Daemon:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.lose_create = False
        self.unavailable = False
        self.keep_after_remove = False
        self.on_create = lambda: None

    def __call__(self, args, timeout=30):
        self.calls.append(args)
        if self.unavailable:
            raise subprocess.TimeoutExpired('docker', timeout)
        if args[:2] == ['container', 'inspect']:
            obj = self.objects.get(args[-1])
            if obj is None:
                return subprocess.CompletedProcess(args, 1, '', 'Error: No such container: ' + args[-1])
            return subprocess.CompletedProcess(args, 0, json.dumps([obj]), '')
        if args[0] == 'create':
            self.on_create()
            name = args[args.index('--name') + 1]
            labels = dict(args[i + 1].split('=', 1) for i, arg in enumerate(args) if arg == '--label')
            obj = {'Id': 'a' * 64, 'Name': '/' + name, 'Image': args[-3],
                   'Config': {'Labels': labels}, 'State': {'Running': False}}
            policy = _policy_object(module().policy_plan(args))
            policy['Config']['Labels'] = labels
            obj.update(policy)
            self.objects[name] = obj
            self.objects[obj['Id']] = obj
            if self.lose_create:
                raise subprocess.TimeoutExpired('docker', timeout)
            return subprocess.CompletedProcess(args, 0, obj['Id'] + '\n', '')
        if args[0] == 'stop':
            self.objects[args[-1]]['State']['Running'] = False
        if args[0] == 'rm' and not self.keep_after_remove:
            obj = self.objects[args[-1]]
            self.objects.pop(obj['Name'][1:], None)
            self.objects.pop(obj['Id'], None)
        return subprocess.CompletedProcess(args, 0, '', '')


def test_durable_create_intent_precedes_daemon_and_lost_output_reconciles(tmp_path):
    m = module()
    d = Daemon()
    manager = m.OwnedDocker(tmp_path, d)
    d.on_create = lambda: (
        pytest.fail('intent not durable') if not json.loads((tmp_path / 'journal.json').read_text())['containers'] else None)
    d.lose_create = True
    cid = manager.create('probe', 'sha256:' + '1' * 64, [], ['python', '-V'])
    assert cid == 'a' * 64
    manager.cleanup()
    assert json.loads((tmp_path / 'journal.json').read_text())['state'] == 'released'
    assert not d.objects


def test_foreign_collision_never_removed_and_blocks_journal(tmp_path):
    m = module(); d = Daemon(); manager = m.OwnedDocker(tmp_path, d)
    d.on_create = lambda: None
    manager.create('probe', 'sha256:' + '1' * 64, [], ['python', '-V'])
    next(iter(d.objects.values()))['Config']['Labels'] = {'other': 'owner'}
    with pytest.raises(m.ProbeError, match='ownership'):
        manager.cleanup()
    assert not any(c[0] == 'rm' for c in d.calls)
    assert json.loads((tmp_path / 'journal.json').read_text())['state'] == 'blocked'


def test_uncertain_cleanup_never_releases_attempt(tmp_path):
    m = module(); d = Daemon(); manager = m.OwnedDocker(tmp_path, d)
    manager.create('probe', 'sha256:' + '1' * 64, [], ['python', '-V'])
    d.unavailable = True
    with pytest.raises(m.ProbeError):
        manager.cleanup()
    assert json.loads((tmp_path / 'journal.json').read_text())['state'] == 'blocked'
    with pytest.raises(m.ProbeError, match='existing'):
        m.OwnedDocker(tmp_path, d)


def test_remove_success_without_absence_is_blocked(tmp_path):
    m = module(); d = Daemon(); manager = m.OwnedDocker(tmp_path, d)
    manager.create('probe', 'sha256:' + '1' * 64, [], ['python', '-V'])
    d.keep_after_remove = True
    with pytest.raises(m.ProbeError, match='absence'):
        manager.cleanup()
    assert json.loads((tmp_path / 'journal.json').read_text())['state'] == 'blocked'


def test_cleanup_can_recover_durable_intent_without_container_id(tmp_path):
    m = module(); d = Daemon(); manager = m.OwnedDocker(tmp_path, d)
    manager.create('probe', 'sha256:' + '1' * 64, [], ['python', '-V'])
    data = json.loads((tmp_path / 'journal.json').read_text())
    data['containers'][0]['id'] = None
    m.atomic_json(tmp_path / 'journal.json', data)
    recovered = m.OwnedDocker(tmp_path, d, recover=True)
    recovered.cleanup()
    assert not d.objects


def test_policy_has_no_network_or_rw_asset_escape(tmp_path):
    m = module()
    args = m.security_flags('b' * 64)
    assert args[args.index('--user') + 1] == '21081:21081'
    assert args[args.index('--network') + 1] == 'container:' + 'b' * 64
    for token in ('--read-only', '--cap-drop', 'ALL', 'no-new-privileges:true'):
        assert token in args
    assert 'noexec,nosuid,nodev,size=268435456,mode=0700,uid=21081,gid=21081' in args[args.index('--tmpfs') + 1]
    assert not any(x in args for x in ('--privileged', '--group-add', '--publish'))
    with pytest.raises(m.ProbeError):
        m.security_flags('production')


def test_input_walk_rejects_symlinks_and_hardlinks(tmp_path):
    m = module(); source = tmp_path / 'asset'; source.mkdir()
    (source / 'good').write_text('content')
    (source / 'link').symlink_to(source / 'good')
    with pytest.raises(m.ProbeError, match='asset'):
        m.tree_digest(source)
    (source / 'link').unlink()
    import os
    os.link(source / 'good', source / 'hard')
    with pytest.raises(m.ProbeError, match='asset'):
        m.tree_digest(source)


def test_safe_error_report_does_not_include_exception_secret():
    m = module()
    assert m.error_category(RuntimeError('password=employee secret')) == 'internal_error'
    assert 'secret' not in json.dumps(m.fresh_report())
    assert all(v['status'] == 'NOT_EXECUTED' for v in m.fresh_report()['gates'].values())


def test_process_identity_requires_main_worker_and_ocr_not_exec_id():
    m = module()
    status = {'Uid': '21081\t21081\t21081\t21081', 'Gid': '21081\t21081\t21081\t21081',
              'CapEff': '0000000000000000', 'CapPrm': '0000000000000000',
              'CapBnd': '0000000000000000', 'NoNewPrivs': '1', 'Groups': ''}
    assert m.identity_valid(status)
    assert not m.identity_valid({**status, 'CapBnd': '0000000000000001'})
    assert not m.identity_valid({**status, 'Groups': '0 21081'})
    assert not m.identity_valid({**status, 'Uid': '0 21081 21081 21081'})


def test_identity_stub_real_http_preserves_frozen_protocol():
    m = module()
    import threading
    import urllib.request
    import urllib.error
    server = m.identity_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    def post(path, payload):
        req = urllib.request.Request(url + path, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        return json.load(urllib.request.urlopen(req))
    try:
        one = post('/api/v1/internal/agent-launch/exchange', {'code': 'a' * 43})
        assert one['subject_id'] == m.SUBJECT_A and 'active' not in one
        two = post('/api/v1/internal/agent-launch/exchange', {'code': 'b' * 43})
        assert two['subject_id'] == m.SUBJECT_B
        valid = post(f'/api/v1/internal/agent-bindings/{m.BINDING_A}/validate', {'agent_id': 'ai-daq-fae-agent'})
        assert valid['active'] is True
        with pytest.raises(urllib.error.HTTPError):
            post('/api/v1/internal/agent-launch/exchange', {'code': 'bad'})
        with pytest.raises(urllib.error.HTTPError):
            post(f'/api/v1/internal/agent-bindings/{m.BINDING_A}/validate', {'agent_id': 'ai-fae-agent'})
    finally:
        server.shutdown(); thread.join(); server.server_close()


def test_environment_is_generated_not_inherited_and_has_only_private_endpoints(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setenv('DAQ_DATABASE_URL', 'postgresql://real:secret@production/business')
    env = m.synthetic_environment()
    assert env['DAQ_DATABASE_URL'] == 'postgresql://daq_probe@127.0.0.1:5432/daq_probe'
    assert env['DAQ_ENVIRONMENT'] == 'production'
    assert env['DAQ_PROVIDER_MODE'] == 'anthropic'
    assert env['DAQ_PLATFORM_IDENTITY_ENABLED'] == 'true'
    assert env['DAQ_PLATFORM_TASK_ENABLED'] == 'false'
    assert env['DAQ_ATTACHMENT_ARCHIVE_ENABLED'] == 'false'
    assert all('production/business' not in value for value in env.values())


def test_unsupported_full_gate_remains_partial():
    m = module()
    result = m.gate_result(['owner_read', 'other_denied'], ['archive_gc'])
    assert result['status'] == 'PARTIAL'
    assert result['not_executed'] == ['archive_gc']
    assert m.gate_result(['real_assertion'], [])['status'] == 'PASS'


def test_mount_builder_rejects_separator_injection(tmp_path):
    m = module()
    with pytest.raises(m.ProbeError):
        m.bind_mount(tmp_path / 'x,readonly=false', '/app/knowledge', True)
    assert m.bind_mount(tmp_path, '/probe-output', False)[0] == '--mount'


def test_upload_fixtures_are_complete_and_image_exceeds_real_spool_limit():
    m = module()
    items = m.upload_fixtures()
    assert [item[0].split('.')[-1] for item in items] == ['txt', 'csv', 'pdf', 'docx', 'xlsx', 'png']
    assert len(items[-1][1]) > 1024 * 1024
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(items[-1][1])).size == (1600, 800)


def test_proc_observer_cannot_mark_missing_tesseract_as_pass(tmp_path):
    m = module()
    observer = m.ProcessObservation()
    observer.seen = {'main': True, 'spawn': True}
    with pytest.raises(m.ProbeError, match='process_evidence_missing'):
        observer.result()


def test_static_index_applies_only_actual_production_mount_transform():
    m = module()
    result = m.expected_static('index.html', b'<html><head>abc</head></html>')
    assert result == (b'<html><head><base href="/daq/"><meta name="fae-browser-base" '
                      b'content="/daq"><meta name="fae-api-base" content="/daq/api">abc</head></html>')
    assert m.expected_static('assets/a.js', b'unchanged') == b'unchanged'


def test_private_fixture_key_copy_never_changes_source(tmp_path, monkeypatch):
    m = module()
    # Root metadata operations are substituted locally; content/filesystem real.
    calls = []
    monkeypatch.setattr(m.os, 'chown', lambda path, uid, gid: calls.append((Path(path), uid, gid)))
    root = tmp_path / 'private'; root.mkdir()
    fake_root_metadata(monkeypatch, m)
    m.prepare_synthetic_files(root)
    old = root / 'old-secrets'; new = root / 'secrets'
    for name in ('session-keyring.json', 'content-keyring.json'):
        assert (old / name).read_bytes() == (new / name).read_bytes()
        assert (new / name).stat().st_mode & 0o777 == 0o600
    assert (new / 'session-keyring.json').read_bytes() != (new / 'content-keyring.json').read_bytes()
    assert all(path.is_relative_to(root) for path, _, _ in calls)


def test_run_cli_rejects_production_sensitive_inputs():
    m = module()
    parser = m.argument_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(['run', '--production-env', '/etc/real.env'])
    with pytest.raises(SystemExit):
        parser.parse_args(['run', '--database-url', 'postgresql://real'])


def test_runtime_seed_uses_actual_store_and_keeps_nonempty_partial_fixture(tmp_path, monkeypatch):
    m = module()
    root = tmp_path / 'attachments'; output = tmp_path / 'output'; output.mkdir()
    actual_path = Path
    def mapped(value):
        if value == '/app/data/attachments': return root
        if value == '/probe-output/seed.json': return output / 'seed.json'
        return actual_path(value)
    monkeypatch.setattr(m, 'Path', mapped)
    monkeypatch.setattr(m, 'crypto_seed', lambda: None)  # Covered with actual crypto below.
    monkeypatch.setattr(m, 'runtime_trace', lambda phase: {})  # Native trace gate owns its mount.
    result = m.runtime_seed()
    assert result['seeded'] == 3
    aid = json.loads((output / 'seed.json').read_text())['attachment_id']
    assert json.loads((root / aid / 'manifest.json').read_text())['status'] == 'ready'
    assert (root / aid / 'chunks.json').is_file()
    assert (root / '.partial-synthetic/incomplete').is_file()


def test_original_key_ciphertext_consumed_by_actual_new_loader(tmp_path, monkeypatch):
    m = module()
    # Same OS uid in engineering test; native test performs actual 0->21081.
    secret = tmp_path / 'secrets'; secret.mkdir(); output = tmp_path / 'output'; output.mkdir()
    import base64
    for name, byte in [('session', b's'), ('content', b'c')]:
        p = secret / (name + '-keyring.json')
        p.write_text(json.dumps({'active_version': 1, 'keys': {'1': base64.b64encode(byte * 32).decode()}})); p.chmod(0o600)
    actual = Path
    def mapped(value):
        if str(value).startswith('/run/daq-secrets/'):
            return secret / actual(value).name
        if str(value).startswith('/probe-output/'):
            return output / actual(value).name
        return actual(value)
    monkeypatch.setattr(m, 'Path', mapped)
    m.crypto_seed()
    assert m.crypto_verify()['old_ciphertext_decrypted'] is True


def test_copy_current_preserves_original_and_creates_fresh_destination(tmp_path, monkeypatch):
    m = module(); source = tmp_path / 'old'; source.mkdir(); (source / 'file').write_bytes(b'current data')
    monkeypatch.setattr(m.os, 'chown', lambda *args: None)
    fake_root_metadata(monkeypatch, m)
    assert m.copy_current(source, tmp_path / 'new') == 1
    assert (source / 'file').read_bytes() == b'current data'
    with pytest.raises(m.ProbeError, match='destination'):
        m.copy_current(source, tmp_path / 'new')


def test_scripted_provider_receipts_drive_actual_local_loop_attachment_tools(tmp_path, monkeypatch):
    """Development/ASGI engineering check, NOT production/PG/native acceptance."""
    m = module()
    for key, value in {'DAQ_DEV_STATE_DB': str(tmp_path / 'state.sqlite3'),
                       'DAQ_ATTACHMENT_STORAGE_DIR': str(tmp_path / 'attachments'),
                       'DAQ_TRACE_LOG_PATH': str(tmp_path / 'trace.jsonl')}.items():
        monkeypatch.setenv(key, value)
    from fastapi.testclient import TestClient
    from daq_fae.app import create_app
    class Adapter:
        model = 'synthetic-probe'
        tool_choice_strategy = 'submit_only_auto'
        chat = m.scripted_chat
    app = create_app(provider_mode='offline', adapter=Adapter())
    with TestClient(app) as client:
        upload = client.post('/attachments', files=[('files', ('probe.txt', ('status=ready\n' + m.FIXTURE_MARKER + '\n').encode(), 'text/plain'))])
        assert upload.status_code == 201
        aid = upload.json()['results'][0]['attachment']['attachment_id']
        response = client.post('/chat', json={'message': 'Read this synthetic log: status=ready.', 'attachment_ids': [aid]})
        done = [json.loads(block.split('data: ', 1)[1]) for block in response.text.strip().split('\n\n')
                if block.startswith('event: done')][0]
        assert done['outcome'] == 'safe_abstained', done
        assert done['fallback_used'] is False
        assert {'search_attachments', 'read_attachment'} <= {item['tool'] for item in done['tool_calls']}
        first_reads = len(m.PROVIDER_EVIDENCE['reads'])
        # Development route requires explicit ids; native authenticated test restores implicit ids.
        second = client.post('/chat', json={'message': '再读取上一轮日志中的status=ready。', 'session_id': done['session_id'], 'attachment_ids': [aid]})
        terminal = [json.loads(block.split('data: ', 1)[1]) for block in second.text.strip().split('\n\n')
                    if block.startswith('event: done')][0]
        assert terminal['outcome'] == 'safe_abstained' and terminal['fallback_used'] is False
        assert len(m.PROVIDER_EVIDENCE['reads']) == first_reads + 1
        assert m.PROVIDER_EVIDENCE['reads'][-1] == m.PROVIDER_EVIDENCE['reads'][-2]


def test_actual_daemon_policy_audit_rejects_supplementary_group_and_wrong_netns():
    m = module(); pg = 'b' * 64
    obj = {'Config': {'User': '21081:21081'}, 'HostConfig': {
        'ReadonlyRootfs': True, 'CapDrop': ['ALL'], 'CapAdd': None,
        'SecurityOpt': ['no-new-privileges:true'], 'GroupAdd': [], 'Privileged': False,
        'LogConfig': {'Type': 'none'}, 'NetworkMode': 'container:' + pg,
        'PortBindings': {}, 'Binds': None,
    }, 'Mounts': [{'Destination': '/app/data/attachments', 'RW': True},
                  {'Destination': '/app/data/daq_traces.jsonl', 'RW': True},
                  {'Destination': '/run/daq-secrets', 'RW': False},
                  {'Destination': m.MAIN_INTERNAL, 'RW': False},
                  {'Destination': m.SDK_INTERNAL, 'RW': False},
                  {'Destination': '/probe.py', 'RW': False}]}
    m.audit_candidate(obj, pg)
    obj['HostConfig']['GroupAdd'] = ['0']
    with pytest.raises(m.ProbeError): m.audit_candidate(obj, pg)
    obj['HostConfig']['GroupAdd'] = []
    obj['HostConfig']['NetworkMode'] = 'host'
    with pytest.raises(m.ProbeError): m.audit_candidate(obj, pg)


def test_create_daemon_failure_not_absence_does_not_allow_unknown_cleanup(tmp_path):
    m = module(); d = Daemon(); manager = m.OwnedDocker(tmp_path, d)
    def inaccessible(args, timeout=30):
        return subprocess.CompletedProcess(args, 1, '', 'Cannot connect to Docker daemon')
    manager.run = inaccessible
    with pytest.raises(m.ProbeError, match='inspect_unknown'):
        manager.create('probe', 'sha256:' + '1' * 64, [], ['python', '-V'])
    assert json.loads((tmp_path / 'journal.json').read_text())['state'] == 'blocked'


def test_internal_runtime_entry_refuses_host_or_inherited_database(monkeypatch):
    m = module()
    monkeypatch.setenv('DAQ_DATABASE_URL', 'postgresql://production')
    with pytest.raises(m.ProbeError, match='runtime_isolation'):
        m.runtime_isolation_check()


def test_inspect_failure_on_create_keeps_intent_for_recovery(tmp_path):
    m = module(); d = Daemon(); manager = m.OwnedDocker(tmp_path, d)
    d.on_create = lambda: setattr(d, 'unavailable', True)
    with pytest.raises(subprocess.TimeoutExpired):
        manager.create('probe', 'sha256:' + '1' * 64, [], ['python', '-V'])
    data = json.loads((tmp_path / 'journal.json').read_text())
    assert data['state'] == 'blocked' and data['containers'][0]['id'] is None
    d.unavailable = False
    m.OwnedDocker(tmp_path, d, recover=True).cleanup()
    assert not d.objects


def test_unknown_create_stays_blocked_until_late_owned_materialization(tmp_path):
    m = module(); daemon = Daemon(); manager = m.OwnedDocker(tmp_path, daemon)
    real = daemon.__call__
    pending = []
    def delayed(args, timeout=30):
        if args[0] == 'create':
            pending.append(args)
            raise subprocess.TimeoutExpired('docker', timeout)
        return real(args, timeout)
    manager.run = delayed
    with pytest.raises(subprocess.TimeoutExpired):
        manager.create('late', 'sha256:' + '1' * 64, [], ['python', '-V'])
    with pytest.raises(m.ProbeError, match='unresolved_create'):
        manager.cleanup()
    assert json.loads((tmp_path / 'journal.json').read_text())['state'] == 'blocked'
    real(pending[0])
    manager.cleanup()
    assert not daemon.objects
    assert json.loads((tmp_path / 'journal.json').read_text())['state'] == 'released'


def test_unknown_create_recovery_requires_actual_new_boot_evidence(tmp_path, monkeypatch):
    m = module(); monkeypatch.setattr(m, 'boot_identity', lambda: 'boot-a')
    manager = m.OwnedDocker(tmp_path, Daemon())
    manager.data['containers'].append({'role': 'late', 'name': 'late', 'image': 'sha256:' + '1' * 64,
                                       'id': None, 'state': 'create_intent', 'create_outcome': 'unknown'})
    manager.save()
    with pytest.raises(m.ProbeError): manager.cleanup(after_reboot=True)
    monkeypatch.setattr(m, 'boot_identity', lambda: 'boot-b')
    manager.cleanup(after_reboot=True)
    assert manager.data['state'] == 'released'


@pytest.mark.parametrize('termination', ['SIGTERM', 'SIGHUP'])
def test_real_signal_reaps_independent_client_group(tmp_path, termination):
    import os
    import signal
    import time
    script = tmp_path / 'controller.py'
    pidfile = tmp_path / 'child.pid'
    script.write_text("""import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('native_probe', sys.argv[1])
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
with m.controlled_signals():
    m.run_process([sys.executable, '-c', 'import os,pathlib,time; pathlib.Path(\\"'+sys.argv[2]+'\\").write_text(str(os.getpid())); time.sleep(120)'], {}, 120)
""")
    proc = subprocess.Popen([sys.executable, str(script), str(MODULE), str(pidfile)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert pidfile.exists(), proc.communicate(timeout=1)
        child = int(pidfile.read_text())
        os.kill(proc.pid, getattr(signal, termination))
        proc.communicate(timeout=5)
        with pytest.raises(ProcessLookupError): os.kill(child, 0)
        with pytest.raises(ProcessLookupError): os.killpg(child, 0)
    finally:
        if proc.poll() is None:
            proc.kill(); proc.communicate(timeout=5)


@pytest.mark.parametrize('path', ['/safe/sibling/../assets/attempt', '/safe/./assets', '/safe//assets', '/safe/assets/'])
def test_asset_contract_refuses_alias_before_any_mutation(path):
    m = module()
    with pytest.raises((m.ProbeError, ValueError)):
        m.strict_path(path)


def test_asset_handoff_uses_reviewed_same_checkout_helper():
    m = module(); helper = m.asset_helpers()
    assert Path(helper.__file__).resolve() == MODULE.with_name('security_assets.py').resolve()
    assert m.ASSET_HELPER_SHA == __import__('hashlib').sha256(Path(helper.__file__).read_bytes()).hexdigest()


def test_asset_inventory_rejects_untrusted_root_and_oversize_before_copy(tmp_path, monkeypatch):
    m = module(); source = tmp_path / 'assets'; source.mkdir(); (source / 'active.json').write_text('{}')
    helper = m.asset_helpers()
    with pytest.raises(Exception):
        m.BoundedTree(source, trusted=True)
    # Isolate size accounting from root ancestry on this nonroot engineering host.
    monkeypatch.setattr(helper, '_open_dir', lambda path, trusted=False: __import__('os').open(path, helper.DIR_FLAGS))
    monkeypatch.setattr(m, 'TRUSTED_UID', __import__('os').getuid())
    monkeypatch.setattr(m, 'ASSET_FILE_LIMIT', 1)
    with pytest.raises(m.ProbeError, match='budget'):
        m.BoundedTree(source, trusted=True)


def test_source_membership_drift_refused_before_destination_publication(tmp_path, monkeypatch):
    m = module(); helper = m.asset_helpers()
    import os
    monkeypatch.setattr(helper, '_open_dir', lambda path, trusted=False: os.open(path, helper.DIR_FLAGS))
    monkeypatch.setattr(m, 'TRUSTED_UID', os.getuid())
    source = tmp_path / 'assets'; source.mkdir(); (source / 'good').write_text('content')
    with m.BoundedTree(source, trusted=True) as tree:
        (source / 'surprise').write_text('unexpected')
        with pytest.raises(Exception): tree.verify()
    assert not (tmp_path / 'destination').exists()


@pytest.mark.parametrize('mutation', ['text', 'locator', 'source', 'hash'])
def test_ready_http_upload_cannot_hide_corrupt_chunks(tmp_path, monkeypatch, mutation):
    m = module()
    monkeypatch.setenv('DAQ_DEV_STATE_DB', str(tmp_path / 'state.sqlite3'))
    monkeypatch.setenv('DAQ_ATTACHMENT_STORAGE_DIR', str(tmp_path / 'attachments'))
    monkeypatch.setenv('DAQ_TRACE_LOG_PATH', str(tmp_path / 'trace.jsonl'))
    from fastapi.testclient import TestClient
    from daq_fae.app import create_app
    app = create_app(provider_mode='offline')
    content = ('status=ready\n' + m.FIXTURE_MARKER + '\n').encode()
    with TestClient(app) as client:
        upload = client.post('/attachments', files=[('files', ('probe.txt', content, 'text/plain'))])
        assert upload.status_code == 201
        aid = upload.json()['results'][0]['attachment']['attachment_id']
        store = app.state.attachment_store
        contract = m.fixture_contract('probe.txt', content, owner=None)
        m.consume_fixture(store, aid, contract)
        path = store.root / aid / 'chunks.json'; chunks = json.loads(path.read_text())
        key, value = {'text': ('text', 'unrelated'), 'locator': ('locator', {'page': 8}),
                      'source': ('source_id', 'foreign'), 'hash': ('content_sha256', '0' * 64)}[mutation]
        chunks[0][key] = value; path.write_text(json.dumps(chunks))
        assert client.get('/attachments/' + aid).status_code == 200
        assert store.get(aid).status == 'ready'
        with pytest.raises(m.ProbeError): m.consume_fixture(store, aid, contract)


@pytest.mark.parametrize('receipt', [
    {'status': 'not_found', 'content': {}},
    {'status': 'ok', 'content': {'source_id': 'wrong', 'locator': {'line_start': 1, 'line_end': 2}, 'text': 'ready'}},
    {'status': 'ok', 'content': {'source_id': 'source', 'locator': {'page': 99}, 'text': 'ready'}},
])
def test_scripted_provider_refuses_bad_read_receipt(receipt):
    m = module()
    search = {'status': 'ok', 'content': {'hits': [{'source_id': 'source',
               'locator': {'line_start': 1, 'line_end': 2}, 'text': 'status=ready\n' + m.FIXTURE_MARKER}]}}
    messages = [{'role': 'tool', 'tool_call_id': 'probe-search_attachments', 'content': json.dumps(search)},
                {'role': 'tool', 'tool_call_id': 'probe-read', 'content': json.dumps(receipt)}]
    with pytest.raises(m.ProbeError): list(m.scripted_chat(None, messages))


def test_policy_plan_checks_actual_tmpfs_and_mount_source_identity(tmp_path):
    m = module(); path = tmp_path / 'asset'; path.write_text('fixture')
    flags = m.security_flags('b' * 64) + m.bind_mount(path, '/fixture', True)
    plan = m.policy_plan(flags)
    obj = _policy_object(plan)
    assert m.audit_plan(obj, plan)['network'] == 'container:' + 'b' * 64
    obj['HostConfig']['Tmpfs']['/tmp'] = 'noexec,nosuid,nodev,size=536870912,mode=0700,uid=21081,gid=21081'
    with pytest.raises(m.ProbeError): m.audit_plan(obj, plan)
    obj = _policy_object(plan)
    path.unlink(); path.write_text('replacement')
    with pytest.raises(m.ProbeError): m.audit_plan(obj, plan)


def test_recorded_process_requires_parent_namespace_and_main_entry_identity(tmp_path):
    m = module()
    record = m.process_record(1, b'python\0/probe.py\0_runtime\0app\0',
          {'Uid': '21081\t21081\t21081\t21081', 'Gid': '21081\t21081\t21081\t21081',
           'CapEff': '0', 'CapPrm': '0', 'CapBnd': '0', 'NoNewPrivs': '1', 'Groups': '', 'PPid': '0'},
          '123', {'pid': 'pid:[42]', 'net': 'net:[43]'})
    assert record['starttime'] == '123' and record['ppid'] == 0 and record['namespace']['net'] == 'net:[43]'
    with pytest.raises(m.ProbeError):
        m.process_record(1, b'python\0wrong.py\0', record['status'], '123', record['namespace'])


@pytest.mark.parametrize('extension', ['txt', 'csv', 'pdf', 'docx', 'xlsx'])
def test_each_format_real_upload_content_and_locator_contract(tmp_path, monkeypatch, extension):
    m = module()
    for key, value in {'DAQ_DEV_STATE_DB': str(tmp_path / 'state.sqlite3'),
                       'DAQ_ATTACHMENT_STORAGE_DIR': str(tmp_path / 'attachments'),
                       'DAQ_TRACE_LOG_PATH': str(tmp_path / 'trace.jsonl')}.items():
        monkeypatch.setenv(key, value)
    from fastapi.testclient import TestClient
    from daq_fae.app import create_app
    app = create_app(provider_mode='offline')
    fixture = next(item for item in m.upload_fixtures() if item[0].endswith('.' + extension))
    with TestClient(app) as client:
        response = client.post('/attachments', files=[('files', fixture)])
        assert response.status_code == 201
        aid = response.json()['results'][0]['attachment']['attachment_id']
        contract = m.fixture_contract(fixture[0], fixture[1], owner=None)
        m.consume_fixture(app.state.attachment_store, aid, contract)
        path = app.state.attachment_store.root / aid / 'chunks.json'
        chunks = json.loads(path.read_text()); chunks[0]['text'] = 'semantically incorrect'
        chunks[0]['content_sha256'] = __import__('hashlib').sha256(chunks[0]['text'].encode()).hexdigest()
        path.write_text(json.dumps(chunks))
        assert client.get('/attachments/' + aid).status_code == 200
        with pytest.raises(m.ProbeError, match='text_mismatch'):
            m.consume_fixture(app.state.attachment_store, aid, contract)


@pytest.mark.parametrize('mutation', ['decode', 'dimensions', 'media_type'])
def test_normalized_image_consumer_rejects_corruption_with_ready_manifest(tmp_path, mutation):
    m = module()
    from src.attachments.models import AttachmentLimits, AttachmentChunk, AttachmentLocator
    from src.attachments.store import AttachmentStore
    store = AttachmentStore(tmp_path / 'images', AttachmentLimits())
    name, content, media = m.upload_fixtures()[-1]
    item = store.create_pending(name, media, content, kind='image', owner_subject_id=m.SUBJECT_A)
    text = 'NATIVE PROBE READY'
    chunk = AttachmentChunk('image-chunk', item.source_id, AttachmentLocator(region_label='ocr'), text,
                            __import__('hashlib').sha256(text.encode()).hexdigest(), 10)
    store.commit_ready(item.attachment_id, chunks=[chunk], normalized_image=content,
                       normalized_media_type='image/png', normalized_width=1600, normalized_height=800)
    contract = m.fixture_contract(name, content)
    m.consume_fixture(store, item.attachment_id, contract)
    root = store.root / item.attachment_id
    if mutation == 'decode':
        (root / 'normalized-image.bin').write_bytes(b'X' * len(content))
    else:
        manifest = json.loads((root / 'manifest.json').read_text())
        manifest['normalized_width' if mutation == 'dimensions' else 'normalized_media_type'] = 1 if mutation == 'dimensions' else 'image/jpeg'
        (root / 'manifest.json').write_text(json.dumps(manifest))
    assert store.get(item.attachment_id).status == 'ready'
    with pytest.raises(m.ProbeError): m.consume_fixture(store, item.attachment_id, contract)


def test_archive_disabled_is_not_mandatory_gate_failure():
    m = module()
    report = m.fresh_report()
    assert report['optional_capabilities']['archive_protection'] == {
        'status': 'NOT_EXECUTED', 'applicability': 'disabled_by_source_configuration'}
    assert m.gate_result(['normal_gc', 'migration_consumers'])['status'] == 'PASS'


def test_handoff_refuses_unexpected_member_even_with_active_pointer(tmp_path, monkeypatch):
    m = module(); helper = m.asset_helpers()
    fake_root_metadata(monkeypatch, m); monkeypatch.setattr(m, 'TRUSTED_UID', __import__('os').getuid())
    source = tmp_path / 'assets'; source.mkdir()
    (source / 'active.json').write_text('{"release_id":"' + 'a' * 64 + '"}')
    (source / 'session-keyring.json').write_text('private material must never be copied')
    with m.BoundedTree(source, trusted=True) as tree:
        with pytest.raises(m.ProbeError, match='handoff'):
            m.validate_release_handoff(tree, 'main')


def test_unique_runtime_evidence_does_not_overwrite_previous_phase(tmp_path, monkeypatch):
    m = module(); actual_path = Path
    monkeypatch.setattr(m, 'runtime_isolation_check', lambda: None)
    monkeypatch.setattr(m, 'runtime_observation', lambda: {'engineering': True})
    monkeypatch.setattr(m, 'runtime_trace', lambda *args, **kwargs: {'known_span': True})
    monkeypatch.setattr(m, 'Path', lambda value: tmp_path if value == '/probe-output' else actual_path(value))
    from types import SimpleNamespace
    for phase in ('trace-before-1', 'trace-before-8'):
        monkeypatch.setenv('DAQ_NATIVE_PROBE_PHASE', phase)
        assert m.runtime_main(SimpleNamespace(phase='trace-before')) == 0
    assert (tmp_path / 'trace-before-1.json').is_file() and (tmp_path / 'trace-before-8.json').is_file()


def test_provider_substitution_is_recorded_before_failing_app_create(tmp_path):
    m = module()
    from types import SimpleNamespace
    class Manager:
        data = {'attempt': 'a' * 32}
        def create(self, *args):
            report = json.loads((tmp_path / 'report.json').read_text())
            assert report['phases'][-1]['provider_substitution'] == 'deterministic_adapter'
            assert 'deterministic_provider_adapter_method_v10' in report['substitutions']
            raise m.ProbeError('deliberate_create_failure')
    controller = m.NativeController(SimpleNamespace(workroot=str(tmp_path), source_image=m.SOURCE_IMAGE, candidate_image=m.CANDIDATE_IMAGE,
                                 pg_image='sha256:' + '1' * 64, gate=['V10']), Manager())
    controller.pg = 'b' * 64
    with pytest.raises(m.ProbeError, match='deliberate_create_failure'):
        controller.start_app(scripted=True)


@pytest.mark.parametrize('stage', ['create', 'wait'])
@pytest.mark.parametrize('termination', ['SIGTERM', 'SIGHUP'])
def test_real_signal_during_daemon_operation_preserves_recovery_state(tmp_path, stage, termination):
    import os
    import signal
    import time
    script = tmp_path / 'signal-controller.py'; pidfile = tmp_path / 'client.pid'
    script.write_text('''import importlib.util,json,pathlib,sys
spec=importlib.util.spec_from_file_location('probe_tests', sys.argv[1]); t=importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
m=t.module(); d=t.Daemon(); original=d.__call__; root=pathlib.Path(sys.argv[2]); stage=sys.argv[3]
def run(args, timeout=30):
    if args[0] == stage:
        m.run_process([sys.executable,'-c','import os,pathlib,time; pathlib.Path('+repr(str(root/'client.pid'))+').write_text(str(os.getpid())); time.sleep(120)'],{},120)
    return original(args, timeout)
manager=m.OwnedDocker(root,run)
t.add_known_running(m,manager,d,'postgres','b'); t.add_known_running(m,manager,d,'app','c')
try:
    with m.controlled_signals():
        cid=manager.create('signal','sha256:'+'1'*64,[],['python','-V'])
        if stage=='wait':
            manager.start(cid); d.objects[cid]['State']['Running']=True; manager.wait(cid,120)
except BaseException:
    pass
finally:
    try: manager.cleanup()
    except m.ProbeError: pass
''')
    proc = subprocess.Popen([sys.executable, str(script), str(Path(__file__).resolve()), str(tmp_path), stage],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and proc.poll() is None and time.monotonic() < deadline: time.sleep(.01)
        assert pidfile.exists(), proc.communicate(timeout=1)
        child = int(pidfile.read_text()); os.kill(proc.pid, getattr(signal, termination))
        proc.communicate(timeout=5)
        with pytest.raises(ProcessLookupError): os.kill(child, 0)
        journal = json.loads((tmp_path / 'journal.json').read_text())
        assert journal['state'] == ('blocked' if stage == 'create' else 'released')
        assert all(item['state'] == 'absent' for item in journal['containers'][:2])
        if stage == 'create': assert journal['cleanup_errors'][0]['category'] == 'unresolved_create'
    finally:
        if proc.poll() is None: proc.kill(); proc.communicate(timeout=5)


def test_nonzero_docker_cli_exit_is_not_proof_daemon_rejected_create(tmp_path):
    m = module(); d = Daemon(); original = d.__call__
    def lost_transport(args, timeout=30):
        if args[0] == 'create':
            return subprocess.CompletedProcess(args, 1, '', 'connection reset after request sent')
        return original(args, timeout)
    manager = m.OwnedDocker(tmp_path, lost_transport)
    with pytest.raises(m.ProbeError): manager.create('unknown', 'sha256:' + '1' * 64, [], ['python', '-V'])
    with pytest.raises(m.ProbeError, match='unresolved_create'): manager.cleanup()


def test_exact_descendant_alias_refuses_before_mutation(tmp_path, monkeypatch):
    m = module()
    import os
    import platform
    from types import SimpleNamespace
    source = tmp_path / 'assets'; source.mkdir(); (source / 'active.json').write_text('{}')
    sibling = tmp_path / 'sibling'; sibling.mkdir()
    before = (source.stat().st_ino, source.stat().st_mtime_ns, source.stat().st_ctime_ns,
              (source / 'active.json').read_bytes(), sorted(source.iterdir()))
    monkeypatch.setattr(m.sys, 'platform', 'linux'); monkeypatch.setattr(m.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(platform, 'machine', lambda: 'x86_64')
    args = SimpleNamespace(source_image=m.SOURCE_IMAGE, candidate_image=m.CANDIDATE_IMAGE, expected_public_sha=m.PUBLIC_SHA,
                           runtime_release=m.RUNTIME_RELEASE, main_internal=m.MAIN_INTERNAL, sdk_internal=m.SDK_INTERNAL,
                           pg_image='sha256:' + '1' * 64, main_root=str(source), sdk_root=str(source),
                           public_approval_key=str(tmp_path / 'public.json'),
                           workroot=str(sibling) + '/../assets/attempt')
    with pytest.raises(m.ProbeError, match='canonical_path'): m.validate_inputs(args)
    assert not (source / 'attempt').exists()
    assert before == (source.stat().st_ino, source.stat().st_mtime_ns, source.stat().st_ctime_ns,
                      (source / 'active.json').read_bytes(), sorted(source.iterdir()))


@pytest.mark.parametrize('limit', ['nodes', 'bytes', 'depth', 'disk'])
def test_copy_budget_refusal_precedes_destination_mkdir(tmp_path, monkeypatch, limit):
    m = module(); fake_root_metadata(monkeypatch, m)
    monkeypatch.setattr(m, 'TRUSTED_UID', __import__('os').getuid())
    source = tmp_path / 'assets'; source.mkdir(); nested = source / 'nested'; nested.mkdir()
    (nested / 'item').write_bytes(b'content')
    destination = tmp_path / 'output'
    if limit == 'nodes': monkeypatch.setattr(m, 'ASSET_NODE_LIMIT', 1)
    if limit == 'bytes': monkeypatch.setattr(m, 'ASSET_TOTAL_LIMIT', 1)
    if limit == 'depth': monkeypatch.setattr(m, 'ASSET_DEPTH_LIMIT', 0)
    if limit == 'disk':
        from types import SimpleNamespace
        monkeypatch.setattr(m.os, 'fstatvfs', lambda fd: SimpleNamespace(f_bavail=0, f_frsize=4096))
    with pytest.raises(m.ProbeError):
        with m.BoundedTree(source, trusted=True) as tree: tree.copy(destination, owner=0, mode=0o640)
    assert not destination.exists()


def test_shutdown_warning_survives_stop_and_blocks_gate(tmp_path):
    m = module()
    from types import SimpleNamespace
    attachments = tmp_path / 'attachments'; attachments.mkdir()
    warning = attachments / '.probe-app-1-warnings.json'; warning.write_text('{"warning_count":0}')
    class Manager:
        data = {'attempt': 'a' * 32}
        def inspect(self, cid): return {'owned': True}
        def item(self, cid): return {}
        def owned(self, item, obj): pass
        def stop(self, cid): warning.write_text('{"warning_count":1}')
    controller = m.NativeController(SimpleNamespace(workroot=str(tmp_path), source_image=m.SOURCE_IMAGE, candidate_image=m.CANDIDATE_IMAGE,
                                 pg_image='sha256:' + '1' * 64, gate=[]), Manager())
    controller.app = 'a' * 64; controller.app_phase = 'app-1'
    with pytest.raises(m.ProbeError, match='stop_warning'):
        controller.stop_app()
    assert json.loads((tmp_path / 'app-1-app-warnings.json').read_text())['warning_count'] == 1
    assert controller.report['app_summaries']['app-1']['app-warnings.json']['warning_count'] == 1


def test_nonempty_fresh_copy_consumers_and_deletions_are_preserved(tmp_path, monkeypatch):
    m = module(); fake_root_metadata(monkeypatch, m)
    from src.attachments.models import AttachmentLimits, AttachmentChunk, AttachmentLocator
    from src.attachments.store import AttachmentStore
    import hashlib
    source = tmp_path / 'old'; store = AttachmentStore(source, AttachmentLimits())
    ids = []; contracts = {}
    for suffix in ('retained', 'disposable'):
        raw = ('status=ready\n' + m.FIXTURE_MARKER + suffix).encode()
        item = store.create_pending(suffix + '.txt', 'text/plain', raw, kind='text', owner_subject_id=m.SUBJECT_A)
        chunk = AttachmentChunk(suffix, item.source_id, AttachmentLocator(line_start=1, line_end=2), raw.decode(),
                                hashlib.sha256(raw).hexdigest(), 10)
        store.commit_ready(item.attachment_id, chunks=[chunk])
        ids.append(item.attachment_id); contracts[item.attachment_id] = m.fixture_contract(suffix + '.txt', raw)
    destination = tmp_path / 'current'; m.copy_current(source, destination)
    migrated = AttachmentStore(destination, AttachmentLimits())
    for aid in ids: m.consume_fixture(migrated, aid, contracts[aid])
    migrated.delete(ids[1])
    next_copy = tmp_path / 'reforward'; m.copy_current(destination, next_copy)
    forward = AttachmentStore(next_copy, AttachmentLimits())
    m.consume_fixture(forward, ids[0], contracts[ids[0]])
    assert not (next_copy / ids[1]).exists() and (source / ids[1]).is_dir()
    path = next_copy / ids[0] / 'chunks.json'; path.write_text('[]')
    with pytest.raises(m.ProbeError): m.consume_fixture(forward, ids[0], contracts[ids[0]])


@pytest.mark.parametrize('command,controlled', [('run', True), ('cleanup', True), ('_runtime', False)])
def test_entrypoint_signals_are_controller_only(monkeypatch, command, controlled):
    m = module(); observations = []
    from contextlib import contextmanager
    @contextmanager
    def signals():
        observations.append('controlled')
        yield
    monkeypatch.setattr(m, 'controlled_signals', signals)
    monkeypatch.setattr(m, 'main', lambda argv: observations.append(tuple(argv)) or 0)
    assert m.entrypoint([command]) == 0
    assert ('controlled' in observations) is controlled
    assert observations[-1] == (command,)


def test_cleanup_retains_final_phase_warning_after_owned_removal(tmp_path):
    m = module()
    from types import SimpleNamespace
    attachments = tmp_path / 'attachments'; attachments.mkdir()
    (attachments / '.probe-scripted-9-warnings.json').write_text('{"warning_count":2}')
    class Manager:
        data = {'attempt': 'a' * 32}
        def item(self, cid): return {'state': 'absent'}
        def inspect(self, cid): raise AssertionError('removed objects must not be re-inspected for file evidence')
    controller = m.NativeController(SimpleNamespace(workroot=str(tmp_path), source_image=m.SOURCE_IMAGE, candidate_image=m.CANDIDATE_IMAGE,
                                 pg_image='sha256:' + '1' * 64, gate=[]), Manager())
    controller.app = 'a' * 64; controller.app_phase = 'scripted-9'
    controller.capture_app_evidence(removed=True)
    assert controller.report['app_summaries']['scripted-9']['app-warnings.json']['warning_count'] == 2


def add_known_running(m, manager, daemon, role, digit):
    cid = digit * 64; name = 'daq-probe-' + manager.data['attempt'] + '-' + role
    item = {'role': role, 'name': name, 'image': 'sha256:' + '1' * 64, 'id': cid, 'state': 'started'}
    obj = {'Id': cid, 'Name': '/' + name, 'Image': item['image'], 'State': {'Running': True},
           'Config': {'Labels': {m.OWNER_LABEL: manager.data['attempt'], m.OWNER_LABEL + '.role': role}}}
    daemon.objects[cid] = daemon.objects[name] = obj
    manager.data['containers'].append(item); manager.save()
    return item


@pytest.mark.parametrize('failure', ['unknown', 'inspect', 'ownership', 'stop'])
def test_cleanup_aggregates_failure_and_continues_known_objects(tmp_path, failure):
    m = module(); daemon = Daemon(); base = daemon.__call__
    manager = m.OwnedDocker(tmp_path, daemon)
    pg = add_known_running(m, manager, daemon, 'postgres', 'b')
    app = add_known_running(m, manager, daemon, 'app', 'c')
    bad = add_known_running(m, manager, daemon, 'worker', 'd')
    if failure == 'unknown':
        daemon.objects.pop(bad['name']); daemon.objects.pop(bad['id']); bad['id'] = None
    if failure == 'ownership': daemon.objects[bad['id']]['Config']['Labels'] = {}
    def run(args, timeout=30):
        if failure == 'inspect' and args[:2] == ['container', 'inspect'] and args[-1] == bad['name']:
            raise subprocess.TimeoutExpired('docker', timeout)
        if failure == 'stop' and args[0] == 'stop' and args[-1] == bad['id']:
            raise subprocess.TimeoutExpired('docker', timeout)
        return base(args, timeout)
    manager.run = run
    with pytest.raises(m.ProbeError): manager.cleanup()
    journal = json.loads((tmp_path / 'journal.json').read_text())
    assert journal['state'] == 'blocked'
    assert pg['state'] == app['state'] == 'absent'
    assert pg['id'] not in daemon.objects and app['id'] not in daemon.objects
    assert bad['cleanup_status'] == 'blocked' and bad['cleanup_error']
    assert journal['cleanup_errors'] == [{'name': bad['name'], 'category': bad['cleanup_error']}]
    assert not any(call[0] == 'rm' and call[-1] == bad['id'] for call in daemon.calls)


@pytest.mark.parametrize('cleaned', [False, True])
def test_initial_startup_snapshot_precedes_side_effecting_consumer(tmp_path, monkeypatch, cleaned):
    m = module(); fake_root_metadata(monkeypatch, m)
    from types import SimpleNamespace
    from src.attachments.models import AttachmentLimits
    from src.attachments.store import AttachmentStore
    attachments = tmp_path / 'attachments'; attachments.mkdir()
    (attachments / '.partial-synthetic').mkdir(); (attachments / '.batch-synthetic').mkdir()
    for aid in ('retained', 'disposable', 'expired'):
        (attachments / aid).mkdir(); (attachments / aid / 'original.bin').write_bytes(b'old fixture')
    output = tmp_path / 'output'; output.mkdir()
    (output / 'seed.json').write_text(json.dumps({'attachment_id': 'retained', 'disposable_id': 'disposable', 'expired_id': 'expired'}))
    manager = SimpleNamespace(data={'attempt': 'a' * 32})
    controller = m.NativeController(SimpleNamespace(workroot=str(tmp_path), source_image=m.SOURCE_IMAGE, candidate_image=m.CANDIDATE_IMAGE,
                                   pg_image='sha256:' + '1' * 64, gate=[]), manager)
    def startup():
        # Only the simulated APP is allowed to perform this prerequisite work.
        if cleaned:
            (attachments / '.partial-synthetic').rmdir(); (attachments / '.batch-synthetic').rmdir()
            (attachments / 'expired' / 'original.bin').unlink(); (attachments / 'expired').rmdir()
        return {'app_ready': True}
    controller.start_app = startup
    if cleaned:
        assert controller.start_initial_app() == {'app_ready': True}
    else:
        with pytest.raises(m.ProbeError, match='startup_maintenance_failed'): controller.start_initial_app()
        assert (attachments / '.partial-synthetic').is_dir() and (attachments / '.batch-synthetic').is_dir()
    evidence = json.loads((tmp_path / 'startup-maintenance.json').read_text())
    assert evidence['status'] == ('PASS' if cleaned else 'FAIL')
    assert controller.report['startup_maintenance'] == evidence
    # The real later consumer would silently remove both interrupted paths.
    AttachmentStore(attachments, AttachmentLimits())
    assert not (attachments / '.partial-synthetic').exists() and not (attachments / '.batch-synthetic').exists()
    assert json.loads((tmp_path / 'startup-maintenance.json').read_text()) == evidence


def test_startup_readiness_worker_has_no_attachment_maintenance(tmp_path, monkeypatch):
    m = module()
    from types import SimpleNamespace
    import httpx
    import psycopg
    from daq_fae.knowledge import code_identity
    from src.attachments.store import AttachmentStore
    (tmp_path / '.partial-synthetic').mkdir(); (tmp_path / '.batch-synthetic').mkdir()
    (tmp_path / 'warnings.json').write_text('{"warning_count":0}')
    (tmp_path / 'source.json').write_text('{"revision":"synthetic"}')
    def forbidden(*args, **kwargs): raise AssertionError('readiness must not construct a maintenance consumer')
    monkeypatch.setattr(AttachmentStore, '__init__', forbidden)
    monkeypatch.setattr(m, 'runtime_store', forbidden)
    monkeypatch.setattr(m, 'Path', lambda path: {
        '/app/data': tmp_path, '/proc/1/root/tmp/app-warnings.json': tmp_path / 'warnings.json',
        '/app/upstream-source.json': tmp_path / 'source.json'}[path])
    health = {'status': 'ok', 'environment': 'production', 'runtime_release': m.RUNTIME_RELEASE,
              'provider_mode': 'anthropic', 'platform_identity_enabled': True, 'platform_task_enabled': False,
              'attachment_archive_enabled': False, 'session_persistence': 'postgres_daq_authenticated',
              'local_dev_only': False, 'knowledge_release': 'synthetic', 'sdk_knowledge_release': 'synthetic',
              'attachment_archive': {'ready': True}}
    class Readiness:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path): return SimpleNamespace(status_code=200, json=lambda: health)
        def execute(self, sql): return SimpleNamespace(fetchone=lambda: ('ai-daq-fae-agent', True))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: Readiness())
    monkeypatch.setattr(psycopg, 'connect', lambda *args: Readiness())
    monkeypatch.setattr(code_identity, 'daq_code_sha', lambda: 'synthetic')
    monkeypatch.setenv('DAQ_DATABASE_URL', 'synthetic')
    assert m.runtime_startup()['runtime_release'] == m.RUNTIME_RELEASE
    assert (tmp_path / '.partial-synthetic').is_dir() and (tmp_path / '.batch-synthetic').is_dir()


@pytest.mark.parametrize('after_reboot', [False, True])
@pytest.mark.parametrize('other_failure', ['none', 'unknown', 'inspect', 'stop'])
def test_continuous_journal_failure_never_skips_known_cleanup(tmp_path, monkeypatch, after_reboot, other_failure):
    m = module(); daemon = Daemon(); base = daemon.__call__
    monkeypatch.setattr(m, 'boot_identity', lambda: 'old-boot')
    manager = m.OwnedDocker(tmp_path, daemon)
    pg = add_known_running(m, manager, daemon, 'postgres', 'b')
    app = add_known_running(m, manager, daemon, 'app', 'c')
    bad = None
    if other_failure != 'none':
        bad = add_known_running(m, manager, daemon, 'worker', 'd')
        if other_failure == 'unknown':
            daemon.objects.pop(bad['name']); daemon.objects.pop(bad['id']); bad['id'] = None
            manager.save()
    durable_before = (tmp_path / 'journal.json').read_bytes()
    def run(args, timeout=30):
        if bad and ((other_failure == 'inspect' and args[:2] == ['container', 'inspect'] and args[-1] == bad['name'])
                    or (other_failure == 'stop' and args[0] == 'stop' and args[-1] == bad['id'])):
            raise subprocess.TimeoutExpired('docker', timeout)
        return base(args, timeout)
    manager.run = run
    if after_reboot: monkeypatch.setattr(m, 'boot_identity', lambda: 'new-boot')
    writes = []
    def unavailable():
        writes.append(manager.data['state'])
        raise OSError('synthetic continuous journal unavailable')
    monkeypatch.setattr(manager, 'save', unavailable)
    with pytest.raises(m.ProbeError, match='journal_persistence_failed'):
        manager.cleanup(after_reboot=after_reboot)
    assert len(writes) >= 3
    assert manager.data['state'] == 'blocked'
    assert manager.data['journal_persistence_errors']
    assert all(row['category'] == 'journal_persistence_failed' and row['exception_type'] == 'OSError'
               for row in manager.data['journal_persistence_errors'])
    if after_reboot:
        assert manager.data['journal_persistence_errors'][0]['stage'] == 'reboot_evidence'
    assert pg['state'] == app['state'] == 'absent'
    assert all(any(call[0] == 'stop' and call[-1] == item['id'] for call in daemon.calls) for item in (pg, app))
    assert pg['id'] not in daemon.objects and app['id'] not in daemon.objects
    if other_failure in {'inspect', 'stop'}:
        assert bad['cleanup_error'] == 'command_timeout'
    if other_failure == 'unknown' and not after_reboot:
        assert bad['cleanup_error'] == 'unresolved_create'
    assert (tmp_path / 'journal.json').read_bytes() == durable_before
    assert json.loads(durable_before)['state'] != 'released'


@pytest.mark.parametrize('prefix', ['Error: No such container: ', 'Error response from daemon: No such container: '])
@pytest.mark.parametrize('stdout', ['', '[]\n'])
def test_inspect_supported_absence_protocol_is_exact(tmp_path, prefix, stdout):
    m = module(); target = 'a' * 64
    manager = m.OwnedDocker(tmp_path, lambda args, **kwargs: subprocess.CompletedProcess(args, 1, stdout, prefix + target + '\n'))
    assert manager.inspect(target) is None


@pytest.mark.parametrize('rc,stdout,stderr', [
    (2, '[]\n', 'Error response from daemon: No such container: TARGET\n'),
    (125, '', 'Error: No such container: TARGET'),
    (1, '[{}]', 'Error response from daemon: No such container: TARGET'),
    (1, '{}', 'Error response from daemon: No such container: TARGET'),
    (1, '[]\nextra', 'Error response from daemon: No such container: TARGET'),
    (1, 'null', 'Error response from daemon: No such container: TARGET'),
    (1, '[]', 'Error response from daemon: No such container: WRONG'),
    (1, '[]', 'Error response from daemon: No such container: TARGET-extra'),
    (1, '[]', 'Error response from daemon: No such container: TARGET\nextra'),
    (1, '', 'unrecognized daemon error TARGET'),
])
def test_inspect_absence_rejects_ambiguous_protocol(tmp_path, rc, stdout, stderr):
    m = module(); target = 'a' * 64
    manager = m.OwnedDocker(tmp_path, lambda args, **kwargs: subprocess.CompletedProcess(
        args, rc, stdout, stderr.replace('TARGET', target)))
    with pytest.raises(m.ProbeError, match='inspect_unknown'): manager.inspect(target)


@pytest.mark.parametrize('known', [True, False])
def test_modern_absence_reconciles_known_only(tmp_path, known):
    m = module(); daemon = Daemon(); manager = m.OwnedDocker(tmp_path, daemon)
    item = add_known_running(m, manager, daemon, 'app', 'c')
    if not known: item['id'] = None; manager.save()
    manager.run = lambda args, **kwargs: subprocess.CompletedProcess(args, 1, '[]\n',
        'Error response from daemon: No such container: ' + args[-1] + '\n')
    if known:
        manager.cleanup(); assert manager.data['state'] == 'released'
    else:
        with pytest.raises(m.ProbeError, match='unresolved_create'): manager.cleanup()
        assert manager.data['state'] == 'blocked'


@pytest.mark.parametrize('missing', ['src.agent.session', 'psycopg', None, '../secret', 'module\nsecret', 'a' * 161])
def test_runtime_missing_module_evidence_is_bounded_and_private(tmp_path, monkeypatch, capsys, missing):
    m = module(); actual_path = Path
    from types import SimpleNamespace
    monkeypatch.setattr(m, 'runtime_isolation_check', lambda: None)
    monkeypatch.setattr(m, 'Path', lambda value: tmp_path if value == '/probe-output' else actual_path(value))
    monkeypatch.setenv('DAQ_NATIVE_PROBE_PHASE', 'migrate-1')
    def missing_import(): raise ModuleNotFoundError('private-token raw message must never appear', name=missing)
    monkeypatch.setattr(m, 'runtime_migrate', missing_import)
    assert m.runtime_main(SimpleNamespace(phase='migrate')) == 1
    raw = (tmp_path / 'migrate-1.json').read_text(); receipt = json.loads(raw)
    assert receipt['exception_type'] == 'ModuleNotFoundError' and receipt['status'] == 'FAIL'
    if missing in {'src.agent.session', 'psycopg'}: assert receipt['missing_module'] == missing
    else: assert 'missing_module' not in receipt
    assert 'private-token' not in raw and 'raw message' not in raw and 'traceback' not in raw
    assert capsys.readouterr().out == ''


def _synthetic_images(m):
    import copy
    config = {'User': '', 'Labels': {'existing': 'yes'}, 'Env': ['SECRET=fixture'], 'Cmd': ['run']}
    base = {'Id': m.SOURCE_IMAGE, 'Architecture': 'amd64', 'Os': 'linux',
            'RootFS': {'Layers': ['sha256:old1', 'sha256:old2']}, 'Config': config}
    candidate = copy.deepcopy(base)
    candidate['Id'] = m.CANDIDATE_IMAGE
    candidate['RootFS']['Layers'] += ['sha256:new1', 'sha256:new2']
    candidate['Config']['User'] = '21081:21081'
    candidate['Config']['Labels'].update(m.CANDIDATE_LABELS)
    return base, candidate


def test_dual_image_audit_preserves_layers_and_config_without_secret_output():
    m = module(); base, candidate = _synthetic_images(m)
    result = m.audit_image_pair(base, candidate)
    assert result['base_layers_preserved'] and result['application_config_preserved']
    assert 'SECRET=fixture' not in json.dumps(result)
    assert result['candidate_image'] == m.CANDIDATE_IMAGE


@pytest.mark.parametrize('mutation', ['layers', 'user', 'policy_label', 'base_label', 'recipe_label', 'env', 'architecture'])
def test_dual_image_audit_rejects_wrong_ancestry_or_config(mutation):
    m = module(); base, candidate = _synthetic_images(m)
    if mutation == 'layers': candidate['RootFS']['Layers'][0] = 'sha256:foreign'
    elif mutation == 'user': candidate['Config']['User'] = '0:0'
    elif mutation.endswith('_label'):
        key = {'policy_label': m.POLICY_LABEL, 'base_label': m.BASE_LABEL, 'recipe_label': m.RECIPE_LABEL}[mutation]
        candidate['Config']['Labels'][key] = 'wrong'
    elif mutation == 'env': candidate['Config']['Env'].append('CHANGED=value')
    else: candidate['Architecture'] = 'arm64'
    with pytest.raises(m.ProbeError): m.audit_image_pair(base, candidate)


def test_candidate_argument_is_required_and_exact_before_mutation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    m = module()
    parser = m.argument_parser()
    assert '--candidate-image' in parser._subparsers._group_actions[0].choices['run'].format_help()
    run_args = ['run']
    for key in ('main-root', 'sdk-root', 'public-approval-key', 'expected-public-sha',
                'source-image', 'pg-image', 'workroot', 'runtime-release', 'main-internal', 'sdk-internal'):
        run_args.extend(['--' + key, 'fixture'])
    with pytest.raises(SystemExit): parser.parse_args(run_args)
    run_args.extend(['--candidate-image', 'wrong'])
    calls = []
    monkeypatch.setattr(m, 'docker_command', lambda *a: calls.append(a))
    with pytest.raises(m.ProbeError): m.main(run_args)
    assert not calls
    assert not list(tmp_path.iterdir())
    with pytest.raises(m.ProbeError):
        m.validate_image_arguments(SimpleNamespace(source_image=m.SOURCE_IMAGE, candidate_image='sha256:' + '0' * 64))
    with pytest.raises(m.ProbeError):
        m.validate_image_arguments(SimpleNamespace(source_image=m.SOURCE_IMAGE))
    m.validate_image_arguments(SimpleNamespace(source_image=m.SOURCE_IMAGE, candidate_image=m.CANDIDATE_IMAGE))


@pytest.mark.parametrize('root_mode', [False, True])
@pytest.mark.parametrize('kind', ['worker', 'app'])
def test_phase_selected_image_and_current_rw_mounts(tmp_path, monkeypatch, root_mode, kind):
    from types import SimpleNamespace
    m = module()
    class Stop(Exception): pass
    class Manager:
        data = {'attempt': 'test'}
        def create(self, role, image, flags, command):
            observed.update(image=image, flags=flags)
            raise Stop()
    args = SimpleNamespace(workroot=str(tmp_path), source_image=m.SOURCE_IMAGE,
                           candidate_image=m.CANDIDATE_IMAGE, pg_image='sha256:'+'1'*64, gate=[])
    controller = m.NativeController(args, Manager()); controller.pg = 'a' * 64
    monkeypatch.setattr(controller, 'save', lambda: None)
    observed = {}
    with pytest.raises(Stop):
        if kind == 'worker': controller.worker('rollback' if root_mode else 'migrate', root_mode=root_mode)
        else: controller.start_app(root_mode=root_mode)
    assert observed['image'] == (m.SOURCE_IMAGE if root_mode else m.CANDIDATE_IMAGE)
    flags = '\n'.join(observed['flags'])
    assert str(controller.attachments) in flags and str(controller.trace) in flags
    assert controller.report['phases'][-1]['image'] == observed['image']


def test_dual_image_inspection_is_readonly_and_rejects_before_control_creation(monkeypatch):
    m = module(); base, candidate = _synthetic_images(m)
    pg = {'Id': 'sha256:' + '1'*64, 'Architecture': 'amd64', 'Os': 'linux'}
    class Result:
        returncode = 0
        def __init__(self, obj): self.stdout = json.dumps([obj])
    calls = []
    def inspect(cmd):
        calls.append(cmd)
        return Result({m.SOURCE_IMAGE: base, m.CANDIDATE_IMAGE: candidate, pg['Id']: pg}[cmd[-1]])
    result = m.inspect_image_inputs(m.SOURCE_IMAGE, m.CANDIDATE_IMAGE, pg['Id'], inspect)
    assert len(calls) == 3 and all(cmd[:2] == ['image', 'inspect'] for cmd in calls)
    assert result['base_layers_preserved']
    candidate['Config']['User'] = '0:0'
    with pytest.raises(m.ProbeError):
        m.inspect_image_inputs(m.SOURCE_IMAGE, m.CANDIDATE_IMAGE, pg['Id'], inspect)
