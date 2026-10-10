"""Synthetic D4 contracts; these tests never call a model or grade its answers."""
from copy import deepcopy
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from daq_fae.knowledge import dev_evaluation as d4
from test_empty_bootstrap import _events


KEY = b'synthetic-d4-review-key'
CAPTURE_KEY = b'synthetic-dev-capture-key'


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


def sign_capture(row):
    payload = digest({k: v for k, v in row.items() if k != 'capture_approval'})
    row['capture_approval'] = {
        'reviewer': 'synthetic-dev-recorder', 'payload_sha256': payload,
        'signature': hmac.new(CAPTURE_KEY, payload.encode(), hashlib.sha256).hexdigest(),
    }
    return row


def verify_capture(reviewer, payload, signature):
    return reviewer == 'synthetic-dev-recorder' and hmac.compare_digest(
        hmac.new(CAPTURE_KEY, payload.encode(), hashlib.sha256).hexdigest(), signature)


def verify_pair(row):
    return row['release_id'] == 'a'*64 and row['previous_release'] == 'c'*64


def validate(s, r, rows, *, pair=verify_pair, capture=verify_capture):
    return d4.validate_replay_batch(
        s, r, rows, verify_suite=verify, verify_release=release_verify,
        verify_release_pair=pair, verify_capture=capture)


def suite():
    questions = [
        {'id': 'q:' + family, 'family': family, 'question': 'Synthetic ' + family + '?',
         'turns': (['Synthetic before?', 'Synthetic after update?']
                   if family == 'update_rollback' else
                   ['Synthetic prior condition?'] if family == 'multi_turn' else [])
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
            'upstream_sha': 'b'*40, 'previous_release': 'c'*64,
            'dev_batch_id': s['batch_id'],
            'questions_sha256': s['questions_sha256']}


def release_verify(row):
    return row['release_id'] == 'a'*64


def replays(s, r):
    rows = []
    for q in s['questions']:
        update = q['family'] == 'update_rollback'
        versions = ([r['previous_release'], r['release_id'], r['previous_release']]
                    if update else [r['release_id']] * len(q['turns']))
        turns = []
        for index, (prompt, version) in enumerate(zip(q['turns'], versions)):
            turns.append({
                'question': prompt, 'answer': 'Synthetic answer',
                'agent_id': 'ai-daq-fae-agent', 'observed_role': q['role'],
                'knowledge_release': version, 'runtime_release': r['runtime_release'],
                'sources': [{'source_id': 'source:synthetic', 'locator': 'page:1'}],
                'planned_capabilities': ['lookup_spec'], 'actual_capabilities': ['lookup_spec'],
                'coverage': [{'requirement_id': 'r1', 'status': 'satisfied'}],
                'fallback': (q['family'] if q['family'] in {'provider_http_400',
                                                           'provider_http_503'} else 'none'),
                'outcome': ('provider_configuration_error' if q['family'] == 'provider_http_400'
                            else 'provider_unavailable' if q['family'] == 'provider_http_503'
                            else 'resolved'),
                'trace_id': f"trace:{q['family']}:{index}", 'latency_ms': 12,
                'provider_terminal_status_code': (400 if q['family'] == 'provider_http_400' else
                                                  503 if q['family'] == 'provider_http_503' else None),
                'provider_http_statuses': ([400] if q['family'] == 'provider_http_400' else
                                           [503] if q['family'] == 'provider_http_503' else []),
            })
        transitions = []
        if update:
            transitions = [
                {'action': 'activate', 'before': versions[0], 'after': versions[1],
                 'observation': {
                     'health': {'agent_id': 'ai-daq-fae-agent',
                                'knowledge_release': versions[1],
                                'runtime_release': r['runtime_release']},
                     'trace': {'agent_id': 'ai-daq-fae-agent',
                               'knowledge_release': versions[1],
                               'runtime_release': r['runtime_release'],
                               'trace_id': turns[1]['trace_id']}}},
                {'action': 'rollback', 'before': versions[1], 'after': versions[2],
                 'observation': {
                     'health': {'agent_id': 'ai-daq-fae-agent',
                                'knowledge_release': versions[2],
                                'runtime_release': r['runtime_release']},
                     'trace': {'agent_id': 'ai-daq-fae-agent',
                               'knowledge_release': versions[2],
                               'runtime_release': r['runtime_release'],
                               'trace_id': turns[2]['trace_id']}}},
            ]
        row = {
            'case_id': q['id'], 'suite_sha256': digest(s), 'environment': 'dev',
            'agent_id': 'ai-daq-fae-agent', 'observed_role': q['role'],
            'capture_ref': 'synthetic-dev-trace-store',
            'knowledge_release': r['release_id'], 'runtime_release': r['runtime_release'],
            'upstream_sha': r['upstream_sha'], 'answer_model': 'synthetic-answer-model-v1',
            'prompt_version': 'synthetic-prompt-v1', 'captured_at': '2026-10-10T12:00:00+08:00',
            'turns': turns, 'release_transitions': transitions,
        }
        rows.append(sign_capture(row))
    return rows


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
                                    verify_release=release_verify,
                                     verify_release_pair=verify_pair,
                                     verify_capture=verify_capture) == digest(rows)
    for field, value in [('environment', 'production'), ('knowledge_release', '0'*64),
                         ('runtime_release', 'wrong'), ('suite_sha256', '0'*64)]:
        bad = deepcopy(rows)
        bad[0][field] = value
        with pytest.raises(ValueError):
            d4.validate_replay_batch(s, r, bad, verify_suite=verify,
                                     verify_release=release_verify,
                                     verify_release_pair=verify_pair,
                                     verify_capture=verify_capture)
    for field in ('prompt_version', 'captured_at'):
        bad = deepcopy(rows)
        del bad[0][field]
        with pytest.raises(ValueError):
            d4.validate_replay_batch(s, r, bad, verify_suite=verify,
                                     verify_release=release_verify,
                                     verify_release_pair=verify_pair,
                                     verify_capture=verify_capture)
    for field in ('answer', 'sources', 'planned_capabilities', 'actual_capabilities',
                  'coverage', 'fallback', 'outcome', 'trace_id', 'latency_ms'):
        bad = deepcopy(rows)
        del bad[0]['turns'][0][field]
        with pytest.raises(ValueError):
            d4.validate_replay_batch(s, r, bad, verify_suite=verify,
                                     verify_release=release_verify,
                                     verify_release_pair=verify_pair,
                                     verify_capture=verify_capture)
    bad = deepcopy(rows)
    bad[0]['turns'][0]['answer'] = 'See /Users/private/raw/guide.pdf'
    with pytest.raises(ValueError, match='source path'):
        d4.validate_replay_batch(s, r, bad, verify_suite=verify,
                                 verify_release=release_verify,
                                     verify_release_pair=verify_pair,
                                     verify_capture=verify_capture)
    with pytest.raises(ValueError):
        d4.validate_replay_batch(s, r, rows, verify_suite=verify)


def test_review_packet_starts_unsigned_and_independent_review_is_required():
    s = suite()
    r = release(s)
    rows = replays(s, r)
    packet = d4.prepare_review_packet(s, r, rows, verify_suite=verify,
                                      verify_release=release_verify,
                                     verify_release_pair=verify_pair,
                                     verify_capture=verify_capture)
    assert len(packet['items']) == len(d4.REQUIRED_FAMILIES)
    assert all(item['review'] is None for item in packet['items'])
    with pytest.raises(ValueError):
        d4.audit_answer_reviews(s, r, rows, [], verify_suite=verify,
                                verify_release=release_verify, verify_reviewer=verify,
                                verify_release_pair=verify_pair,
                                verify_capture=verify_capture)
    assert d4.audit_answer_reviews(s, r, rows, reviews(rows), verify_suite=verify,
                                   verify_release=release_verify, verify_reviewer=verify,
                                verify_release_pair=verify_pair,
                                verify_capture=verify_capture)['accepted']


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
                                verify_release=release_verify, verify_reviewer=verify,
                                verify_release_pair=verify_pair,
                                verify_capture=verify_capture)
    bad = deepcopy(decisions)
    bad[0]['approval']['signature'] = 'forged'
    with pytest.raises(ValueError):
        d4.audit_answer_reviews(s, r, rows, bad, verify_suite=verify,
                                verify_release=release_verify, verify_reviewer=verify,
                                verify_release_pair=verify_pair,
                                verify_capture=verify_capture)
    bad = deepcopy(decisions)
    bad[0].update(verdict='fail', serious_failures=['model_identity_mix'],
                  failure_layer='synthesis', notes='Synthetic severe failure.')
    bad[0]['approval'] = sign({k: v for k, v in bad[0].items() if k != 'approval'})
    report = d4.audit_answer_reviews(s, r, rows, bad, verify_suite=verify,
                                     verify_release=release_verify, verify_reviewer=verify,
                                verify_release_pair=verify_pair,
                                verify_capture=verify_capture)
    assert not report['accepted'] and report['serious_failure_count'] == 1
    bad = deepcopy(decisions)
    bad[0]['serious_failures'] = [{}]
    with pytest.raises(ValueError):
        d4.audit_answer_reviews(s, r, rows, bad, verify_suite=verify,
                                verify_release=release_verify, verify_reviewer=verify,
                                verify_release_pair=verify_pair,
                                verify_capture=verify_capture)


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
                                     verify_release=release_verify,
                                     verify_release_pair=verify_pair,
                                     verify_capture=verify_capture)


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


@pytest.mark.parametrize(('status_code', 'expected'), [
    (None, 'safe_abstained'),
    (400, 'provider_configuration_error'),
    (503, 'provider_unavailable'),
])
def test_d4_accepts_exact_real_app_terminal_outcome(status_code, expected):
    from daq_fae.app import create_app

    class FailingAdapter:
        model = 'synthetic-model'
        tool_choice_strategy = 'submit_only_auto'

        def chat(self, messages, tools, required_tool=None):
            request = httpx.Request('POST', 'https://example.invalid/v1/messages')
            response = httpx.Response(status_code, request=request)
            raise httpx.HTTPStatusError('synthetic gateway error', request=request,
                                        response=response)
            yield

    app = (create_app(provider_mode='offline') if status_code is None else
           create_app(adapter=FailingAdapter()))
    response = TestClient(app).post('/chat', json={'question': 'Synthetic DAQ question'})
    done = next(event for event in _events(response) if event['type'] == 'done')
    assert done['outcome'] == expected
    assert done['outcome'] in d4.OUTCOMES
    if status_code is not None:
        assert done['provider_status_code'] == status_code
    s = suite()
    r = release(s)
    rows = replays(s, r)
    family = ('evidence_gap' if status_code is None else
              'provider_http_' + str(status_code))
    row = next(item for item in rows if item['case_id'] == 'q:' + family)
    turn = row['turns'][0]
    turn['outcome'] = done['outcome']
    turn['answer'] = done['answer']
    turn['provider_terminal_status_code'] = done.get('provider_status_code')
    turn['fallback'] = done.get('fallback_reason') or 'none'
    turn['provider_http_statuses'] = ([done['provider_status_code']]
                                      if status_code is not None else [])
    sign_capture(row)
    assert validate(s, r, rows) == digest(rows)


def test_replay_rejects_role_spoof_and_unsigned_capture():
    s = suite()
    r = release(s)
    rows = replays(s, r)
    bad = deepcopy(rows)
    bad[0]['observed_role'] = 'channel'
    sign_capture(bad[0])
    with pytest.raises(ValueError):
        validate(s, r, bad)
    for field, value in [('observed_role', 'channel'),
                         ('knowledge_release', 'f'*64),
                         ('trace_id', '')]:
        bad = deepcopy(rows)
        bad[0]['turns'][0][field] = value
        sign_capture(bad[0])
        with pytest.raises(ValueError):
            validate(s, r, bad)
    with pytest.raises(ValueError):
        validate(s, r, rows, capture=None)
    bad = deepcopy(rows)
    bad[0]['capture_approval'] = {'reviewer': 'forged', 'signature': 'forged'}
    with pytest.raises(ValueError):
        validate(s, r, bad)


def test_update_rollback_rejects_unrelated_or_same_release_transition():
    s = suite()
    r = release(s)
    r['previous_release'] = 'c' * 64
    rows = replays(s, r)
    case = next(row for row in rows if row['case_id'] == 'q:update_rollback')
    case['release_transitions'] = [
        {'before': 'd' * 64, 'after': 'e' * 64, 'action': 'rollback'}]
    sign_capture(case)
    with pytest.raises(ValueError):
        validate(s, r, rows)
    assert validate(s, r, replays(s, r)) == digest(replays(s, r))
    with pytest.raises(ValueError):
        validate(s, r, replays(s, r), pair=None)
    with pytest.raises(ValueError):
        validate(s, r, replays(s, r), pair=lambda _: False)
    case['release_transitions'] = [
        {'before': 'c' * 64, 'after': 'c' * 64, 'action': 'rollback'}]
    sign_capture(case)
    with pytest.raises(ValueError):
        validate(s, r, rows)
    unrelated = deepcopy(r)
    unrelated['previous_release'] = 'd' * 64
    with pytest.raises(ValueError):
        validate(s, unrelated, replays(s, unrelated))
    same = deepcopy(r)
    same['previous_release'] = same['release_id']
    with pytest.raises(ValueError):
        validate(s, same, replays(s, same))


def test_malformed_case_id_is_validation_error():
    s = suite()
    r = release(s)
    rows = replays(s, r)
    rows[0]['case_id'] = []
    with pytest.raises(ValueError):
        validate(s, r, rows)


def test_provider_terminal_cannot_be_relabelled_as_knowledge_gap():
    s = suite()
    r = release(s)
    rows = replays(s, r)
    for family in ('provider_http_400', 'provider_http_503'):
        bad = deepcopy(rows)
        row = next(item for item in bad if item['case_id'] == 'q:' + family)
        row['turns'][0]['outcome'] = 'safe_abstained'
        sign_capture(row)
        with pytest.raises(ValueError):
            validate(s, r, bad)
        bad = deepcopy(rows)
        row = next(item for item in bad if item['case_id'] == 'q:' + family)
        row['turns'][0]['provider_terminal_status_code'] = None
        row['turns'][0]['outcome'] = 'safe_abstained'
        sign_capture(row)
        with pytest.raises(ValueError):
            validate(s, r, bad)
