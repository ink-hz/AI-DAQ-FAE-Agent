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
    assert result['seeded'] == 1
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
        upload = client.post('/attachments', files=[('files', ('probe.txt', b'status=ready\n', 'text/plain'))])
        assert upload.status_code == 201
        aid = upload.json()['results'][0]['attachment']['attachment_id']
        response = client.post('/chat', json={'message': 'Read this synthetic log: status=ready.', 'attachment_ids': [aid]})
        done = [json.loads(block.split('data: ', 1)[1]) for block in response.text.strip().split('\n\n')
                if block.startswith('event: done')][0]
        assert done['outcome'] == 'safe_abstained', done
        assert done['fallback_used'] is False
        assert {'search_attachments', 'read_attachment'} <= {item['tool'] for item in done['tool_calls']}


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
