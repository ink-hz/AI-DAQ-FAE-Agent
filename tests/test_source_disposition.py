import copy
import json
from pathlib import Path

import pytest

from daq_fae.knowledge.source_disposition import build_inventory, write_inventory


def snapshot():
    return {'format_version': 1, 'extractor_version': '1', 'archive_manifest_sha256': 'a'*64,
            'sources': [
                {'path': 'guide.md', 'sha256': 'b'*64, 'size': 10, 'kind': 'markdown', 'extraction_status': 'extracted', 'modified_at_utc': 'date'},
                {'path': 'other/guide.md', 'sha256': 'b'*64, 'size': 10, 'kind': 'markdown', 'extraction_status': 'extracted', 'modified_at_utc': 'date'},
                {'path': 'image.png', 'sha256': 'c'*64, 'size': 20, 'kind': 'asset', 'extraction_status': 'metadata_only', 'modified_at_utc': 'date'}],
            'chunks': [{'source_path': p, 'source_sha256': 'b'*64, 'locator': {'kind': 'lines', 'start': 1, 'end': 2}, 'text': 'private content'} for p in ['guide.md', 'other/guide.md']]}


def decisions():
    return {'archive_manifest_sha256': 'a'*64, 'extractor_version': '1', 'text': [
        {'path': p, 'sha256': 'b'*64, 'status': 'needs_fact_adjudication', 'reason': 'scope unresolved', 'owner': 'FAE reviewer', 'reviewer': 'Codex', 'reviewed_on': '2026-10-10', 'evidence_locators': [{'kind': 'lines', 'start': 1, 'end': 2}], 'update_relation': {'status': 'unresolved', 'reason': 'No supersession evidence'}} for p in ['guide.md', 'other/guide.md']]}


def test_complete_partition_and_same_bytes_preserve_identity_without_text():
    result = build_inventory(snapshot(), decisions())
    assert len(result['text_dispositions']) == 2
    assert len(result['asset_inventory']) == 1
    assert result['text_dispositions'][0]['same_bytes_sources'] == ['other/guide.md']
    assert result['asset_inventory'][0]['authorization_status'] == 'pending_review'
    assert result['asset_inventory'][0]['default_text_index'] is False
    assert 'private content' not in json.dumps(result)
    assert snapshot()['chunks'][0]['text'] == 'private content'


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'hash', 'locator', 'curated', 'relation'])
def test_reject_incomplete_stale_or_unsubstantiated_judgments(mutation):
    plan = decisions()
    if mutation == 'missing': plan['text'].pop()
    if mutation == 'duplicate': plan['text'].append(copy.deepcopy(plan['text'][0]))
    if mutation == 'hash': plan['text'][0]['sha256'] = 'z'*64
    if mutation == 'locator': plan['text'][0]['evidence_locators'][0]['end'] = 99
    if mutation == 'curated': plan['text'][0]['status'] = 'curated'
    if mutation == 'relation': plan['text'][0]['update_relation']['status'] = 'superseded'
    with pytest.raises(ValueError): build_inventory(snapshot(), plan)


def test_asset_classification_is_metadata_and_never_compatibility():
    source = snapshot()
    source['sources'][2]['path'] = 'firmware.bin'
    result = build_inventory(source, decisions())['asset_inventory'][0]
    assert result['category'] == 'firmware_binary_candidate'
    assert result['locator'] == {'kind': 'file'}
    assert result['compatibility_status'] == 'not_established'


def test_private_writer_refuses_overwrite_and_sets_permissions(tmp_path):
    result = build_inventory(snapshot(), decisions())
    output = tmp_path / 'curated'
    write_inventory(output, result)
    assert (output.stat().st_mode & 0o777) == 0o700
    assert ((output / 'source-disposition.json').stat().st_mode & 0o777) == 0o600
    with pytest.raises(FileExistsError): write_inventory(output, result)


def test_verified_inventory_rejects_changed_create_archive(tmp_path):
    from scripts.archive_candidate_snapshot import create_archive
    from daq_fae.knowledge.source_import import import_archive
    from daq_fae.knowledge.source_disposition import verified_inventory
    raw = tmp_path / 'raw'
    raw.mkdir()
    (raw / 'note.txt').write_text('candidate')
    archive = tmp_path / 'archive'
    manifest = create_archive(raw, archive, '2026-10-10')
    snap = import_archive(archive, manifest['manifest_sha256'])
    plan = {'archive_manifest_sha256': snap['archive_manifest_sha256'], 'extractor_version': '1', 'text': [dict(decisions()['text'][0], path='note.txt', sha256=snap['sources'][0]['sha256'], evidence_locators=[{'kind': 'lines', 'start': 1, 'end': 1}])]}
    assert verified_inventory(archive, snap, plan)['summary']['text'] == 1
    snap['chunks'][0]['text'] = 'changed'
    with pytest.raises(ValueError, match='differs'): verified_inventory(archive, snap, plan)


def test_reject_binary_chunk_and_unknown_or_missing_responsibility():
    snap = snapshot()
    snap['chunks'].append({'source_path': 'image.png', 'source_sha256': 'c'*64, 'locator': {'kind': 'file'}})
    with pytest.raises(ValueError): build_inventory(snap, decisions())
    plan = decisions()
    plan['text'][0]['owner'] = ''
    with pytest.raises(ValueError): build_inventory(snapshot(), plan)


def test_asset_purpose_links_only_explicit_document_reference():
    snap = snapshot()
    snap['chunks'][0]['text'] = '![connection diagram](image.png)'
    result = build_inventory(snap, decisions())['asset_inventory'][0]
    assert result['document_references'] == [{'source_path': 'guide.md', 'source_sha256': 'b'*64, 'locator': {'kind': 'lines', 'start': 1, 'end': 2}, 'label': 'connection diagram'}]
    assert result['content_review_status'] == 'not_reviewed'


def test_cli_rejects_public_output_before_reading_private_inputs(tmp_path):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, 'scripts/daq_source_disposition.py',
        '--archive', str(tmp_path / 'missing'), '--snapshot', str(tmp_path / 'missing'),
        '--decisions', str(tmp_path / 'missing'), '--output', str(tmp_path / 'public')],
        capture_output=True, text=True)
    assert result.returncode == 2
    assert 'data/knowledge/curated' in result.stderr
    assert not (tmp_path / 'public').exists()
