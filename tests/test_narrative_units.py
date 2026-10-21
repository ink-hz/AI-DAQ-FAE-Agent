"""Synthetic paragraph evidence; never records a real source approval."""
from copy import deepcopy
import hashlib
import json

import pytest

from daq_fae.knowledge.records import access_fingerprint, record_fingerprint, validate_records
from daq_fae.knowledge.section_consistency import audit_sections
from daq_fae.knowledge.section_release import compile_sections, section_fingerprint
from test_knowledge_releases import SNAPSHOT, _entity, _record

CORE = ('id', 'kind', 'status', 'scope', 'source_refs', 'data')


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def unit_digest(unit, body, rows):
    core = {k: v for k, v in unit.items() if k not in {'fact_review', 'permission_review'}}
    deps = {r['id']: {'record': record_fingerprint(r), 'access': access_fingerprint(r)}
            for r in rows if r['id'] in unit['dependency_record_ids']}
    return sha(json.dumps([core, body[unit['start']:unit['end']], deps],
                          ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def sign_unit(unit, body, rows):
    digest = unit_digest(unit, body, rows)
    unit['fact_review'] = {'reviewer': 'independent-fae', 'reviewed_at': '2026-10-21',
                          'unit_sha256': digest, 'decision': 'approved',
                          'evidence_assessments': [{'evidence_id': 'evidence:source',
                            'assessment': 'The exact cited source states this explanation without extra guarantees.'}],
                          'semantic_checks': {'complete': True, 'scope_preserved': True,
                                              'conditions_preserved': True, 'no_added_claims': True}}
    unit['permission_review'] = {'reviewer': 'source-owner', 'reviewed_at': '2026-10-21',
                                'unit_sha256': digest, 'decision': 'approved',
                                'rationale': 'The cited material is approved for these exact roles.'}


def sign_section(section, body, rows):
    section['body_sha256'] = sha(body)
    digest = section_fingerprint(section, body, rows)
    for key in ('fact_review', 'permission_review'):
        section[key] = {'reviewer': 'section-owner', 'reviewed_at': '2026-10-21',
                        'section_sha256': digest}


def fixture():
    snap = deepcopy(SNAPSHOT)
    body = 'The guide describes the device and its documented boundaries.'
    chunk = snap['chunks'][0]
    chunk.update(text=body, text_sha256=sha(body))
    rows, findings = validate_records([_entity(), _record()], snap)
    assert not findings
    ref = deepcopy(rows[0]['source_refs'][0])
    unit = {'unit_id': 'unit:introduction', 'unit_type': 'paragraph', 'mode': 'source_excerpt',
            'knowledge_type': 'explanation', 'start': 0, 'end': len(body), 'body_sha256': sha(body),
            'authored_by': 'synthetic-author', 'scope': {'variant': '1600'},
            'entity_ids': ['entity:ego-1600'], 'topology_id': None, 'conditions': {},
            'view_roles': ['internal_fae'], 'forward_roles': [], 'dependency_record_ids': [],
            'record_assertions': [], 'source_evidence': [{
                'evidence_id': 'evidence:source', 'source_ref': ref,
                'chunk_sha256': sha(body), 'start': 0, 'end': len(body),
                'excerpt': body, 'excerpt_sha256': sha(body)}]}
    sign_unit(unit, body, rows)
    section = {'section_id': 'section:synthetic', 'title': 'Synthetic source explanation',
               'knowledge_type': 'product', 'scope': {'variant': '1600'},
               'source_refs': [ref], 'review_status': 'verified', 'view_roles': ['internal_fae'],
               'forward_roles': [], 'dependency_claim_ids': [], 'record_assertions': [],
               'narrative_units': [unit]}
    sign_section(section, body, rows)
    return section, body, rows, snap


def run(section, body, rows, snapshot):
    candidate = deepcopy(section)
    candidate.update(review_status='candidate', fact_review=None, permission_review=None,
                     view_roles=[], forward_roles=[])
    return audit_sections([candidate], {section['section_id']: body}, rows, snapshot)


def test_exact_source_paragraph_has_full_coverage_without_authorizing_candidate():
    s, b, rows, snap = fixture()
    result = run(s, b, rows, snap)
    assert result['consistent'], result['findings']
    assert not result['online_eligible']
    assert result['section_coverage'][0]['narrative_units'] == 1
    assert compile_sections([s], {s['section_id']: b}, rows, snap)[0]['body'] == b


@pytest.mark.parametrize('mutation', ['missing_unit', 'missing_evidence', 'missing_text',
    'bad_chunk_hash', 'wrong_locator', 'wrong_source_hash', 'wrong_excerpt', 'stale_unit',
    'self_review', 'missing_assessment', 'false_semantics', 'overlap', 'wrong_scope',
    'unknown_mode', 'mixed_roles', 'duplicate_chunk', 'bad_span', 'body_hash', 'stale_permission'])
def test_unsafe_narrative_is_rejected_even_with_fresh_section_review(mutation):
    s, b, rows, snap = fixture()
    u = s['narrative_units'][0]
    if mutation == 'missing_unit': s['narrative_units'] = []
    elif mutation == 'missing_evidence': u['source_evidence'] = []
    elif mutation == 'missing_text': snap['chunks'][0].pop('text')
    elif mutation == 'bad_chunk_hash': snap['chunks'][0]['text_sha256'] = 'b' * 64
    elif mutation == 'wrong_locator': u['source_evidence'][0]['source_ref']['locator']['end'] = 7
    elif mutation == 'wrong_source_hash': u['source_evidence'][0]['source_ref']['sha256'] = 'b' * 64
    elif mutation == 'wrong_excerpt': u['source_evidence'][0]['excerpt'] = 'Different facts'
    elif mutation == 'stale_unit': u['conditions'] = {'mode': 'changed'}
    elif mutation == 'self_review': u['fact_review']['reviewer'] = u['authored_by']
    elif mutation == 'missing_assessment': u['fact_review']['evidence_assessments'] = []
    elif mutation == 'false_semantics': u['fact_review']['semantic_checks']['conditions_preserved'] = False
    elif mutation == 'overlap': s['narrative_units'].append(deepcopy(u))
    elif mutation == 'wrong_scope': u['scope'] = {}
    elif mutation == 'unknown_mode': u['mode'] = 'verified'
    elif mutation == 'mixed_roles': u['view_roles'].append('channel'); sign_unit(u, b, rows)
    elif mutation == 'duplicate_chunk': snap['chunks'].append(deepcopy(snap['chunks'][0]))
    elif mutation == 'bad_span': u['start'] = -1
    elif mutation == 'body_hash': u['body_sha256'] = 'c' * 64
    elif mutation == 'stale_permission': u['permission_review']['unit_sha256'] = 'd' * 64
    sign_section(s, b, rows)
    with pytest.raises(ValueError):
        compile_sections([s], {s['section_id']: b}, rows, snap)


def test_unmapped_heading_is_not_an_automatic_semantic_exemption():
    s, b, rows, snap = fixture()
    b += '\n# Guaranteed compatibility with every topology'
    sign_section(s, b, rows)
    assert any(f['code'] == 'uncovered_body' for f in run(s, b, rows, snap)['findings'])


def test_reviewed_explanation_requires_source_text_and_independent_semantic_assessment():
    s, b, rows, snap = fixture()
    u = s['narrative_units'][0]
    b = 'This chapter explains the device within the guide’s documented boundaries.'
    u.update(mode='reviewed_narrative', end=len(b), body_sha256=sha(b))
    sign_unit(u, b, rows); sign_section(s, b, rows)
    assert run(s, b, rows, snap)['consistent']
    assert compile_sections([s], {s['section_id']: b}, rows, snap)


def test_formatting_change_requires_new_unit_and_section_reviews():
    s, b, rows, snap = fixture()
    u = s['narrative_units'][0]
    b = b.replace('guide', '**guide**')
    u.update(mode='reviewed_narrative', end=len(b), body_sha256=sha(b))
    sign_section(s, b, rows)
    with pytest.raises(ValueError): compile_sections([s], {s['section_id']: b}, rows, snap)


def deterministic_fixture():
    s, _, rows, snap = fixture()
    row = rows[1]
    # Exact canonical values, including scope, comparator, units and conditions.
    b = 'scope: {"variant":"1600"}\nconditions: {}\nentity_id: "entity:ego-1600"\nfield: "resolution"\nunit: "px"\nvalue: "1600x1200"'
    u = s['narrative_units'][0]
    u.update(mode='record_render', knowledge_type='claim', end=len(b), body_sha256=sha(b),
             dependency_record_ids=[row['id']], record_assertions=[{k: deepcopy(row[k]) for k in CORE}],
             conditions={row['id']: deepcopy(row['data']['conditions'])})
    s['dependency_claim_ids'] = [row['id']]
    sign_unit(u, b, rows); sign_section(s, b, rows)
    return s, b, rows, snap


def test_deterministic_claim_preserves_exact_value_units_and_conditions():
    s, b, rows, snap = deterministic_fixture()
    result = run(s, b, rows, snap)
    assert result['consistent'], result['findings']
    assert result['dependencies'][0]['asserted']
    assert compile_sections([s], {s['section_id']: b}, rows, snap)


@pytest.mark.parametrize('field,value', [('value', '1920x1080'), ('unit', 'm'),
    ('conditions', {'mode': 'changed'}), ('comparator', '<='), ('entity_id', 'entity:other')])
def test_fresh_reviews_do_not_allow_changed_typed_values(field, value):
    s, b, rows, snap = deterministic_fixture()
    u = s['narrative_units'][0]
    u['record_assertions'][0]['data'][field] = value
    sign_unit(u, b, rows); sign_section(s, b, rows)
    with pytest.raises(ValueError): compile_sections([s], {s['section_id']: b}, rows, snap)


def test_wrong_body_value_rejected_even_when_all_checksums_and_reviews_are_refreshed():
    s, b, rows, snap = deterministic_fixture()
    b = b.replace('1600x1200', '1920x1080')
    u = s['narrative_units'][0]
    u.update(end=len(b), body_sha256=sha(b))
    sign_unit(u, b, rows); sign_section(s, b, rows)
    with pytest.raises(ValueError): compile_sections([s], {s['section_id']: b}, rows, snap)


def test_explanation_mode_cannot_claim_typed_hard_fact_consistency():
    s, b, rows, snap = deterministic_fixture()
    u = s['narrative_units'][0]
    u.update(mode='reviewed_narrative')
    sign_unit(u, b, rows); sign_section(s, b, rows)
    with pytest.raises(ValueError): compile_sections([s], {s['section_id']: b}, rows, snap)


def test_record_render_cannot_drop_dependency_or_mismatch_conditions():
    for mutation in ('dependency', 'conditions', 'entity'):
        s, b, rows, snap = deterministic_fixture()
        u = s['narrative_units'][0]
        if mutation == 'dependency': s['dependency_claim_ids'] = []
        elif mutation == 'conditions': u['conditions'] = {}
        else: u['entity_ids'] = ['entity:other']
        sign_unit(u, b, rows); sign_section(s, b, rows)
        with pytest.raises(ValueError): compile_sections([s], {s['section_id']: b}, rows, snap)


def test_table_row_render_preserves_exact_claim_and_rejects_added_cell():
    from daq_fae.knowledge import narrative_units
    assert hasattr(narrative_units, 'render_narrative_unit')
    s, _, rows, snap = deterministic_fixture()
    u = s['narrative_units'][0]; u['unit_type'] = 'table_row'
    b = narrative_units.render_narrative_unit(u)
    assert '\n' not in b and b.startswith('|') and b.endswith('|')
    u.update(end=len(b), body_sha256=sha(b)); sign_unit(u,b,rows); sign_section(s,b,rows)
    assert compile_sections([s],{s['section_id']:b},rows,snap)
    b = b[:-1] + ' unreviewed guarantee |'
    u.update(end=len(b),body_sha256=sha(b));sign_unit(u,b,rows);sign_section(s,b,rows)
    with pytest.raises(ValueError):compile_sections([s],{s['section_id']:b},rows,snap)


def test_individual_procedure_step_retains_prerequisites_checks_failures_and_topology():
    from daq_fae.knowledge import narrative_units
    from test_knowledge_releases import _reviewed
    assert hasattr(narrative_units, 'render_narrative_unit')
    s, _, rows, snap = fixture()
    procedure = _reviewed({'id': 'procedure:synthetic', 'kind': 'procedure', 'status':'verified',
        'scope': deepcopy(s['scope']), 'source_refs':deepcopy(s['source_refs']),
        'data': {'entity_ids':['entity:ego-1600'],'topology_id':'topology:synthetic','task':'record',
                 'prerequisites':['powered device'], 'steps':['Connect cable', 'Start recording'],
                 'checks':['Recording indicator'], 'failure_branches':['Check connection']}})
    topology = _reviewed({'id':'topology:synthetic', 'kind':'topology', 'status':'verified',
        'scope':deepcopy(s['scope']), 'source_refs':deepcopy(s['source_refs']),
        'data':{'members':['entity:ego-1600'],'roles':{},'connections':[],
                'power':{},'platform':'standalone','sync_target':'device','storage':'local'}})
    rows, findings = validate_records([*rows, topology, procedure], snap)
    assert not findings
    u = s['narrative_units'][0]
    u.update(unit_type='step', mode='record_render', knowledge_type='procedure',
        topology_id='topology:synthetic', dependency_record_ids=[procedure['id']],
        record_assertions=[{k:deepcopy(procedure[k]) for k in CORE}],
        conditions={procedure['id']:{}}, step_index=1)
    s.update(dependency_claim_ids=[procedure['id']],topology_id='topology:synthetic')
    b = narrative_units.render_narrative_unit(u)
    assert 'Start recording' in b and 'Connect cable' not in b
    assert all(term in b for term in ['powered device','Recording indicator','Check connection','topology:synthetic'])
    u.update(end=len(b),body_sha256=sha(b));sign_unit(u,b,rows);sign_section(s,b,rows)
    assert compile_sections([s],{s['section_id']:b},rows,snap)
    b = b.replace('powered device','')
    u.update(end=len(b),body_sha256=sha(b));sign_unit(u,b,rows);sign_section(s,b,rows)
    with pytest.raises(ValueError):compile_sections([s],{s['section_id']:b},rows,snap)


def test_narrative_changes_invalidate_asserted_record_reviews_in_impact_graph():
    from daq_fae.knowledge.update_impact import plan_update
    from test_update_impact import bundle
    before = bundle()
    section = before['sections'][0]
    section['narrative_units'] = [{'unit_id':'unit:one',
        'record_assertions':section.pop('record_assertions'),
        'dependency_record_ids':['claim:one'], 'source_evidence':[{
            'source_ref':deepcopy(section['source_refs'][0])}]}]
    after = deepcopy(before)
    after['sections'][0]['narrative_units'][0]['conditions'] = {'mode':'changed'}
    report = plan_update(before,after)
    assert report['affected']['record_ids'] == ['claim:one']
    assert report['affected']['question_ids'] == ['dev:one']
    assert report['reusable_signature_record_ids'] == ['claim:two']


def test_impact_graph_rejects_hidden_narrative_dependency():
    from daq_fae.knowledge.update_impact import plan_update
    from test_update_impact import bundle
    before = bundle()
    before['sections'][0]['narrative_units'] = [{'unit_id':'unit:one',
        'record_assertions':[{'id':'claim:two'}], 'dependency_record_ids':['claim:two'],
        'source_evidence':[]}]
    with pytest.raises(ValueError,match='dependency'):
        plan_update(before,deepcopy(before))


@pytest.mark.parametrize('bad', [None, 'invalid', {}, 3])
def test_malformed_narrative_inventory_fails_as_validation_error(bad):
    s,b,rows,snap=fixture();s['narrative_units']=bad
    sign_section(s,b,rows)
    with pytest.raises(ValueError):compile_sections([s],{s['section_id']:b},rows,snap)


def test_candidate_audit_rejects_mixed_unit_roles_before_publication():
    s,b,rows,snap=fixture();first=s['narrative_units'][0]
    second=deepcopy(first);second['unit_id']='unit:second';second['view_roles'].append('channel')
    second.update(start=len(b)+2,end=2*len(b)+2)
    b+='\n\n'+b
    sign_unit(second,b,rows);s['narrative_units'].append(second);sign_section(s,b,rows)
    result=run(s,b,rows,snap)
    assert not result['consistent']
    assert any(f['code']=='narrative_mixed_permissions' for f in result['findings'])


def test_narrative_source_selection_is_exact_and_deduplicated():
    from daq_fae.knowledge.narrative_units import narrative_source_keys
    s,_,_,snap=fixture()
    ref=s['source_refs'][0]
    assert narrative_source_keys([s,s]) == {(ref['path'],ref['sha256'],json.dumps(ref['locator'],sort_keys=True,separators=(',',':')))}


def test_checked_explanation_cannot_conceal_path_or_url_in_body_or_metadata():
    for field in ('body', 'authored_by'):
        for value in ('https://example.com/unreviewed','tmp/private/source.md'):
            s,b,rows,snap=fixture();u=s['narrative_units'][0]
            if field=='body':
                b=value;u.update(mode='reviewed_narrative',end=len(b),body_sha256=sha(b))
            else:u[field]=value
            sign_unit(u,b,rows);sign_section(s,b,rows)
            with pytest.raises(ValueError):compile_sections([s],{s['section_id']:b},rows,snap)


def test_unit_identity_participates_in_scoped_update_impact():
    from daq_fae.knowledge.update_impact import plan_update
    from test_update_impact import bundle
    before=bundle()
    section=before['sections'][0]
    section['narrative_units']=[{'unit_id':'unit:one','entity_ids':['entity:unit-only'],
        'dependency_record_ids':[], 'record_assertions':[], 'source_evidence':[]}]
    after=deepcopy(before)
    after['sku_definitions']={'entity:unit-only':{'revision':'new'}}
    result=plan_update(before,after)
    assert result['review']['sku_changed']['section_ids']==['section:one']
    assert result['affected']['question_ids']==['dev:one']


def test_distinct_units_cannot_overlap_exact_body_spans():
    s,b,rows,snap=fixture();u=deepcopy(s['narrative_units'][0]);u['unit_id']='unit:other'
    sign_unit(u,b,rows);s['narrative_units'].append(u);sign_section(s,b,rows)
    result=run(s,b,rows,snap)
    assert any(f['code']=='narrative_overlap' for f in result['findings'])
