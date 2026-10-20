import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from daq_fae.knowledge.records import access_fingerprint, record_fingerprint
from daq_fae.knowledge import releases


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'data/knowledge/local_archive/candidate-originals-v2-local-copy-20261010'
BASE = ROOT / 'tmp/daq-history-hotfix/main-preview/releases/25dabeaa34d64b8eae49ca714c63adf759c36fe1159736c65128cab3eb971af8/manifest.json'
CANDIDATES = ROOT / 'data/knowledge/review/e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56-k5-records-1a030e8e3e698d2a.json'
EXTENSION = ROOT / 'data/knowledge/curated/source-checked-extension-20261020/new-records.json'


def test_extension_binds_existing_release_source_and_dev_review(tmp_path):
    if not BASE.is_file() or not CANDIDATES.is_file() or not ARCHIVE.is_dir():
        pytest.skip('private source-reviewed release inputs absent')
    from daq_fae.knowledge.operator_release import stage_operator_spec_extension, verify_operator_manifest
    from daq_fae.knowledge.code_identity import daq_code_sha

    baseline = json.loads(BASE.read_text())
    baseline_id = hashlib.sha256(BASE.read_bytes()).hexdigest()
    root = tmp_path / 'published'
    destination = root / 'releases' / baseline_id
    destination.mkdir(parents=True)
    (destination / 'manifest.json').write_bytes(BASE.read_bytes())
    releases._write_active_pointer(root, baseline_id)
    row = deepcopy(next(row for row in json.loads(CANDIDATES.read_text())
                        if row['id'] == 'claim:eg-db-depth-resolution'))
    row['status'] = 'verified'
    row['fact_review'] = {'reviewer': 'Codex', 'reviewed_at': '2026-10-20'}
    row['fact_review']['record_sha256'] = record_fingerprint(row)
    row['access_review'] = {'reviewer': 'Codex', 'reviewed_at': '2026-10-20',
                            'view_roles': ['internal_fae'],
                            'forward_roles': ['internal_fae']}
    row['access_review']['record_sha256'] = access_fingerprint(row)
    ref = row['source_refs'][0]
    page = ref['locator']['page']
    source = ARCHIVE / 'files' / ref['path']
    page_text = subprocess.run(['pdftotext', '-f', str(page), '-l', str(page),
                                '-layout', str(source), '-'], check=True,
                               capture_output=True).stdout
    source_check = {'reviewer': 'Codex', 'entries': [{
        'record_id': row['id'], 'record_sha256': record_fingerprint(row),
        'source_ref': ref, 'page_text_sha256': hashlib.sha256(page_text).hexdigest(),
    }]}
    dev_review = {'environment': 'dev', 'review_method': 'codex',
                  'code_sha256': daq_code_sha(), 'cases': [{
                      'id': 'egdb_overview', 'trace_id': 'synthetic-dev',
                      'passed': True, 'answer': '1280x800', 'outcome': 'resolved',
                  }]}
    runtime = 'daq-' + daq_code_sha()[:12]
    upstream = baseline['review']['operator_release']['runtime']['upstream_sha']
    rid = stage_operator_spec_extension(
        root, archive=ARCHIVE, new_records=[row], source_check=source_check,
        dev_review=dev_review, runtime_release=runtime, upstream_sha=upstream)
    manifest, _ = releases._read_release(root, rid)
    assert manifest['record_count'] == baseline['record_count'] + 1
    verify_operator_manifest(manifest, runtime_release=runtime, upstream_sha=upstream)
    bad = deepcopy(manifest)
    next(item for item in bad['records'] if item['id'] == row['id'])['data']['value'] = 'bogus'
    with pytest.raises(ValueError):
        verify_operator_manifest(bad, runtime_release=runtime, upstream_sha=upstream)


def test_hub_strict_upper_bounds_are_explicit_in_reviewed_delta():
    if not EXTENSION.is_file():
        pytest.skip('private reviewed delta absent')
    rows = {row['id']: row for row in json.loads(EXTENSION.read_text())}
    for record_id in ('claim:hub-four-port-trigger-deviation',
                      'claim:hub-system-sync-accuracy'):
        assert rows[record_id]['data']['comparison'] == '<'
        assert rows[record_id]['fact_review']['record_sha256'] == record_fingerprint(
            rows[record_id])
