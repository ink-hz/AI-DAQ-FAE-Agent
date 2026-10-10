"""D3 uses synthetic reviewed records only; never touches service release roots."""
from copy import deepcopy
import hashlib
import hmac
import importlib
import json

import pytest

from test_knowledge_releases import SNAPSHOT, REVIEW, _entity, _record
from test_section_releases import approved_section
from daq_fae.knowledge import releases


def api():
    module = importlib.import_module('daq_fae.knowledge.release_readiness')
    assert hasattr(module, 'audit_readiness'), 'D3 readiness gate missing'
    return module


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def sign(bundle):
    core = {k: v for k, v in bundle.items() if k != 'approval'}
    bundle['approval'] = {'reviewer': 'synthetic-independent-reviewer',
                          'payload_sha256': digest(core),
                          'signature': hmac.new(b'synthetic-only-key', digest(core).encode(), hashlib.sha256).hexdigest()}
    return bundle


def verify(reviewer, payload, signature):
    return reviewer == 'synthetic-independent-reviewer' and hmac.compare_digest(
        hmac.new(b'synthetic-only-key', payload.encode(), hashlib.sha256).hexdigest(), signature)


def candidate(previous=None):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    snapshot = deepcopy(SNAPSHOT)
    sources_digest = digest(snapshot['sources'])
    return sign({
        'snapshot': snapshot, 'records': rows, 'sections': [section],
        'bodies': {section['section_id']: body}, 'previous_release': previous,
        'review': deepcopy(REVIEW),
        'archive': {'manifest_sha256': snapshot['archive_manifest_sha256'],
                    'sources_sha256': sources_digest, 'retrieved_sources_sha256': sources_digest,
                    'independent_durable': True, 'custody_ref': 'synthetic-custody',
                    'retrieval_ref': 'synthetic-retrieval', 'permissions_ref': 'synthetic-acl'},
        'runtime': {'release': 'synthetic-runtime', 'upstream_sha': 'a'*40,
                    'contract': 'daq-reviewed-sections-v2', 'contract_tests_sha256': 'a'*64,
                    'tasks_enabled': False, 'generic_trace_enabled': False},
        'scope': {'answerable_ids': [r['id'] for r in rows],
                  'excluded_topics': ['software', 'links', 'module_projection'],
                  'gaps': ['synthetic incomplete topic'], 'identity_review_sha256': 'b'*64},
        'reviews': {'unsigned_count': 0, 'review_packet_sha256': 'c'*64,
                    'review_decisions_sha256': 'd'*64},
        'impact': {'plan_sha256': 'e'*64, 'open_obligations': [],
                   'required_question_ids': ['q:synthetic']},
        'dev_batch': {'id': 'synthetic-k1', 'environment': 'dev', 'frozen': True,
                      'questions': [{'id': 'q:synthetic', 'question': 'Synthetic question'}],
                      'questions_sha256': digest([{'id': 'q:synthetic', 'question': 'Synthetic question'}])},
        'blockers': [],
    })


def test_ready_is_read_only_and_stage_is_immutable_explicit(tmp_path):
    m = api()
    bundle = candidate()
    report = m.audit_readiness(bundle, verify_approval=verify)
    assert report['ready'] and report['reasons'] == []
    rid = m.stage_release(tmp_path, bundle, verify_approval=verify)
    assert releases.read_active_release(tmp_path) is None
    manifest = releases._read_release(tmp_path, rid)[0]
    assert manifest['previous_release'] is None
    assert manifest['review']['readiness']['approval'] == bundle['approval']
    assert manifest['section_count'] == 1 and manifest['record_count'] == 2
    assert not (tmp_path/'releases'/rid/'manifest.json').stat().st_mode & 0o222
    assert m.stage_release(tmp_path, bundle, verify_approval=verify) == rid


@pytest.mark.parametrize('field,value,reason', [
    ('archive.independent_durable', False, 'archive_custody'),
    ('archive.custody_ref', '', 'archive_custody'),
    ('archive.retrieved_sources_sha256', '0'*64, 'archive_retrieval'),
    ('archive.manifest_sha256', '0'*64, 'archive_manifest'),
    ('reviews.unsigned_count', 572, 'unsigned_reviews'),
    ('reviews.review_decisions_sha256', '', 'review_digests'),
    ('impact.open_obligations', ['re-review changed source'], 'impact_obligations'),
    ('impact.required_question_ids', ['q:missing'], 'impact_batch_coverage'),
    ('runtime.contract', 'old-contract', 'runtime_compatibility'),
    ('runtime.tasks_enabled', True, 'unsupported_entrypoints'),
    ('runtime.generic_trace_enabled', True, 'unsupported_entrypoints'),
    ('scope.answerable_ids', [], 'answerable_scope'),
    ('scope.identity_review_sha256', None, 'identity_review'),
    ('dev_batch.frozen', False, 'dev_batch'),
    ('dev_batch.environment', 'production', 'dev_batch'),
    ('dev_batch.questions_sha256', '0'*64, 'dev_batch_digest'),
    ('blockers', ['B2 unresolved'], 'declared_blockers'),
])
def test_each_hard_gate_rejects_even_with_authentic_review(tmp_path, field, value, reason):
    m = api()
    bundle = candidate()
    target = bundle
    parts = field.split('.')
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    sign(bundle)
    report = m.audit_readiness(bundle, verify_approval=verify)
    assert not report['ready'] and reason in report['reasons']
    with pytest.raises(ValueError):
        m.stage_release(tmp_path, bundle, verify_approval=verify)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('mutation', ['record', 'body', 'source', 'role', 'signature', 'missing_verifier'])
def test_no_review_or_content_bypass(tmp_path, mutation):
    m = api()
    b = candidate()
    if mutation == 'record': b['records'][1]['data']['value'] = 'changed'
    if mutation == 'body': b['bodies'][b['sections'][0]['section_id']] += 'unreconciled'
    if mutation == 'source': b['snapshot']['sources'][0]['sha256'] = '0'*64
    if mutation == 'role': b['sections'][0]['view_roles'] = ['channel']
    if mutation == 'signature': b['approval']['signature'] = 'forged'
    if mutation in {'record', 'body', 'source', 'role'}: sign(b)
    report = m.audit_readiness(b, verify_approval=None if mutation == 'missing_verifier' else verify)
    assert not report['ready']


def observe(rid):
    return {'health': {'agent_id': 'ai-daq-fae-agent', 'knowledge_release': rid,
                       'runtime_release': 'synthetic-runtime'},
            'trace': {'agent_id': 'ai-daq-fae-agent', 'knowledge_release': rid,
                      'runtime_release': 'synthetic-runtime', 'trace_id': 'synthetic-trace'}}


def test_explicit_activation_rollback_and_failed_health_restore(tmp_path):
    m = api()
    first = m.stage_release(tmp_path, candidate(), verify_approval=verify)
    m.activate_checked(tmp_path, first, verify_approval=verify, observe=observe)
    second_bundle = candidate(first)
    second = m.stage_release(tmp_path, second_bundle, verify_approval=verify)
    m.activate_checked(tmp_path, second, verify_approval=verify, observe=observe)
    assert releases.read_active_release(tmp_path)['release_id'] == second
    m.rollback_checked(tmp_path, verify_approval=verify, observe=observe)
    assert releases.read_active_release(tmp_path)['release_id'] == first
    with pytest.raises(ValueError, match='observation'):
        m.activate_checked(tmp_path, second, verify_approval=verify,
                           observe=lambda rid: observe(first))
    assert releases.read_active_release(tmp_path)['release_id'] == first


def test_failed_pointer_and_failed_rollback_keep_previous(tmp_path, monkeypatch):
    m = api()
    first = m.stage_release(tmp_path, candidate(), verify_approval=verify)
    m.activate_checked(tmp_path, first, verify_approval=verify, observe=observe)
    second = m.stage_release(tmp_path, candidate(first), verify_approval=verify)
    original = releases.activate_release
    monkeypatch.setattr(releases, 'activate_release', lambda *a: (_ for _ in ()).throw(OSError('synthetic failure')))
    with pytest.raises(OSError): m.activate_checked(tmp_path, second, verify_approval=verify, observe=observe)
    assert releases.read_active_release(tmp_path)['release_id'] == first
    monkeypatch.setattr(releases, 'activate_release', original)
    m.activate_checked(tmp_path, second, verify_approval=verify, observe=observe)
    with pytest.raises(ValueError, match='observation'):
        m.rollback_checked(tmp_path, verify_approval=verify, observe=lambda rid: observe(second))
    assert releases.read_active_release(tmp_path)['release_id'] == second


def test_stale_prior_or_changed_approval_rejected_before_pointer(tmp_path):
    m = api()
    first = m.stage_release(tmp_path, candidate(), verify_approval=verify)
    m.activate_checked(tmp_path, first, verify_approval=verify, observe=observe)
    other = candidate()
    other['review']['reviewer'] = 'other'
    second = m.stage_release(tmp_path, sign(other), verify_approval=verify)
    with pytest.raises(ValueError, match='previous'):
        m.activate_checked(tmp_path, second, verify_approval=verify, observe=observe)
    assert releases.read_active_release(tmp_path)['release_id'] == first


def test_approval_cannot_be_reused_after_bundle_drift():
    m = api()
    b = candidate()
    b['scope']['gaps'].append('changed scope')
    assert 'independent_approval' in m.audit_readiness(b, verify_approval=verify)['reasons']


@pytest.mark.parametrize('observation', [None, {}, {'health': {}, 'trace': {}}])
def test_initial_bad_observation_restores_absent_pointer(tmp_path, observation):
    m = api()
    rid = m.stage_release(tmp_path, candidate(), verify_approval=verify)
    with pytest.raises(ValueError, match='observation'):
        m.activate_checked(tmp_path, rid, verify_approval=verify, observe=lambda rid: observation)
    assert releases.read_active_release(tmp_path) is None


def test_signed_manifest_must_match_the_candidate_content(tmp_path):
    m = api()
    rid = m.stage_release(tmp_path, candidate(), verify_approval=verify)
    manifest = releases._read_release(tmp_path, rid)[0]
    manifest['source_date'] = '2026-01-01'
    data = releases._json_bytes(manifest)
    altered = releases._digest(data)
    path = tmp_path/'releases'/altered
    path.mkdir()
    (path/'manifest.json').write_bytes(data)
    with pytest.raises(ValueError, match='binding'):
        m.activate_checked(tmp_path, altered, verify_approval=verify, observe=observe)
    assert releases.read_active_release(tmp_path) is None


def test_failed_restore_does_not_claim_rollback_success(tmp_path, monkeypatch):
    m = api()
    first = m.stage_release(tmp_path, candidate(), verify_approval=verify)
    m.activate_checked(tmp_path, first, verify_approval=verify, observe=observe)
    second = m.stage_release(tmp_path, candidate(first), verify_approval=verify)
    original = releases.activate_release
    def broken_restore(root, rid):
        if rid == first: raise OSError('rollback pointer failed')
        original(root, rid)
    monkeypatch.setattr(releases, 'activate_release', broken_restore)
    with pytest.raises(OSError, match='rollback pointer failed'):
        m.activate_checked(tmp_path, second, verify_approval=verify, observe=lambda rid: {})
    assert releases.read_active_release(tmp_path)['release_id'] == second


def test_missing_topic_disposition_cannot_be_silently_omitted():
    m = api()
    b = candidate()
    b['scope']['excluded_topics'].remove('module_projection')
    assert 'topic_dispositions' in m.audit_readiness(sign(b), verify_approval=verify)['reasons']
