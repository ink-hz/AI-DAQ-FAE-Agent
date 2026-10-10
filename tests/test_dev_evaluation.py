"""Synthetic D4 contracts; these tests never call a model or grade its answers."""
from copy import deepcopy
import hashlib
import hmac
import json

import pytest

from daq_fae.knowledge import dev_evaluation as d4


KEY = b'synthetic-d4-review-key'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def sign(core, reviewer='synthetic-fae'):
    payload = digest(core)
    return {'reviewer': reviewer, 'payload_sha256': payload,
            'signature': hmac.new(KEY, payload.encode(), hashlib.sha256).hexdigest()}


def verify(reviewer, payload, signature):
    return reviewer == 'synthetic-fae' and hmac.compare_digest(
        hmac.new(KEY, payload.encode(), hashlib.sha256).hexdigest(), signature)


def suite():
    questions = [
        {'id': 'q:' + family, 'family': family, 'question': 'Synthetic ' + family + '?',
         'turns': (['Synthetic prior condition?'] if family == 'multi_turn' else [])
                  + ['Synthetic ' + family + '?'], 'role': 'internal_fae',
         'coverage_ids': ['coverage:' + family],
         'expected_boundary': 'Cite reviewed evidence or expose a gap.'}
        for family in d4.REQUIRED_FAMILIES
    ]
    core = {'format': 'daq-dev-questions/v1', 'environment': 'dev', 'status': 'frozen',
            'batch_id': 'synthetic-batch', 'questions': questions,
            'questions_sha256': digest(questions),
            'required_question_ids': [q['id'] for q in questions],
            'reviewed_at': '2026-10-10'}
    return {**core, 'approval': sign(core)}


def release(s):
    return {'environment': 'dev', 'release_id': 'a'*64, 'runtime_release': 'runtime-1',
            'upstream_sha': 'b'*40, 'dev_batch_id': s['batch_id'],
            'questions_sha256': s['questions_sha256']}


def release_verify(row):
    return row['release_id'] == 'a'*64


def replays(s, r):
    return [{
        'case_id': q['id'], 'suite_sha256': digest(s), 'environment': 'dev',
        'knowledge_release': r['release_id'], 'runtime_release': r['runtime_release'],
        'upstream_sha': r['upstream_sha'], 'answer_model': 'synthetic-answer-model-v1',
        'prompt_version': 'synthetic-prompt-v1', 'captured_at': '2026-10-10T12:00:00+08:00',
        'turns': [{'question': prompt, 'answer': 'Synthetic answer',
                   'sources': [{'source_id': 'source:synthetic', 'locator': 'page:1'}],
                   'planned_capabilities': ['lookup_spec'], 'actual_capabilities': ['lookup_spec'],
                   'coverage': [{'requirement_id': 'r1', 'status': 'satisfied'}],
                   'fallback': 'none', 'outcome': 'resolved', 'trace_id': 'trace:synthetic',
                   'latency_ms': 12,
                   'provider_http_statuses': ([400] if q['family'] == 'provider_http_400' else
                                              [503] if q['family'] == 'provider_http_503' else [])}
                  for prompt in q['turns']],
        'release_transitions': [{'before': r['release_id'], 'after': 'c'*64,
                                 'action': 'rollback'}] if q['family'] == 'update_rollback' else [],
    } for q in s['questions']]


def reviews(rows):
    result = []
    for row in rows:
        core = {'case_id': row['case_id'], 'replay_sha256': digest(row),
                'reviewer': 'synthetic-fae', 'reviewed_at': '2026-10-10',
                'verdict': 'pass', 'serious_failures': [], 'failure_layer': None,
                'notes': 'Synthetic contract only; no semantic conclusion.'}
        result.append({**core, 'approval': sign(core)})
    return result


def test_frozen_suite_requires_every_family_full_question_and_trusted_approval():
    s = suite()
    assert d4.validate_frozen_suite(s, verify_approval=verify) == digest(s)
    for mutation in ('draft', 'missing_family', 'generic_prompt', 'missing_impact', 'forged', 'production'):
        bad = deepcopy(s)
        if mutation == 'draft':
            bad['status'] = 'draft'
        if mutation == 'missing_family':
            bad['questions'].pop()
        if mutation == 'generic_prompt':
            bad['questions'][0]['question'] = 'Check evidence status, exact applicability and sources for this coverage cell; preserve gaps and conflicts.'
        if mutation == 'missing_impact':
            bad['required_question_ids'].append('q:missing')
        if mutation == 'forged':
            bad['approval']['signature'] = 'forged'
        if mutation == 'production':
            bad['environment'] = 'production'
        with pytest.raises(ValueError):
            d4.validate_frozen_suite(bad, verify_approval=verify)
    with pytest.raises(ValueError):
        d4.validate_frozen_suite(s)
    bad = deepcopy(s)
    bad['questions'][0]['role'] = []
    with pytest.raises(ValueError):
        d4.validate_frozen_suite(bad, verify_approval=verify)


def test_replay_requires_trusted_dev_release_exact_identity_and_per_turn_evidence():
    s = suite()
    r = release(s)
    rows = replays(s, r)
    assert d4.validate_replay_batch(s, r, rows, verify_suite=verify,
                                    verify_release=release_verify) == digest(rows)
    for field, value in [('environment', 'production'), ('knowledge_release', '0'*64),
                         ('runtime_release', 'wrong'), ('suite_sha256', '0'*64)]:
        bad = deepcopy(rows)
        bad[0][field] = value
        with pytest.raises(ValueError):
            d4.validate_replay_batch(s, r, bad, verify_suite=verify,
                                     verify_release=release_verify)
    for field in ('prompt_version', 'captured_at'):
        bad = deepcopy(rows)
        del bad[0][field]
        with pytest.raises(ValueError):
            d4.validate_replay_batch(s, r, bad, verify_suite=verify,
                                     verify_release=release_verify)
    for field in ('answer', 'sources', 'planned_capabilities', 'actual_capabilities',
                  'coverage', 'fallback', 'outcome', 'trace_id', 'latency_ms'):
        bad = deepcopy(rows)
        del bad[0]['turns'][0][field]
        with pytest.raises(ValueError):
            d4.validate_replay_batch(s, r, bad, verify_suite=verify,
                                     verify_release=release_verify)
    bad = deepcopy(rows)
    bad[0]['turns'][0]['answer'] = 'See /Users/private/raw/guide.pdf'
    with pytest.raises(ValueError, match='source path'):
        d4.validate_replay_batch(s, r, bad, verify_suite=verify,
                                 verify_release=release_verify)
    with pytest.raises(ValueError):
        d4.validate_replay_batch(s, r, rows, verify_suite=verify)


def test_review_packet_starts_unsigned_and_independent_review_is_required():
    s = suite()
    r = release(s)
    rows = replays(s, r)
    packet = d4.prepare_review_packet(s, r, rows, verify_suite=verify,
                                      verify_release=release_verify)
    assert len(packet['items']) == len(d4.REQUIRED_FAMILIES)
    assert all(item['review'] is None for item in packet['items'])
    with pytest.raises(ValueError):
        d4.audit_answer_reviews(s, r, rows, [], verify_suite=verify,
                                verify_release=release_verify, verify_reviewer=verify)
    assert d4.audit_answer_reviews(s, r, rows, reviews(rows), verify_suite=verify,
                                   verify_release=release_verify, verify_reviewer=verify)['accepted']


def test_self_review_forgery_and_severe_answer_error_cannot_pass():
    s = suite()
    r = release(s)
    rows = replays(s, r)
    decisions = reviews(rows)
    bad = deepcopy(decisions)
    bad[0]['reviewer'] = 'synthetic-answer-model-v1'
    core = {k: v for k, v in bad[0].items() if k != 'approval'}
    bad[0]['approval'] = sign(core)
    with pytest.raises(ValueError):
        d4.audit_answer_reviews(s, r, rows, bad, verify_suite=verify,
                                verify_release=release_verify, verify_reviewer=verify)
    bad = deepcopy(decisions)
    bad[0]['approval']['signature'] = 'forged'
    with pytest.raises(ValueError):
        d4.audit_answer_reviews(s, r, rows, bad, verify_suite=verify,
                                verify_release=release_verify, verify_reviewer=verify)
    bad = deepcopy(decisions)
    bad[0].update(verdict='fail', serious_failures=['model_identity_mix'],
                  failure_layer='synthesis', notes='Synthetic severe failure.')
    bad[0]['approval'] = sign({k: v for k, v in bad[0].items() if k != 'approval'})
    report = d4.audit_answer_reviews(s, r, rows, bad, verify_suite=verify,
                                     verify_release=release_verify, verify_reviewer=verify)
    assert not report['accepted'] and report['serious_failure_count'] == 1
    bad = deepcopy(decisions)
    bad[0]['serious_failures'] = [{}]
    with pytest.raises(ValueError):
        d4.audit_answer_reviews(s, r, rows, bad, verify_suite=verify,
                                verify_release=release_verify, verify_reviewer=verify)


def test_provider_and_rollback_families_require_specific_observation():
    s = suite()
    r = release(s)
    rows = replays(s, r)
    for family in ('provider_http_400', 'provider_http_503', 'update_rollback'):
        bad = deepcopy(rows)
        case = next(row for row in bad if row['case_id'] == 'q:' + family)
        if family == 'update_rollback':
            case['release_transitions'] = []
        else:
            case['turns'][0]['provider_http_statuses'] = []
        with pytest.raises(ValueError):
            d4.validate_replay_batch(s, r, bad, verify_suite=verify,
                                     verify_release=release_verify)


def test_b3_generic_drafts_are_only_gap_candidates_not_frozen_d4_coverage():
    drafts = [{'question_id': 'dev:draft:' + str(i),
               'question_family': 'coverage_state_and_boundaries',
               'coverage_ids': ['coverage:' + str(i)], 'status': 'draft',
               'frozen': False, 'replay_status': 'not_replayed',
               'approval_status': 'not_approved',
               'question': 'Check evidence status, exact applicability and sources for this coverage cell; preserve gaps and conflicts.'}
              for i in range(2)]
    report = d4.audit_draft_candidates(drafts)
    assert report['draft_count'] == 2
    assert report['candidate_family'] == 'evidence_gap'
    assert len(report['missing_candidate_families']) == 9
    assert set(report['missing_frozen_families']) == set(d4.REQUIRED_FAMILIES)
    assert report['frozen_eligible_count'] == 0
