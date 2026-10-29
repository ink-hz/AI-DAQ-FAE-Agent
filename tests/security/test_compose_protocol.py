import hashlib
import json
from pathlib import Path

import pytest

from test_assets import assets, setup, add_runtime_evidence


def protocol_fixture(root, spec, reference):
    env = root / 'production.env'
    env.write_text("FILE_SECRET='synthetic-file-only'\nSHARED=from-file\nMULTI='one\ntwo'\n")
    runtime = spec['runtime'] = {'env_file': str(env), 'old_secrets_dir': spec['paths']['secrets'], 'old_data_dir': str(root), 'project': 'deploy', 'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw')}
    add_runtime_evidence(root, spec)
    base, override = (Path(p) for p in runtime['config_files'])
    base_model = json.loads(base.read_text())
    base_model['services']['ai-daq-fae-backend']['env_file'] = ['${ENV_FILE}']
    base.write_text(json.dumps(base_model))
    override_model = json.loads(override.read_text())
    override_model['services']['ai-daq-fae-backend']['environment'].update({'SHARED': '${OVERRIDE}', 'LITERAL_DOLLAR': '$$VARIABLE', 'MULTILINE_EXPLICIT': 'first\nsecond'})
    override.write_text(json.dumps(override_model))
    ctx = Path(runtime['interpolation_file'])
    ctx.write_text('ENV_FILE=' + (str(env) if reference == 'absolute' else env.name) + '\nOVERRIDE="from-context"\n')
    runtime['interpolation_sha256'] = hashlib.sha256(ctx.read_bytes()).hexdigest()
    runtime['config_files'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (base, override)}
    return runtime


@pytest.mark.parametrize('reference', ['absolute', 'relative'])
def test_real_compose_raw_protocol_preserves_reference_and_explicit_layer(setup, reference):
    root, spec = setup
    runtime = protocol_fixture(root, spec, reference)
    source, effective = assets._compose_source(runtime)
    service = source['services']['ai-daq-fae-backend']
    assert service['env_file'][0]['path'] == runtime['env_file']
    assert service['environment']['SHARED'] == '${OVERRIDE}'
    assert service['environment']['LITERAL_DOLLAR'] == '$$VARIABLE'
    assert service['environment']['MULTILINE_EXPLICIT'] == 'first\nsecond'
    assert 'FILE_SECRET' not in service['environment']
    assert 'synthetic-file-only' not in json.dumps(source)
    assert effective['services']['ai-daq-fae-backend']['environment']['FILE_SECRET'] == 'synthetic-file-only'
    assets._verify_models(runtime, effective, assets._config(runtime, False, source), assets._config(runtime, True, source))


def test_simulated_240_no_env_resolution_discards_reference_without_breaking_protocol(setup, monkeypatch):
    root, spec = setup
    runtime = protocol_fixture(root, spec, 'absolute')
    original = assets._compose_model
    calls = []
    def emulate_240(runtime, resolve_env, config_files=None, *, raw=False, project_directory=None, resolve_paths=False):
        calls.append((resolve_env, raw))
        if not resolve_env and not raw:
            result = original(runtime, True, config_files)
            result['services']['ai-daq-fae-backend'].pop('env_file', None)
            return result
        return original(runtime, resolve_env, config_files, raw=raw, project_directory=project_directory, resolve_paths=resolve_paths)
    monkeypatch.setattr(assets, '_compose_model', emulate_240)
    simulated_no_env_model = assets._compose_model(runtime, False)
    simulated_service = simulated_no_env_model['services']['ai-daq-fae-backend']
    assert 'env_file' not in simulated_service
    assert simulated_service['environment']['FILE_SECRET'] == 'synthetic-file-only'
    calls.clear()
    source, effective = assets._compose_source(runtime)
    assert source['services']['ai-daq-fae-backend']['env_file'][0]['path'] == runtime['env_file']
    assert 'FILE_SECRET' not in source['services']['ai-daq-fae-backend']['environment']
    assert effective['services']['ai-daq-fae-backend']['environment']['FILE_SECRET'] == 'synthetic-file-only'
    assert (False, False) not in calls


def test_project_directory_requires_controller_label_evidence(setup):
    root, spec = setup
    runtime = protocol_fixture(root, spec, 'relative')
    labels = Path(runtime['labels_file'])
    payload = json.loads(labels.read_text())
    payload['com.docker.compose.project.working_dir'] = str(root / 'other-directory')
    labels.write_text(json.dumps(payload))
    with pytest.raises(assets.AssetError):
        assets._validate_deployment(runtime)


def test_raw_env_file_string_representation_is_supported(setup, monkeypatch):
    root, spec = setup
    runtime = protocol_fixture(root, spec, 'absolute')
    original = assets._compose_model
    def string_model(runtime, resolve_env, config_files=None, *, raw=False, project_directory=None, resolve_paths=False):
        model = original(runtime, resolve_env, config_files, raw=raw, project_directory=project_directory, resolve_paths=resolve_paths)
        if raw:
            for service in model.get('services', {}).values():
                if service.get('env_file'):
                    service['env_file'] = [item['path'] if isinstance(item, dict) else item for item in service['env_file']]
        return model
    monkeypatch.setattr(assets, '_compose_model', string_model)
    source, _ = assets._compose_source(runtime)
    assert source['services']['ai-daq-fae-backend']['env_file'][0]['path'] == runtime['env_file']


def test_reference_resolution_uses_compose_reported_origin_not_guessed_directory(setup, monkeypatch):
    root, spec = setup
    runtime = protocol_fixture(root, spec, 'relative')
    actual = assets._compose_model
    seen_raw_resolved = []
    def observed(runtime, resolve_env, config_files=None, *, raw=False, project_directory=None, resolve_paths=False):
        result = actual(runtime, resolve_env, config_files, raw=raw, project_directory=project_directory, resolve_paths=resolve_paths)
        if raw and resolve_paths and config_files is None:
            seen_raw_resolved.append(True)
        return result
    monkeypatch.setattr(assets, '_compose_model', observed)
    source, effective = assets._compose_source(runtime)
    assert seen_raw_resolved == [True]
    assert source['services']['ai-daq-fae-backend']['env_file'][0]['path'] == runtime['env_file']
    assets._verify_models(runtime, effective, assets._config(runtime, False, source), assets._config(runtime, True, source))


def test_label_project_directory_controls_relative_reference_across_ordered_files(setup):
    root, spec = setup
    runtime = protocol_fixture(root, spec, 'relative')
    original = Path(next(iter(runtime['config_files'])))
    subdir = root / 'other-config-directory'
    subdir.mkdir()
    moved = subdir / 'base.json'
    moved.write_bytes(original.read_bytes())
    override = Path(list(runtime['config_files'])[-1])
    runtime['config_files'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (moved, override)}
    source, effective = assets._compose_source(runtime)
    assert source['services']['ai-daq-fae-backend']['env_file'][0]['path'] == runtime['env_file']
    assets._verify_models(runtime, effective, assets._config(runtime, False, source), assets._config(runtime, True, source))


def test_real_yaml_source_and_ordered_json_override_preserve_explicit_layer(setup):
    root, spec = setup
    runtime = protocol_fixture(root, spec, 'relative')
    old_base = Path(next(iter(runtime['config_files'])))
    override = Path(list(runtime['config_files'])[-1])
    base = root / 'base.yml'
    base.write_text('''services:
  ai-daq-fae-backend:
    image: old
    container_name: ai-daq-fae-backend
    restart: unless-stopped
    env_file:
      - ${ENV_FILE}
    volumes:
      - "''' + str(root) + ''':/app/data"
      - "''' + spec['paths']['secrets'] + ''':/run/daq-secrets:ro"
    ports:
      - "127.0.0.1:8081:8081"
    networks:
      camera-db: {}
      platform-edge:
        ipv4_address: 172.31.0.9
    healthcheck:
      test: [CMD, curl, -fsS, 'http://127.0.0.1:8081/health']
      interval: 15s
      timeout: 5s
      retries: 12
networks:
  camera-db:
    external: true
    name: deploy_default
  platform-edge:
    external: true
    name: orbbec-agent-platform-edge
''')
    assert old_base.exists()
    runtime['config_files'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (base, override)}
    source, effective = assets._compose_source(runtime)
    assert source['services']['ai-daq-fae-backend']['env_file'][0]['path'] == runtime['env_file']
    assert 'FILE_SECRET' not in source['services']['ai-daq-fae-backend']['environment']
    assets._verify_models(runtime, effective, assets._config(runtime, False, source), assets._config(runtime, True, source))


def test_240_producer_protocol_full_render_keeps_env_file_opaque(setup, monkeypatch, capsys):
    root, spec = setup
    assets.stage_ro(spec, root / 'ro')
    evidence = {'container_id': 'a' * 64, 'image_id': assets.IMAGE, 'stopped_at': '2026-10-29T00:00:00Z', 'quiescence_evidence': 'sha256:' + 'b' * 64}
    assets.capture_rw(spec, root / 'rw', evidence)
    runtime = protocol_fixture(root, spec, 'relative')
    env = Path(runtime['env_file'])
    pin = spec['expected']['secrets']['feedback-approval-keys-20261023.json']['sha256']
    env.write_text(env.read_text() + '\n'.join(f'{k}="{v}" # test comment' for k, v in assets.REQUIRED_ENV.items()) + '\nDAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=' + pin + '\n')
    before = env.read_bytes()
    runtime.update({'env_sha256': hashlib.sha256(before).hexdigest(), 'config_identity': 'sha256:' + hashlib.sha256(Path(runtime['labels_file']).read_bytes()).hexdigest()})
    original = assets._compose_model
    def emulate_240(runtime, resolve_env, config_files=None, *, raw=False, project_directory=None, resolve_paths=False):
        if not resolve_env and not raw:
            result = original(runtime, True, config_files)
            result['services']['ai-daq-fae-backend'].pop('env_file', None)
            return result
        return original(runtime, resolve_env, config_files, raw=raw, project_directory=project_directory, resolve_paths=resolve_paths)
    monkeypatch.setattr(assets, '_compose_model', emulate_240)
    assets.render(spec, root / 'configs')
    assert assets.verify(spec, root / 'configs')['matched'] is True
    assert env.read_bytes() == before
    for name in ('runtime.json', 'rollback.json', 'manifest.json'):
        assert b'synthetic-file-only' not in (root / 'configs' / name).read_bytes()
    assert capsys.readouterr().out == ''


def test_actual_project_name_is_not_hardcoded(setup):
    root, spec = setup
    runtime = protocol_fixture(root, spec, 'relative')
    runtime['project'] = 'ai-daq-fae-agent'
    labels = Path(runtime['labels_file'])
    evidence = json.loads(labels.read_text())
    evidence['com.docker.compose.project'] = runtime['project']
    labels.write_text(json.dumps(evidence))
    runtime['config_identity'] = 'sha256:' + hashlib.sha256(labels.read_bytes()).hexdigest()
    assert assets._validate_deployment(runtime) == runtime['config_identity']
    source, effective = assets._compose_source(runtime)
    assert source['name'] == 'ai-daq-fae-agent'
    assets._verify_models(runtime, effective, assets._config(runtime, False, source), assets._config(runtime, True, source))


def test_long_form_env_file_options_are_preserved_with_raw_format(setup):
    root, spec = setup
    runtime = protocol_fixture(root, spec, 'relative')
    base = Path(next(iter(runtime['config_files'])))
    model = json.loads(base.read_text())
    model['services']['ai-daq-fae-backend']['env_file'] = [{'path': '${ENV_FILE}', 'required': True, 'format': 'raw'}]
    base.write_text(json.dumps(model))
    runtime['config_files'][str(base)] = hashlib.sha256(base.read_bytes()).hexdigest()
    source, effective = assets._compose_source(runtime)
    env_file = source['services']['ai-daq-fae-backend']['env_file'][0]
    assert env_file['path'] == runtime['env_file']
    assert env_file['required'] is True
    assert env_file['format'] == 'raw'
    assets._verify_models(runtime, effective, assets._config(runtime, False, source), assets._config(runtime, True, source))
