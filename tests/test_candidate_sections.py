"""Synthetic source-neutral contracts for unpublished chapter drafts."""
import copy
import hashlib
import pytest
from daq_fae.knowledge import candidate_sections


def draft():
    ref = {'path': 'synthetic.md', 'sha256': 'a' * 64,
           'locator': {'kind': 'lines', 'start': 1, 'end': 3}}
    return {'section_id': 'system:demo:setup', 'body_sha256': hashlib.sha256(b'Draft').hexdigest(),
            'review_status': 'candidate', 'fact_review': None, 'permission_review': None,
            'view_roles': [], 'forward_roles': [], 'source_refs': [ref],
            'scope': {'product': 'unspecified', 'variant_unconfirmed': True},
            'topology_id': 'topology:demo', 'gaps': ['version'],
            'steps': [{'step_id': 'connect', 'topology_id': 'topology:demo', 'source_refs': [ref]}]}


def check(s):
    candidate_sections.validate_section(s, 'Draft')


def test_valid_candidate_is_accepted():
    check(draft())


@pytest.mark.parametrize('field,value', [('review_status', 'verified'), ('fact_review', {}),
                                        ('permission_review', {}), ('view_roles', ['internal_fae']),
                                        ('forward_roles', ['channel'])])
def test_candidate_cannot_grant_approval_or_roles(field, value):
    s = draft(); s[field] = value
    with pytest.raises(ValueError): check(s)


def test_step_cannot_borrow_another_topology():
    s = draft(); s['steps'][0]['topology_id'] = 'topology:standalone'
    with pytest.raises(ValueError): check(s)


def test_every_step_requires_exact_source_from_section():
    s = draft(); s['steps'][0]['source_refs'] = []
    with pytest.raises(ValueError): check(s)
    s = draft(); s['steps'][0]['source_refs'] = [copy.deepcopy(s['source_refs'][0])]
    s['steps'][0]['source_refs'][0]['locator']['start'] = 2
    with pytest.raises(ValueError): check(s)


def test_source_and_body_tamper_rejected():
    s = draft(); s['body_sha256'] = '0' * 64
    with pytest.raises(ValueError): check(s)
    s = draft(); s['source_refs'][0]['locator']['start'] = 0
    with pytest.raises(ValueError): check(s)


def test_unresolved_variant_cannot_select_resolution():
    s = draft(); s['scope']['resolution_variant'] = 'synthetic-resolution'
    with pytest.raises(ValueError): check(s)


@pytest.mark.parametrize('identity', [42, [], {}, ' ', 'BAD ID'])
def test_step_identity_must_be_a_stable_string(identity):
    s = draft(); s['steps'][0]['step_id'] = identity
    with pytest.raises(ValueError): check(s)


@pytest.mark.parametrize('field,values', [('dependency_claim_ids', [1]), ('dependency_claim_ids', ['claim:a','claim:a']),
                                         ('gap_ids', ['gap:a','gap:a']), ('entity_ids', ['entity:a',{}]),
                                         ('dependency_section_ids', [' '])])
def test_dependency_and_gap_lists_have_unique_string_ids(field, values):
    s = draft(); s[field] = values
    with pytest.raises(ValueError): check(s)
