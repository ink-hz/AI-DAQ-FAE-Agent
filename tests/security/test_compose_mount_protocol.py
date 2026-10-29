import copy

import pytest

from test_assets import assets, setup
from test_execution_boundary import source_model


def models(root, spec, rollback):
    runtime, source = source_model(root, spec, {}, {})
    desired = assets._config(runtime, rollback, source)
    effective = copy.deepcopy(desired)
    mounts = effective['services']['ai-daq-fae-backend']['volumes']
    for mount in mounts:
        del mount['bind']['create_host_path']
        if not mount['read_only']:
            del mount['read_only']
    return runtime, desired, effective


@pytest.mark.parametrize('rollback', [False, True], ids=['runtime', 'rollback'])
def test_typed_effective_omits_false_but_author_remains_explicit(setup, rollback):
    root, spec = setup
    runtime, desired, effective = models(root, spec, rollback)
    assert all(m['bind']['create_host_path'] is False for m in desired['services']['ai-daq-fae-backend']['volumes'])
    assets._check_effective_policy(runtime, effective, rollback, desired)


@pytest.mark.parametrize('rollback', [False, True], ids=['runtime', 'rollback'])
@pytest.mark.parametrize('mutation', ['missing_bind', 'null_bind', 'wrong_bind_type', 'create_true', 'create_string', 'create_number', 'create_null', 'unknown_bind', 'wrong_type', 'rw_true', 'rw_string', 'rw_number', 'rw_null', 'ro_missing', 'ro_number', 'source_changed', 'duplicate_target', 'unknown_mount'], ids=str)
def test_typed_effective_rejects_unsafe_or_ambiguous_mounts(setup, rollback, mutation):
    root, spec = setup
    runtime, desired, effective = models(root, spec, rollback)
    assets._check_effective_policy(runtime, effective, rollback, desired)
    mounts = effective['services']['ai-daq-fae-backend']['volumes']
    ro, rw = mounts[0], mounts[3]
    if mutation == 'missing_bind': del ro['bind']
    elif mutation == 'null_bind': ro['bind'] = None
    elif mutation == 'wrong_bind_type': ro['bind'] = []
    elif mutation == 'create_true': ro['bind']['create_host_path'] = True
    elif mutation == 'create_string': ro['bind']['create_host_path'] = 'false'
    elif mutation == 'create_number': ro['bind']['create_host_path'] = 0
    elif mutation == 'create_null': ro['bind']['create_host_path'] = None
    elif mutation == 'unknown_bind': ro['bind']['propagation'] = 'shared'
    elif mutation == 'wrong_type': ro['type'] = 'volume'
    elif mutation == 'rw_true': rw['read_only'] = True
    elif mutation == 'rw_string': rw['read_only'] = 'false'
    elif mutation == 'rw_number': rw['read_only'] = 0
    elif mutation == 'rw_null': rw['read_only'] = None
    elif mutation == 'ro_missing': del ro['read_only']
    elif mutation == 'ro_number': ro['read_only'] = 1
    elif mutation == 'source_changed': ro['source'] = '/different/source'
    elif mutation == 'duplicate_target': rw['target'] = ro['target']
    elif mutation == 'unknown_mount': ro['volume'] = {}
    with pytest.raises(assets.AssetError):
        assets._check_effective_policy(runtime, effective, rollback, desired)


@pytest.mark.parametrize('rollback', [False, True], ids=['runtime', 'rollback'])
@pytest.mark.parametrize('mutation', ['missing_bind', 'null_bind', 'wrong_bind_type', 'missing_flag', 'true', 'string', 'number', 'null', 'unknown_bind', 'readonly_string'], ids=str)
def test_author_must_explicitly_disable_create(setup, rollback, mutation):
    root, spec = setup
    runtime, desired, effective = models(root, spec, rollback)
    assets._check_effective_policy(runtime, effective, rollback, desired)
    mount = desired['services']['ai-daq-fae-backend']['volumes'][0]
    if mutation == 'missing_bind': del mount['bind']
    elif mutation == 'null_bind': mount['bind'] = None
    elif mutation == 'wrong_bind_type': mount['bind'] = []
    elif mutation == 'missing_flag': del mount['bind']['create_host_path']
    elif mutation == 'true': mount['bind']['create_host_path'] = True
    elif mutation == 'string': mount['bind']['create_host_path'] = 'false'
    elif mutation == 'number': mount['bind']['create_host_path'] = 0
    elif mutation == 'null': mount['bind']['create_host_path'] = None
    elif mutation == 'unknown_bind': mount['bind']['propagation'] = 'shared'
    elif mutation == 'readonly_string': mount['read_only'] = 'true'
    with pytest.raises(assets.AssetError):
        assets._check_effective_policy(runtime, effective, rollback, desired)


def test_240_typed_omission_survives_full_private_model_comparison(setup, monkeypatch):
    root, spec = setup
    runtime, source = source_model(root, spec, {}, {})
    desired_runtime = assets._config(runtime, False, source)
    desired_rollback = assets._config(runtime, True, source)
    source_effective = assets._compose_model(runtime, True)
    original = assets._compose_model
    def omit_typed_false(runtime, resolve_env, config_files=None, **kwargs):
        model = original(runtime, resolve_env, config_files, **kwargs)
        if config_files is not None:
            for mount in model['services']['ai-daq-fae-backend']['volumes']:
                assert mount['type'] == 'bind' and isinstance(mount['bind'], dict)
                assert mount['bind'].pop('create_host_path') is False
                if mount.get('read_only') is False:
                    del mount['read_only']
        return model
    monkeypatch.setattr(assets, '_compose_model', omit_typed_false)
    assets._verify_models(runtime, source_effective, desired_runtime, desired_rollback)


@pytest.mark.parametrize('rollback', [False, True], ids=['runtime', 'rollback'])
def test_omitted_false_requires_approved_source_not_just_matching_pair(setup, rollback):
    root, spec = setup
    runtime, desired, effective = models(root, spec, rollback)
    assets._check_effective_policy(runtime, effective, rollback, desired)
    desired['services']['ai-daq-fae-backend']['volumes'][0]['source'] = '/unapproved'
    effective['services']['ai-daq-fae-backend']['volumes'][0]['source'] = '/unapproved'
    with pytest.raises(assets.AssetError):
        assets._check_effective_policy(runtime, effective, rollback, desired)


@pytest.mark.parametrize('rollback', [False, True], ids=['runtime', 'rollback'])
def test_author_declares_access_mode_explicitly(setup, rollback):
    root, spec = setup
    runtime, desired, effective = models(root, spec, rollback)
    assets._check_effective_policy(runtime, effective, rollback, desired)
    del desired['services']['ai-daq-fae-backend']['volumes'][3]['read_only']
    with pytest.raises(assets.AssetError):
        assets._check_effective_policy(runtime, effective, rollback, desired)


@pytest.mark.parametrize('rollback', [False, True])
def test_effective_image_must_match_selected_phase(setup, rollback):
    root, spec = setup
    runtime, desired, effective = models(root, spec, rollback)
    service = effective['services']['ai-daq-fae-backend']
    assert service['image'] == (assets.IMAGE if rollback else assets.CANDIDATE_IMAGE)
    assets._check_effective_policy(runtime, effective, rollback, desired)
    service['image'] = assets.CANDIDATE_IMAGE if rollback else assets.IMAGE
    with pytest.raises(assets.AssetError):
        assets._check_effective_policy(runtime, effective, rollback, desired)
