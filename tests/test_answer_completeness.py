"""Completeness comes from source navigation and the requested delivery goals."""
from copy import deepcopy
from types import SimpleNamespace

from daq_fae.domain_tools import DaqToolBox
from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.response_language import localize_answer
from src.agent.loop.answer_contract import AnswerDeliveryUnit, AnswerSubmission, render_submission
from test_document_references import _fixture


def manual():
    view, goal = _fixture()
    base = view.records_for('internal_fae')
    docs = []
    sections = []
    texts = [
        ('mobile', '## 1 Mobile platform'),
        ('wifi', '### 1.1 Wireless connection\nMobile supports wireless only.'),
        ('join', '#### 1.1.1 Join network\nSelect the network and confirm.'),
        ('record', '#### 1.1.2 Save recording\nChoose the card; start recording and inspect the result.'),
        ('desktop', '## 2 Desktop platform'),
        ('desktop-record', '### 2.1 Save recording\nSelect a desktop folder.'),
    ]
    for i, (key, text) in enumerate(texts):
        r = deepcopy(base[-1]);r['id'] = 'claim:' + key
        r['data']['value'] = text
        r['source_refs'][0]['locator'] = {'kind': 'lines', 'start': i * 10 + 1, 'end': i * 10 + 9}
        s = {'section_id': 'section:' + key, 'title': text.splitlines()[0].lstrip('# '),
             'knowledge_type': 'product', 'review_status': 'verified',
             'scope': deepcopy(r['scope']), 'entity_id': r['data']['entity_id'],
             'dependency_claim_ids': [r['id']], 'source_refs': deepcopy(r['source_refs']),
             'body': text}
        docs.append(r);sections.append(s)
    # Same file hash/path is insufficient: a different scope is a separate context.
    hidden = deepcopy(sections[-1]);hidden.update(section_id='section:hidden', body='Secret procedure')
    other = deepcopy(sections[-1]);other.update(section_id='section:other-scope', scope={'product': 'probe'})
    all_sections = sections + [other]
    rows = base[:2] + docs
    view = SimpleNamespace(release_id='fixture', is_candidate_preview=False,
        records_for=lambda role: deepcopy(rows) if role == 'internal_fae' else [],
        sections_for=lambda role: deepcopy(all_sections) if role == 'internal_fae' else [deepcopy(hidden)],
        conflict_notices_for=lambda role: [])
    return view, goal


def test_document_support_binds_delivery_tasks_instead_of_identity_setup():
    view, goal = _fixture()
    identity = {**goal, 'id': 'identity', 'capability': 'resolve_entity', 'delivery_required': False}
    operation = {**goal, 'id': 'operation', 'delivery_required': True}
    limits = {**goal, 'id': 'limits', 'capability': 'search_knowledge', 'delivery_required': True}
    goals = [identity, operation, limits]
    r = DaqToolBox(knowledge=view, role='internal_fae', requirements=goals).dispatch(
        'read_doc', {'section_id': 'section:guide'})
    assert {s['parent_requirement_id'] for s in r.content['supporting_evidence']} == {'operation', 'limits'}
    assert all(s['capability'] == 'read_doc' for s in r.content['supporting_evidence'])
    session = DaqEvidencePolicy(knowledge=view, role='internal_fae').begin({'requirements': goals})
    session.observe('read_doc', r)
    assert session.snapshot().capability_coverage['lookup_procedure'] == 'partial'
    assert session.snapshot().requirement_status['operation'] == 'unknown'


def test_read_navigation_preserves_platform_hierarchy_without_automatic_reading():
    view, goal = manual()
    r = DaqToolBox(knowledge=view, role='internal_fae', requirements=[goal]).dispatch(
        'read_doc', {'section_id': 'section:join'})
    nav = r.content['matches'][0]['document_context']
    assert nav['heading_path'] == ['1 Mobile platform', '1.1 Wireless connection', '1.1.1 Join network']
    assert nav['parent']['section_id'] == 'section:wifi'
    assert [s['section_id'] for s in nav['siblings']] == ['section:record']
    assert nav['requires_read_doc'] is True
    assert 'guide.md' not in str(nav)
    assert 'section:hidden' not in str(nav) and 'section:other-scope' not in str(nav)
    assert {s['source_ids'][0] for s in r.content['supporting_evidence']} == {'claim:join'}


def test_search_recalls_child_steps_under_its_platform_not_only_leaf_heading():
    view, goal = manual()
    r = DaqToolBox(knowledge=view, role='internal_fae', requirements=[goal]).dispatch(
        'search_knowledge', {'query': 'Mobile save recording'})
    matches = r.content['matches']
    ids = [s['section_id'] for s in matches]
    assert ids.index('section:record') < ids.index('section:desktop-record')
    assert 'supporting_evidence' not in r.content
    assert 'source_limits' not in next(s for s in matches if s['section_id'] == 'section:record')


def test_partial_presentation_delivers_facts_before_common_boundary_in_both_languages():
    for language in ('zh', 'en'):
        submission = AnswerSubmission(outcome='safe_abstained',
            basis='Shared document scope.', missing='One actual unresolved target.',
            delivery_units=(AnswerDeliveryUnit('support', ('claim:record',), 'Useful recorded steps.'),))
        done = {'outcome': 'safe_abstained', 'answer': render_submission(submission)}
        localize_answer(done, language, submission=submission)
        assert done['answer'].index('Useful recorded steps.') < done['answer'].index('Shared document scope.')
        assert done['answer'].count('One actual unresolved target.') == 1
        assert done['outcome'] == 'safe_abstained'


def test_navigation_never_exposes_another_source_hash_or_candidate_preview():
    view, goal = manual()
    view.is_candidate_preview = True
    r = DaqToolBox(knowledge=view, role='internal_fae', requirements=[goal]).dispatch(
        'read_doc', {'section_id': 'section:join'})
    assert 'document_context' not in r.content['matches'][0]
    assert not r.content.get('supporting_evidence')


def test_a_hidden_ancestor_is_a_location_barrier_never_a_context_inference():
    from daq_fae.knowledge.document_navigation import document_navigation
    view, _ = manual()
    sections = view.sections_for('internal_fae')
    rows = view.records_for('internal_fae')
    chunks = [{'source_path': 'guide.md', 'source_sha256': 'a' * 64,
               'locator': {'kind': 'lines', 'start': 20, 'end': 20},
               'text': '## Hidden platform must never be consumed'}]
    nav = document_navigation(sections, rows, source_chunks=chunks)
    assert nav['section:join']['heading_path'] == ['1.1.1 Join network']
    assert nav['section:join']['parent'] is None
    assert 'Hidden platform' not in str(nav)


def test_pdf_pages_only_have_page_order_no_guessed_heading_tree():
    from daq_fae.knowledge.document_navigation import document_navigation
    view, _ = manual()
    sections = view.sections_for('internal_fae')[:2]
    rows = view.records_for('internal_fae')
    for i, s in enumerate(sections):
        row = next(r for r in rows if r['id'] == s['dependency_claim_ids'][0])
        row['source_refs'][0].update(path='guide.pdf', locator={'kind': 'page', 'page': i+1})
        s['source_refs'] = deepcopy(row['source_refs'])
    nav = document_navigation(sections, rows)
    assert nav['section:mobile']['heading_path'] == []
    assert nav['section:wifi']['parent'] is None
    assert nav['section:mobile']['next']['section_id'] == 'section:wifi'


def test_overlapping_source_ranges_do_not_create_a_false_platform_ancestor():
    from daq_fae.knowledge.document_navigation import document_navigation
    view, _ = manual()
    sections = view.sections_for('internal_fae')
    rows = view.records_for('internal_fae')
    rows[2]['source_refs'][0]['locator']['end'] = 30
    sections[0]['source_refs'] = deepcopy(rows[2]['source_refs'])
    nav = document_navigation(sections, rows, source_chunks=[{
        'source_path': 'guide.md', 'source_sha256': 'a' * 64,
        'locator': {'kind': 'lines', 'start': 10, 'end': 10}}])
    assert '1 Mobile platform' not in nav['section:join']['heading_path']
    assert nav['section:join']['parent'] is None


def test_multi_heading_source_passage_is_not_a_single_inferred_ancestor():
    from daq_fae.knowledge.document_navigation import document_navigation
    view, _ = manual()
    sections = view.sections_for('internal_fae')
    rows = view.records_for('internal_fae')
    rows[2]['data']['value'] += '\n## Another platform inside the same passage'
    nav = document_navigation(sections, rows)
    assert 'section:mobile' not in nav
    assert '1 Mobile platform' not in nav['section:join']['heading_path']
