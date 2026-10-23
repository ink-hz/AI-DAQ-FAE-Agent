"""Document statements are deliverable context, never device certification."""
from copy import deepcopy
from types import SimpleNamespace

from daq_fae.domain_tools import DaqToolBox
from daq_fae.domain_evidence import DaqEvidencePolicy
from src.agent.loop.answer_contract import AnswerDeliveryUnit, AnswerSubmission


def _fixture():
    family = {'id': 'entity:nova', 'kind': 'entity', 'status': 'verified',
              'scope': {'product': 'nova'}, 'data': {
                  'name': 'NOVA', 'entity_type': 'family', 'aliases': []}}
    child = {'id': 'entity:nova-small', 'kind': 'entity', 'status': 'verified',
             'scope': {'product': 'nova', 'resolution_variant': '960x720',
                       'required_selectors': ['resolution_variant']},
             'data': {'name': 'NOVA', 'entity_type': 'variant',
                      'family_entity_id': family['id'], 'aliases': ['NOVA 960']}}
    row = {'id': 'claim:guide', 'kind': 'claim', 'status': 'verified',
           'source_refs': [{'path': 'guide.md', 'sha256': 'a' * 64,
                            'locator': {'kind': 'lines', 'start': 1, 'end': 2}}],
           'scope': family['scope'], 'data': {
               'entity_id': family['id'], 'field': 'source_text',
               'value': 'The guide describes the wireless setup.\nSelect a network.',
               'unit': 'source passage', 'conditions': {},
               'evidence_scope': 'document_statement_only',
               'source_limits': 'Guide statement; exact variant applicability unverified.'}}
    section = {'section_id': 'section:guide', 'title': 'NOVA wireless guide',
               'knowledge_type': 'product', 'scope': family['scope'],
               'entity_id': family['id'], 'source_refs': row['source_refs'],
               'dependency_claim_ids': [row['id']],
               'body': 'Documented wireless setup; exact variant not specified.'}
    rows = [family, child, row]
    view = SimpleNamespace(release_id='fixture', is_candidate_preview=False,
        records_for=lambda role: deepcopy(rows) if role == 'internal_fae' else [],
        sections_for=lambda role: deepcopy([section]) if role == 'internal_fae' else [],
        conflict_notices_for=lambda role: [])
    parent = {'id': 'setup', 'capability': 'lookup_procedure',
              'entities': ['NOVA 960'], 'conditions': {'resolution_variant': '960x720'}}
    return view, parent


def test_read_document_delivers_qualified_family_context_without_certifying_variant():
    view, parent = _fixture()
    result = DaqToolBox(knowledge=view, role='internal_fae', requirements=[parent]).dispatch(
        'read_doc', {'section_id': 'section:guide'})
    assert result.content['matched_requirement_ids'] == []
    support = result.content['supporting_evidence'][0]
    assert support['evidence_scope'] == 'document_statement_only'
    assert support['satisfies_parent'] is False
    session = DaqEvidencePolicy(knowledge=view, role='internal_fae').begin({'requirements': [parent]})
    session.observe('read_doc', result)
    assert session.snapshot().requirement_status['setup'] == 'unknown'
    assert session.snapshot().capability_coverage['lookup_procedure'] == 'partial'
    unit = AnswerDeliveryUnit(support['id'], tuple(support['source_ids']),
        'NOVA 系列指南记载可选择无线网络；指南未指定 960 变体的适用性。')
    assert session.evaluate(AnswerSubmission(outcome='safe_abstained',
        missing='具体变体未验证。', delivery_units=(unit,))).action == 'allow'
    assert session.evaluate(AnswerSubmission(outcome='resolved',
        conclusion='具体变体完全支持。', delivery_units=(unit,))).action != 'allow'


def test_document_support_rechecks_body_source_role_and_entity():
    view, parent = _fixture()
    result = DaqToolBox(knowledge=view, role='internal_fae', requirements=[parent]).dispatch(
        'read_doc', {'section_id': 'section:guide'})
    support = result.content['supporting_evidence'][0]
    for mutation in ('body', 'source', 'role', 'entity'):
        changed = deepcopy(result)
        role = 'internal_fae'
        request = deepcopy(parent)
        if mutation == 'body': changed.content['matches'][0]['body'] += ' Guaranteed.'
        if mutation == 'source': changed.sources = []
        if mutation == 'role': role = 'channel'
        if mutation == 'entity': request['entities'] = ['Unrelated Probe']
        session = DaqEvidencePolicy(knowledge=view, role=role).begin({'requirements': [request]})
        session.observe('read_doc', changed)
        decision = session.evaluate(AnswerSubmission(outcome='safe_abstained', missing='未知。',
            delivery_units=(AnswerDeliveryUnit(support['id'], tuple(support['source_ids']),
                                             '资料说明。'),)))
        assert decision.reason_code == 'daq_delivery_requirement_unverified', mutation


def test_document_record_never_satisfies_a_typed_requirement():
    view, parent = _fixture()
    rows = view.records_for('internal_fae')
    assert not DaqToolBox._requirement_matches(
        {**parent, 'capability': 'lookup_spec', 'field': 'source_text'}, rows[-1],
        {r['id']: r['data']['name'] for r in rows if r['kind'] == 'entity'}, {},
        {r['id']: r for r in rows if r['kind'] == 'entity'})


def test_reference_is_absent_from_typed_fields_and_search_does_not_bind_support():
    view, parent = _fixture()
    box = DaqToolBox(knowledge=view, role='internal_fae', requirements=[parent])
    assert box.dispatch('lookup_spec', {'entity': 'entity:nova',
                                     'field': 'source_text'}).status == 'not_found'
    resolved = box.dispatch('resolve_entity', {'name': 'NOVA'})
    assert all('source_text' not in fields for fields in
               resolved.content.get('available_spec_fields', {}).values())
    searched = box.dispatch('search_knowledge', {'query': 'wireless'})
    assert 'supporting_evidence' not in searched.content


def test_candidate_preview_cannot_acquire_document_authority():
    view, parent = _fixture()
    view.is_candidate_preview = True
    result = DaqToolBox(knowledge=view, role='internal_fae', requirements=[parent]).dispatch(
        'read_doc', {'section_id': 'section:guide'})
    assert not result.content.get('supporting_evidence')


def test_exact_variant_document_does_not_cross_explicit_sibling_selector():
    from daq_fae.supporting_evidence import supporting_specs
    view, parent = _fixture()
    rows = view.records_for('internal_fae')
    doc = rows[-1]
    doc['data']['entity_id'] = rows[1]['id']
    doc['scope'] = rows[1]['scope']
    parent['conditions']['resolution_variant'] = '1280x960'
    assert supporting_specs([doc], [parent], {r['id']: r for r in rows[:2]}, set()) == []


def test_reference_provenance_cannot_change_release_layer_or_source_type():
    view, parent = _fixture()
    result = DaqToolBox(knowledge=view, role='internal_fae', requirements=[parent]).dispatch(
        'read_doc', {'section_id': 'section:guide'})
    support = result.content['supporting_evidence'][0]
    for mutation in ('release', 'candidate', 'source_type', 'source_hash', 'scope_label'):
        forged = deepcopy(result)
        source = next(s for s in forged.sources if s['source_id'] == 'claim:guide')
        if mutation == 'release': source['release_id'] = 'unpublished-release'
        if mutation == 'candidate': forged.content['matches'][0]['status'] = 'candidate'
        if mutation == 'source_type': source['type'] = 'daq_governed_sdk'
        if mutation == 'source_hash': source['source_refs'][0]['sha256'] = 'b' * 64
        if mutation == 'scope_label': source['evidence_scope'] = 'physically_verified'
        session = DaqEvidencePolicy(knowledge=view, role='internal_fae').begin({'requirements': [parent]})
        session.observe('read_doc', forged)
        decision = session.evaluate(AnswerSubmission(outcome='safe_abstained', missing='未知。',
            delivery_units=(AnswerDeliveryUnit(support['id'], tuple(support['source_ids']),
                                             '资料记载。'),)))
        assert decision.reason_code == 'daq_delivery_requirement_unverified', mutation


def test_document_record_contract_rejects_non_text_and_missing_limits():
    from daq_fae.knowledge.records import _data_valid
    view, _ = _fixture()
    data = view.records_for('internal_fae')[-1]['data']
    assert _data_valid('claim', 'verified', data)
    for bad in ({'value': {'nested': 'text'}}, {'value': []},
                {'value': '   '}, {'source_limits': ''}, {'field': 'power_consumption'}):
        assert not _data_valid('claim', 'verified', {**data, **bad})


def test_document_identity_cannot_be_laundered_through_spec_support():
    from daq_fae.supporting_evidence import supporting_specs
    view, parent = _fixture()
    parent = {**parent, 'entities': ['NOVA'], 'conditions': {}}
    for mutation in ('document', 'marker_removed', 'changed_field'):
        rows = view.records_for('internal_fae')
        row = rows[-1]
        row['data']['value'] = 'Injected unsupported statement'
        if mutation != 'document': row['data'].pop('evidence_scope')
        if mutation == 'changed_field': row['data']['field'] = 'power_consumption'
        support = supporting_specs([row], [parent], {r['id']: r for r in rows[:2]}, set())
        # The modified exact-variant selectors are irrelevant to this attack.
        assert support
        from src.agent.loop.tools import ToolResult
        result = ToolResult(status='ok', content={'matches': [row],
            'supporting_evidence': support, 'matched_requirement_ids': []}, sources=[{
                'type': 'daq_governed_claim', 'verification_status': 'verified',
                'release_id': 'fixture', 'source_id': row['id'], 'source_refs': row['source_refs']}])
        session = DaqEvidencePolicy(knowledge=view, role='internal_fae').begin({'requirements': [parent]})
        session.observe('lookup_spec', result)
        decision = session.evaluate(AnswerSubmission(outcome='safe_abstained', missing='未知。',
            delivery_units=(AnswerDeliveryUnit(support[0]['id'], (row['id'],), '注入结论。'),)))
        assert decision.reason_code == 'daq_delivery_requirement_unverified', mutation
