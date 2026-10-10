"""Offline review envelopes. This module neither authenticates users nor publishes knowledge."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import csv
from datetime import date
import hashlib
import json
import os
from pathlib import Path

ROLES = {'internal_fae', 'tmall_support', 'channel'}
AXES = ('fact', 'access', 'page')
CORE = ('item_id', 'kind', 'record', 'source_refs', 'source_bodies', 'body',
        'conditions', 'view_roles', 'forward_roles', 'deferred', 'blockers', 'page_evidence')
DECISION_FIELDS = ('axis', 'item_id', 'item_sha256', 'reviewer', 'reviewed_at',
                   'decision', 'summary', 'disposition')
CSV_FIELDS = ['item_id', 'item_sha256', 'item_json'] + [
    f'{axis}_{key}' for axis in AXES
    for key in ('decision', 'reviewer', 'reviewed_at', 'summary', 'disposition', 'signature')]


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def fingerprints(row):
    return {'source_sha256': digest([row['source_refs'], row['source_bodies']]),
            'record_sha256': digest(row['record']), 'body_sha256': digest(row['body']),
            'conditions_sha256': digest(row['conditions']),
            'roles_sha256': digest([row['view_roles'], row['forward_roles']]),
            'item_sha256': digest({k: row[k] for k in CORE})}


def refresh_item(row):
    """Create a new proposal identity; prior signatures must never be carried over."""
    result = {k: deepcopy(row[k]) for k in CORE}
    if not isinstance(result['item_id'], str) or not result['item_id'].strip():
        raise ValueError('item identity required')
    for field in ('view_roles', 'forward_roles'):
        roles = result[field]
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles) or \
                len(set(roles)) != len(roles) or not set(roles) <= ROLES:
            raise ValueError('invalid proposed roles')
    if not set(result['forward_roles']) <= set(result['view_roles']):
        raise ValueError('forward roles must also be able to view')
    result.update(fingerprints(result))
    result['supersedes'] = row.get('supersedes')
    if row.get('item_sha256') and row['item_sha256'] != result['item_sha256']:
        result['supersedes'] = row['item_sha256']
    return result


def prepare_item(**kwargs):
    return refresh_item(kwargs)


def decision_bytes(decision):
    """Domain-separated canonical signing payload, excluding signature bytes."""
    return canonical({'contract': 'daq-item-adjudication/v1',
                      **{k: decision[k] for k in DECISION_FIELDS}})


def validate_decisions(row, decisions, *, reviewers, verify_signature):
    """Verify explicit axis decisions using a caller-owned, trusted identity verifier.

    ready_for_import only means the review envelope is complete. It does NOT mean
    a record is semantically valid, source-authentic, answerable or publishable.
    """
    errors = []
    valid = {}
    try:
        fresh = refresh_item(row)
        if any(row.get(k) != v for k, v in fingerprints(fresh).items()):
            errors.append('item_drift')
    except (KeyError, TypeError, ValueError):
        errors.append('item_invalid')
    seen = set()
    for decision in decisions:
        try:
            axis = decision['axis']
            if axis not in AXES or axis in seen:
                errors.append('duplicate_or_unknown_axis'); continue
            seen.add(axis)
            reviewed = date.fromisoformat(decision['reviewed_at'])
            if (decision['item_id'] != row['item_id'] or
                    decision['item_sha256'] != row['item_sha256'] or
                    decision['reviewer'] not in reviewers.get(axis, set()) or
                    reviewed > date.today() or not decision['summary'].strip() or
                    decision['decision'] not in {'approve', 'reject', 'hold'} or
                    decision['disposition'] not in {'resolved', 'continuing_conflict', 'awaiting_source'} or
                    not decision['signature'] or
                    not verify_signature(decision['reviewer'], decision_bytes(decision), decision['signature'])):
                errors.append(f'{axis}_invalid'); continue
            valid[axis] = deepcopy(decision)
        except (KeyError, TypeError, ValueError, AttributeError):
            errors.append('decision_invalid')
    required = {'fact', 'access'} | ({'page'} if row.get('kind') == 'link' else set())
    for axis in sorted(required):
        if axis not in valid:
            errors.append(f'{axis}_unsigned')
        elif valid[axis]['decision'] != 'approve' or valid[axis]['disposition'] != 'resolved':
            errors.append(f'{axis}_unresolved')
    if row.get('blockers'):
        errors.append('proposal_blockers')
    return {'item_id': row.get('item_id'), 'item_sha256': row.get('item_sha256'),
            'ready_for_import': not errors, 'online_eligible': False,
            'disposition': valid.get('fact', {}).get('disposition', 'pending_review'),
            'reviews': valid, 'findings': sorted(set(errors))}


def _unique(items):
    by_id = {row['item_id']: row for row in items}
    if len(by_id) != len(items):
        raise ValueError('duplicate item IDs')
    return by_id


def write_review_csv(items, path):
    """Write private proposal rows with every decision/signature cell empty."""
    _unique(items)
    # Refuse overwrite and symlinks: do not destroy a human's existing decisions.
    fd = os.open(Path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in items:
            writer.writerow({'item_id': row['item_id'], 'item_sha256': row['item_sha256'],
                             'item_json': canonical(row).decode('utf-8')})


@contextmanager
def _large_csv_fields():
    previous = csv.field_size_limit(64 * 1024 * 1024)
    try:
        yield
    finally:
        csv.field_size_limit(previous)


def read_review_csv(path, items, *, reviewers, verify_signature):
    """Import against trusted current proposals, rejecting edits, omissions or duplicates."""
    expected = _unique(items)
    results, seen = [], set()
    with _large_csv_fields(), Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != CSV_FIELDS:
            raise ValueError('review CSV schema mismatch')
        for csv_row in reader:
            key = csv_row['item_id']
            if key not in expected or key in seen:
                raise ValueError('unknown or duplicate review item')
            seen.add(key)
            row = expected[key]
            if csv_row['item_sha256'] != row['item_sha256'] or \
                    json.loads(csv_row['item_json']) != row:
                raise ValueError('proposal edited: regenerate and supersede before review')
            decisions = []
            for axis in AXES:
                fields = {k: csv_row[f'{axis}_{k}'] for k in
                          ('decision', 'reviewer', 'reviewed_at', 'summary', 'disposition', 'signature')}
                if any(fields.values()):
                    decisions.append({**fields, 'axis': axis, 'item_id': key,
                                      'item_sha256': row['item_sha256']})
            results.append(validate_decisions(row, decisions, reviewers=reviewers,
                                              verify_signature=verify_signature))
    if seen != set(expected):
        raise ValueError('review CSV omitted items')
    return results
