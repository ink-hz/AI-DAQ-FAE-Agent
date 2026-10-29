import hashlib
import json
import os
from pathlib import Path

import pytest

from test_assets import assets, setup, identity, add_runtime_evidence


def test_multiline_env_is_not_interpreted_as_assignments(setup):
    root, spec = setup
    env = root / 'env'
    body = '\n'.join(f'{k}={v}' for k, v in assets.REQUIRED_ENV.items())
    pin = spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256']
    env.write_text("WRAPPER='header\n" + body + '\nDAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=' + pin + "\nTAIL=footer'\n")
    spec['runtime'] = {'env_file': str(env), 'old_secrets_dir': spec['paths']['secrets'], 'project': 'deploy'}
    add_runtime_evidence(root, spec)
    effective = assets._compose_model(spec['runtime'], True)['services']['ai-daq-fae-backend']['environment']
    assert effective['WRAPPER'].startswith('header\n')
    assert 'DAQ_PLATFORM_IDENTITY_ENABLED' not in effective
    with pytest.raises(assets.AssetError):
        assets._validate_env(str(env), hashlib.sha256(env.read_bytes()).hexdigest(), pin, effective)


def test_ordered_compose_override_is_preserved_or_rejected(setup):
    root, spec = setup
    env = root / 'env'
    env.write_text('SYNTHETIC_ENV=1\n')
    runtime = spec['runtime'] = {'env_file': str(env), 'old_secrets_dir': spec['paths']['secrets'], 'old_data_dir': str(root), 'project': 'deploy', 'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw')}
    add_runtime_evidence(root, spec)
    model = assets._compose_model(runtime, resolve_env=False)
    derived = assets._config(runtime, False, model)
    service = derived['services']['ai-daq-fae-backend']
    assert service['environment']['SYNTHETIC_SETTING'] == 'present'
    assert service['mem_limit'] == '536870912'
    assert service['logging']['options']['max-size'] == '10m'


def test_published_tree_rejects_same_bytes_new_inode_and_empty_dir(setup):
    root, spec = setup
    manifest = assets.stage_ro(spec, root / 'out')
    file = root / 'out/main/nested/file'
    content = file.read_bytes()
    os.rename(file, file.with_name('original'))
    file.write_bytes(content)
    file.chmod(0o640)
    file.with_name('original').unlink()
    (root / 'out/main/empty').mkdir(mode=0o750)
    with pytest.raises(assets.AssetError):
        assets._verify_published_tree(str(root / 'out/main'), manifest['target_trees']['main'])


def test_preflight_rejects_oversize_inventory_before_copy(setup, monkeypatch):
    root, spec = setup
    monkeypatch.setattr(assets, 'MAX_NODES', 2, raising=False)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')
    assert not (root / 'out/main').exists()


@pytest.mark.parametrize('change', ['file_inode', 'directory_inode', 'add_empty', 'remove_empty', 'file_mode'])
def test_published_target_metadata_and_empty_directory_fences(setup, change):
    root, spec = setup
    source = root / 'knowledge/reviewed-thinking-prefix-20261028'
    (source / 'empty').mkdir()
    manifest = assets.stage_ro(spec, root / 'out')
    target = root / 'out/main'
    if change == 'file_inode':
        file = target / 'nested/file'
        data = file.read_bytes()
        os.rename(file, file.with_name('prior'))
        file.write_bytes(data)
        file.chmod(0o640)
        file.with_name('prior').unlink()
    elif change == 'directory_inode':
        os.rename(target / 'empty', target / 'prior')
        (target / 'empty').mkdir(mode=0o750)
        (target / 'prior').rmdir()
    elif change == 'add_empty':
        (target / 'extra').mkdir(mode=0o750)
    elif change == 'remove_empty':
        (target / 'empty').rmdir()
    else:
        (target / 'nested/file').chmod(0o600)
    with pytest.raises(assets.AssetError):
        assets._verify_published_tree(str(target), manifest['target_trees']['main'])


def test_preflight_limits_at_boundary_and_reserves_disk(setup, monkeypatch):
    from types import SimpleNamespace
    root, spec = setup
    first = assets.stage_ro(spec, root / 'first')
    count = first['preflight']['entries']
    total = first['preflight']['bytes']
    bound = first['preflight']['manifest_bound']
    monkeypatch.setattr(assets, 'MAX_NODES', count)
    monkeypatch.setattr(assets, 'MAX_TOTAL', total)
    monkeypatch.setattr(assets, 'MAX_MANIFEST', bound)
    assert assets.stage_ro(spec, root / 'at-boundary')['matched'] is True
    monkeypatch.setattr(assets, 'MAX_NODES', count - 1)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'too-many')
    monkeypatch.setattr(assets, 'MAX_NODES', count)
    monkeypatch.setattr(assets, 'MAX_TOTAL', total - 1)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'too-large')
    monkeypatch.setattr(assets, 'MAX_TOTAL', total)
    monkeypatch.setattr(assets, 'MAX_MANIFEST', bound - 1)
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'manifest-too-large')
    monkeypatch.setattr(assets, 'MAX_MANIFEST', bound)
    monkeypatch.setattr(assets.os, 'fstatvfs', lambda fd: SimpleNamespace(f_bavail=1, f_frsize=4096))
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'no-space')
    assert 'insufficient free space' in (root / 'no-space/failure.json').read_text()


def test_compose_effective_quoted_interpolation_and_private_render(setup, capsys):
    root, spec = setup
    assets.stage_ro(spec, root / 'ro')
    evidence = {'container_id': 'a' * 64, 'image_id': assets.IMAGE, 'stopped_at': '2026-10-29T00:00:00Z', 'quiescence_evidence': 'sha256:' + 'b' * 64}
    assets.capture_rw(spec, root / 'rw', evidence)
    env = root / 'production.env'
    values = dict(assets.REQUIRED_ENV)
    values['DAQ_PLATFORM_IDENTITY_ENABLED'] = '${IDENTITY_VALUE}'
    env.write_text('\n'.join(f'{k}="{v}" # synthetic comment' for k, v in values.items()) + '\nDAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=' + spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256'] + "\nSYNTHETIC_SECRET='private-value'\nMULTILINE='line1\nline2'\n")
    original = env.read_bytes()
    spec['runtime'] = {'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw'), 'env_file': str(env), 'env_sha256': hashlib.sha256(original).hexdigest(), 'old_secrets_dir': spec['paths']['secrets'], 'project': 'deploy'}
    add_runtime_evidence(root, spec)
    ctx = Path(spec['runtime']['interpolation_file'])
    ctx.write_text('IDENTITY_VALUE=true\n')
    spec['runtime']['interpolation_sha256'] = hashlib.sha256(ctx.read_bytes()).hexdigest()
    assets.render(spec, root / 'configs')
    assert env.read_bytes() == original
    for config in ('runtime.json', 'rollback.json', 'manifest.json'):
        assert b'private-value' not in (root / 'configs' / config).read_bytes()
    assert capsys.readouterr().out == ''


def test_unset_interpolation_fails_closed_without_secret_output(setup, capsys):
    root, spec = setup
    env = root / 'env'
    env.write_text('SYNTHETIC_VALUE=${MISSING_SECRET}\n')
    spec['runtime'] = {'env_file': str(env), 'old_secrets_dir': spec['paths']['secrets'], 'project': 'deploy'}
    add_runtime_evidence(root, spec)
    with pytest.raises(assets.AssetError):
        assets._compose_model(spec['runtime'], True)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


def test_source_extra_mount_is_rejected_instead_of_discarded(setup):
    root, spec = setup
    env = root / 'env'
    env.write_text('SYNTHETIC_ENV=1\n')
    runtime = spec['runtime'] = {'env_file': str(env), 'old_secrets_dir': spec['paths']['secrets'], 'old_data_dir': str(root), 'project': 'deploy', 'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw')}
    add_runtime_evidence(root, spec)
    model = assets._compose_model(runtime, False)
    model['services']['ai-daq-fae-backend']['volumes'].append({'type': 'bind', 'source': '/another', 'target': '/unreviewed'})
    with pytest.raises(assets.AssetError):
        assets._config(runtime, False, model)


def test_compose_explicit_override_of_auth_flag_is_rejected(setup):
    root, spec = setup
    env = root / 'env'
    pin = spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256']
    env.write_text('\n'.join(f'{k}={v}' for k, v in assets.REQUIRED_ENV.items()) + '\nDAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=' + pin + '\n')
    runtime = spec['runtime'] = {'env_file': str(env), 'old_secrets_dir': spec['paths']['secrets'], 'project': 'deploy'}
    add_runtime_evidence(root, spec)
    override = Path(list(runtime['config_files'])[-1])
    model = json.loads(override.read_text())
    model['services']['ai-daq-fae-backend']['environment']['DAQ_PLATFORM_IDENTITY_ENABLED'] = 'false'
    override.write_text(json.dumps(model))
    effective = assets._compose_model(runtime, True)['services']['ai-daq-fae-backend']['environment']
    assert effective['DAQ_PLATFORM_IDENTITY_ENABLED'] == 'false'
    with pytest.raises(assets.AssetError):
        assets._validate_env(str(env), hashlib.sha256(env.read_bytes()).hexdigest(), pin, effective)


def test_render_refuses_original_knowledge_drift_after_staging(setup):
    root, spec = setup
    assets.stage_ro(spec, root / 'ro')
    evidence = {'container_id': 'a' * 64, 'image_id': assets.IMAGE, 'stopped_at': '2026-10-29T00:00:00Z', 'quiescence_evidence': 'sha256:' + 'b' * 64}
    assets.capture_rw(spec, root / 'rw', evidence)
    env = root / 'production.env'
    pin = spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256']
    env.write_text('\n'.join(f'{k}={v}' for k, v in assets.REQUIRED_ENV.items()) + '\nDAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=' + pin + '\n')
    spec['runtime'] = {'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw'), 'env_file': str(env), 'env_sha256': hashlib.sha256(env.read_bytes()).hexdigest(), 'old_secrets_dir': spec['paths']['secrets'], 'project': 'deploy'}
    add_runtime_evidence(root, spec)
    source = Path(spec['paths']['main']) / 'nested/file'
    source.write_bytes(b'X' * len(source.read_bytes()))
    with pytest.raises(assets.AssetError):
        assets.render(spec, root / 'configs')


def test_documented_file_limit_rejects_sparse_oversize_before_copy(setup):
    root, spec = setup
    large = Path(spec['paths']['main']) / 'oversize'
    with large.open('wb') as f:
        f.truncate(assets.MAX_FILE + 1)
    spec['expected']['main']['oversize'] = {'size': assets.MAX_FILE + 1, 'sha256': '0' * 64}
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')
    assert not (root / 'out/main').exists()


def test_documented_total_limit_rejects_sparse_inventory_before_copy(setup):
    root, spec = setup
    for i in range(5):
        name = f'large-{i}'
        path = Path(spec['paths']['main']) / name
        with path.open('wb') as f:
            f.truncate(assets.MAX_FILE)
        spec['expected']['main'][name] = {'size': assets.MAX_FILE, 'sha256': '0' * 64}
    with pytest.raises(assets.AssetError):
        assets.stage_ro(spec, root / 'out')
    assert not (root / 'out/main').exists()


@pytest.mark.parametrize('change', ['bind_propagation', 'disabled_health'])
def test_unsupported_source_mount_semantics_and_disabled_health_fail_closed(setup, change):
    root, spec = setup
    env = root / 'env'
    env.write_text('SYNTHETIC_ENV=1\n')
    runtime = spec['runtime'] = {'env_file': str(env), 'old_secrets_dir': spec['paths']['secrets'], 'old_data_dir': str(root), 'project': 'deploy', 'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw')}
    add_runtime_evidence(root, spec)
    model = assets._compose_model(runtime, False)
    service = model['services']['ai-daq-fae-backend']
    if change == 'bind_propagation':
        service['volumes'][0]['bind']['propagation'] = 'rshared'
    else:
        service['healthcheck']['disable'] = True
    with pytest.raises(assets.AssetError):
        assets._config(runtime, False, model)
