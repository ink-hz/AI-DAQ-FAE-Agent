"""Synthetic signatures only; no real reviewer or source content."""
from copy import deepcopy
from datetime import date
import hashlib
import hmac

import pytest

from daq_fae.knowledge import adjudication


POLICY = {axis: {'synthetic-reviewer'} for axis in ('fact', 'access', 'page')}


def item(kind='claim', deferred=False):
    return adjudication.prepare_item(
        item_id='synthetic:one', kind=kind,
        record={'id': 'synthetic:one', 'value': 42, 'status': 'candidate'},
        source_refs=[{'sha256': 'a' * 64, 'locator': {'page': 1}}],
        source_bodies=['synthetic source'], body='synthetic body',
        conditions={'revision': 'R1'}, view_roles=['internal_fae'], forward_roles=[],
        deferred=deferred, blockers=[], page_evidence={'snapshot_sha256': 'b' * 64},
    )


def signed(row, axis, decision='approve'):
    result = {'axis': axis, 'item_id': row['item_id'],
              'item_sha256': row['item_sha256'], 'reviewer': 'synthetic-reviewer',
              'reviewed_at': date.today().isoformat(), 'decision': decision,
              'summary': 'Synthetic review only', 'disposition': 'resolved'}
    result['signature'] = hmac.new(b'synthetic-test-key', adjudication.decision_bytes(result), hashlib.sha256).hexdigest()
    return result


def verify(reviewer, payload, signature):
    return reviewer == 'synthetic-reviewer' and hmac.compare_digest(
        signature, hmac.new(b'synthetic-test-key', payload, hashlib.sha256).hexdigest())


def check(row, decisions):
    return adjudication.validate_decisions(row, decisions, reviewers=POLICY, verify_signature=verify)


def test_missing_contract_red():
    assert callable(getattr(adjudication, 'prepare_item', None))


def test_independent_fact_and_access_are_required():
    row = item()
    assert not check(row, [])['ready_for_import']
    assert not check(row, [signed(row, 'fact')])['ready_for_import']
    assert check(row, [signed(row, 'fact'), signed(row, 'access')])['ready_for_import']
    assert row['record']['status'] == 'candidate'


@pytest.mark.parametrize('field,value', [('record', {'value': 43}), ('body', 'changed'),
    ('conditions', {'revision': 'R2'}), ('source_refs', []),
    ('source_bodies', ['changed']), ('view_roles', ['channel']), ('forward_roles', ['internal_fae']),
    ('page_evidence', {'snapshot_sha256': 'c' * 64}), ('blockers', ['unresolved'])])
def test_any_scope_edit_supersedes_prior_decisions(field, value):
    row = item(); decisions = [signed(row, 'fact'), signed(row, 'access')]
    row[field] = value
    assert not check(row, decisions)['ready_for_import']


@pytest.mark.parametrize('mutation', ['unsigned', 'forged', 'reviewer', 'future', 'decision', 'duplicate'])
def test_bad_decisions_fail_closed(mutation):
    row = item(); decisions = [signed(row, 'fact'), signed(row, 'access')]
    if mutation == 'unsigned': decisions[0]['signature'] = ''
    if mutation == 'forged': decisions[0]['signature'] = 'fake'
    if mutation == 'reviewer': decisions[0]['reviewer'] = 'unassigned'
    if mutation == 'future': decisions[0]['reviewed_at'] = '2999-01-01'
    if mutation == 'decision': decisions[0]['decision'] = 'reject'
    if mutation == 'duplicate': decisions.append(deepcopy(decisions[0]))
    assert not check(row, decisions)['ready_for_import']


def test_untrusted_verifier_cannot_be_omitted():
    row = item()
    with pytest.raises(TypeError):
        adjudication.validate_decisions(row, [], reviewers=POLICY)


def test_unknown_roles_rejected():
    with pytest.raises(ValueError):
        row = item(); row['view_roles'] = ['public']; adjudication.refresh_item(row)


def test_link_needs_separate_page_signature():
    row = item('link'); decisions = [signed(row, 'fact'), signed(row, 'access')]
    assert not check(row, decisions)['ready_for_import']
    assert check(row, decisions + [signed(row, 'page')])['ready_for_import']


def test_deferred_disposition_is_a_signed_choice_and_does_not_clear_blockers():
    row = item(deferred=True)
    decisions = [signed(row, 'fact', 'hold'), signed(row, 'access')]
    decisions[0]['disposition'] = 'awaiting_source'
    decisions[0]['signature'] = hmac.new(b'synthetic-test-key', adjudication.decision_bytes(decisions[0]), hashlib.sha256).hexdigest()
    result = check(row, decisions)
    assert result['disposition'] == 'awaiting_source'
    assert not result['ready_for_import']
    row['blockers'] = ['unresolved_source_conflict']; row = adjudication.refresh_item(row)
    assert not check(row, [signed(row, 'fact'), signed(row, 'access')])['ready_for_import']


def test_refresh_links_supersession_and_invalidates_old_signature():
    old = item(); new = deepcopy(old); new['body'] = 'corrected'
    new = adjudication.refresh_item(new)
    assert new['supersedes'] == old['item_sha256']
    assert not check(new, [signed(old, 'fact'), signed(old, 'access')])['ready_for_import']


def test_csv_roundtrip_and_duplicate_rows_rejected(tmp_path):
    row = item(); path = tmp_path / 'review.csv'
    adjudication.write_review_csv([row], path)
    assert path.stat().st_mode & 0o777 == 0o600
    imported = adjudication.read_review_csv(path, [row], reviewers=POLICY, verify_signature=verify)
    assert imported[0]['ready_for_import'] is False
    with pytest.raises(ValueError): adjudication.write_review_csv([row, row], path)


def test_signed_csv_roundtrip_with_large_private_source(tmp_path):
    import csv
    row = item(); row['source_bodies'] = ['source' * 30000]
    row = adjudication.refresh_item(row)
    path = tmp_path / 'review.csv'
    adjudication.write_review_csv([row], path)
    csv.field_size_limit(1_000_000)
    with path.open(newline='') as f: rows = list(csv.DictReader(f))
    for axis in ('fact', 'access'):
        for key, value in signed(row, axis).items():
            if f'{axis}_{key}' in rows[0]: rows[0][f'{axis}_{key}'] = value
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=adjudication.CSV_FIELDS)
        writer.writeheader(); writer.writerows(rows)
    csv.field_size_limit(131072)
    result = adjudication.read_review_csv(path, [row], reviewers=POLICY, verify_signature=verify)
    assert result[0]['ready_for_import']


def test_axis_authorization_is_independent():
    row = item()
    result = adjudication.validate_decisions(row, [signed(row, 'fact'), signed(row, 'access')],
        reviewers={'fact': {'synthetic-reviewer'}, 'access': {'other-reviewer'}}, verify_signature=verify)
    assert not result['ready_for_import']


@pytest.mark.parametrize('edit', ['duplicate', 'missing', 'body', 'signature_swap'])
def test_csv_rejects_partial_or_altered_input(tmp_path, edit):
    import csv
    row = item(); path = tmp_path / 'review.csv'; adjudication.write_review_csv([row], path)
    with path.open(newline='') as f: rows = list(csv.DictReader(f))
    if edit == 'duplicate': rows *= 2
    elif edit == 'missing': rows = []
    elif edit == 'body': rows[0]['item_json'] = '{}'
    else:
        for axis in ('fact', 'access'):
            decision = signed(row, 'access' if axis == 'fact' else 'fact')
            for key, value in decision.items():
                if f'{axis}_{key}' in rows[0]: rows[0][f'{axis}_{key}'] = value
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=adjudication.CSV_FIELDS)
        writer.writeheader(); writer.writerows(rows)
    if edit != 'signature_swap':
        with pytest.raises(ValueError): adjudication.read_review_csv(path, [row], reviewers=POLICY, verify_signature=verify)
    else:
        assert not adjudication.read_review_csv(path, [row], reviewers=POLICY, verify_signature=verify)[0]['ready_for_import']
