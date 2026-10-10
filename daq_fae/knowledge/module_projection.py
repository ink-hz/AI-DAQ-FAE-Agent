"""Offline, read-only module evidence preparation. Never an online release loader."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
import subprocess

from .records import ROLES

_CORE = ('relation', 'source', 'facts', 'view_roles', 'forward_roles')
_REVISION = re.compile(r'^[0-9a-f]{40}$')
_HASH = re.compile(r'^[0-9a-f]{64}$')


def projection_fingerprint(bundle: dict) -> str:
    """Bind separate review decisions to the entire content and access envelope."""
    raw = json.dumps({key: bundle.get(key) for key in _CORE}, sort_keys=True,
                     ensure_ascii=False, separators=(',', ':')).encode()
    return sha256(raw).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def read_git_source(repository, *, repository_id: str, ref: str, commit: str,
                    path: str) -> tuple[bytes, dict]:
    """Read one blob at an exact tag revision without using working-tree contents.

    A tag is an anchor, not proof of remote protection or publication approval.
    The source maintainer must attest durability and applicability separately.
    """
    if not _text(repository_id) or not ref.startswith('refs/tags/') or not _REVISION.fullmatch(commit):
        raise ValueError('exact tagged source revision required')
    if not path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
        raise ValueError('repository-relative source path required')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repository), *args], stderr=subprocess.PIPE)
    resolved = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
    if resolved != commit:
        raise ValueError('source revision drift')
    raw = git('show', commit + ':' + path)
    return raw, {'repository_id': repository_id, 'ref': ref, 'commit': commit,
                 'path': path, 'sha256': sha256(raw).hexdigest()}


def project_module_facts(bundle: dict, *, role: str | None,
                         requested_scope: str = 'module') -> dict:
    """Return only reviewed module-scoped preparation data to an entitled reviewer.

    Review objects are trusted offline adjudication records, not authentication
    tokens. This function never verifies reviewer identity or publishes a release.
    """
    denied = {'status': 'pending', 'facts': [], 'device_claims': [], 'audit': {},
              'online_eligible': False, 'view_roles': [], 'forward_roles': []}
    if requested_scope != 'module':
        return {**denied, 'status': 'whole_device_evidence_required'}
    roles = bundle.get('view_roles', [])
    forward = bundle.get('forward_roles', [])
    if not isinstance(roles, list) or not isinstance(forward, list) or any(
        not isinstance(r, str) or r not in ROLES for r in roles + forward
    ) or role not in ROLES or role not in roles or not set(forward) <= set(roles):
        return {**denied, 'status': 'not_authorized'}
    digest = projection_fingerprint(bundle)
    for kind in ('relation', 'source', 'republication', 'permission'):
        review = bundle.get(kind + '_review')
        if not isinstance(review, dict) or review.get('decision') != 'approved' or not _text(
            review.get('reviewer')
        ) or review.get('sha256') != digest:
            return denied
        try:
            date.fromisoformat(review.get('reviewed_at', ''))
        except (ValueError, TypeError):
            return denied
    relation, source, facts = (bundle.get(key) for key in ('relation', 'source', 'facts'))
    if not isinstance(relation, dict) or not isinstance(source, dict) or not isinstance(facts, list) or not facts:
        return denied
    if relation.get('status') != 'verified' or not relation.get('source_refs') or any(
        not _text(relation.get(key)) for key in
        ('id', 'device_id', 'device_revision', 'module_id', 'module_revision')
    ) or relation.get('module_revision_unconfirmed') or relation.get('fact_projection_allowed') is False:
        return denied
    if any(not _text(source.get(key)) for key in ('repository_id', 'ref', 'commit', 'path', 'sha256')) or not (
        source['ref'].startswith('refs/tags/') and _REVISION.fullmatch(source['commit'])
        and _HASH.fullmatch(source['sha256'])
    ):
        return denied
    for fact in facts:
        if not isinstance(fact, dict) or fact.get('status') != 'verified' or fact.get('scope') != 'module' or any(
            fact.get(key) != relation[key] for key in ('module_id', 'module_revision')
        ) or any(not _text(fact.get(key)) for key in ('id', 'field', 'unit')) or (
            fact.get('value') is None or not isinstance(fact.get('conditions'), dict)
            or not fact.get('source_refs')
        ):
            return denied
    return deepcopy({
        'status': 'reviewed_module_projection', 'facts': facts, 'device_claims': [],
        'online_eligible': False, 'view_roles': [role],
        'forward_roles': [role] if role in forward else [],
        'audit': {'projection_sha256': digest, 'source': source, 'relation': relation,
                  'reviews': {kind: bundle[kind + '_review'] for kind in
                              ('relation', 'source', 'republication', 'permission')}},
    })
