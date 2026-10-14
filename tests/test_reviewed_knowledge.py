from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daq_fae.app import create_app
from daq_fae.knowledge.records import access_fingerprint, record_fingerprint
from daq_fae.knowledge.releases import _activate_release as activate_release, _publish_release as publish_release
from daq_fae.knowledge.releases import read_active_release
from synthetic_release_helpers import load_fixture_active, approve_fixture_for_app
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.domain_tools import DaqToolBox
from daq_fae.domain_tools import _field_intent_matches
from daq_fae.task_context import prepare_turn


SOURCE_SHA = "a" * 64


def test_frame_rate_requirement_matches_the_verified_fps_field_without_crossing_modalities():
    assert _field_intent_matches('rgb_max_frame_rate', 'rgb_max_fps')
    assert _field_intent_matches('rgb_frame_rate', 'rgb_max_fps')
    assert not _field_intent_matches('rgb_max_frame_rate', 'stereo_mono_max_fps')


def test_context_only_toolbox_does_not_expose_unrequested_product_search():
    box = DaqToolBox(allowed_tools=frozenset({'resolve_entity', 'session_state'}))
    names = {item['function']['name'] for item in box.tool_schemas()}
    assert names == {'resolve_entity', 'session_state'}
    assert box.dispatch('lookup_spec', {'entity': 'EGO', 'field': 'horizontal_fov'}).status == 'tool_error'
SOURCE_REF = {"path": "spec.md", "sha256": SOURCE_SHA,
              "locator": {"kind": "lines", "start": 1, "end": 2}}
SNAPSHOT = {
    "archive_manifest_sha256": "f" * 64,
    "sources": [{"path": "spec.md", "sha256": SOURCE_SHA,
                 "size": 20, "kind": "markdown"}],
    "chunks": [{"source_path": "spec.md", "source_sha256": SOURCE_SHA,
                "locator": SOURCE_REF["locator"], "text": "EGO specification"}],
}
RELEASE_REVIEW = {"reviewer": "release-owner", "reviewed_at": "2026-10-09",
                  "dev_batch": "synthetic-k4"}


def test_records_only_active_release_is_searchable_without_sections():
    root = Path(__file__).resolve().parents[1] / 'data/knowledge/published'
    active = read_active_release(root)
    assert active is not None
    assert active['manifest']['format_version'] == 2
    assert len(active['manifest']['records']) == 23
    assert active['manifest']['sections'] == []
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    result = DaqToolBox(knowledge=view, role='internal_fae').dispatch(
        'search_knowledge', {'query': 'EGO'})
    assert result.status == 'ok'
    assert result.content['matches']
    ids = {row['id'] for row in result.content['matches']}
    assert {'entity:ego-1600', 'entity:ego-1920', 'entity:ego-pro'} <= ids
    assert 'claim:ego-1600-horizontal-fov' not in ids
    assert 'claim:ego-1920-horizontal-fov' not in ids
    assert all(source['release_id'] == view.release_id for source in result.sources)


def test_published_module_entity_proves_its_own_module_scope_but_not_system_scope():
    root = Path(__file__).resolve().parents[1] / 'data/knowledge/published'
    active = read_active_release(root)
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    requirements = [{
        'id': 'module-mono', 'capability': 'lookup_spec',
        'field': 'stereo_mono_resolution',
        'entities': ['entity:umi-vision-module'], 'conditions': {'scope': 'module'},
    }]
    box = DaqToolBox(knowledge=view, role='internal_fae', requirements=requirements)
    result = box.dispatch('lookup_spec', {
        'entity': 'entity:umi-vision-module', 'field': 'stereo_mono_resolution',
        'conditions': {'scope': 'module'},
    })
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['module-mono']
    assert result.content['matches'][0]['data']['value'] == '1280x960'
    assert box.dispatch('lookup_spec', {
        'entity': 'entity:umi-vision-module', 'field': 'stereo_mono_resolution',
        'conditions': {'scope': 'whole_system'},
    }).status == 'not_found'


def test_missing_module_baseline_by_unique_reviewed_product_key_is_attributed():
    root = Path(__file__).resolve().parents[1] / 'data/knowledge/published'
    active = read_active_release(root)
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    requirements = [
        {'id': 'module-baseline', 'capability': 'lookup_spec',
         'field': 'stereo_mono_baseline', 'entities': ['UMI 视觉模块'],
         'conditions': {}},
        {'id': 'finger-baseline', 'capability': 'lookup_spec',
         'field': 'stereo_mono_baseline', 'entities': ['UMI Finger'],
         'conditions': {}},
    ]
    box = DaqToolBox(knowledge=view, role='internal_fae', requirements=requirements)
    missing = box.dispatch('lookup_spec', {
        'entity': 'umi-vision-module', 'field': 'stereo_mono_baseline',
    })
    assert missing.status == 'not_found'
    assert missing.content['matched_requirement_ids'] == ['module-baseline']
    assert box.dispatch('lookup_spec', {
        'entity': 'umi-finger', 'field': 'stereo_mono_baseline',
    }).content['matched_requirement_ids'] == ['finger-baseline']
    unknown = box.dispatch('lookup_spec', {
        'entity': 'umi-vision-module-other', 'field': 'stereo_mono_baseline',
    })
    assert unknown.content['matched_requirement_ids'] == []


def test_equivalent_rgb_resolution_alias_keeps_missing_fps_goal_attribution():
    root = Path(__file__).resolve().parents[1] / 'data/knowledge/published'
    active = read_active_release(root)
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    requirement = {'id': 'fps1080', 'capability': 'lookup_spec',
                   'field': 'rgb_max_frame_rate', 'entities': ['EGO'],
                   'conditions': {'resolution_variant': '1920x1200',
                                  'resolution': '1920x1080'}}
    box = DaqToolBox(knowledge=view, role='internal_fae',
                     requirements=[requirement])
    for mode_key in ('resolution', 'rgb_resolution'):
        result = box.dispatch('lookup_spec', {
            'entity': 'entity:ego-1920', 'field': 'rgb_max_fps',
            'conditions': {'resolution_variant': '1920x1200',
                           mode_key: '1920x1080'},
        })
        assert result.status == 'not_found'
        assert result.content['matched_requirement_ids'] == ['fps1080']


def test_resolve_entity_does_not_promote_a_distinct_prefix_product_to_a_variant():
    root = Path(__file__).resolve().parents[1] / 'data/knowledge/published'
    active = read_active_release(root)
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    box = DaqToolBox(knowledge=view, role='internal_fae')

    def ids(text):
        result = box.dispatch('resolve_entity', {'text': text})
        assert result.status == 'ok'
        return {row['id'] for row in result.content['matches']}

    assert ids('EGO') == {'entity:ego-1600', 'entity:ego-1920'}
    assert ids('EGO Pro') == {'entity:ego-pro'}
    assert ids('EGO Pro on Windows 11') == {'entity:ego-pro'}
    assert ids('EGO Pro is connected to Windows') == {'entity:ego-pro'}
    assert ids('EGO on a PC') == {'entity:ego-1600', 'entity:ego-1920'}
    assert ids('EGO有1600×1200版本吗') == {'entity:ego-1600'}
    assert box.dispatch('resolve_entity', {'text': 'EGO Max'}).status == 'not_found'
    assert box.dispatch('resolve_entity', {'text': 'EGO P'}).status == 'not_found'
    assert box.dispatch('resolve_entity', {'text': 'EGO 双目'}).status == 'not_found'
    assert box.dispatch('resolve_entity', {'text': 'EGO 三目'}).status == 'not_found'
    assert box.dispatch('resolve_entity', {'text': 'EGO 新款'}).status == 'not_found'
    for unknown in ('UMI Finger pro on Windows', 'EGO Pro max on Windows',
                    'EGO max on a PC', 'EGO 接口版'):
        assert box.dispatch('resolve_entity', {'text': unknown}).status == 'not_found'
    assert ids('HUB') == {'entity:hub'}
    assert ids('HUB 接 PC') == {'entity:hub'}
    assert ids('HUB to PC') == {'entity:hub'}
    assert ids('HUB supports PC') == {'entity:hub'}
    assert box.dispatch('resolve_entity', {'text': 'EgoViewer'}).status == 'not_found'
    assert box.dispatch('resolve_entity', {'text': 'EGOViewer'}).status == 'not_found'
    assert view.verified_variant_scopes('entity:ego-pro', 'rgb_resolution',
                                        role='internal_fae') == {}
    assert view.verified_variant_scopes('EGO Max', 'rgb_resolution',
                                        role='internal_fae') == {}
    assert view.verified_variant_scopes('EGO 双目', 'horizontal_fov',
                                        role='internal_fae') == {}
    assert box.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'horizontal_fov'}).content['reason'] == \
        'reviewed_evidence_unavailable'
    assert box.dispatch('lookup_spec', {
        'entity': 'EGO Pro', 'field': 'rgb_resolution'}).content['reason'] == \
        'reviewed_field_unavailable'
    assert box.dispatch('lookup_spec', {
        'entity': 'EGO Max', 'field': 'rgb_resolution'}).content['reason'] == \
        'reviewed_evidence_unavailable'
    assert box.dispatch('lookup_spec', {
        'entity': 'EGO Pro Max', 'field': 'rgb_resolution'}).content['reason'] == \
        'reviewed_evidence_unavailable'
    assert box.dispatch('lookup_spec', {
        'entity': 'UMI Finger Pro', 'field': 'rgb_resolution'}).content['reason'] == \
        'reviewed_evidence_unavailable'


def _row(record_id, kind, data, *, view=("internal_fae",), forward=()):
    row = {"id": record_id, "kind": kind, "status": "verified",
           "scope": {"variant": "1600"}, "source_refs": [deepcopy(SOURCE_REF)],
           "data": data,
           "fact_review": {"reviewer": "fae", "reviewed_at": "2026-10-09"},
           "access_review": {"reviewer": "owner", "reviewed_at": "2026-10-09",
                             "view_roles": list(view), "forward_roles": list(forward)}}
    if row['kind'] == 'link':
        row['data']['page_evidence'] = {
            'title': 'Synthetic reviewed page', 'version': 'not_stated',
            'captured_at': '2026-10-08', 'valid_until': '2099-12-31',
            'snapshot_sha256': 'c' * 64, 'sku_scope': deepcopy(row['scope']),
        }
    row["fact_review"]["record_sha256"] = record_fingerprint(row)
    row["access_review"]["record_sha256"] = access_fingerprint(row)
    if kind == "link":
        row["link_review"] = {"reviewer": "link-owner", "reviewed_at": "2026-10-09",
                              "final_url": data["url"],
                              "record_sha256": record_fingerprint(row)}
    return row


def _records(*, claim_view=("internal_fae",), link_forward=()):
    return [
        _row("entity:ego-1600", "entity", {"name": "EGO 1600", "entity_type": "variant"}),
        _row("claim:resolution", "claim", {"entity_id": "entity:ego-1600",
             "field": "resolution", "value": "1600x1200", "unit": "px", "conditions": {}},
             view=claim_view),
        _row("link:ego", "link", {"url": "https://example.com/ego", "title": "EGO",
             "link_type": "documentation"}, forward=link_forward),
    ]


def test_active_release_is_frozen_and_reviewed_records_are_role_filtered(tmp_path):
    first = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, first)
    view = load_fixture_active(tmp_path)
    assert view.release_id == first
    assert [row["id"] for row in view.records_for("internal_fae")] == [
        "claim:resolution", "entity:ego-1600",
    ]
    assert view.records_for("channel") == []
    assert view.records_for("internal_fae", for_delivery=True) == []
    second = publish_release(tmp_path, SNAPSHOT, _records(claim_view=("internal_fae", "channel")),
                             first, RELEASE_REVIEW)
    activate_release(tmp_path, second)
    assert view.release_id == first
    assert load_fixture_active(tmp_path).release_id == second


def test_forward_authorization_is_separate_from_view_permission(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT,
                                 _records(link_forward=("internal_fae",)),
                                 None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    view = load_fixture_active(tmp_path)
    assert [row["id"] for row in view.records_for("internal_fae", for_delivery=True)] == [
        "link:ego",
    ]
    assert view.records_for("channel", for_delivery=True) == []


def test_manifest_that_lies_about_review_or_source_fails_closed(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    manifest = load_fixture_active(tmp_path).manifest
    stale = deepcopy(manifest)
    stale["records"][0]["data"]["value"] = "fabricated"
    with pytest.raises(ValueError, match="review|record"):
        ReviewedKnowledge.from_manifest(release_id, stale)
    stale = deepcopy(manifest)
    stale["sources"][0]["sha256"] = "b" * 64
    with pytest.raises(ValueError, match="source"):
        ReviewedKnowledge.from_manifest(release_id, stale)


def test_toolbox_returns_only_visible_matching_claim_with_governed_source(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    view = load_fixture_active(tmp_path)
    requirements = [
        {"id": "resolution", "capability": "lookup_spec", "field": "resolution",
         "entities": ["EGO 1600"], "conditions": {}},
        {"id": "wrong-field", "capability": "lookup_spec", "field": "fps",
         "entities": ["EGO 1600"], "conditions": {}},
    ]
    box = DaqToolBox(knowledge=view, role="internal_fae", requirements=requirements)
    result = box.dispatch("lookup_spec", {"entity": "EGO 1600", "field": "resolution"})
    assert result.status == "ok"
    assert result.content["matches"][0]["data"]["value"] == "1600x1200"
    assert result.content["matched_requirement_ids"] == ["resolution"]
    assert result.sources[0]["type"] == "daq_governed_claim"
    assert result.sources[0]["release_id"] == release_id
    assert result.sources[0]["source_refs"] == [SOURCE_REF]
    missing = box.dispatch("lookup_spec", {"entity": "EGO 1600", "field": "fps"})
    assert missing.status == "not_found"
    assert missing.content["matched_requirement_ids"] == ["wrong-field"]
    assert box.dispatch("lookup_spec", {"entity": "another device", "field": "fps"}).content[
        "matched_requirement_ids"] == []
    channel = DaqToolBox(knowledge=view, role="channel", requirements=requirements)
    assert channel.dispatch("lookup_spec", {"entity": "EGO 1600", "field": "resolution"}).status == "not_found"
    assert channel.dispatch("lookup_spec", {"entity": "EGO 1600", "field": "resolution"}).sources == []


def test_spec_tool_distinguishes_unpublished_field_from_unknown_product(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role='internal_fae')
    missing_field = box.dispatch('lookup_spec', {
        'entity': 'EGO 1600', 'field': 'rgb_resolution'})
    assert missing_field.status == 'not_found'
    assert missing_field.content['reason'] == 'reviewed_field_unavailable'
    assert missing_field.content['available_fields'] == ['resolution']
    unknown_product = box.dispatch('lookup_spec', {
        'entity': 'WristCam', 'field': 'rgb_resolution'})
    assert unknown_product.content['reason'] == 'reviewed_evidence_unavailable'
    hidden = DaqToolBox(knowledge=load_fixture_active(tmp_path), role='channel')
    hidden_result = hidden.dispatch('lookup_spec', {
        'entity': 'EGO 1600', 'field': 'rgb_resolution'})
    assert hidden_result.content['reason'] == 'reviewed_evidence_unavailable'
    assert 'available_fields' not in hidden_result.content


def test_source_bound_entity_alias_resolves_and_obeys_role_visibility(tmp_path):
    rows = _records()
    rows[0]['data']['aliases'] = ['EGO 双目']
    rows[0]['fact_review']['record_sha256'] = record_fingerprint(rows[0])
    rows[0]['access_review']['record_sha256'] = access_fingerprint(rows[0])
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    view = load_fixture_active(tmp_path)
    box = DaqToolBox(knowledge=view, role='internal_fae')
    assert {row['id'] for row in box.dispatch(
        'resolve_entity', {'text': 'EGO 双目'}).content['matches']} == {'entity:ego-1600'}
    assert box.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'resolution'}).status == 'ok'
    assert box.dispatch('resolve_entity', {'text': 'EGO 三目'}).status == 'not_found'
    assert box.dispatch('lookup_spec', {
        'entity': 'EGO 三目', 'field': 'resolution'}).status == 'not_found'
    hidden = DaqToolBox(knowledge=view, role='channel')
    assert hidden.dispatch('resolve_entity', {'text': 'EGO 双目'}).status == 'not_found'
    assert hidden.dispatch('lookup_spec', {
        'entity': 'EGO 双目', 'field': 'resolution'}).status == 'not_found'


def test_entity_alias_changes_require_review_and_cannot_cross_products(tmp_path):
    rows = _records()
    rows[0]['data']['aliases'] = ['EGO 双目']
    with pytest.raises(ValueError):
        publish_release(tmp_path / 'stale', SNAPSHOT, rows, None, RELEASE_REVIEW)
    rows[0]['fact_review']['record_sha256'] = record_fingerprint(rows[0])
    rows[0]['access_review']['record_sha256'] = access_fingerprint(rows[0])
    other = _row('entity:other', 'entity', {
        'name': 'Other', 'entity_type': 'device', 'aliases': ['EGO 双目']})
    with pytest.raises(ValueError):
        publish_release(tmp_path / 'collision', SNAPSHOT, [*rows, other],
                        None, RELEASE_REVIEW)


def test_runtime_rejects_malformed_or_colliding_reviewed_aliases(tmp_path):
    rows = _records()
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    manifest = load_fixture_active(tmp_path).manifest
    for invalid_aliases in ('EGO 双目', ['EGO 1600', 'EGO 双目'],
                            ['EGO 双目', 'EGO双目']):
        changed = deepcopy(manifest)
        row = next(item for item in changed['records']
                   if item['id'] == 'entity:ego-1600')
        row['data']['aliases'] = invalid_aliases
        row['fact_review']['record_sha256'] = record_fingerprint(row)
        row['access_review']['record_sha256'] = access_fingerprint(row)
        with pytest.raises(ValueError):
            ReviewedKnowledge.from_manifest(release_id, changed)
    active = read_active_release(Path(__file__).resolve().parents[1]
                                 / 'data/knowledge/published')
    changed = deepcopy(active['manifest'])
    other = next(item for item in changed['records']
                 if item['id'] == 'entity:ego-pro')
    other['data']['aliases'] = ['EGO 双目']
    other['fact_review']['record_sha256'] = record_fingerprint(other)
    other['access_review']['record_sha256'] = access_fingerprint(other)
    first = next(item for item in changed['records']
                 if item['id'] == 'entity:ego-1600')
    first['data']['aliases'] = ['EGO 双目']
    first['fact_review']['record_sha256'] = record_fingerprint(first)
    first['access_review']['record_sha256'] = access_fingerprint(first)
    with pytest.raises(ValueError, match='alias collision'):
        ReviewedKnowledge.from_manifest(active['release_id'], changed)
    changed = deepcopy(active['manifest'])
    for record_id, alias in [('entity:ego-pro', 'HUB 2×2'),
                             ('entity:hub', 'HUB 2x2')]:
        row = next(item for item in changed['records'] if item['id'] == record_id)
        row['data']['aliases'] = [alias]
        row['fact_review']['record_sha256'] = record_fingerprint(row)
        row['access_review']['record_sha256'] = access_fingerprint(row)
    forged = ReviewedKnowledge(active['release_id'], changed)
    assert DaqToolBox(knowledge=forged, role='internal_fae').dispatch(
        'lookup_spec', {'entity': 'HUB 2x2', 'field': 'rgb_camera_count'}
    ).content['reason'] == 'ambiguous_entity_identity'
    with pytest.raises(ValueError, match='alias collision'):
        ReviewedKnowledge.from_manifest(active['release_id'], changed)
    changed = deepcopy(active['manifest'])
    first = next(item for item in changed['records']
                 if item['id'] == 'entity:ego-1600')
    first['data']['aliases'] = ['entity:ego-1920']
    first['fact_review']['record_sha256'] = record_fingerprint(first)
    first['access_review']['record_sha256'] = access_fingerprint(first)
    with pytest.raises(ValueError, match='alias collision'):
        ReviewedKnowledge.from_manifest(active['release_id'], changed)
    changed = deepcopy(active['manifest'])
    other = next(item for item in changed['records']
                 if item['id'] == 'entity:ego-pro')
    other['data']['aliases'] = ['HUB']
    other['fact_review']['record_sha256'] = record_fingerprint(other)
    other['access_review']['record_sha256'] = access_fingerprint(other)
    with pytest.raises(ValueError, match='alias collision'):
        ReviewedKnowledge.from_manifest(active['release_id'], changed)


def test_spec_tool_resolves_scoped_alias_for_canonical_entity_id(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    view = load_fixture_active(tmp_path)
    manifest = deepcopy(view.manifest)
    manifest['review']['operator_release'] = {
        'scoped_field_aliases': {'entity:ego-1600': {'pixel_total': 'resolution'}}}
    alias_view = ReviewedKnowledge(view.release_id, manifest)
    box = DaqToolBox(knowledge=alias_view, role='internal_fae')
    for entity in ('entity:ego-1600', 'ENTITY:EGO-1600'):
        result = box.dispatch('lookup_spec', {'entity': entity, 'field': 'pixel_total'})
        assert result.status == 'ok'
        assert result.content['matches'][0]['data']['value'] == '1600x1200'


def test_spec_tool_does_not_report_selector_scoped_conflict_as_absent(tmp_path):
    from daq_fae.knowledge.conflict_visibility import conflict_notice_fingerprint
    from test_section_tools import make_view

    view = make_view(tmp_path)
    manifest = deepcopy(view.manifest)
    entity = next(row for row in manifest['records'] if row['id'] == 'entity:ego-1600')
    entity['scope']['required_selectors'] = ['variant']
    conflict = next(row for row in manifest['records'] if row['id'] == 'claim:conflict')
    conflict['scope']['required_selectors'] = ['variant']
    conflict['conflict_notice_review'] = {
        'reviewer': 'synthetic-permission-owner', 'reviewed_at': '2026-10-10',
        'view_roles': ['internal_fae'], 'statement': 'conflict_exists',
    }
    conflict['conflict_notice_review']['sha256'] = conflict_notice_fingerprint(conflict)
    scoped_view = ReviewedKnowledge(view.release_id, manifest)
    result = DaqToolBox(knowledge=scoped_view, role='internal_fae').dispatch(
        'lookup_spec', {'entity': 'EGO 1600', 'field': 'conflict_only'})
    assert result.status == 'not_found'
    assert result.content['reason'] != 'reviewed_field_unavailable'


def test_link_needs_forward_permission_in_all_tool_paths(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae")
    for tool in ("official_links", "search_knowledge"):
        result = box.dispatch(tool, {"query": "example.com/ego"})
        assert result.status == "not_found"
        assert result.sources == []
        assert "example.com" not in str(result.content)


def test_forwardable_link_is_delivered_only_by_link_tool(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT,
                                 _records(link_forward=("internal_fae",)),
                                 None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae")
    assert box.dispatch("official_links", {"query": "example.com/ego"}).status == "ok"
    assert box.dispatch("search_knowledge", {"query": "example.com/ego"}).status == "not_found"


def test_lookup_spec_requires_scope_when_verified_values_differ(tmp_path):
    rows = _records()
    other = _row("claim:resolution-other", "claim", {
        "entity_id": "entity:ego-1600", "field": "resolution",
        "value": "1920x1200", "unit": "px", "conditions": {},
    })
    other["scope"] = {"variant": "1920"}
    other["fact_review"]["record_sha256"] = record_fingerprint(other)
    other["access_review"]["record_sha256"] = access_fingerprint(other)
    rows.append(other)
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae")
    assert box.dispatch("lookup_spec", {"entity": "EGO 1600", "field": "resolution"}).status == "not_found"
    selected = box.dispatch("lookup_spec", {
        "entity": "EGO 1600", "field": "resolution", "conditions": {"variant": "1600"},
    })
    assert selected.status == "ok"
    assert [row["data"]["value"] for row in selected.content["matches"]] == ["1600x1200"]


def test_shared_product_name_requires_explicit_resolution_variant(tmp_path):
    rows = []
    for variant, fov in (("1600x1200", 165), ("1920x1200", 149)):
        suffix = variant.split("x")[0]
        scope = {"product": "ego", "resolution_variant": variant,
                 "required_selectors": ["resolution_variant"]}
        entity = _row(f"entity:ego-{suffix}", "entity",
                      {"name": "EGO", "entity_type": "variant"})
        claim = _row(f"claim:ego-{suffix}-fov", "claim", {
            "entity_id": entity["id"], "field": "horizontal_fov", "value": fov,
            "unit": "deg", "conditions": {},
        })
        for row in (entity, claim):
            row["scope"] = deepcopy(scope)
            row["fact_review"]["record_sha256"] = record_fingerprint(row)
            row["access_review"]["record_sha256"] = access_fingerprint(row)
            rows.append(row)
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    requirements = [
        {"id": "generic", "capability": "lookup_spec", "field": "horizontal_fov",
         "entities": ["EGO"], "conditions": {}},
        {"id": "selected", "capability": "lookup_spec", "field": "horizontal_fov",
         "entities": ["EGO"], "conditions": {"resolution_variant": "1600x1200"}},
    ]
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path),
                     role="internal_fae", requirements=requirements)
    generic = box.dispatch("lookup_spec", {"entity": "EGO", "field": "horizontal_fov"})
    assert generic.status == "not_found"
    selected = box.dispatch("lookup_spec", {
        "entity": "EGO", "field": "horizontal_fov",
        "conditions": {"resolution_variant": "1600x1200"},
    })
    assert [row["data"]["value"] for row in selected.content["matches"]] == [165]
    assert selected.content["matched_requirement_ids"] == ["selected"]
    search = box.dispatch("search_knowledge", {"query": "EGO"})
    assert all(row["kind"] != "claim" for row in search.content["matches"])
    single_root = tmp_path / "single"
    single_release = publish_release(single_root, SNAPSHOT, rows[:2], None, RELEASE_REVIEW)
    activate_release(single_root, single_release)
    single = DaqToolBox(knowledge=load_fixture_active(single_root),
                        role="internal_fae")
    assert single.dispatch("lookup_spec", {
        "entity": "EGO", "field": "horizontal_fov",
    }).status == "not_found"
    planned = prepare_turn("设备是 EGO；分辨率版本是 1600×1200；水平视场角是多少？")
    planned_box = DaqToolBox(knowledge=load_fixture_active(tmp_path),
                             role="internal_fae", requirements=planned.requirements)
    planned_result = planned_box.dispatch("lookup_spec", {
        "entity": "EGO", "field": "horizontal_fov",
        "conditions": {"resolution_variant": "1600x1200"},
    })
    assert planned_result.content["matched_requirement_ids"] == [
        next(item["id"] for item in planned.requirements
             if item["capability"] == "lookup_spec")
    ]
    broad = prepare_turn("设备是 EGO；分辨率版本是 1600×1200；规格是什么？")
    broad_box = DaqToolBox(knowledge=load_fixture_active(tmp_path),
                           role="internal_fae", requirements=broad.requirements)
    broad_result = broad_box.dispatch("lookup_spec", {
        "entity": "EGO", "field": "horizontal_fov",
        "conditions": {"resolution_variant": "1600x1200"},
    })
    assert broad_result.content["matched_requirement_ids"] == []


def test_query_only_experience_cannot_expose_variant_scoped_claim(tmp_path):
    entity = _row("entity:ego-1600", "entity",
                  {"name": "EGO", "entity_type": "variant"})
    claim = _row("claim:ego-experience", "claim", {
        "entity_id": entity["id"], "field": "experience",
        "value": "EGO variant observation", "unit": "note", "conditions": {},
    })
    for row in (entity, claim):
        row["scope"] = {"product": "ego", "resolution_variant": "1600x1200",
                        "required_selectors": ["resolution_variant"]}
        row["fact_review"]["record_sha256"] = record_fingerprint(row)
        row["access_review"]["record_sha256"] = access_fingerprint(row)
    release_id = publish_release(tmp_path, SNAPSHOT, [entity, claim], None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae")
    assert box.dispatch("experience", {"query": "EGO"}).status == "not_found"


def test_software_support_accepts_explicit_variant_selector(tmp_path):
    entity = _row("entity:ego-1600", "entity",
                  {"name": "EGO", "entity_type": "variant"})
    software = _row("software:ego-viewer-1600", "software", {
        "entity_id": entity["id"], "hardware_revision": "A", "platform": "Windows",
        "connection_mode": "USB", "software": "EgoViewer", "version": "2.0",
        "capability": "record", "evidence_level": "end_to_end_verified",
    })
    for row in (entity, software):
        row["scope"] = {"product": "ego", "resolution_variant": "1600x1200",
                        "required_selectors": ["resolution_variant"]}
        row["fact_review"]["record_sha256"] = record_fingerprint(row)
        row["access_review"]["record_sha256"] = access_fingerprint(row)
    release_id = publish_release(tmp_path, SNAPSHOT, [entity, software], None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae")
    args = {"entity": "EGO", "software": "EgoViewer", "platform": "Windows",
            "version": "2.0", "hardware_revision": "A", "connection_mode": "USB",
            "capability": "record"}
    assert box.dispatch("check_software_support", args).status == "not_found"
    assert box.dispatch("check_software_support", {
        **args, "conditions": {"resolution_variant": "1600x1200"},
    }).status == "ok"
    schema = next(item["function"] for item in box.tool_schemas()
                  if item["function"]["name"] == "check_software_support")
    assert "conditions" in schema["parameters"]["properties"]


def test_local_dev_app_refuses_nonempty_immutable_release(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    release_id, verifier = approve_fixture_for_app(tmp_path)
    with pytest.raises(ValueError, match='requires_authenticated_mode'):
        create_app(provider_mode="offline", knowledge_release_root=tmp_path,
                   knowledge_approval_verifier=verifier, state_db_path=tmp_path / "state.sqlite3")


def test_authenticated_app_rejects_real_release_without_role_contract(tmp_path, monkeypatch):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    monkeypatch.setenv("DAQ_PLATFORM_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("DAQ_DATABASE_URL", "postgresql://placeholder/daq")
    monkeypatch.setenv("DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE", str(tmp_path / "keyring.json"))
    _, verifier = approve_fixture_for_app(tmp_path)
    with pytest.raises(ValueError, match="role_contract"):
        create_app(provider_mode="offline", knowledge_release_root=tmp_path, knowledge_approval_verifier=verifier)


def test_release_reader_rejects_linked_root_and_malformed_roles(tmp_path):
    release_id = publish_release(tmp_path / "real", SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path / "real", release_id)
    (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        load_fixture_active(tmp_path / "linked")
    manifest = load_fixture_active(tmp_path / "real").manifest
    manifest["records"][0]["access_review"]["view_roles"] = [["internal_fae"]]
    with pytest.raises(ValueError, match="review"):
        ReviewedKnowledge.from_manifest(release_id, manifest)


def test_release_reader_rejects_unknown_record_kind_and_unsafe_source_path(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    manifest = load_fixture_active(tmp_path).manifest
    bad = deepcopy(manifest)
    bad["records"][0]["kind"] = "unreviewed_note"
    with pytest.raises(ValueError, match="record"):
        ReviewedKnowledge.from_manifest(release_id, bad)
    bad = deepcopy(manifest)
    bad["sources"][0]["path"] = "../private/spec.md"
    with pytest.raises(ValueError, match="source"):
        ReviewedKnowledge.from_manifest(release_id, bad)


def test_unresolved_or_platform_hinted_scope_cannot_satisfy_evidence(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    view = load_fixture_active(tmp_path)
    requirements = [
        {"id": "unresolved", "capability": "lookup_spec", "field": "resolution",
         "entities": [], "conditions": {}, "reason": "intent_or_entity_not_grounded"},
        {"id": "hinted", "capability": "lookup_spec", "field": "resolution",
         "entities": ["EGO 1600"], "conditions": {"variant": "1600"},
         "conditions_authority": "platform_context_unverified"},
    ]
    box = DaqToolBox(knowledge=view, role="internal_fae", requirements=requirements)
    result = box.dispatch("lookup_spec", {
        "entity": "EGO 1600", "field": "resolution", "conditions": {"variant": "1600"},
    })
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []


def test_non_link_record_cannot_embed_unreviewed_url(tmp_path):
    rows = _records()
    software = _row("software:viewer", "software", {
        "entity_id": "entity:ego-1600", "hardware_revision": "A",
        "platform": "Windows", "connection_mode": "USB", "software": "EgoViewer",
        "version": "2.0", "capability": "record",
        "evidence_level": "end_to_end_verified",
        "url": "https://example.com/private-download",
    })
    rows.append(software)
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    with pytest.raises(ValueError, match="URL"):
        activate_release(tmp_path, release_id)
    assert load_fixture_active(tmp_path) is None
    import json
    manifest = json.loads((tmp_path / "releases" / release_id / "manifest.json").read_text())
    with pytest.raises(ValueError, match="URL"):
        ReviewedKnowledge.from_manifest(release_id, manifest)


def test_non_link_record_cannot_embed_protocol_relative_delivery_link(tmp_path):
    rows = _records()
    rows[1]["data"]["value"] = "//example.com/private?token=abc"
    rows[1]["fact_review"]["record_sha256"] = record_fingerprint(rows[1])
    rows[1]["access_review"]["record_sha256"] = access_fingerprint(rows[1])
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    with pytest.raises(ValueError, match="URL"):
        activate_release(tmp_path, release_id)
    assert load_fixture_active(tmp_path) is None
    import json
    manifest = json.loads((tmp_path / "releases" / release_id / "manifest.json").read_text())
    with pytest.raises(ValueError, match="URL"):
        ReviewedKnowledge.from_manifest(release_id, manifest)


@pytest.mark.parametrize("value", ["www.example.com/private", "example.com/private?token=abc",
                                     "www.example.com", "example.com?token=abc"])
def test_non_link_record_cannot_embed_bare_delivery_link(tmp_path, value):
    rows = _records()
    rows[1]["data"]["value"] = value
    rows[1]["fact_review"]["record_sha256"] = record_fingerprint(rows[1])
    rows[1]["access_review"]["record_sha256"] = access_fingerprint(rows[1])
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    with pytest.raises(ValueError, match="URL"):
        activate_release(tmp_path, release_id)
    assert load_fixture_active(tmp_path) is None
    import json
    manifest = json.loads((tmp_path / "releases" / release_id / "manifest.json").read_text())
    with pytest.raises(ValueError, match="URL"):
        ReviewedKnowledge.from_manifest(release_id, manifest)


def test_non_link_record_cannot_hide_delivery_link_in_data_key(tmp_path):
    rows = _records()
    rows[1]["data"]["//example.com/private?token=abc"] = True
    rows[1]["fact_review"]["record_sha256"] = record_fingerprint(rows[1])
    rows[1]["access_review"]["record_sha256"] = access_fingerprint(rows[1])
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    with pytest.raises(ValueError, match="URL"):
        activate_release(tmp_path, release_id)
    assert load_fixture_active(tmp_path) is None
    import json
    manifest = json.loads((tmp_path / "releases" / release_id / "manifest.json").read_text())
    with pytest.raises(ValueError, match="URL"):
        ReviewedKnowledge.from_manifest(release_id, manifest)


def test_conflict_candidate_pdf_source_refs_are_provenance_not_links(tmp_path):
    snapshot = deepcopy(SNAPSHOT)
    pdf_path = "specs/EG-DB-v1.0.pdf"
    snapshot["sources"][0].update(path=pdf_path, kind="pdf")
    snapshot["chunks"][0]["source_path"] = pdf_path
    ref = deepcopy(SOURCE_REF)
    ref["path"] = pdf_path
    second_ref = deepcopy(ref)
    second_ref["path"] = "specs/EG-DB-v1.1.pdf"
    second_ref["sha256"] = "b" * 64
    second_ref["locator"] = {"kind": "lines", "start": 3, "end": 4}
    snapshot["sources"].append({"path": second_ref["path"],
                                "sha256": second_ref["sha256"], "size": 20, "kind": "pdf"})
    snapshot["chunks"].append({"source_path": second_ref["path"],
                               "source_sha256": second_ref["sha256"],
                               "locator": second_ref["locator"], "text": "another value"})
    row = {"id": "claim:rgb-camera-count", "kind": "claim", "status": "conflict",
           "scope": {"product": "eg-db"}, "source_refs": [ref],
           "data": {"entity_id": "entity:eg-db", "field": "rgb_camera_count",
                    "unit": "camera", "conditions": {}, "candidates": [
                        {"value": 2, "source_ref": deepcopy(ref)},
                        {"value": 3, "source_ref": second_ref},
                    ]}}
    release_id = publish_release(tmp_path, snapshot, [row], None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    view = load_fixture_active(tmp_path)
    assert view.records_for("internal_fae") == []
    assert view.manifest["records"][0]["data"]["candidates"][0]["source_ref"] == ref
    assert view.manifest["records"][0]["data"]["candidates"][1]["source_ref"] == second_ref
    tampered = deepcopy(view.manifest)
    tampered["records"][0]["data"]["candidates"][1]["source_ref"]["path"] = \
        "www.example.com/private"
    with pytest.raises(ValueError, match="source reference"):
        ReviewedKnowledge.from_manifest(release_id, tampered)


def test_conflict_candidate_value_cannot_hide_unreviewed_url(tmp_path):
    snapshot = deepcopy(SNAPSHOT)
    pdf_path = "specs/EG-DB-v1.0.pdf"
    snapshot["sources"][0].update(path=pdf_path, kind="pdf")
    snapshot["chunks"][0]["source_path"] = pdf_path
    ref = deepcopy(SOURCE_REF)
    ref["path"] = pdf_path
    row = {"id": "claim:rgb-camera-count", "kind": "claim", "status": "conflict",
           "scope": {"product": "eg-db"}, "source_refs": [ref],
           "data": {"entity_id": "entity:eg-db", "field": "rgb_camera_count",
                    "unit": "camera", "conditions": {}, "candidates": [
                        {"value": "https://example.com/private", "source_ref": deepcopy(ref)},
                        {"value": 3, "source_ref": deepcopy(ref)},
                    ]}}
    release_id = publish_release(tmp_path, snapshot, [row], None, RELEASE_REVIEW)
    with pytest.raises(ValueError, match="URL"):
        activate_release(tmp_path, release_id)
    assert load_fixture_active(tmp_path) is None
    import json
    manifest = json.loads((tmp_path / "releases" / release_id / "manifest.json").read_text())
    with pytest.raises(ValueError, match="URL"):
        ReviewedKnowledge.from_manifest(release_id, manifest)


def test_procedure_and_software_require_exact_product_topology_and_applicability(tmp_path):
    rows = _records()
    rows.append(_row("topology:ego-single", "topology", {
        "members": ["entity:ego-1600"], "roles": {}, "connections": [],
        "power": {}, "platform": "Windows", "sync_target": "none", "storage": "TF",
    }))
    rows.append(_row("procedure:record", "procedure", {
        "task": "record", "topology_id": "topology:ego-single", "prerequisites": [],
        "steps": ["start"], "checks": ["file"], "failure_branches": [],
    }))
    rows.append(_row("software:viewer", "software", {
        "entity_id": "entity:ego-1600", "hardware_revision": "A",
        "platform": "Windows", "connection_mode": "USB", "software": "EgoViewer",
        "version": "2.0", "capability": "record",
        "evidence_level": "end_to_end_verified",
    }))
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae",
                     requirements=[{"id": "record", "capability": "lookup_procedure",
                                    "entities": [], "conditions": {}}])
    assert box.dispatch("lookup_procedure", {"task": "record", "entity": "other-product"}).status == "not_found"
    procedure = box.dispatch("lookup_procedure", {"task": "record", "entity": "EGO 1600"})
    assert procedure.status == "ok"
    assert procedure.content["matched_requirement_ids"] == []
    request = {"entity": "EGO 1600", "software": "EgoViewer", "platform": "Windows",
               "version": "2.0", "hardware_revision": "B", "connection_mode": "Wi-Fi",
               "capability": "record"}
    assert box.dispatch("check_software_support", request).status == "not_found"
    request.update({"hardware_revision": "A", "connection_mode": "USB"})
    assert box.dispatch("check_software_support", request).status == "ok"


def test_exact_user_software_scope_can_cover_matching_requirement(tmp_path):
    rows = _records()
    rows.append(_row("software:viewer", "software", {
        "entity_id": "entity:ego-1600", "hardware_revision": "A",
        "platform": "Windows", "connection_mode": "USB", "software": "EgoViewer",
        "version": "2.0", "capability": "record",
        "evidence_level": "end_to_end_verified",
    }))
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    plan = prepare_turn("设备是 EGO 1600，硬件修订是 A，连接方式是 USB，平台是 Windows，Viewer 2.0 的录制兼容吗？")
    requirement = next(row for row in plan.requirements
                       if row["capability"] == "check_software_support")
    assert requirement["conditions"]["connection"] == "USB"
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae",
                     requirements=plan.requirements)
    result = box.dispatch("check_software_support", {
        "entity": "EGO 1600", "software": "EgoViewer", "platform": "Windows",
        "version": "2.0", "hardware_revision": "A", "connection_mode": "USB",
        "capability": "record",
    })
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == [requirement["id"]]


def test_broad_software_question_is_not_covered_by_recording_only_evidence(tmp_path):
    rows = _records()
    rows.append(_row("software:viewer", "software", {
        "entity_id": "entity:ego-1600", "hardware_revision": "A",
        "platform": "Windows", "connection_mode": "USB", "software": "EgoViewer",
        "version": "2.0", "capability": "record", "evidence_level": "device_tested",
    }))
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    plan = prepare_turn("设备是 EGO 1600，硬件修订是 A，连接方式是 USB，平台是 Windows，Viewer 2.0 兼容吗？")
    requirement = next(row for row in plan.requirements
                       if row["capability"] == "check_software_support")
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae",
                     requirements=plan.requirements)
    result = box.dispatch("check_software_support", {
        "entity": "EGO 1600", "software": "EgoViewer", "platform": "Windows",
        "version": "2.0", "hardware_revision": "A", "connection_mode": "USB",
        "capability": "record",
    })
    assert result.status == "ok"
    assert requirement["id"] not in result.content["matched_requirement_ids"]


def test_viewer_record_cannot_certify_separate_sdk_version(tmp_path):
    rows = _records()
    rows.append(_row("software:viewer", "software", {
        "entity_id": "entity:ego-1600", "hardware_revision": "A",
        "platform": "Windows", "connection_mode": "USB", "software": "EgoViewer",
        "version": "2.0", "capability": "record",
        "evidence_level": "end_to_end_verified",
    }))
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae",
                     requirements=[{"id": "combo", "capability": "check_software_support",
                                    "entities": ["EGO 1600"], "software": "viewer",
                                    "conditions": {"platform": "Windows", "variant": "A",
                                                   "connection": "USB", "task": ["recording"],
                                                   "viewer_version": "2.0",
                                                   "sdk_version": "2.0"}}])
    result = box.dispatch("check_software_support", {
        "entity": "EGO 1600", "software": "EgoViewer", "platform": "Windows",
        "version": "2.0", "hardware_revision": "A", "connection_mode": "USB",
        "capability": "record",
    })
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []


def test_generic_search_does_not_certify_unresolved_product_evidence(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae",
                     requirements=[{"id": "search", "capability": "search_knowledge",
                                    "entities": [], "conditions": {}}])
    result = box.dispatch("search_knowledge", {"query": "resolution"})
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []


def test_entity_identity_does_not_certify_platform_filtered_catalog(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, [_records()[0]], None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    plan = prepare_turn("平台是 Linux，有哪些型号可用？")
    box = DaqToolBox(knowledge=load_fixture_active(tmp_path), role="internal_fae",
                     requirements=plan.requirements)
    result = box.dispatch("catalog", {"query": ""})
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []
    result = box.dispatch("resolve_entity", {"text": "EGO"})
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []
