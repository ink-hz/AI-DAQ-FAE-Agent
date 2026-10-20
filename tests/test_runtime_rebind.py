"""A DAQ-only runtime release may inherit reviewed facts without changing them."""

from copy import deepcopy
import hashlib
import json

import pytest

from test_operator_pro_release import _proposal, _stage_v2


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def test_operator_runtime_rebind_keeps_exact_reviewed_content(tmp_path, monkeypatch):
    from daq_fae.knowledge import releases
    from daq_fae.knowledge.operator_release import stage_operator_runtime_rebind
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge

    root, archive, bundle, review, _previous = _proposal(tmp_path, monkeypatch)
    origin = _stage_v2(root, archive, bundle, review)
    releases._write_active_pointer(root, origin)
    evidence = {
        'reviewer': 'Codex', 'dev_preview_code_sha256': 'a' * 64,
        'single_results_sha256': 'b' * 64, 'single_review_sha256': 'c' * 64,
        'single_reviewed_turns': 19, 'single_unrun_cases': 36,
        'focus_results_sha256': 'd' * 64, 'focus_review_sha256': 'e' * 64,
        'focus_reviewed_turns': 3,
        'quality_gate_status': 'user_directed_partial_no_repeat',
    }
    rebound = stage_operator_runtime_rebind(
        root, origin_release=origin, runtime_release='daq-new',
        upstream_sha='f' * 40, evidence=evidence)
    manifest, _ = releases._read_release(root, rebound)
    old, _ = releases._read_release(root, origin)
    assert manifest['records'] == old['records']
    assert manifest['sources'] == old['sources']
    assert manifest['sections'] == old['sections']
    assert manifest['previous_release'] == origin
    releases._write_active_pointer(root, rebound)
    loaded = ReviewedKnowledge.load_active(
        root, runtime_release='daq-new', upstream_sha='f' * 40)
    assert loaded.release_id == rebound
    with pytest.raises(ValueError, match='runtime_identity'):
        ReviewedKnowledge.load_active(
            root, runtime_release='daq-wrong', upstream_sha='f' * 40)


def test_runtime_rebind_requires_coordinated_image_cutover(tmp_path, monkeypatch):
    from daq_fae.knowledge import releases
    from daq_fae.knowledge.operator_release import (
        activate_operator_release, rollback_operator_release,
        stage_operator_runtime_rebind,
    )

    root, archive, bundle, review, _previous = _proposal(tmp_path, monkeypatch)
    origin = _stage_v2(root, archive, bundle, review)
    releases._write_active_pointer(root, origin)
    evidence = {
        'reviewer': 'Codex', 'dev_preview_code_sha256': 'a' * 64,
        'single_results_sha256': 'b' * 64, 'single_review_sha256': 'c' * 64,
        'single_reviewed_turns': 19, 'single_unrun_cases': 36,
        'focus_results_sha256': 'd' * 64, 'focus_review_sha256': 'e' * 64,
        'focus_reviewed_turns': 3,
        'quality_gate_status': 'user_directed_partial_no_repeat',
    }
    rebound = stage_operator_runtime_rebind(
        root, origin_release=origin, runtime_release='daq-new',
        upstream_sha='f' * 40, evidence=evidence)
    with pytest.raises(ValueError, match='coordinated_image_cutover_required'):
        activate_operator_release(root, rebound, observe=lambda *_: None)
    assert json.loads((root / 'active.json').read_text())['release_id'] == origin
    releases._write_active_pointer(root, rebound)
    with pytest.raises(ValueError, match='cross_runtime_image_restore_required'):
        rollback_operator_release(root, observe=lambda *_: None)
    assert json.loads((root / 'active.json').read_text())['release_id'] == rebound


def test_runtime_rebind_rejects_task_mode_change(tmp_path, monkeypatch):
    from daq_fae.knowledge import releases
    from daq_fae.knowledge.operator_release import (
        stage_operator_runtime_rebind, _verify_runtime_rebind_manifest,
    )

    root, archive, bundle, review, _previous = _proposal(tmp_path, monkeypatch)
    origin = _stage_v2(root, archive, bundle, review)
    releases._write_active_pointer(root, origin)
    evidence = {
        'reviewer': 'Codex', 'dev_preview_code_sha256': 'a' * 64,
        'single_results_sha256': 'b' * 64, 'single_review_sha256': 'c' * 64,
        'single_reviewed_turns': 19, 'single_unrun_cases': 36,
        'focus_results_sha256': 'd' * 64, 'focus_review_sha256': 'e' * 64,
        'focus_reviewed_turns': 3,
        'quality_gate_status': 'user_directed_partial_no_repeat',
    }
    rebound = stage_operator_runtime_rebind(
        root, origin_release=origin, runtime_release='daq-new',
        upstream_sha='f' * 40, evidence=evidence)
    manifest, _ = releases._read_release(root, rebound)
    manifest['review']['operator_release']['runtime']['tasks_enabled'] = True
    with pytest.raises(ValueError, match='runtime_identity'):
        _verify_runtime_rebind_manifest(
            root, manifest, runtime_release='daq-new', upstream_sha='f' * 40)


def test_operator_runtime_rebind_rejects_changed_record(tmp_path, monkeypatch):
    from daq_fae.knowledge import releases
    from daq_fae.knowledge.operator_release import (
        stage_operator_runtime_rebind, _verify_runtime_rebind_manifest,
    )

    root, archive, bundle, review, _previous = _proposal(tmp_path, monkeypatch)
    origin = _stage_v2(root, archive, bundle, review)
    releases._write_active_pointer(root, origin)
    evidence = {
        'reviewer': 'Codex', 'dev_preview_code_sha256': 'a' * 64,
        'single_results_sha256': 'b' * 64, 'single_review_sha256': 'c' * 64,
        'single_reviewed_turns': 19, 'single_unrun_cases': 36,
        'focus_results_sha256': 'd' * 64, 'focus_review_sha256': 'e' * 64,
        'focus_reviewed_turns': 3,
        'quality_gate_status': 'user_directed_partial_no_repeat',
    }
    rebound = stage_operator_runtime_rebind(
        root, origin_release=origin, runtime_release='daq-new',
        upstream_sha='f' * 40, evidence=evidence)
    manifest, _ = releases._read_release(root, rebound)
    mutated = deepcopy(manifest)
    mutated['records'][0]['answerable'] = False
    from daq_fae.knowledge.adjudication import digest
    from daq_fae.knowledge.operator_release import _rebind_content
    mutated['review']['operator_release']['origin_content_sha256'] = digest(
        _rebind_content(mutated))
    with pytest.raises(ValueError, match='rebind_content'):
        _verify_runtime_rebind_manifest(
            root, mutated, runtime_release='daq-new', upstream_sha='f' * 40)


def test_sdk_runtime_rebind_keeps_records_notes_and_reviews(tmp_path):
    from daq_fae.knowledge import sdk_release
    from daq_fae.knowledge.code_identity import daq_code_sha
    from daq_fae.knowledge.sdk_catalog import _serialized as serialized_sdk

    root = tmp_path / 'sdk'
    records = [{
        'kb_id': 'daq-sdk-test', 'view_roles': ['internal_fae'],
        'source_refs': [{'package_sha256': '1' * 64, 'member_sha256': '2' * 64,
                         'member': 'README', 'start_line': 1, 'end_line': 2}],
    }]
    notes = [{
        'kb_id': 'daq-note-test', 'doc_type': 'release_note',
        'evidence_scope': 'release_note_statement_only', 'device_support': 'unknown',
        'view_roles': ['internal_fae'], 'file_sha256': '3' * 64,
        'source_refs': [{'package_sha256': '3' * 64, 'member_sha256': '3' * 64,
                         'span_sha256': '4' * 64, 'start_line': 1, 'end_line': 2}],
    }]
    origin_manifest = {
        'format_version': 'daq-sdk-published/v1',
        'archive_manifest_sha256': '5' * 64,
        'catalog_sha256': _sha(serialized_sdk(records)),
        'record_count': 1, 'records': records,
        'release_notes_sha256': _sha(sdk_release._serialized_notes(notes)),
        'release_notes': notes, 'daq_code_sha256': '6' * 64,
        'knowledge_release': '7' * 64,
        'runtime_release': 'daq-old', 'upstream_sha': '8' * 40,
        'dev_answer_review': {'reviewer': 'Codex'},
    }
    raw = sdk_release._json_bytes(origin_manifest)
    origin = _sha(raw)
    path = root / 'releases' / origin
    path.mkdir(parents=True)
    (path / 'manifest.json').write_bytes(raw)
    sdk_release.activate_sdk_release(root, origin)
    rebound = sdk_release.rebind_sdk_release(
        root, origin_release=origin, runtime_release='daq-new',
        upstream_sha='f' * 40, daq_code_sha256=daq_code_sha(),
        knowledge_release='9' * 64,
        evidence={'reviewer': 'Codex', 'focus_review_sha256': 'a' * 64,
                  'quality_gate_status': 'user_directed_partial_no_repeat'})
    current = sdk_release._read(root, rebound)
    assert current['records'] == records
    assert current['release_notes'] == notes
    assert current['dev_answer_review'] == origin_manifest['dev_answer_review']
    sdk_release.activate_sdk_release(root, rebound)
    assert sdk_release.load_active_sdk_release(
        root, runtime_release='daq-new', upstream_sha='f' * 40,
        daq_code_sha256=daq_code_sha(), knowledge_release='9' * 64).release_id == rebound
    altered = deepcopy(current)
    altered['records'][0]['kb_id'] = 'other'
    with pytest.raises(ValueError, match='rebind content'):
        sdk_release._verify_sdk_rebind(root, altered)
