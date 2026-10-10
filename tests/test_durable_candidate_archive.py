import importlib.util
from pathlib import Path
import pytest
from scripts.archive_candidate_snapshot import create_archive, verify_archive

MODULE = Path(__file__).parents[1] / 'scripts/durable_candidate_archive.py'

def api():
    assert MODULE.exists(), 'durable transfer workflow missing'
    spec = importlib.util.spec_from_file_location('durable', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.fixture
def archive(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'sample.bin').write_bytes(b'original bytes')
    target = tmp_path / 'archive'
    return target, create_archive(source, target, '2026-09-20')

def evidence():
    return dict(format_version=1, target_id='controlled-archive-001', version_id='originals-v1',
                storage_independence_record='review-001', authorization_record='approval-001',
                permission_record='acl-review-001', read_only_record='retention-001',
                custodian='archive-owner', authorized_readers=['internal-fae'],
                retrieval_record='fresh-session-001', retrieval_environment_id='independent-reader-001')

def test_copy_preserves_manifest_and_does_not_claim_d1(archive, tmp_path):
    source, summary = archive
    target = tmp_path / 'remote-copy'
    result = api().copy_archive(source, target, summary['manifest_sha256'], evidence())
    assert (target / 'manifest.json').read_bytes() == (source / 'manifest.json').read_bytes()
    assert verify_archive(target, summary['manifest_sha256']) == summary
    assert result['status'] == 'copy_verified_pending_independent_retrieval'
    assert verify_archive(source, summary['manifest_sha256']) == summary

def test_retrieval_checks_all_bytes_and_has_no_private_paths(archive):
    source, summary = archive
    result = api().verify_retrieval(source, summary['manifest_sha256'], evidence())
    assert result['status'] == 'retrieved_bytes_verified_pending_review'
    assert result['files'] == 1
    assert str(source) not in str(result)
    assert 'sample.bin' not in str(result)

def test_missing_permission_evidence_blocks_copy_before_writes(archive, tmp_path):
    source, summary = archive
    target = tmp_path / 'remote-copy'
    facts = evidence()
    facts.pop('permission_record')
    with pytest.raises(ValueError, match='permission_record'):
        api().copy_archive(source, target, summary['manifest_sha256'], facts)
    assert not target.exists()

def test_bad_anchor_and_existing_destination_fail_closed(archive, tmp_path):
    source, summary = archive
    with pytest.raises(ValueError, match='manifest hash mismatch'):
        api().copy_archive(source, tmp_path / 'copy', '0' * 64, evidence())
    with pytest.raises(ValueError, match='destination already exists'):
        api().copy_archive(source, source, summary['manifest_sha256'], evidence())

def test_corruption_is_detected_on_retrieval(archive):
    source, summary = archive
    sample = source / 'files/sample.bin'
    sample.chmod(0o600)
    sample.write_bytes(b'changed bytes!')
    sample.chmod(0o400)
    with pytest.raises(ValueError, match='mismatch'):
        api().verify_retrieval(source, summary['manifest_sha256'], evidence())

def test_evidence_rejects_private_paths(archive):
    source, summary = archive
    facts = evidence()
    facts['permission_record'] = '/private/custody/acl.json'
    with pytest.raises(ValueError, match='permission_record'):
        api().verify_retrieval(source, summary['manifest_sha256'], facts)

def test_symlink_original_is_never_copied(archive, tmp_path):
    source, summary = archive
    link = tmp_path / 'linked'
    link.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match='real directory'):
        api().copy_archive(link, tmp_path / 'copy', summary['manifest_sha256'], evidence())
    assert not (tmp_path / 'copy').exists()
