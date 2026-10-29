import json
from pathlib import Path

import pytest

from test_assets import assets, setup, add_runtime_evidence


CASES = [
    ('volumes_from', {'volumes_from': ['container:synthetic-other:rw']}, {}),
    ('secrets', {'secrets': ['synthetic']}, {'secrets': {'synthetic': {'file': '/tmp/synthetic-secret'}}}),
    ('configs', {'configs': ['synthetic']}, {'configs': {'synthetic': {'file': '/tmp/synthetic-config'}}}),
    ('api_socket', {'use_api_socket': True}, {}),
    ('devices', {'devices': ['/dev/null:/dev/null']}, {}),
    ('device_rules', {'device_cgroup_rules': ['c 1:3 rwm']}, {}),
    ('gpu', {'gpus': 'all'}, {}),
    ('reserved_devices', {'deploy': {'resources': {'reservations': {'devices': [{'driver': 'nvidia', 'count': 1, 'capabilities': ['gpu']}]}}}}, {}),
    ('deploy_replicas', {'deploy': {'replicas': 1}}, {}),
    ('supplementary_groups', {'group_add': ['123']}, {}),
    ('cap_add', {'cap_add': ['SYS_ADMIN']}, {}),
    ('privileged', {'privileged': True}, {}),
    ('pid', {'pid': 'host'}, {}),
    ('ipc', {'ipc': 'host'}, {}),
    ('uts', {'uts': 'host'}, {}),
    ('userns', {'userns_mode': 'host'}, {}),
    ('network_mode', {'network_mode': 'host'}, {}),
    ('tmpfs', {'tmpfs': ['/run/other']}, {}),
    ('security_option', {'security_opt': ['seccomp:unconfined']}, {}),
    ('sysctls', {'sysctls': {'net.ipv4.ip_forward': '1'}}, {}),
    ('cgroup', {'cgroup': 'host'}, {}),
    ('runtime', {'runtime': 'nvidia'}, {}),
    ('develop_watch', {'develop': {'watch': [{'action': 'sync', 'path': '/tmp', 'target': '/tmp/synthetic'}]}}, {}),
    ('post_start', {'post_start': [{'command': 'true', 'privileged': True}]}, {}),
    ('pre_stop', {'pre_stop': [{'command': 'true', 'user': '0'}]}, {}),
]


def source_model(root, spec, fields, top_level):
    env = root / 'env'
    env.write_text('SYNTHETIC_ENV=1\n')
    runtime = spec['runtime'] = {'env_file': str(env), 'old_secrets_dir': spec['paths']['secrets'], 'old_data_dir': str(root), 'project': 'deploy', 'ro_dir': str(root / 'ro'), 'rw_dir': str(root / 'rw')}
    add_runtime_evidence(root, spec)
    configs = list(runtime['config_files'])
    if 'network_mode' in fields:
        base_path = Path(configs[0])
        base = json.loads(base_path.read_text())
        del base['services']['ai-daq-fae-backend']['networks']
        base_path.write_text(json.dumps(base))
    override_path = Path(configs[-1])
    override = json.loads(override_path.read_text())
    override['services']['ai-daq-fae-backend'].update(fields)
    override.update(top_level)
    override_path.write_text(json.dumps(override))
    return runtime, assets._compose_model(runtime, False)


@pytest.mark.parametrize('name,fields,top_level', CASES, ids=[case[0] for case in CASES])
def test_real_compose_source_rejects_unauthorized_channels(setup, name, fields, top_level):
    root, spec = setup
    runtime, model = source_model(root, spec, fields, top_level)
    with pytest.raises(assets.AssetError):
        assets._config(runtime, False, model)
    with pytest.raises(assets.AssetError):
        assets._config(runtime, True, model)


@pytest.mark.parametrize('rollback', [False, True], ids=['runtime', 'rollback'])
def test_effective_models_reject_inherited_volumes_even_with_five_binds(setup, rollback):
    root, spec = setup
    runtime, source = source_model(root, spec, {}, {})
    desired = assets._config(runtime, rollback, source)
    effective = json.loads(json.dumps(desired))
    effective['services']['ai-daq-fae-backend']['volumes_from'] = ['container:synthetic-other:rw']
    with pytest.raises(assets.AssetError):
        assets._check_effective_policy(runtime, effective, rollback, desired)


def test_unknown_service_execution_field_rejected(setup):
    root, spec = setup
    runtime, model = source_model(root, spec, {}, {})
    model['services']['ai-daq-fae-backend']['future_execution_capability'] = True
    with pytest.raises(assets.AssetError):
        assets._config(runtime, False, model)


@pytest.mark.parametrize('rollback', [False, True], ids=['runtime', 'rollback'])
@pytest.mark.parametrize('name,fields,top_level', CASES, ids=[case[0] for case in CASES])
def test_real_effective_models_reject_unauthorized_channels(setup, rollback, name, fields, top_level):
    root, spec = setup
    runtime, source = source_model(root, spec, {}, {})
    desired = assets._config(runtime, rollback, source)
    candidate = json.loads(json.dumps(desired))
    service = candidate['services']['ai-daq-fae-backend']
    if 'network_mode' in fields:
        del service['networks']
    if 'tmpfs' in fields and not rollback:
        service['tmpfs'] = list(service['tmpfs']) + fields['tmpfs']
    else:
        service.update(fields)
    candidate.update(top_level)
    path = root / 'synthetic-effective.json'
    path.write_text(json.dumps(candidate))
    path.chmod(0o600)
    effective = assets._compose_model(runtime, True, [str(path)])
    with pytest.raises(assets.AssetError):
        assets._check_effective_policy(runtime, effective, rollback, desired)


def test_standard_command_environment_resource_and_logging_are_preserved(setup):
    root, spec = setup
    fields = {'command': ['python', '-m', 'synthetic'], 'entrypoint': ['/synthetic/entry'], 'environment': {'SYNTHETIC': 'value'}, 'mem_limit': '512m', 'logging': {'driver': 'json-file', 'options': {'max-size': '10m'}}, 'deploy': {'resources': {'limits': {'memory': '512M'}, 'reservations': {'memory': '128M'}}}}
    runtime, source = source_model(root, spec, fields, {})
    for rollback in (False, True):
        desired = assets._config(runtime, rollback, source)
        service = desired['services']['ai-daq-fae-backend']
        assert service['command'] == source['services']['ai-daq-fae-backend']['command']
        assert service['entrypoint'] == source['services']['ai-daq-fae-backend']['entrypoint']
        assert service['environment']['SYNTHETIC'] == 'value'
        assert service['mem_limit'] == '536870912'
        assert service['logging']['options']['max-size'] == '10m'
        assert service['deploy']['resources'] == source['services']['ai-daq-fae-backend']['deploy']['resources']
    source_effective = assets._compose_model(runtime, True)
    assets._verify_models(runtime, source_effective, assets._config(runtime, False, source), assets._config(runtime, True, source))


def test_unreviewed_network_endpoint_alias_rejected_after_real_compose_parse(setup):
    root, spec = setup
    runtime, source = source_model(root, spec, {'networks': {'platform-edge': {'aliases': ['synthetic-alias']}}}, {})
    assert source['services']['ai-daq-fae-backend']['networks']['platform-edge']['aliases'] == ['synthetic-alias']
    with pytest.raises(assets.AssetError, match='network endpoint'):
        assets._config(runtime, False, source)


def test_known_execution_capability_error_names_field_without_value(setup):
    root, spec = setup
    runtime, source = source_model(root, spec, {'volumes_from': ['container:synthetic-other:rw']}, {})
    with pytest.raises(assets.AssetError) as error:
        assets._config(runtime, False, source)
    assert str(error.value) == 'unauthorized compose execution capability: volumes_from'
    assert 'synthetic-other' not in str(error.value)


@pytest.mark.parametrize('rollback', [False, True], ids=['runtime', 'rollback'])
def test_unreviewed_effective_service_field_fails_closed(setup, rollback):
    root, spec = setup
    runtime, source = source_model(root, spec, {}, {})
    desired = assets._config(runtime, rollback, source)
    effective = json.loads(json.dumps(desired))
    effective['services']['ai-daq-fae-backend']['future_execution_capability'] = True
    with pytest.raises(assets.AssetError, match='unreviewed compose service field'):
        assets._check_effective_policy(runtime, effective, rollback, desired)


@pytest.mark.parametrize('rollback', [False, True], ids=['runtime', 'rollback'])
def test_effective_external_network_identity_checked_independently(setup, rollback):
    root, spec = setup
    runtime, source = source_model(root, spec, {}, {})
    desired = assets._config(runtime, rollback, source)
    effective = json.loads(json.dumps(desired))
    effective['networks']['camera-db']['name'] = 'other-external-network'
    with pytest.raises(assets.AssetError):
        assets._check_effective_policy(runtime, effective, rollback, desired)
