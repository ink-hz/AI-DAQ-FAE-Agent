"""Synthetic boundaries for unpublished selection and diagnostic evidence."""
import copy
import hashlib
import pytest
from daq_fae.knowledge import candidate_sections


def draft():
    ref = {'path': 'synthetic.md', 'sha256': 'a' * 64,
           'locator': {'kind': 'lines', 'start': 1, 'end': 2}}
    item = {'item_id': 'check', 'text': 'Check connection.', 'entity_ids': ['entity:demo'],
            'topology_id': 'topology:demo', 'conditions': {'platform': 'demo'},
            'source_refs': [ref], 'evidence_level': 'documented_procedure',
            'conclusion_scope': 'topology', 'actionable': True}
    return {'section_id': 'guidance:demo:troubleshoot', 'body_sha256': hashlib.sha256(b'Check connection.').hexdigest(),
            'review_status': 'candidate', 'fact_review': None, 'permission_review': None,
            'view_roles': [], 'forward_roles': [], 'source_refs': [ref],
            'capability': 'troubleshoot', 'entity_ids': ['entity:demo'], 'topology_id': 'topology:demo',
            'evidence_items': [item], 'gap_ids': ['gap:demo']}


def check(s):
    validator = getattr(candidate_sections, 'validate_guidance_section', None)
    assert callable(validator), 'guidance validator is missing'
    validator(s, 'Check connection.')


def test_scoped_documented_check_is_valid():
    check(draft())


@pytest.mark.parametrize('field,value', [('entity_ids', []), ('entity_ids', ['entity:other']),
    ('topology_id', 'topology:other'), ('conditions', {}), ('source_refs', []),
    ('evidence_level', 'verified'), ('text', 'invented diagnosis')])
def test_actions_require_scope_conditions_and_original_locator(field, value):
    s = draft(); s['evidence_items'][0][field] = value
    with pytest.raises(ValueError): check(s)


def test_product_parameters_cannot_prove_a_combination():
    s = draft(); s['evidence_items'][0]['evidence_level'] = 'product_specification'
    with pytest.raises(ValueError, match='specification'): check(s)


@pytest.mark.parametrize('level', ['combination_validation', 'field_experience'])
def test_documented_steps_cannot_be_promoted_to_tests_or_experience(level):
    s = draft(); s['evidence_items'][0]['evidence_level'] = level
    with pytest.raises(ValueError, match='evidence record'): check(s)


def test_gap_only_section_requires_explicit_gap():
    s = draft(); s['evidence_items'] = []
    check(s)
    s['gap_ids'] = []
    with pytest.raises(ValueError): check(s)


def test_guidance_keeps_candidate_approval_gate():
    s = draft(); s['view_roles'] = ['internal_fae']
    with pytest.raises(ValueError): check(s)


def test_action_locator_must_belong_to_section():
    s = draft(); r = copy.deepcopy(s['source_refs'][0]); r['locator']['end'] = 3
    s['evidence_items'][0]['source_refs'] = [r]
    with pytest.raises(ValueError): check(s)
