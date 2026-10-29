from pathlib import Path
import yaml


def test_nonroot_runtime_contract():
    config = yaml.safe_load(Path('deploy/compose.prod.yml').read_text())
    service = config['services']['ai-daq-fae-backend']
    assert service['image'] == 'sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7'
    assert service['pull_policy'] == 'never'
    assert 'build' not in service
    assert service['user'] == '21081:21081'
    assert service['read_only'] is True
    assert service['cap_drop'] == ['ALL']
    assert service['security_opt'] == ['no-new-privileges:true']
    assert service['environment'] == {'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': '/tmp'}
    assert service['tmpfs'] == ['/tmp:rw,noexec,nosuid,nodev,size=256m,uid=21081,gid=21081,mode=0700']
    assert service['ports'] == ['127.0.0.1:8081:8081']
    assert service['networks']['platform-edge']['ipv4_address'] == '172.31.0.9'
    mounts = {v['target']: v for v in service['volumes']}
    assert set(mounts) == {'/app/data/knowledge/reviewed-thinking-prefix-20261028', '/app/data/knowledge/sdk-thinking-prefix-20261028', '/run/daq-secrets', '/app/data/attachments', '/app/data/daq_traces.jsonl'}
    for target, mount in mounts.items():
        assert mount['bind']['create_host_path'] is False
        assert ':?' in mount['source']
        assert mount['read_only'] is (target not in {'/app/data/attachments', '/app/data/daq_traces.jsonl'})
