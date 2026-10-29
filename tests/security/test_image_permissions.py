import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import subprocess

import pytest

ROOTS = ('app/daq_fae', 'app/src', 'app/scripts', 'app/prompts', 'app/migrations', 'app/webui/dist')
FENCE_DIRS = ('app', 'app/webui', 'app/data', 'app/data/knowledge', 'run', 'app/deploy')
FENCE_FILES = ('app/upstream-source.json', 'app/deploy/provision_env.py')
SCRIPT = Path('deploy/normalize_code_permissions.py')


def module():
    spec = importlib.util.spec_from_file_location('normalizer', SCRIPT)
    assert spec is not None and SCRIPT.exists(), 'normalizer is missing'
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


@pytest.fixture
def tree(tmp_path):
    for path in FENCE_DIRS + ROOTS:
        (tmp_path / path).mkdir(parents=True, exist_ok=True)
        (tmp_path / path).chmod(0o755)
    for path in FENCE_FILES:
        (tmp_path / path).write_bytes(b'fenced')
        (tmp_path / path).chmod(0o644)
    for path in ROOTS:
        d = tmp_path / path / 'private'
        d.mkdir(mode=0o700)
        (d / 'code.py').write_bytes(b'value = 42\n')
        (d / 'code.py').chmod(0o600)
        (d / 'run').write_bytes(b'#!/bin/sh\n')
        (d / 'run').chmod(0o700)
    return tmp_path


def run(tree, apply=True):
    return module().normalize(tree, apply=apply, expected_uid=os.getuid())


def test_normalization_keeps_bytes_members_and_mtime_and_fixes_traversal(tree):
    p = tree / 'app/src/private/code.py'
    old = p.stat()
    before = hashlib.sha256(p.read_bytes()).digest()
    result = run(tree)
    assert result['before']['digest'] == result['after']['digest']
    assert result['changed'] > 0
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(p.stat().st_mode) == 0o644
    assert stat.S_IMODE((p.parent / 'run').stat().st_mode) == 0o755
    assert p.stat().st_mtime_ns == old.st_mtime_ns
    assert hashlib.sha256(p.read_bytes()).digest() == before
    subprocess.run([os.sys.executable, '-B', '-c', 'import code; assert code.value == 42'],
                   env=dict(os.environ, PYTHONPATH=str(p.parent)), cwd=tree, check=True)
    assert run(tree, False)['changed'] == 0


@pytest.mark.parametrize('bad', ['missing', 'symlink', 'hardlink', 'fifo', 'writable', 'fence', 'owner', 'budget'])
def test_preflight_rejects_before_any_chmod(tree, monkeypatch, bad):
    m = module()
    victim = tree / 'app/src/private/code.py'
    if bad == 'missing': (tree / 'app/prompts').rename(tree / 'app/absent')
    elif bad == 'symlink': victim.unlink(); victim.symlink_to('/etc/passwd')
    elif bad == 'hardlink': os.link(victim, victim.parent / 'copy')
    elif bad == 'fifo': os.mkfifo(victim.parent / 'pipe')
    elif bad == 'writable': victim.chmod(0o666)
    elif bad == 'fence': (tree / 'app/data').chmod(0o700)
    elif bad == 'owner': monkeypatch.setattr(m, '_owner_matches', lambda uid, expected: False)
    elif bad == 'budget': monkeypatch.setattr(m, 'MAX_NODES', 2)
    observed = []
    monkeypatch.setattr(m.os, 'fchmod', lambda *args: observed.append(args))
    with pytest.raises((ValueError, OSError)):
        m.normalize(tree, apply=True, expected_uid=os.getuid())
    assert not observed


def test_audit_rejects_unnormalized_modes_without_changes(tree):
    p = tree / 'app/src/private/code.py'
    with pytest.raises(ValueError):
        run(tree, False)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_race_after_preflight_refuses_success(tree, monkeypatch):
    m = module()
    original = m._apply_one
    calls = []
    def mutate(*args, **kwargs):
        if not calls:
            (tree / 'app/src/private/code.py').write_text('changed')
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(m, '_apply_one', mutate)
    with pytest.raises((ValueError, OSError)):
        m.normalize(tree, apply=True, expected_uid=os.getuid())


def test_recipes_and_actual_release_package_contain_normalizer(tmp_path):
    derivative = Path('deploy/Dockerfile.nonroot').read_text()
    assert 'FROM orbbec-daq-nonroot-base:5ed10ead83ee10ad' in derivative
    assert 'USER 21081:21081' in derivative
    assert '--apply' in derivative and 'python -B' in derivative
    full = Path('deploy/Dockerfile.prod').read_text()
    assert 'deploy/normalize_code_permissions.py' in full and 'USER 21081:21081' in full
    release = Path('deploy/build_release.sh').read_text()
    assert 'deploy/normalize_code_permissions.py' in release


def test_release_archive_contains_every_docker_copy_input(tmp_path):
    import shutil
    import tarfile

    repo = tmp_path / 'repo'
    repo.mkdir()
    for name in ('daq_fae', 'src', 'scripts', 'migrations', 'prompts'):
        (repo / name).mkdir()
        (repo / name / 'fixture').write_text('fixture')
    (repo / 'deploy').mkdir()
    for name in ('Dockerfile.prod', 'compose.prod.yml', 'provision_env.py',
                 'normalize_code_permissions.py', 'build_release.sh'):
        shutil.copy(Path('deploy') / name, repo / 'deploy' / name)
    for name in ('upstream-source.json', 'requirements.txt', 'requirements-daq.txt'):
        (repo / name).write_text('fixture')
    (repo / 'webui').mkdir()
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    npm = bindir / 'npm'
    npm.write_text('#!/bin/sh\nmkdir -p webui/dist\nprintf fixture > webui/dist/index.html\n')
    npm.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'])
    subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-qm', 'fixture'], cwd=repo, check=True)
    subprocess.run(['bash', 'deploy/build_release.sh'], cwd=repo, env=env, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    package = next((repo / 'dist/release').glob('*.tar.gz'))
    with tarfile.open(package) as tar:
        names = set(tar.getnames())
    for line in (repo / 'deploy/Dockerfile.prod').read_text().splitlines():
        if line.startswith('COPY '):
            sources = line.split()[1:-1]
            for source in sources:
                assert source in names or any(n.startswith(source.rstrip('/') + '/') for n in names), source


def test_fence_file_link_and_oversized_file_fail_before_chmod(tree, monkeypatch):
    m = module()
    fence = tree / 'app/upstream-source.json'
    os.link(fence, tree / 'app/other')
    calls = []
    monkeypatch.setattr(m.os, 'fchmod', lambda *args: calls.append(args))
    with pytest.raises(ValueError):
        m.normalize(tree, apply=True, expected_uid=os.getuid())
    assert not calls
    (tree / 'app/other').unlink()
    with (tree / 'app/src/private/code.py').open('wb') as f:
        f.truncate(m.MAX_FILE + 1)
    with pytest.raises(ValueError):
        m.normalize(tree, apply=True, expected_uid=os.getuid())
    assert not calls


def test_ancestor_replaced_after_preflight_is_refused(tree, monkeypatch):
    m = module()
    original = m._apply_one
    def replace(base, path, entry, expected):
        if path == 'app/src':
            (tree / 'app/src').rename(tree / 'app/moved')
            (tree / 'app/src').mkdir()
        return original(base, path, entry, expected)
    monkeypatch.setattr(m, '_apply_one', replace)
    with pytest.raises(ValueError):
        m.normalize(tree, apply=True, expected_uid=os.getuid())
