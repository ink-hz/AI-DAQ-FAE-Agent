"""Offline D4 question, replay, and independent-answer-review contracts.

This module has no model client, reviewer credential, or release publisher. The
caller must supply trusted verifiers; missing verifiers always reject acceptance.
"""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import re

from .source_paths import contains_source_path
from src.agent.protocol import FINAL_OUTCOMES


REQUIRED_FAMILIES = (
    'product_identity', 'spec_conflict', 'combination_flow', 'software_links',
    'permissions', 'multi_turn', 'evidence_gap', 'provider_http_400',
    'provider_http_503', 'update_rollback',
)
SERIOUS_FAILURES = frozenset({
    'model_identity_mix', 'permission_leak', 'unsupported_resolved',
    'runtime_error_masked_as_knowledge_gap',
})
FAILURE_LAYERS = frozenset({'source', 'governance', 'retrieval', 'coverage',
                            'synthesis', 'runtime'})
# The DAQ HTTP adapters emit these additional terminal failures around the
# shared Loop. Preserve the wire outcome instead of normalizing it for eval.
OUTCOMES = FINAL_OUTCOMES | frozenset({
    'provider_rate_limited', 'provider_protocol_error', 'provider_error',
    'internal_error', 'persistence_error', 'authorization_error',
})
_DRAFT_PROMPT = ('Check evidence status, exact applicability and sources for this '
                 'coverage cell; preserve gaps and conflicts.')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_SHA1 = re.compile(r'^[0-9a-f]{40}$')


def audit_draft_candidates(drafts):
    """Inventory B3 drafts without promoting generic prompts to frozen tests."""
    _need(isinstance(drafts, list), 'draft candidates must be a list')
    ids = []
    for row in drafts:
        _need(isinstance(row, dict) and _text(row.get('question_id')) and
              row.get('question_family') == 'coverage_state_and_boundaries' and
              row.get('question') == _DRAFT_PROMPT and
              _strings(row.get('coverage_ids'), nonempty=True) and
              row.get('status') == 'draft' and row.get('frozen') is False and
              row.get('replay_status') == 'not_replayed' and
              row.get('approval_status') == 'not_approved',
              'B3 candidate is not an unreviewed generic draft')
        ids.append(row['question_id'])
    _need(len(ids) == len(set(ids)), 'duplicate B3 question IDs')
    return {'draft_count': len(drafts), 'candidate_family': 'evidence_gap',
            'missing_candidate_families': [f for f in REQUIRED_FAMILIES
                                           if f != 'evidence_gap'],
            'missing_frozen_families': list(REQUIRED_FAMILIES),
            'frozen_eligible_count': 0, 'canonical_draft_sha256': digest(drafts)}


def digest(value):
    """SHA-256 of canonical JSON; content is retained by the private caller."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _need(condition, reason):
    if not condition:
        raise ValueError(reason)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _strings(value, *, nonempty=False):
    return isinstance(value, list) and (not nonempty or bool(value)) and all(
        _text(item) for item in value)


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (TypeError, ValueError):
        return False


def _approved(core, approval, verify, *, reviewer=None):
    _need(callable(verify), 'trusted approval verifier required')
    _need(isinstance(approval, dict), 'approval missing')
    signer = approval.get('reviewer')
    _need(_text(signer) and (reviewer is None or signer == reviewer), 'approval reviewer mismatch')
    payload = digest(core)
    _need(approval.get('payload_sha256') == payload and _text(approval.get('signature')),
          'approval content binding mismatch')
    try:
        accepted = verify(signer, payload, approval['signature']) is True
    except Exception:
        accepted = False
    _need(accepted, 'trusted approval rejected')


def validate_frozen_suite(suite, *, verify_approval=None):
    """Require an explicitly reviewed, source-neutral Dev suite.

    B3 draft questions must be rewritten into concrete cases and signed by a
    trusted review adapter. This does not run them or approve any answer.
    """
    _need(isinstance(suite, dict), 'question suite missing')
    _need(suite.get('format') == 'daq-dev-questions/v1' and
          suite.get('environment') == 'dev' and suite.get('status') == 'frozen',
          'question suite must be frozen for Dev')
    _need(_text(suite.get('batch_id')) and _day(suite.get('reviewed_at')),
          'question suite identity or review date missing')
    questions = suite.get('questions')
    _need(isinstance(questions, list) and bool(questions), 'questions missing')
    ids = []
    families = set()
    for case in questions:
        _need(isinstance(case, dict), 'question case invalid')
        case_id, family = case.get('id'), case.get('family')
        _need(_text(case_id) and family in REQUIRED_FAMILIES, 'question identity or family invalid')
        ids.append(case_id)
        families.add(family)
        prompt = case.get('question')
        turns = case.get('turns')
        _need(_text(prompt) and prompt != _DRAFT_PROMPT and _strings(turns, nonempty=True)
              and turns[-1] == prompt, 'question prompt is draft or inconsistent')
        _need(_text(case.get('role')) and case['role'] in {'internal_fae', 'tmall_support', 'channel',
                                   'unauthorized'}, 'question role missing')
        _need(_strings(case.get('coverage_ids'), nonempty=True) and
              _text(case.get('expected_boundary')), 'question evidence boundary missing')
        if family == 'multi_turn':
            _need(len(turns) >= 2, 'multi-turn question requires multiple turns')
        if family == 'update_rollback':
            _need(len(turns) == 3, 'update and rollback require three observed turns')
    _need(len(ids) == len(set(ids)), 'duplicate question IDs')
    _need(set(REQUIRED_FAMILIES) <= families, 'required question family missing')
    required = suite.get('required_question_ids')
    _need(_strings(required, nonempty=True) and len(required) == len(set(required))
          and set(required) <= set(ids), 'impact question ID missing')
    _need(suite.get('questions_sha256') == digest(questions), 'question digest mismatch')
    _approved({k: v for k, v in suite.items() if k != 'approval'},
              suite.get('approval'), verify_approval)
    return digest(suite)


def validate_replay_batch(suite, release, replays, *, verify_suite=None,
                          verify_release=None, verify_release_pair=None,
                          verify_capture=None):
    """Validate captured Dev observations against a trusted D3 release identity.

    The caller's verifier must authenticate the actual D3 release manifest and
    active Dev observation. A caller-supplied ID alone is never sufficient.
    """
    suite_sha = validate_frozen_suite(suite, verify_approval=verify_suite)
    _need(isinstance(release, dict) and release.get('environment') == 'dev',
          'Dev release required')
    _need(callable(verify_release), 'trusted Dev release verifier required')
    try:
        trusted = verify_release(deepcopy(release)) is True
    except Exception:
        trusted = False
    _need(trusted, 'Dev release not verified')
    _need(isinstance(release.get('release_id'), str) and _SHA256.fullmatch(release['release_id'])
          and _text(release.get('runtime_release')) and
          isinstance(release.get('upstream_sha'), str) and _SHA1.fullmatch(release['upstream_sha']),
          'Dev release identity incomplete')
    _need(release.get('dev_batch_id') == suite['batch_id'] and
          release.get('questions_sha256') == suite['questions_sha256'],
          'Dev release question batch mismatch')
    _need(callable(verify_capture), 'trusted Dev capture verifier required')
    if any(q['family'] == 'update_rollback' for q in suite['questions']):
        prior = release.get('previous_release')
        _need(isinstance(prior, str) and _SHA256.fullmatch(prior) and
              prior != release['release_id'], 'distinct previous release required')
        _need(callable(verify_release_pair), 'trusted adjacent release verifier required')
        try:
            adjacent = verify_release_pair(deepcopy(release)) is True
        except Exception:
            adjacent = False
        _need(adjacent, 'signed adjacent release pair not verified')
    _need(isinstance(replays, list), 'replay rows missing')
    cases = {q['id']: q for q in suite['questions']}
    replay_ids = [row.get('case_id') for row in replays if isinstance(row, dict)]
    _need(len(replay_ids) == len(replays) and all(_text(i) for i in replay_ids),
          'replay case identity invalid')
    _need(len(replays) == len(cases) and len(set(replay_ids)) == len(replay_ids)
          and set(replay_ids) == set(cases),
          'replay case set incomplete or duplicated')
    for row in replays:
        case = cases[row['case_id']]
        _need(row.get('environment') == 'dev' and row.get('suite_sha256') == suite_sha
              and row.get('knowledge_release') == release['release_id']
              and row.get('runtime_release') == release['runtime_release']
              and row.get('upstream_sha') == release['upstream_sha']
              and row.get('agent_id') == 'ai-daq-fae-agent'
              and row.get('observed_role') == case['role']
              and _text(row.get('capture_ref'))
              and _text(row.get('answer_model')) and _text(row.get('prompt_version'))
              and _timestamp(row.get('captured_at')), 'replay identity mismatch')
        turns = row.get('turns')
        _need(isinstance(turns, list) and len(turns) == len(case['turns']),
              'replay turn count mismatch')
        expected_releases = ([release['previous_release'], release['release_id'],
                              release['previous_release']]
                             if case['family'] == 'update_rollback' else
                             [release['release_id']] * len(turns))
        for prompt, version, turn in zip(case['turns'], expected_releases, turns):
            _need(isinstance(turn, dict) and turn.get('question') == prompt and
                  isinstance(turn.get('answer'), str), 'replay question or answer missing')
            _need(turn.get('agent_id') == 'ai-daq-fae-agent' and
                  turn.get('observed_role') == case['role'] and
                  turn.get('knowledge_release') == version and
                  turn.get('runtime_release') == release['runtime_release'],
                  'observed turn identity, role or release mismatch')
            _need(not contains_source_path({'body': turn['answer']}),
                  'answer prose contains a local source path')
            sources = turn.get('sources')
            _need(isinstance(sources, list) and all(isinstance(src, dict) and
                  _text(src.get('source_id')) and _text(src.get('locator')) for src in sources),
                  'structured replay sources missing')
            _need(_strings(turn.get('planned_capabilities')) and
                  _strings(turn.get('actual_capabilities')), 'replay capabilities missing')
            coverage = turn.get('coverage')
            _need(isinstance(coverage, list) and bool(coverage) and all(
                  isinstance(cell, dict) and _text(cell.get('requirement_id')) and
                  cell.get('status') in {'satisfied', 'missing', 'conflict', 'unknown'}
                  for cell in coverage), 'replay coverage missing')
            _need(_text(turn.get('fallback')) and turn.get('outcome') in OUTCOMES and
                  _text(turn.get('trace_id')) and type(turn.get('latency_ms')) is int and
                  turn['latency_ms'] >= 0, 'replay fallback, outcome, trace or latency missing')
            statuses = turn.get('provider_http_statuses')
            _need(isinstance(statuses, list) and all(type(s) is int and 100 <= s <= 599
                  for s in statuses), 'provider status observation missing')
            terminal_status = turn.get('provider_terminal_status_code')
            _need(terminal_status is None or
                  (type(terminal_status) is int and terminal_status in statuses),
                  'provider terminal status inconsistent with observation')
            if terminal_status is not None:
                expected_outcome = (
                    'provider_configuration_error' if terminal_status in {400, 401, 403, 404, 422}
                    else 'provider_rate_limited' if terminal_status == 429
                    else 'provider_unavailable' if terminal_status >= 500
                    else 'provider_error'
                )
                _need(turn['outcome'] == expected_outcome and turn['fallback'] != 'none',
                      'provider terminal error was relabelled')
        if case['family'] in {'provider_http_400', 'provider_http_503'}:
            required_status = int(case['family'][-3:])
            _need(any(required_status in turn['provider_http_statuses'] and
                      turn['provider_terminal_status_code'] == required_status
                      for turn in turns), 'provider terminal failure family not observed')
        transitions = row.get('release_transitions')
        _need(isinstance(transitions, list), 'release transitions missing')
        if case['family'] == 'update_rollback':
            _need(len({turn['trace_id'] for turn in turns}) == 3,
                  'update/rollback turns need distinct traces')
            expected = [('activate', expected_releases[0], expected_releases[1]),
                        ('rollback', expected_releases[1], expected_releases[2])]
            _need(len(transitions) == 2, 'activation and rollback observations required')
            for index, (action, before, after) in enumerate(expected):
                transition = transitions[index]
                _need(isinstance(transition, dict) and
                      transition.get('action') == action and
                      transition.get('before') == before and
                      transition.get('after') == after and before != after,
                      'update/rollback release chain mismatch')
                observation = transition.get('observation')
                _need(isinstance(observation, dict), 'transition observation missing')
                for kind in ('health', 'trace'):
                    observed = observation.get(kind)
                    _need(isinstance(observed, dict) and
                          observed.get('agent_id') == 'ai-daq-fae-agent' and
                          observed.get('knowledge_release') == after and
                          observed.get('runtime_release') == release['runtime_release'],
                          'transition health/trace identity mismatch')
                _need(observation['trace'].get('trace_id') == turns[index + 1]['trace_id'],
                      'transition trace does not bind observed answer turn')
        else:
            _need(not transitions, 'unexpected release transition in ordinary case')
        _approved({k: v for k, v in row.items() if k != 'capture_approval'},
                  row.get('capture_approval'), verify_capture)
    return digest(replays)


def prepare_review_packet(suite, release, replays, *, verify_suite=None,
                          verify_release=None, verify_release_pair=None,
                          verify_capture=None):
    """Return private, unsigned answer records for an independent reviewer."""
    replay_sha = validate_replay_batch(suite, release, replays, verify_suite=verify_suite,
                                       verify_release=verify_release,
                                       verify_release_pair=verify_release_pair,
                                       verify_capture=verify_capture)
    cases = {q['id']: q for q in suite['questions']}
    return {'format': 'daq-dev-answer-review/v1', 'environment': 'dev',
            'suite_sha256': digest(suite), 'replays_sha256': replay_sha,
            'knowledge_release': release['release_id'],
            'items': [{'case_id': row['case_id'], 'expected_boundary':
                       cases[row['case_id']]['expected_boundary'],
                       'replay_sha256': digest(row), 'replay': deepcopy(row),
                       'review': None} for row in replays]}


def audit_answer_reviews(suite, release, replays, reviews, *, verify_suite=None,
                         verify_release=None, verify_reviewer=None,
                         verify_release_pair=None, verify_capture=None):
    """Require one externally authenticated reviewer decision per captured case."""
    replay_sha = validate_replay_batch(suite, release, replays, verify_suite=verify_suite,
                                       verify_release=verify_release,
                                       verify_release_pair=verify_release_pair,
                                       verify_capture=verify_capture)
    _need(callable(verify_reviewer), 'independent answer reviewer verifier required')
    _need(isinstance(reviews, list) and len(reviews) == len(replays),
          'independent answer reviews incomplete')
    replay_by_id = {r['case_id']: r for r in replays}
    _need({r.get('case_id') for r in reviews if isinstance(r, dict)} == set(replay_by_id),
          'answer review case set mismatch')
    serious = 0
    failures = 0
    for decision in reviews:
        row = replay_by_id[decision['case_id']]
        _need(decision.get('replay_sha256') == digest(row) and
              _text(decision.get('reviewer')) and _day(decision.get('reviewed_at')) and
              decision['reviewer'] != row['answer_model'], 'answer review identity mismatch')
        serious_codes = decision.get('serious_failures')
        _need(decision.get('verdict') in {'pass', 'fail'} and
              _strings(serious_codes) and set(serious_codes) <= SERIOUS_FAILURES and
              len(serious_codes) == len(set(serious_codes)) and
              _text(decision.get('notes')), 'answer review decision invalid')
        if decision['verdict'] == 'fail':
            _need(decision.get('failure_layer') in FAILURE_LAYERS,
                  'failed answer requires failure layer')
            failures += 1
        else:
            _need(decision.get('failure_layer') is None and not decision['serious_failures'],
                  'passing answer cannot carry a failure')
        serious += len(decision['serious_failures'])
        _approved({k: v for k, v in decision.items() if k != 'approval'},
                  decision.get('approval'), verify_reviewer, reviewer=decision['reviewer'])
    return {'accepted': failures == 0 and serious == 0, 'status':
            'independently_reviewed' if failures == 0 else 'failed_review',
            'environment': 'dev', 'knowledge_release': release['release_id'],
            'suite_sha256': digest(suite), 'replays_sha256': replay_sha,
            'reviewed_cases': len(reviews), 'failed_cases': failures,
            'serious_failure_count': serious}
