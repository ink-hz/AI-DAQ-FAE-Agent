"""C2 synthetic reviewed releases; no real knowledge or model evaluation."""
from copy import deepcopy
import hashlib
import json

import pytest

from daq_fae.domain_tools import DaqToolBox
from daq_fae.knowledge import releases
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.knowledge.section_consistency import render_record
from test_knowledge_releases import SNAPSHOT, REVIEW, _entity, _record, _reviewed
from test_section_releases import approved_section, sign


def make_view(tmp_path, *, product_text=None):
    rows = [_entity(), _record()]
    sections, bodies = [], {}
    for sid, title, kind, aliases, terms, text in [
        ('product', '合成采集器产品介绍', 'product', ['演示设备'], ['分辨率'], '双目图像可用于采集。'),
        ('recording', '采集流程', 'system', ['保存数据'], ['落盘', '录制'], '开始录制后检查存储目录和剩余空间。'),
        ('sync', '同步与时间戳', 'topic', ['对时'], ['同步'], '时间戳应在相同的时钟域内比较。'),
    ]:
        if sid == 'product' and product_text is not None:
            text = product_text
        row = _record(value=text)
        row.update(id='claim:' + sid)
        row['data'].update(field='source_text', unit='text', conditions={'section': sid})
        row = _reviewed(row)
        rows.append(row)
        section, _ = approved_section([rows[0], row])
        body = render_record(row)
        section.update(section_id='section:' + sid, title=title, knowledge_type=kind,
                       aliases=aliases, domain_terms=terms, entity_id=rows[0]['id'])
        section['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        sign(section, body, rows)
        sections.append(section)
        bodies[section['section_id']] = body
    candidate = _record(status='candidate', value='候选机密')
    candidate['id'] = 'claim:candidate'
    candidate['data']['field'] = 'candidate_only'
    conflict = _record(status='conflict')
    conflict['id'] = 'claim:conflict'
    conflict['data']['field'] = 'conflict_only'
    conflict['data'].pop('value')
    conflict['data']['candidates'] = [
        {'value': '冲突机密甲', 'source_ref': deepcopy(conflict['source_refs'][0])},
        {'value': '冲突机密乙', 'source_ref': deepcopy(conflict['source_refs'][0])},
    ]
    rows += [candidate, conflict]
    rid = releases.publish_release(tmp_path, SNAPSHOT, rows, None, REVIEW,
                                   sections=sections, bodies=bodies)
    manifest = json.loads((tmp_path / 'releases' / rid / 'manifest.json').read_text())
    return ReviewedKnowledge.from_manifest(rid, manifest)


@pytest.mark.parametrize('query,expected', [
    ('请介绍一下演示设备是什么？', 'product'),
    ('如何把采集结果保存数据？', 'recording'),
    ('录制完成后怎么落盘？', 'recording'),
    ('如何检查剩余空间？', 'recording'),
    ('多设备的时间戳应该怎样比较？', 'sync'),
    ('ＥＧＯ 1600 产品介绍', 'product'),
])
def test_natural_language_search_ranks_reviewed_sections(tmp_path, query, expected):
    box = DaqToolBox(knowledge=make_view(tmp_path), role='internal_fae')
    result = box.dispatch('search_knowledge', {'query': query})
    assert result.status == 'ok'
    assert result.content['matches'][0]['section_id'] == 'section:' + expected
    assert result.content['release_id'] == box.knowledge.release_id
    assert result.content['matches'][0]['scope'] == {'variant': '1600'}
    assert result.sources[0]['source_refs'] == [{
        'source_id': 'source:' + SNAPSHOT_REF['sha256'],
        'sha256': SNAPSHOT_REF['sha256'], 'locator': SNAPSHOT_REF['locator']}]
    assert 'spec.md' not in json.dumps(result.sources)
    assert 'spec.md' not in json.dumps(result.content)
    assert result.content['matched_requirement_ids'] == []


SNAPSHOT_REF = {'path': 'spec.md', 'sha256': 'a' * 64,
                'locator': {'kind': 'lines', 'start': 1, 'end': 2}}


def test_read_doc_returns_whole_authorized_section_and_schema(tmp_path):
    box = DaqToolBox(knowledge=make_view(tmp_path), role='internal_fae')
    assert 'read_doc' in [item['function']['name'] for item in box.tool_schemas()]
    result = box.dispatch('read_doc', {'section_id': 'section:recording'})
    assert result.status == 'ok'
    match = result.content['matches'][0]
    assert match['body'] == box.knowledge.sections_for('internal_fae')[1]['body']
    assert match['evidence_layer'] == 'reviewed_section'
    assert result.sources[0]['source_id'] == 'section:recording'
    assert 'record_assertions' not in match
    assert 'source_refs' not in match
    assert 'spec.md' not in json.dumps(result.sources)


@pytest.mark.parametrize('name,args', [
    ('search_knowledge', {'query': '采集'}),
    ('read_doc', {'section_id': 'section:recording'}),
    ('read_doc', {'section_id': 'section:missing'}),
])
def test_unauthorized_sections_and_sources_are_invisible(tmp_path, name, args):
    box = DaqToolBox(knowledge=make_view(tmp_path), role='channel')
    result = box.dispatch(name, args)
    assert result.status == 'not_found'
    assert result.content['matches'] == []
    assert not result.sources
    assert 'section:recording' not in json.dumps(result.content)


@pytest.mark.parametrize('query', ['', '？', 'zzzz-not-present', '候选机密', '冲突机密', 'spec.md', 'source_text'])
def test_search_does_not_match_metadata_candidates_conflicts_or_empty_query(tmp_path, query):
    box = DaqToolBox(knowledge=make_view(tmp_path), role='internal_fae',
                     context={'attachment': '候选机密'})
    result = box.dispatch('search_knowledge', {'query': query})
    assert result.status == 'not_found'
    assert not result.sources


def test_typed_spec_still_works_in_section_release(tmp_path):
    box = DaqToolBox(knowledge=make_view(tmp_path), role='internal_fae')
    result = box.dispatch('lookup_spec', {'entity': 'EGO 1600', 'field': 'resolution'})
    assert result.status == 'ok'
    assert result.content['matches'][0]['data']['value'] == '1600x1200'


@pytest.mark.parametrize('name,args', [
    ('read_doc', {}), ('read_doc', {'section_id': ['section:product']}),
    ('search_knowledge', {'query': 123}), ('search_knowledge', {'query': 'x' * 2001}),
])
def test_invalid_section_tool_arguments_fail_explicitly(tmp_path, name, args):
    box = DaqToolBox(knowledge=make_view(tmp_path), role='internal_fae')
    result = box.dispatch(name, args)
    assert result.status == 'tool_error'
    assert result.content['error'] == 'invalid_tool_arguments'
    assert not result.sources


def test_oversize_read_is_explicit_and_search_excerpt_is_bounded(tmp_path):
    box = DaqToolBox(knowledge=make_view(tmp_path, product_text='合成正文' * 9000),
                     role='internal_fae')
    result = box.dispatch('read_doc', {'section_id': 'section:product'})
    assert result.status == 'tool_error'
    assert result.content['error'] == 'section_too_large'
    assert not result.sources
    search = box.dispatch('search_knowledge', {'query': '演示设备'})
    assert len(search.content['matches'][0]['excerpt']) == 600
    assert search.content['matches'][0]['excerpt_truncated'] is True


@pytest.mark.parametrize('text', ['原件在 /private/archive/secret',
                                  r'原件在 C:\private\secret'])
def test_source_paths_in_body_never_enter_search_or_read_content(tmp_path, text):
    box = DaqToolBox(knowledge=make_view(tmp_path, product_text=text), role='internal_fae')
    for name, args in [('read_doc', {'section_id': 'section:product'}),
                       ('search_knowledge', {'query': '演示设备'})]:
        result = box.dispatch(name, args)
        assert result.status == 'not_found'
        assert not result.sources
        assert '原件在' not in json.dumps(result.content, ensure_ascii=False)


def test_results_are_deterministic_and_do_not_certify_search_requirements(tmp_path):
    box = DaqToolBox(knowledge=make_view(tmp_path), role='internal_fae', requirements=[
        {'id': 'search', 'capability': 'search_knowledge', 'entities': ['EGO 1600']}])
    first = box.dispatch('search_knowledge', {'query': 'EGO 1600'})
    second = box.dispatch('search_knowledge', {'query': 'EGO 1600'})
    assert first.content == second.content
    assert [s['section_id'] for s in first.content['matches']] == [
        'section:product', 'section:recording', 'section:sync']
    assert first.content['matched_requirement_ids'] == []
