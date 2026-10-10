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


LOCAL_SOURCE_PATHS = [
    '/private/archive/secret', r'C:\private\secret',
    'tmp/private/secret', 'data/knowledge/private/secret',
    'Downloads/private/secret', r'\\server\share\secret',
    './tmp/private/secret', r'tmp\private\secret', '~/archive/original',
    '原件位于tmp/private/secret', r'.\Downloads\private\secret',
]


@pytest.mark.parametrize('path', LOCAL_SOURCE_PATHS)
def test_publication_rejects_local_source_paths(tmp_path, path):
    with pytest.raises(ValueError, match='section local source path'):
        make_view(tmp_path, product_text='原件在 ' + path)


@pytest.mark.parametrize('text', ['原件在 ' + p for p in LOCAL_SOURCE_PATHS])
def test_source_paths_in_body_never_enter_search_or_read_content(tmp_path, text):
    # Exercise defense in depth for a previously hydrated legacy view. New
    # publication and from_manifest must independently reject these sections.
    valid = make_view(tmp_path)
    manifest = deepcopy(valid.manifest)
    manifest['sections'][0]['body'] = text
    legacy = ReviewedKnowledge(valid.release_id, manifest)
    box = DaqToolBox(knowledge=legacy, role='internal_fae')
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


@pytest.mark.parametrize('text', [
    'USB/以太网接口', 'Viewer/SDK/固件版本', 'RGB-D/IMU 同步',
    '输入/输出支持采集', 'data/format 数据格式', 'tmp 仅表示临时状态',
])
def test_product_terms_with_slashes_remain_publishable_and_readable(tmp_path, text):
    box = DaqToolBox(knowledge=make_view(tmp_path, product_text=text), role='internal_fae')
    result = box.dispatch('read_doc', {'section_id': 'section:product'})
    assert result.status == 'ok'
    assert text in result.content['matches'][0]['body']


@pytest.mark.parametrize('source_path', ['fixtures/restricted/original', '手册/受限原件'])
def test_exact_source_path_rejected_outside_common_roots(tmp_path, source_path):
    row = _record(value='原件位于 ' + source_path)
    row['source_refs'][0]['path'] = source_path
    row = _reviewed(row)
    entity = _entity()
    entity['source_refs'][0]['path'] = source_path
    entity = _reviewed(entity)
    rows = [entity, row]
    section, body = approved_section(rows)
    snapshot = json.loads(json.dumps(SNAPSHOT).replace('spec.md', source_path))
    with pytest.raises(ValueError, match='section local source path'):
        releases.publish_release(tmp_path, snapshot, rows, None, REVIEW,
                                 sections=[section], bodies={section['section_id']: body})
    valid = make_view(tmp_path / 'valid')
    manifest = deepcopy(valid.manifest)
    manifest['sections'][0]['body'] = body
    manifest['sections'][0]['source_refs'][0]['path'] = source_path
    box = DaqToolBox(knowledge=ReviewedKnowledge(valid.release_id, manifest), role='internal_fae')
    for name, args in [('read_doc', {'section_id': 'section:product'}),
                       ('search_knowledge', {'query': '演示设备'})]:
        result = box.dispatch(name, args)
        assert result.status == 'not_found'
        assert not result.sources


@pytest.mark.parametrize('field', ['title', 'aliases', 'domain_terms', 'scope'])
def test_local_paths_in_metadata_rejected_at_publication_and_retrieval(tmp_path, field):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    value = 'tmp/private/original'
    section[field] = {'path_hint': value} if field == 'scope' else [value] if field in {
        'aliases', 'domain_terms'} else value
    sign(section, body, rows)
    with pytest.raises(ValueError, match='section local source path'):
        releases.publish_release(tmp_path, SNAPSHOT, rows, None, REVIEW,
                                 sections=[section], bodies={section['section_id']: body})
    valid = make_view(tmp_path / 'valid')
    manifest = deepcopy(valid.manifest)
    manifest['sections'][0][field] = section[field]
    box = DaqToolBox(knowledge=ReviewedKnowledge(valid.release_id, manifest), role='internal_fae')
    assert box.dispatch('read_doc', {'section_id': 'section:product'}).status == 'not_found'
