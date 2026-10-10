"""Actual nonempty release entrypoints must not bypass independent D3 approval."""
import json
import subprocess
import sys

import pytest

from daq_fae.app import create_app
from daq_fae.knowledge import releases
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.knowledge import release_readiness as gate
from test_release_readiness import candidate, verify, observe
from test_knowledge_releases import SNAPSHOT, REVIEW, _entity, _record
from test_section_releases import approved_section


def unchecked_fixture(root):
    # Private primitive once hardened; before hardening this reproduces the public bypass.
    publish = getattr(releases, '_publish_release', releases.publish_release)
    activate = getattr(releases, '_activate_release', releases.activate_release)
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    rid = publish(root, SNAPSHOT, rows, None, REVIEW, sections=[section],
                  bodies={section['section_id']: body})
    activate(root, rid)
    return rid


def test_public_nonempty_publication_rejects_missing_readiness(tmp_path):
    with pytest.raises(ValueError, match='readiness'):
        releases.publish_release(tmp_path, SNAPSHOT, [_entity(), _record()], None, REVIEW)
    assert not tmp_path.exists() or not list(tmp_path.iterdir())


def test_public_nonempty_activation_rejects_missing_adapter(tmp_path):
    rid = unchecked_fixture(tmp_path)
    pointer = (tmp_path/'active.json').read_bytes()
    with pytest.raises(ValueError, match='readiness'):
        releases.activate_release(tmp_path, rid)
    assert (tmp_path/'active.json').read_bytes() == pointer


def test_runtime_reader_rejects_unapproved_nonempty_release(tmp_path):
    unchecked_fixture(tmp_path)
    with pytest.raises(ValueError, match='readiness'):
        ReviewedKnowledge.load_active(tmp_path)


def test_real_application_rejects_unapproved_nonempty_release(tmp_path):
    unchecked_fixture(tmp_path)
    with pytest.raises(ValueError, match='readiness'):
        create_app(provider_mode='offline', knowledge_release_root=tmp_path,
                   state_db_path=tmp_path/'state.sqlite3')


@pytest.mark.parametrize('command', ['activate', 'rollback'])
def test_cli_rejects_nonempty_unapproved_release_without_pointer_change(tmp_path, command):
    rid = unchecked_fixture(tmp_path)
    before = (tmp_path/'active.json').read_bytes()
    result = subprocess.run([sys.executable, 'scripts/daq_knowledge.py', command,
                             '--root', str(tmp_path), '--release-id', rid], capture_output=True, text=True)
    assert result.returncode == 2, result.stdout
    assert 'readiness' in result.stderr
    assert (tmp_path/'active.json').read_bytes() == before


def test_pointer_post_replace_fsync_failure_is_compensated(tmp_path, monkeypatch):
    first = gate.stage_release(tmp_path, candidate(), verify_approval=verify)
    gate.activate_checked(tmp_path, first, verify_approval=verify, observe=observe)
    second = gate.stage_release(tmp_path, candidate(first), verify_approval=verify)
    original = releases.os.fsync
    calls = 0
    def failing(fd):
        nonlocal calls
        calls += 1
        if calls == 2:  # First is pointer tempfile fsync; second is after os.replace.
            raise OSError('post-replace fsync failed')
        return original(fd)
    monkeypatch.setattr(releases.os, 'fsync', failing)
    with pytest.raises((ValueError, OSError)):
        gate.activate_checked(tmp_path, second, verify_approval=verify, observe=observe)
    assert releases.read_active_release(tmp_path)['release_id'] == first


def test_public_signed_publication_and_activation_require_explicit_adapters(tmp_path):
    bundle = candidate()
    rid = releases.publish_release(tmp_path, bundle['snapshot'], bundle['records'], None,
        {**bundle['review'], 'readiness': bundle}, sections=bundle['sections'],
        bodies=bundle['bodies'], verify_approval=verify)
    assert releases.read_active_release(tmp_path) is None
    releases.activate_release(tmp_path, rid, verify_approval=verify, observe=observe)
    view = ReviewedKnowledge.load_active(tmp_path, verify_approval=verify,
        runtime_release='synthetic-runtime', upstream_sha='a'*40)
    assert view.release_id == rid
    with pytest.raises(ValueError, match='approval'):
        ReviewedKnowledge.load_active(tmp_path, verify_approval=lambda *args: False,
            runtime_release='synthetic-runtime', upstream_sha='a'*40)


@pytest.mark.parametrize('runtime,upstream', [('wrong-runtime', 'a'*40), ('synthetic-runtime', 'b'*40)])
def test_runtime_identity_is_checked_against_trusted_process_configuration(tmp_path, runtime, upstream):
    rid = gate.stage_release(tmp_path, candidate(), verify_approval=verify)
    gate.activate_checked(tmp_path, rid, verify_approval=verify, observe=observe)
    with pytest.raises(ValueError, match='runtime identity'):
        ReviewedKnowledge.load_active(tmp_path, verify_approval=verify,
            runtime_release=runtime, upstream_sha=upstream)
    with pytest.raises(ValueError, match='runtime identity'):
        create_app(provider_mode='offline', knowledge_release_root=tmp_path,
                   knowledge_approval_verifier=verify)


def test_real_app_requires_auth_even_with_trusted_release_verifier(tmp_path):
    from synthetic_release_helpers import approve_fixture_for_app
    from fastapi.testclient import TestClient
    unchecked_fixture(tmp_path)
    rid, trusted = approve_fixture_for_app(tmp_path)
    with pytest.raises(ValueError, match='requires_authenticated_mode'):
        create_app(provider_mode='offline', knowledge_release_root=tmp_path,
            knowledge_approval_verifier=trusted, state_db_path=tmp_path/'state.sqlite3')


@pytest.mark.parametrize('field,value', [('archive.independent_durable', False),
    ('reviews.unsigned_count', 572), ('dev_batch.frozen', False)])
def test_actual_runtime_rejects_authentic_but_unready_package(tmp_path, field, value):
    from test_release_readiness import sign
    bundle = candidate()
    outer, inner = field.split('.')
    bundle[outer][inner] = value
    sign(bundle)
    manifest = gate._manifest(bundle)
    data = releases._json_bytes(manifest)
    rid = releases._digest(data)
    directory = tmp_path/'releases'/rid
    directory.mkdir(parents=True)
    (directory/'manifest.json').write_bytes(data)
    (tmp_path/'active.json').write_text(json.dumps({'release_id': rid}))
    with pytest.raises(ValueError, match='readiness'):
        ReviewedKnowledge.load_active(tmp_path, verify_approval=verify,
            runtime_release='synthetic-runtime', upstream_sha='a'*40)
    with pytest.raises(ValueError, match='readiness'):
        create_app(provider_mode='offline', knowledge_release_root=tmp_path,
                   knowledge_approval_verifier=verify)


def test_no_environment_switch_can_disable_readiness(tmp_path, monkeypatch):
    unchecked_fixture(tmp_path)
    for name in ['DAQ_ALLOW_SYNTHETIC_KNOWLEDGE', 'DAQ_SKIP_READINESS', 'DAQ_KNOWLEDGE_APPROVAL_VERIFIER']:
        monkeypatch.setenv(name, 'true')
    with pytest.raises(ValueError, match='readiness'):
        create_app(provider_mode='offline', knowledge_release_root=tmp_path)


def test_only_genuinely_empty_bootstrap_needs_no_approval(tmp_path):
    empty = {'archive_manifest_sha256': 'f'*64, 'sources': [], 'chunks': []}
    rid = releases.publish_release(tmp_path, empty, [], None, REVIEW)
    releases.activate_release(tmp_path, rid)
    assert ReviewedKnowledge.load_active(tmp_path).release_id == rid
    with pytest.raises(ValueError, match='readiness'):
        releases.publish_release(tmp_path/'source-only', SNAPSHOT, [], None, REVIEW)


def test_failed_initial_pointer_fsync_restores_absent_pointer(tmp_path, monkeypatch):
    rid = gate.stage_release(tmp_path, candidate(), verify_approval=verify)
    original = releases.os.fsync
    calls = 0
    def failing(fd):
        nonlocal calls
        calls += 1
        if calls == 2: raise OSError('post-replace failed')
        return original(fd)
    monkeypatch.setattr(releases.os, 'fsync', failing)
    with pytest.raises(ValueError, match='restored'):
        gate.activate_checked(tmp_path, rid, verify_approval=verify, observe=observe)
    assert releases.read_active_release(tmp_path) is None


def test_runtime_reload_preserves_facts_after_natural_link_expiry(tmp_path, monkeypatch):
    from test_reviewed_knowledge import _records, SNAPSHOT as LINK_SNAPSHOT, RELEASE_REVIEW
    from test_official_link_gate import advance_past_link_expiry
    from synthetic_release_helpers import approve_fixture_for_app
    rid = releases._publish_release(tmp_path, LINK_SNAPSHOT, _records(), None, RELEASE_REVIEW)
    releases._activate_release(tmp_path, rid)
    rid, trusted = approve_fixture_for_app(tmp_path)
    advance_past_link_expiry(monkeypatch)
    from daq_fae.app import RUNTIME_RELEASE
    from pathlib import Path
    upstream = json.loads((Path(__file__).resolve().parents[1]/'upstream-source.json').read_text())['revision']
    view = ReviewedKnowledge.load_active(tmp_path, verify_approval=trusted,
        runtime_release=RUNTIME_RELEASE, upstream_sha=upstream)
    assert view.release_id == rid
    assert any(r['kind'] == 'claim' for r in view.records_for('internal_fae'))
    assert not any(r['kind'] == 'link' for r in view.records_for('internal_fae'))
    with pytest.raises(ValueError):
        gate.activate_checked(tmp_path, rid, verify_approval=trusted, observe=observe)


def test_empty_bootstrap_cannot_replace_reviewed_active_knowledge(tmp_path):
    first = gate.stage_release(tmp_path, candidate(), verify_approval=verify)
    gate.activate_checked(tmp_path, first, verify_approval=verify, observe=observe)
    empty = {'archive_manifest_sha256': 'f'*64, 'sources': [], 'chunks': []}
    bootstrap = releases.publish_release(tmp_path, empty, [], None, REVIEW)
    before = (tmp_path/'active.json').read_bytes()
    with pytest.raises(ValueError, match='bootstrap'):
        releases.activate_release(tmp_path, bootstrap)
    assert (tmp_path/'active.json').read_bytes() == before


def test_empty_bootstrap_first_boot_and_idempotence_only(tmp_path):
    empty = {'archive_manifest_sha256': 'f'*64, 'sources': [], 'chunks': []}
    first = releases.publish_release(tmp_path, empty, [], None, REVIEW)
    second = releases.publish_release(tmp_path, empty, [], first, REVIEW)
    releases.activate_release(tmp_path, first)
    before = (tmp_path/'active.json').read_bytes()
    releases.activate_release(tmp_path, first)
    assert (tmp_path/'active.json').read_bytes() == before
    with pytest.raises(ValueError, match='bootstrap'):
        releases.activate_release(tmp_path, second)
    assert (tmp_path/'active.json').read_bytes() == before


def test_bootstrap_checks_pointer_only_after_acquiring_transition_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    empty = {'archive_manifest_sha256': 'f'*64, 'sources': [], 'chunks': []}
    bootstrap = releases.publish_release(tmp_path, empty, [], None, REVIEW)
    first = gate.stage_release(tmp_path, candidate(), verify_approval=verify)
    original = gate._lock
    @contextmanager
    def concurrent_activation(root):
        with original(root):
            # A preceding writer wins the lock before bootstrap sees current state.
            releases._activate_release(root, first)
            yield
    monkeypatch.setattr(gate, '_lock', concurrent_activation)
    with pytest.raises(ValueError, match='bootstrap'):
        releases.activate_release(tmp_path, bootstrap)
    assert releases.read_active_release(tmp_path)['release_id'] == first
