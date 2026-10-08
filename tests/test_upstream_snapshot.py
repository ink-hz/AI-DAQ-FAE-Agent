import json
import subprocess

import pytest

from scripts.verify_upstream_snapshot import SourceMismatch, verify_upstream_snapshot


def _git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _repo(path, source='shared = 1\n'):
    path.mkdir()
    _git(path, 'init', '-q')
    _git(path, 'config', 'user.name', 'Test')
    _git(path, 'config', 'user.email', 'test@example.invalid')
    (path / 'src').mkdir()
    (path / 'src/runtime.py').write_text(source)
    (path / 'requirements.txt').write_text('fastapi==1.0\n')
    _git(path, 'add', 'src', 'requirements.txt')
    _git(path, 'commit', '-qm', 'snapshot')
    return path


def test_upstream_snapshot_matches_exact_revision_and_rejects_worktree_drift(tmp_path):
    upstream = _repo(tmp_path / 'upstream')
    daq = _repo(tmp_path / 'daq')
    manifest = tmp_path / 'upstream-source.json'
    manifest.write_text(json.dumps({'revision': _git(upstream, 'rev-parse', 'HEAD')}))

    result = verify_upstream_snapshot(daq, upstream, manifest)
    assert result['revision'] == _git(upstream, 'rev-parse', 'HEAD')
    assert result['src_tree'] == _git(daq, 'rev-parse', 'HEAD:src')

    (daq / 'src/runtime.py').write_text('shared = 2\n')
    with pytest.raises(SourceMismatch, match='dirty'):
        verify_upstream_snapshot(daq, upstream, manifest)


def test_upstream_snapshot_rejects_wrong_tree_and_unavailable_revision(tmp_path):
    upstream = _repo(tmp_path / 'upstream')
    daq = _repo(tmp_path / 'daq', source='different = 1\n')
    manifest = tmp_path / 'upstream-source.json'
    manifest.write_text(json.dumps({'revision': _git(upstream, 'rev-parse', 'HEAD')}))
    with pytest.raises(SourceMismatch, match='src tree'):
        verify_upstream_snapshot(daq, upstream, manifest)

    manifest.write_text(json.dumps({'revision': '0' * 40}))
    with pytest.raises(SourceMismatch, match='unavailable'):
        verify_upstream_snapshot(daq, upstream, manifest)
