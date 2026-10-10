"""Separately approved disclosure of conflict existence, never candidate values."""
from copy import deepcopy
import hashlib
import json

from .records import ROLES, _review_valid, record_fingerprint


def conflict_notice_fingerprint(row):
    review = row.get('conflict_notice_review') or {}
    payload = [record_fingerprint(row), review.get('statement'), review.get('view_roles')]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def conflict_notice(row, role):
    review = row.get('conflict_notice_review')
    if row.get('status') != 'conflict' or row.get('kind') != 'claim' or not _review_valid(review):
        return None
    roles = review.get('view_roles')
    if not isinstance(roles, list) or not roles or any(not isinstance(r, str) or r not in ROLES for r in roles) or len(set(roles)) != len(roles):
        return None
    if role not in roles or review.get('statement') != 'conflict_exists' or review.get('sha256') != conflict_notice_fingerprint(row):
        return None
    return {'id': row['id'], 'kind': 'claim', 'status': 'conflict',
            'scope': deepcopy(row['scope']), 'data': {key: deepcopy(row['data'][key])
                for key in ('entity_id', 'field', 'conditions')}}
