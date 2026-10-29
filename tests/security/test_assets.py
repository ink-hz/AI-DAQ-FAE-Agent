import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

MODULE = Path('deploy/security_assets.py')
if MODULE.exists():
    spec = importlib.util.spec_from_file_location('security_assets', MODULE)
    assets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(assets)
else:
    assets = None

SECRETS = ['session-keyring.json', 'content-keyring.json', 'entitlements.json', 'feedback-approval-keys-20261023.json']


def identity(data):
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    if assets is not None:
        monkeypatch.setattr(assets, '_is_root', lambda: True)
        monkeypatch.setattr(assets, '_trusted_parent', lambda st: True)
        monkeypatch.setattr(assets.os, 'fchown', lambda *args: None)
        monkeypatch.setattr(assets, '_owner_mode', lambda st, uid, gid, mode: stat.S_IMODE(st.st_mode) == mode)
        monkeypatch.setattr(assets, '_check_compose_binary', lambda runtime: {'synthetic_boundary': True})
    paths = {}
    expected = {}
    for name in ['main', 'sdk', 'secrets', 'attachments']:
        relative = {'main': 'knowledge/reviewed-thinking-prefix-20261028', 'sdk': 'knowledge/sdk-thinking-prefix-20261028'}.get(name, name)
        p = tmp_path / relative
        p.mkdir(parents=True)
        paths[name] = str(p)
        files = SECRETS if name == 'secrets' else ['nested/file']
        expected[name] = {}
        for f in files:
            content = (name + f).encode()
            file = p / f
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
            expected[name][f] = identity(content)
    trace = tmp_path / 'daq_traces.jsonl'
    trace.write_bytes(b'old trace\n')
    paths['trace'] = str(trace)
    expected['trace'] = {'daq_traces.jsonl': identity(b'old trace\n')}
    return tmp_path, {'paths': paths, 'expected': expected, 'uid': 21081, 'gid': 21081}



def add_runtime_evidence(root, spec):
    config = root / 'old-compose.json'
    override = root / 'override.json'
    runtime = spec['runtime']
    service = {'image': 'old', 'container_name': 'ai-daq-fae-backend', 'restart': 'unless-stopped', 'env_file': runtime['env_file'], 'volumes': [str(root) + ':/app/data', runtime['old_secrets_dir'] + ':/run/daq-secrets:ro'], 'ports': ['127.0.0.1:8081:8081'], 'networks': {'camera-db': {}, 'platform-edge': {'ipv4_address': '172.31.0.9'}}, 'healthcheck': {'test': ['CMD', 'curl', '-fsS', 'http://127.0.0.1:8081/health'], 'interval': '15s', 'timeout': '5s', 'retries': 12}}
    config.write_text(json.dumps({'services': {'ai-daq-fae-backend': service}, 'networks': {'camera-db': {'external': True, 'name': 'deploy_default'}, 'platform-edge': {'external': True, 'name': 'orbbec-agent-platform-edge'}}}))
    override.write_text(json.dumps({'services': {'ai-daq-fae-backend': {'environment': {'SYNTHETIC_SETTING': 'present'}, 'mem_limit': '512m', 'logging': {'driver': 'json-file', 'options': {'max-size': '10m'}}}}}))
    labels = root / 'labels.json'
    labels.write_text(json.dumps({'com.docker.compose.project': runtime['project'], 'com.docker.compose.service': 'ai-daq-fae-backend', 'com.docker.compose.project.config_files': str(config) + ',' + str(override), 'com.docker.compose.project.working_dir': str(root)}))
    labels.chmod(0o600)
    ctx = root / 'interpolation.env'
    ctx.write_text('')
    ctx.chmod(0o600)
    runtime.update({'project_directory': str(root), 'old_data_dir': str(root), 'labels_file': str(labels), 'config_files': {str(config): hashlib.sha256(config.read_bytes()).hexdigest(), str(override): hashlib.sha256(override.read_bytes()).hexdigest()}, 'config_identity': 'sha256:' + hashlib.sha256(labels.read_bytes()).hexdigest(), 'compose_binary': '/opt/homebrew/bin/docker-compose', 'compose_sha256': 'ignored-under-test', 'interpolation_file': str(ctx), 'interpolation_sha256': hashlib.sha256(ctx.read_bytes()).hexdigest()})


def test_staging_exact_scope_and_private_metadata(setup):
    assert assets is not None, 'security asset implementation missing'
    root, spec = setup
    result = assets.stage_ro(spec, root / 'out')
    assert result['matched'] is True
    assert (root / 'out/main/nested/file').read_bytes() == b'mainnested/file'
    assert set(p.name for p in (root / 'out/secrets').iterdir()) == set(SECRETS)
    assert (root / 'out/secrets/session-keyring.json').stat().st_mode & 0o777 == 0o600
    assert (root / 'out/secrets/entitlements.json').stat().st_mode & 0o777 == 0o640
    assert (root / 'out/manifest.json').stat().st_mode & 0o777 == 0o600
    assert b'secret' not in (root / 'out/manifest.json').read_bytes().lower().replace(b'secrets', b'')


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo'])
def test_refuses_unsafe_source_files(setup, kind):
    assert assets is not None, 'security asset implementation missing'
    root, spec = setup
    path = root / 'knowledge/reviewed-thinking-prefix-20261028/nested/file'
    if kind == 'hardlink':
        os.link(path, root / 'hard')
    else:
        path.unlink()
        if kind == 'symlink':
            path.symlink_to(root / 'knowledge/sdk-thinking-prefix-20261028/nested/file')
        else:
            os.mkfifo(path)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')
    assert json.loads((root / 'out/failure.json').read_text())['status'] == 'failed'


def test_refuses_target_collision_and_hash_drift(setup):
    assert assets is not None, 'security asset implementation missing'
    root, spec = setup
    (root / 'out').mkdir()
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')
    (root / 'knowledge/reviewed-thinking-prefix-20261028/nested/file').write_bytes(b'modified')
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out2')


def test_capture_requires_controller_evidence_and_preserves_bytes(setup):
    assert assets is not None, 'security asset implementation missing'
    root, spec = setup
    with pytest.raises(assets.AssetError):
        assets.capture_rw(spec, root / 'bad', {})
    evidence = {'container_id': 'a' * 64, 'image_id': assets.IMAGE, 'stopped_at': '2026-10-29T00:00:00Z', 'quiescence_evidence': 'sha256:' + 'b' * 64}
    result = assets.capture_rw(spec, root / 'out', evidence)
    assert result['stopped_evidence']['verification'] == 'controller_asserted_not_verified'
    assert (root / 'out/attachments/nested/file').read_bytes() == b'attachmentsnested/file'
    assert (root / 'out/trace/daq_traces.jsonl').read_bytes() == b'old trace\n'


def test_source_mutation_after_copy_refused(setup, monkeypatch):
    assert assets is not None
    root, spec = setup
    original = assets._copy_file
    changed = False
    def copy_then_mutate(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            (root / 'knowledge/reviewed-thinking-prefix-20261028/nested/file').write_bytes(b'changed after copy')
            changed = True
        return result
    monkeypatch.setattr(assets, '_copy_file', copy_then_mutate)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')


def test_published_root_traversable_but_metadata_private(setup):
    assert assets is not None
    root, spec = setup
    assets.stage_ro(spec, root / 'out')
    assert (root / 'out').stat().st_mode & 0o777 == 0o750
    assert (root / 'out/manifest.json').stat().st_mode & 0o777 == 0o600


def test_render_pins_image_and_rollback_uses_new_data(setup):
    assert assets is not None
    root, spec = setup
    assets.stage_ro(spec, root / 'ro')
    evidence = {'container_id': 'a' * 64, 'image_id': assets.IMAGE, 'stopped_at': '2026-10-29T00:00:00Z', 'quiescence_evidence': 'sha256:' + 'b' * 64}
    assets.capture_rw(spec, root / 'rw', evidence)
    env = root / 'production.env'
    env.write_text('\n'.join(f'{k}={v}' for k, v in assets.REQUIRED_ENV.items()) + '\nDAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=' + spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256'] + '\n')
    old = Path(spec['paths']['secrets'])
    spec['runtime'] = {'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw'), 'env_file': str(env), 'env_sha256': hashlib.sha256(env.read_bytes()).hexdigest(), 'old_secrets_dir': str(old), 'project': 'deploy', 'config_identity': 'sha256:' + 'c' * 64}
    add_runtime_evidence(root, spec)
    assets.render(spec, root / 'configs')
    candidate = json.loads((root / 'configs/runtime.json').read_text())
    rollback = json.loads((root / 'configs/rollback.json').read_text())
    a = candidate['services']['ai-daq-fae-backend']
    b = rollback['services']['ai-daq-fae-backend']
    assert a['image'] == b['image'] == assets.IMAGE
    assert a['pull_policy'] == b['pull_policy'] == 'never'
    assert a['environment']['SYNTHETIC_SETTING'] == b['environment']['SYNTHETIC_SETTING'] == 'present'
    assert a['mem_limit'] == b['mem_limit'] == '536870912'
    assert a['logging'] == b['logging']
    assert a['user'] == '21081:21081' and b['user'] == '0:0'
    assert 'cap_drop' not in b and 'read_only' not in b
    mounts_a = {v['target']: v['source'] for v in a['volumes']}
    mounts_b = {v['target']: v['source'] for v in b['volumes']}
    for path in ['/app/data/attachments', '/app/data/daq_traces.jsonl']:
        assert mounts_a[path] == mounts_b[path]
    assert mounts_b['/run/daq-secrets'] == str(old)
    assert '/app/data' not in mounts_a and '/app/data' not in mounts_b
    assert assets.verify(spec, root / 'configs')['matched'] is True
    a['cap_drop'] = []
    (root / 'configs/runtime.json').write_text(json.dumps(candidate))
    with pytest.raises(assets.AssetError):
        assets.verify(spec, root / 'configs')


def test_render_rejects_env_identity_drift(setup):
    assert assets is not None
    root, spec = setup
    assets.stage_ro(spec, root / 'ro')
    env = root / 'production.env'
    env.write_text('DAQ_PLATFORM_IDENTITY_ENABLED=false\n')
    spec['runtime'] = {'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw'), 'env_file': str(env), 'env_sha256': '0'*64, 'old_secrets_dir': str(root / 'old'), 'project': 'deploy', 'config_identity': 'sha256:' + 'c' * 64}
    with pytest.raises(assets.AssetError):
        assets.render(spec, root / 'configs')


def test_trace_mutation_after_copy_refused(setup, monkeypatch):
    assert assets is not None
    root, spec = setup
    original = assets._copy_file
    def copy_then_mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[1] == 'daq_traces.jsonl':
            (root / 'daq_traces.jsonl').write_bytes(b'new trace')
        return result
    monkeypatch.setattr(assets, '_copy_file', copy_then_mutate)
    evidence = {'container_id': 'a' * 64, 'image_id': assets.IMAGE, 'stopped_at': '2026-10-29T00:00:00Z', 'quiescence_evidence': 'sha256:' + 'b' * 64}
    with pytest.raises(assets.AssetError):
        assets.capture_rw(spec, root / 'out', evidence)


def test_render_rejects_manifest_drift_from_expected(setup):
    assert assets is not None
    root, spec = setup
    assets.stage_ro(spec, root / 'ro')
    evidence = {'container_id': 'a' * 64, 'image_id': assets.IMAGE, 'stopped_at': '2026-10-29T00:00:00Z', 'quiescence_evidence': 'sha256:' + 'b' * 64}
    assets.capture_rw(spec, root / 'rw', evidence)
    env = root / 'production.env'
    env.write_text('\n'.join(f'{k}={v}' for k, v in assets.REQUIRED_ENV.items()) + '\nDAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=' + spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256'] + '\n')
    old = root / 'old-secrets'
    old.mkdir()
    spec['runtime'] = {'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw'), 'env_file': str(env), 'env_sha256': hashlib.sha256(env.read_bytes()).hexdigest(), 'old_secrets_dir': str(old), 'project': 'deploy', 'config_identity': 'sha256:' + 'c' * 64}
    spec['expected']['main']['nested/file']['sha256'] = '0' * 64
    add_runtime_evidence(root, spec)
    with pytest.raises(assets.AssetError):
        assets.render(spec, root / 'configs')


def test_cli_reports_only_sanitized_result(setup, capsys):
    assert assets is not None
    root, spec = setup
    path = root / 'spec.json'
    path.write_text(json.dumps(spec))
    path.chmod(0o600)
    assert assets.main(['stage-ro', '--spec', str(path), '--target', str(root / 'out')]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {'stage': 'stage-ro', 'matched': True}
    assert captured.err == ''
    assert 'session-keyring' not in captured.out


def test_cli_rejects_unprivate_spec(setup, capsys):
    assert assets is not None
    root, spec = setup
    path = root / 'spec.json'
    path.write_text(json.dumps(spec))
    path.chmod(0o644)
    assert assets.main(['stage-ro', '--spec', str(path), '--target', str(root / 'out')]) != 0
    assert not (root / 'out').exists()
    assert 'session-keyring' not in capsys.readouterr().err


def test_env_validation_requires_production_and_approval_pin(setup):
    assert assets is not None
    root, spec = setup
    env = root / 'env'
    env.write_text('\n'.join(f'{k}={v}' for k, v in assets.REQUIRED_ENV.items()) + '\nDAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=' + '0' * 64 + '\n')
    with pytest.raises(assets.AssetError):
        assets._validate_env(str(env), hashlib.sha256(env.read_bytes()).hexdigest(), spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256'])


def test_target_mutation_before_publication_refused(setup, monkeypatch):
    assert assets is not None
    root, spec = setup
    original = assets._copy_file
    changed = False
    def copy_then_corrupt(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            (root / 'out/main/nested/file').write_bytes(b'bad target')
            changed = True
        return result
    monkeypatch.setattr(assets, '_copy_file', copy_then_corrupt)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')


def test_rejects_untrusted_target_ancestor(setup, monkeypatch):
    assert assets is not None
    root, spec = setup
    child = root / 'parent'
    child.mkdir()
    denied_inode = root.stat().st_ino
    monkeypatch.setattr(assets, '_trusted_parent', lambda st: st.st_ino != denied_inode)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, child / 'out')
    assert not (child / 'out').exists()


def test_invalid_evidence_writes_private_failure_receipt(setup):
    assert assets is not None
    root, spec = setup
    with pytest.raises(assets.AssetError):
        assets.capture_rw(spec, root / 'out', {})
    receipt = root / 'out/failure.json'
    assert json.loads(receipt.read_text())['status'] == 'failed'
    assert receipt.stat().st_mode & 0o777 == 0o600


def test_render_validation_failure_writes_private_receipt(setup):
    assert assets is not None
    root, spec = setup
    spec['runtime'] = {}
    with pytest.raises(assets.AssetError):
        assets.render(spec, root / 'configs')
    assert json.loads((root / 'configs/failure.json').read_text())['status'] == 'failed'


def test_stopped_evidence_rejects_fabricated_short_identity(setup):
    assert assets is not None
    root, spec = setup
    evidence = {'container_id': 'abc', 'image_id': assets.IMAGE, 'stopped_at': 'not a time', 'quiescence_evidence': 'receipt'}
    with pytest.raises(assets.AssetError):
        assets.capture_rw(spec, root / 'out', evidence)


def test_render_rejects_stage_with_failure_receipt(setup):
    assert assets is not None
    root, spec = setup
    assets.stage_ro(spec, root / 'ro')
    evidence = {'container_id': 'a' * 64, 'image_id': assets.IMAGE, 'stopped_at': '2026-10-29T00:00:00Z', 'quiescence_evidence': 'sha256:' + 'b' * 64}
    assets.capture_rw(spec, root / 'rw', evidence)
    (root / 'ro/failure.json').write_text('{}')
    env = root / 'production.env'
    env.write_text('\n'.join(f'{k}={v}' for k, v in assets.REQUIRED_ENV.items()) + '\nDAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=' + spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256'] + '\n')
    old = root / 'old-secrets'
    old.mkdir()
    spec['runtime'] = {'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw'), 'env_file': str(env), 'env_sha256': hashlib.sha256(env.read_bytes()).hexdigest(), 'old_secrets_dir': str(old), 'project': 'deploy', 'config_identity': 'sha256:' + 'c' * 64}
    add_runtime_evidence(root, spec)
    with pytest.raises(assets.AssetError):
        assets.render(spec, root / 'configs')


def test_render_rejects_different_rollback_secrets(setup):
    assert assets is not None
    root, spec = setup
    assets.stage_ro(spec, root / 'ro')
    evidence = {'container_id': 'a' * 64, 'image_id': assets.IMAGE, 'stopped_at': '2026-10-29T00:00:00Z', 'quiescence_evidence': 'sha256:' + 'b' * 64}
    assets.capture_rw(spec, root / 'rw', evidence)
    env = root / 'production.env'
    env.write_text('\n'.join(f'{k}={v}' for k, v in assets.REQUIRED_ENV.items()) + '\nDAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=' + spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256'] + '\n')
    old = root / 'old-secrets'
    old.mkdir()
    spec['runtime'] = {'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw'), 'env_file': str(env), 'env_sha256': hashlib.sha256(env.read_bytes()).hexdigest(), 'old_secrets_dir': str(old), 'project': 'deploy', 'config_identity': 'sha256:' + 'c' * 64}
    add_runtime_evidence(root, spec)
    with pytest.raises(assets.AssetError):
        assets.render(spec, root / 'configs')


def test_deployment_identity_validates_labels_and_config_bytes(setup):
    assert assets is not None
    root, _ = setup
    config = root / 'old-compose.yml'
    config.write_text('services: {}\n')
    labels = root / 'labels.json'
    labels.write_text(json.dumps({'com.docker.compose.project': 'deploy', 'com.docker.compose.service': 'ai-daq-fae-backend', 'com.docker.compose.project.config_files': str(config), 'com.docker.compose.project.working_dir': str(root)}))
    labels.chmod(0o600)
    runtime = {'project': 'deploy', 'project_directory': str(root), 'labels_file': str(labels), 'config_files': {str(config): hashlib.sha256(config.read_bytes()).hexdigest()}}
    assets._validate_deployment(runtime)
    config.write_text('changed')
    with pytest.raises(assets.AssetError):
        assets._validate_deployment(runtime)


def test_target_cannot_be_created_within_source_tree(setup):
    assert assets is not None
    root, spec = setup
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'knowledge/reviewed-thinking-prefix-20261028/new-target')
    assert not (root / 'knowledge/reviewed-thinking-prefix-20261028/new-target').exists()


def test_symlink_in_source_root_path_rejected(setup):
    assert assets is not None
    root, spec = setup
    alias = root / 'alias'
    alias.symlink_to(root / 'knowledge/reviewed-thinking-prefix-20261028', target_is_directory=True)
    spec['paths']['main'] = str(alias)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')


def test_source_mode_change_after_copy_refused(setup, monkeypatch):
    assert assets is not None
    root, spec = setup
    original = assets._copy_file
    changed = False
    def copy_then_chmod(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            (root / 'knowledge/reviewed-thinking-prefix-20261028/nested/file').chmod(0o600)
            changed = True
        return result
    monkeypatch.setattr(assets, '_copy_file', copy_then_chmod)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')


def test_source_directory_mode_change_after_copy_refused(setup, monkeypatch):
    assert assets is not None
    root, spec = setup
    original = assets._copy_file
    count = 0
    def copy_then_chmod(*args, **kwargs):
        nonlocal count
        result = original(*args, **kwargs)
        count += 1
        if count == 2:
            (root / 'knowledge/reviewed-thinking-prefix-20261028/nested').chmod(0o700)
        return result
    monkeypatch.setattr(assets, '_copy_file', copy_then_chmod)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')


def test_ro_staging_does_not_require_live_runtime_data(setup):
    assert assets is not None
    root, spec = setup
    spec['paths']['attachments'] = str(root / 'not-yet-captured')
    spec['paths']['trace'] = str(root / 'missing-trace')
    assert assets.stage_ro(spec, root / 'out')['matched'] is True


def test_path_rejects_compose_interpolation_syntax():
    assert assets is not None
    with pytest.raises(assets.AssetError):
        assets._parts('/tmp/${UNTRUSTED}/asset')


def test_directory_depth_is_bounded(setup, monkeypatch):
    assert assets is not None
    root, spec = setup
    deep = root / 'knowledge/reviewed-thinking-prefix-20261028/nested/deeper'
    deep.mkdir()
    (deep / 'file').write_bytes(b'x')
    spec['expected']['main']['nested/deeper/file'] = identity(b'x')
    monkeypatch.setattr(assets, 'MAX_DEPTH', 1, raising=False)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')


def test_failure_receipt_explains_identity_mismatch_without_contents(setup):
    assert assets is not None
    root, spec = setup
    (root / 'knowledge/reviewed-thinking-prefix-20261028/nested/file').write_bytes(b'X' * len((root / 'knowledge/reviewed-thinking-prefix-20261028/nested/file').read_bytes()))
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')
    receipt = (root / 'out/failure.json').read_text()
    assert 'source identity mismatch' in receipt
    assert 'synthetic confidential content' not in receipt
