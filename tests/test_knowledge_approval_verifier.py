"""Synthetic Ed25519 trust and real D3/app loading; no model or service calls."""
import base64
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from fastapi.testclient import TestClient

from daq_fae.app import create_app
from daq_fae.knowledge import releases
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from test_release_readiness import candidate, digest, observe
from tests.test_authenticated_persistence import Conversations, Feedback, environment as persistence_environment
from tests.test_platform_identity import CODE, FakePlatform, environment as identity_environment
from src.platform_identity.service import InMemoryAuthenticatedSessionRepository


REVIEWER = 'synthetic-independent-reviewer'
PAYLOAD = 'a' * 64
DOMAIN = b'daq-knowledge-release-approval/v1\0'
KEY_FILE_ENV = 'DAQ_KNOWLEDGE_APPROVAL_KEYS_FILE'
KEY_HASH_ENV = 'DAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256'


def api():
    name = 'daq_fae.knowledge.approval_verifier'
    assert importlib.util.find_spec(name) is not None, 'trusted Ed25519 approval adapter missing'
    return importlib.import_module(name)


def key_document(private_key, reviewer=REVIEWER):
    return {'format_version': 'daq-knowledge-approval-keys/v1', 'keys': [
        {'reviewer': reviewer, 'public_key_b64': base64.b64encode(
            private_key.public_key().public_bytes_raw()).decode('ascii')}]}


def key_file(tmp_path, private_key):
    path = tmp_path / 'approval-keys.json'
    path.write_bytes(json.dumps(key_document(private_key)).encode())
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def signature(private_key, reviewer=REVIEWER, payload=PAYLOAD):
    message = DOMAIN + reviewer.encode() + b'\0' + payload.encode()
    return 'ed25519:' + base64.b64encode(private_key.sign(message)).decode('ascii')


@pytest.fixture(autouse=True)
def isolate_approval_environment(monkeypatch):
    monkeypatch.delenv(KEY_FILE_ENV, raising=False)
    monkeypatch.delenv(KEY_HASH_ENV, raising=False)


def test_domain_message_and_real_signature(tmp_path):
    module = api()
    private_key = Ed25519PrivateKey.generate()
    path, sha = key_file(tmp_path, private_key)
    verify = module.load_approval_verifier(path, sha)
    assert module.approval_message(REVIEWER, PAYLOAD) == DOMAIN + REVIEWER.encode() + b'\0' + PAYLOAD.encode()
    assert verify(REVIEWER, PAYLOAD, signature(private_key)) is True


@pytest.mark.parametrize('reviewer,payload', [
    ('', PAYLOAD), ('  ', PAYLOAD), (None, PAYLOAD), ([], PAYLOAD),
    ('reviewer\0suffix', PAYLOAD), ('\ud800', PAYLOAD),
    (REVIEWER, 'A' * 64), (REVIEWER, 'a' * 63), (REVIEWER, 'g' * 64),
    (REVIEWER, None), (REVIEWER, 123),
])
def test_message_rejects_malformed_identity_and_digest(reviewer, payload):
    with pytest.raises(ValueError):
        api().approval_message(reviewer, payload)


@pytest.mark.parametrize('mutation', [
    'unknown', 'other_reviewer_same_key', 'changed_payload', 'wrong_key', 'wrong_domain',
    'garbled', 'wrong_prefix', 'short', 'long', 'none', 'object',
    'base64_whitespace', 'bad_payload', 'bad_reviewer',
])
def test_verification_fails_closed(tmp_path, mutation):
    module = api()
    private_key = Ed25519PrivateKey.generate()
    path, sha = key_file(tmp_path, private_key)
    if mutation == 'other_reviewer_same_key':
        document = key_document(private_key)
        document['keys'].append({**document['keys'][0], 'reviewer': 'other-reviewer'})
        path.write_text(json.dumps(document))
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
    verify = module.load_approval_verifier(path, sha)
    reviewer, payload, signed = REVIEWER, PAYLOAD, signature(private_key)
    if mutation == 'unknown': reviewer = 'unknown-reviewer'
    if mutation == 'other_reviewer_same_key': reviewer = 'other-reviewer'
    if mutation == 'changed_payload': payload = 'b' * 64
    if mutation == 'wrong_key': signed = signature(Ed25519PrivateKey.generate())
    if mutation == 'wrong_domain':
        signed = 'ed25519:' + base64.b64encode(private_key.sign(PAYLOAD.encode())).decode()
    if mutation == 'garbled': signed = 'ed25519:!@!'
    if mutation == 'wrong_prefix': signed = signed.replace('ed25519:', 'rsa:')
    if mutation == 'short': signed = 'ed25519:' + base64.b64encode(b'x' * 63).decode()
    if mutation == 'long': signed = 'ed25519:' + base64.b64encode(b'x' * 65).decode()
    if mutation == 'none': signed = None
    if mutation == 'object': signed = {}
    if mutation == 'base64_whitespace': signed += '\n'
    if mutation == 'bad_payload': payload = 'A' * 64
    if mutation == 'bad_reviewer': reviewer = []
    assert verify(reviewer, payload, signed) is False


@pytest.mark.parametrize('mutation', [
    'relative', 'symlink', 'symlink_parent', 'missing', 'directory', 'wrong_hash', 'bad_hash',
    'malformed_json', 'wrong_version', 'keys_object', 'empty_keys', 'duplicate_reviewer',
    'blank_reviewer', 'nul_reviewer', 'non_string_reviewer', 'bad_key_base64',
    'short_key', 'long_key', 'missing_key', 'non_object_entry', 'duplicate_json_field',
])
def test_invalid_trust_configuration_fails_startup(tmp_path, mutation):
    module = api()
    private_key = Ed25519PrivateKey.generate()
    path, sha = key_file(tmp_path, private_key)
    document = key_document(private_key)
    if mutation == 'relative': path = Path('approval-keys.json')
    elif mutation == 'symlink':
        link = tmp_path / 'link.json'
        link.symlink_to(path)
        path = link
    elif mutation == 'symlink_parent':
        link = tmp_path / 'linked-directory'
        link.symlink_to(tmp_path, target_is_directory=True)
        path = link / path.name
    elif mutation == 'missing': path = tmp_path / 'absent.json'
    elif mutation == 'directory': path = tmp_path
    elif mutation == 'wrong_hash': sha = '0' * 64
    elif mutation == 'bad_hash': sha = sha.upper()
    else:
        if mutation == 'wrong_version': document['format_version'] = 'other/v1'
        if mutation == 'keys_object': document['keys'] = {}
        if mutation == 'empty_keys': document['keys'] = []
        if mutation == 'duplicate_reviewer': document['keys'].append(deepcopy(document['keys'][0]))
        if mutation == 'blank_reviewer': document['keys'][0]['reviewer'] = ' '
        if mutation == 'nul_reviewer': document['keys'][0]['reviewer'] = 'a\0b'
        if mutation == 'non_string_reviewer': document['keys'][0]['reviewer'] = 42
        if mutation == 'bad_key_base64': document['keys'][0]['public_key_b64'] = '!'
        if mutation == 'short_key': document['keys'][0]['public_key_b64'] = base64.b64encode(b'x' * 31).decode()
        if mutation == 'long_key': document['keys'][0]['public_key_b64'] = base64.b64encode(b'x' * 33).decode()
        if mutation == 'missing_key': del document['keys'][0]['public_key_b64']
        if mutation == 'non_object_entry': document['keys'] = ['key']
        content = json.dumps(document)
        if mutation == 'malformed_json': content = '{broken'
        if mutation == 'duplicate_json_field':
            content = content.replace('"keys":', '"keys": [], "keys":')
        path.write_text(content)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='daq_knowledge_approval'):
        module.load_approval_verifier(path, sha)


def test_keys_are_frozen_and_changed_raw_file_requires_new_pin(tmp_path):
    module = api()
    private_key = Ed25519PrivateKey.generate()
    path, sha = key_file(tmp_path, private_key)
    verify = module.load_approval_verifier(path, sha)
    path.write_text(json.dumps(key_document(Ed25519PrivateKey.generate())))
    assert verify(REVIEWER, PAYLOAD, signature(private_key)) is True
    with pytest.raises(ValueError, match='daq_knowledge_approval'):
        module.load_approval_verifier(path, sha)
    # A semantically identical JSON document with different bytes also needs a new pin.
    path.write_text(json.dumps(key_document(private_key), indent=2))
    with pytest.raises(ValueError, match='daq_knowledge_approval'):
        module.load_approval_verifier(path, sha)


def signed_bundle(private_key, *, runtime_release='synthetic-runtime', upstream_sha='a' * 40):
    bundle = candidate()
    bundle['runtime'].update(release=runtime_release, upstream_sha=upstream_sha)
    payload = digest({k: v for k, v in bundle.items() if k != 'approval'})
    bundle['approval'] = {'reviewer': REVIEWER, 'payload_sha256': payload,
                          'signature': signature(private_key, payload=payload)}
    return bundle


def publish(root, bundle, verify):
    return releases.publish_release(root, bundle['snapshot'], bundle['records'],
        bundle['previous_release'], {**bundle['review'], 'readiness': bundle},
        sections=bundle['sections'], bodies=bundle['bodies'], verify_approval=verify)


def test_real_signed_d3_public_publish_activate_and_default_load(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    path, sha = key_file(tmp_path, private_key)
    verify = api().load_approval_verifier(path, sha)
    bundle = signed_bundle(private_key)
    root = tmp_path / 'releases'
    rid = publish(root, bundle, verify)
    releases.activate_release(root, rid, verify_approval=verify, observe=observe)
    view = ReviewedKnowledge.load_active(root, verify_approval=verify,
        runtime_release='synthetic-runtime', upstream_sha='a' * 40)
    assert view.release_id == rid
    assert view.manifest['record_count'] == 2 and view.manifest['section_count'] == 1
    with pytest.raises(ValueError, match='runtime identity'):
        ReviewedKnowledge.load_active(root, verify_approval=verify,
            runtime_release='wrong-runtime', upstream_sha='a' * 40)
    with pytest.raises(ValueError, match='independent_approval'):
        ReviewedKnowledge.load_active(root, runtime_release='synthetic-runtime', upstream_sha='a' * 40)
    bundle['scope']['gaps'].append('changed after review')
    with pytest.raises(ValueError, match='independent_approval'):
        publish(tmp_path / 'changed', bundle, verify)


def configure_authenticated_app(tmp_path, monkeypatch):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for name, value in env.items(): monkeypatch.setenv(name, value)
    monkeypatch.setenv('DAQ_RUNTIME_RELEASE', 'synthetic-app-runtime')
    monkeypatch.setenv('DAQ_ENVIRONMENT', 'development')
    grants = tmp_path / 'roles.json'
    grants.write_text(json.dumps({'agent_id': 'ai-daq-fae-agent', 'subjects': {
        str(FakePlatform().subject.subject_id): 'internal_fae'}}))
    grants.chmod(0o600)
    monkeypatch.setenv('DAQ_KNOWLEDGE_ENTITLEMENTS_FILE', str(grants))
    return dict(provider_mode='offline', platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=Conversations(), feedback_store=Feedback(), review_store=object(),
        durable_state=object(), archive_repository=object(), attachment_dir=tmp_path / 'attachments')


@pytest.mark.parametrize('missing', [KEY_FILE_ENV, KEY_HASH_ENV])
def test_app_requires_both_trust_settings(tmp_path, monkeypatch, missing):
    options = configure_authenticated_app(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    path, sha = key_file(tmp_path, private_key)
    monkeypatch.setenv(KEY_FILE_ENV, str(path))
    monkeypatch.setenv(KEY_HASH_ENV, sha)
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError, match='daq_knowledge_approval_configuration_missing'):
        create_app(knowledge_release_root=tmp_path / 'empty-release', **options)


def test_authenticated_app_uses_pinned_trust_and_injected_dependency_precedence(tmp_path, monkeypatch):
    options = configure_authenticated_app(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    path, sha = key_file(tmp_path, private_key)
    verify = api().load_approval_verifier(path, sha)
    upstream = json.loads((Path(__file__).resolve().parents[1] / 'upstream-source.json').read_text())['revision']
    bundle = signed_bundle(private_key, runtime_release='synthetic-app-runtime', upstream_sha=upstream)
    root = tmp_path / 'release'
    rid = publish(root, bundle, verify)
    releases.activate_release(root, rid, verify_approval=verify, observe=lambda current: {
        key: {**value, 'runtime_release': 'synthetic-app-runtime'} for key, value in observe(current).items()})
    # The existing no-config behavior still rejects an unsigned/untrusted D3 release.
    with pytest.raises(ValueError, match='independent_approval'):
        create_app(knowledge_release_root=root, **options)
    monkeypatch.setenv(KEY_FILE_ENV, str(path))
    monkeypatch.setenv(KEY_HASH_ENV, sha)
    app = create_app(knowledge_release_root=root, **options)
    assert app.state.daq_knowledge.release_id == rid
    assert app.state.daq_knowledge.manifest['review']['readiness']['approval'] == bundle['approval']
    client = TestClient(app, base_url=identity_environment(tmp_path)['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    assert client.get('/authenticated/conversations').status_code == 401
    launch = client.post('/enterprise/session', json={'code': CODE},
        headers={'Origin': identity_environment(tmp_path)['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    assert launch.status_code == 201
    assert client.get('/authenticated/conversations').status_code == 200
    monkeypatch.setenv(KEY_HASH_ENV, '0' * 64)
    with pytest.raises(ValueError, match='daq_knowledge_approval'):
        create_app(knowledge_release_root=root, **options)
    assert create_app(knowledge_release_root=root, knowledge_approval_verifier=verify,
        **options).state.daq_knowledge.release_id == rid


def test_trusted_signature_does_not_enable_local_dev_scope(tmp_path, monkeypatch):
    options = configure_authenticated_app(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    path, sha = key_file(tmp_path, private_key)
    verify = api().load_approval_verifier(path, sha)
    bundle = signed_bundle(private_key)
    bundle['scope']['execution_scope'] = 'loopback_dev_internal_fae_only'
    payload = digest({k: v for k, v in bundle.items() if k != 'approval'})
    bundle['approval'].update(payload_sha256=payload, signature=signature(private_key, payload=payload))
    # Seed the stored candidate with the private fixture primitive to exercise the actual app load guard.
    root = tmp_path / 'local-release'
    rid = releases._publish_release(root, bundle['snapshot'], bundle['records'], None,
        {**bundle['review'], 'readiness': bundle}, sections=bundle['sections'], bodies=bundle['bodies'])
    releases._activate_release(root, rid)
    monkeypatch.setenv(KEY_FILE_ENV, str(path))
    monkeypatch.setenv(KEY_HASH_ENV, sha)
    with pytest.raises(ValueError, match='local Dev knowledge requires explicit opt-in'):
        create_app(knowledge_release_root=root, **options)
