"""Synthetic contracts: candidate consistency never grants answer permission."""
from copy import deepcopy
import hashlib
import pytest
from daq_fae.knowledge import section_consistency as audit


def fixture():
    ref = {'path': 'synthetic.md', 'sha256': 'a'*64,
           'locator': {'kind': 'lines', 'start': 1, 'end': 3}}
    row = {'id': 'claim:demo', 'kind': 'claim', 'status': 'candidate',
           'scope': {'product': 'demo', 'variant': 'small'}, 'source_refs': [ref],
           'data': {'entity_id': 'entity:demo', 'field': 'rate', 'value': 10,
                    'unit': 'Hz', 'comparator': '<', 'conditions': {'mode': 'test'}}}
    projection = deepcopy(row)
    body = audit.render_record(projection)
    section = {'section_id': 'section:demo', 'body_sha256': hashlib.sha256(body.encode()).hexdigest(),
               'review_status': 'candidate', 'fact_review': None, 'permission_review': None,
               'view_roles': [], 'forward_roles': [], 'source_refs': [ref], 'scope': row['scope'],
               'dependency_claim_ids': [row['id']], 'record_assertions': [projection]}
    snapshot = {'sources': [{'path': ref['path'], 'sha256': ref['sha256']}],
                'chunks': [{'source_path': ref['path'], 'source_sha256': ref['sha256'], 'locator': ref['locator']}]}
    return section, body, row, snapshot


def run(section, body, rows, snapshot, history=None):
    return audit.audit_sections([section], {section['section_id']: body}, rows, snapshot,
                                historical_records=history)


def test_consistent_candidate_remains_unanswerable_and_does_not_mutate():
    s,b,r,snap = fixture(); before = deepcopy((s,r,snap))
    result = run(s,b,[r],snap,[r])
    assert result['consistent'] and not result['online_eligible']
    assert result['coverage']['assertions'] == 1
    assert result['dependencies'][0]['source_refs'] == r['source_refs']
    assert result['dependencies'][0]['answerable'] is False
    assert (s,r,snap) == before


@pytest.mark.parametrize('field,value', [('value', 11), ('unit', 'kHz'), ('comparator', '<='),
                                         ('conditions', {}), ('entity_id', 'entity:other')])
def test_any_data_difference_fails_even_when_body_hash_is_updated(field, value):
    s,b,r,snap = fixture(); s['record_assertions'][0]['data'][field] = value
    b = audit.render_record(s['record_assertions'][0]);s['body_sha256']=hashlib.sha256(b.encode()).hexdigest()
    result = run(s,b,[r],snap)
    assert not result['consistent']
    assert any(f['code'] == 'record_data_mismatch' for f in result['findings'])


@pytest.mark.parametrize('field,value', [('scope', {'product':'demo'}), ('source_refs', []), ('status','verified')])
def test_variant_source_status_drift_fails(field,value):
    s,b,r,snap=fixture();s['record_assertions'][0][field]=value
    b=audit.render_record(s['record_assertions'][0]);s['body_sha256']=hashlib.sha256(b.encode()).hexdigest()
    assert not run(s,b,[r],snap)['consistent']


def test_correct_projection_cannot_hide_body_tampering():
    s,b,r,snap=fixture();b=b.replace('10','99');s['body_sha256']=hashlib.sha256(b.encode()).hexdigest()
    assert any(f['code']=='rendered_assertion_missing' for f in run(s,b,[r],snap)['findings'])


def test_missing_dependency_and_uncovered_body_are_explicit_gaps():
    s,b,r,snap=fixture();s['dependency_claim_ids'].append('claim:missing');b+='\nUnmapped hard parameter: 123 V'
    s['body_sha256']=hashlib.sha256(b.encode()).hexdigest()
    result=run(s,b,[r],snap)
    assert not result['consistent']
    assert result['coverage']['uncovered_body_sections']==1
    assert any(f['code']=='dependency_missing' for f in result['findings'])


def test_history_is_exact_not_only_ids_or_status():
    s,b,r,snap=fixture();history=deepcopy([r]);r['data']['value']=11
    assert any(f['code']=='historical_record_changed' for f in run(s,b,[r],snap,history)['findings'])


def test_conflict_keeps_both_sources_and_never_answers():
    s,b,r,snap=fixture();r['status']='conflict';r['data']['candidates']=[
        {'value':10,'source_ref':deepcopy(r['source_refs'][0])},
        {'value':20,'source_ref':deepcopy(r['source_refs'][0])}]
    s['record_assertions']=[deepcopy(r)];b=audit.render_record(r);s['body_sha256']=hashlib.sha256(b.encode()).hexdigest()
    result=run(s,b,[r],snap)
    assert result['consistent'] and not result['dependencies'][0]['answerable']
    assert result['dependencies'][0]['conflict_candidates']==r['data']['candidates']
    s['record_assertions'][0]['data']['candidates'].pop()
    assert not run(s,b,[r],snap)['consistent']


def test_duplicate_sections_and_assertions_fail():
    s,b,r,snap=fixture()
    assert not audit.audit_sections([s,s],{s['section_id']:b},[r],snap)['consistent']
    s['record_assertions']*=2
    assert not run(s,b,[r],snap)['consistent']


def test_bad_source_hash_or_locator_fails():
    s,b,r,snap=fixture();snap['chunks']=[]
    assert not run(s,b,[r],snap)['consistent']


def test_context_dependencies_do_not_claim_fact_consistency():
    s,b,r,snap=fixture();s['record_assertions']=[]
    result=run(s,b,[r],snap)
    assert not result['consistent'] and result['coverage']['assertions']==0


def test_section_scope_cannot_drop_variant_even_without_required_selector_list():
    s,b,r,snap=fixture();s['scope']={'product':'demo'}
    assert any(f['code']=='section_scope_mismatch' for f in run(s,b,[r],snap)['findings'])


def test_conflict_candidate_source_cannot_be_omitted_from_section():
    s,b,r,snap=fixture();other=deepcopy(r['source_refs'][0]);other['locator']['end']=4
    snap['chunks'].append({'source_path':other['path'],'source_sha256':other['sha256'],'locator':other['locator']})
    r['status']='conflict';r['data']['candidates']=[{'value':10,'source_ref':r['source_refs'][0]}, {'value':20,'source_ref':other}]
    s['record_assertions']=[deepcopy(r)];b=audit.render_record(r);s['body_sha256']=hashlib.sha256(b.encode()).hexdigest()
    assert any(f['code']=='assertion_source_not_in_section' for f in run(s,b,[r],snap)['findings'])


def test_extra_section_source_is_checked_against_snapshot():
    s,b,r,snap=fixture();extra=deepcopy(r['source_refs'][0]);extra['sha256']='b'*64;s['source_refs'].append(extra)
    assert any(f['code']=='section_source_hash_mismatch' for f in run(s,b,[r],snap)['findings'])
