"""Synthetic link delivery contracts; no real approvals or URLs."""
from copy import deepcopy
import pytest
from test_reviewed_knowledge import _records, SNAPSHOT, RELEASE_REVIEW
from daq_fae.knowledge.records import record_fingerprint, access_fingerprint, validate_records
from daq_fae.knowledge.releases import publish_release, activate_release
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.domain_tools import DaqToolBox


def reviewed_rows():
    rows = _records(link_forward=('internal_fae',))
    row = rows[-1]
    row['data']['page_evidence'] = {
        'title': 'Synthetic EGO documentation', 'version': 'not_stated',
        'captured_at': '2026-10-08', 'valid_until': '2099-12-31', 'snapshot_sha256': 'c' * 64,
        'sku_scope': deepcopy(row['scope']),
    }
    bind(row)
    return rows


def bind(row):
    row['fact_review']['record_sha256'] = record_fingerprint(row)
    row['access_review']['record_sha256'] = access_fingerprint(row)
    row['link_review']['record_sha256'] = record_fingerprint(row)


def manifest(rows):
    return {'format_version': 1, 'sources': SNAPSHOT['sources'], 'source_count': 1,
            'records': [{**r, 'answerable': True} for r in rows],
            'record_count': len(rows), 'answerable_count': len(rows)}


@pytest.mark.parametrize('damage', ['missing_page', 'missing_title', 'sku_mismatch',
    'missing_hash', 'missing_reviewer', 'missing_date', 'expired', 'redirect',
    'local_path', 'package_name', 'credentials', 'malformed_url', 'invalid_data'])
def test_invalid_link_rejected_by_offline_and_runtime_gates(damage):
    rows = reviewed_rows(); row = rows[-1]
    if damage == 'invalid_data': row['data'] = None
    elif damage == 'missing_page': row['data'].pop('page_evidence')
    elif damage == 'missing_title': row['data']['page_evidence']['title'] = ''
    elif damage == 'sku_mismatch': row['data']['page_evidence']['sku_scope'] = {'variant': 'other'}
    elif damage == 'missing_hash': row['data']['page_evidence']['snapshot_sha256'] = ''
    elif damage == 'missing_reviewer': row['link_review']['reviewer'] = ''
    elif damage == 'missing_date': row['link_review']['reviewed_at'] = 'invalid'
    elif damage == 'expired': row['data']['page_evidence']['valid_until'] = '2020-01-01'
    elif damage == 'redirect': row['link_review']['final_url'] = 'https://example.com/changed'
    else:
        row['data']['url'] = {'local_path': '/tmp/sdk.zip', 'package_name': 'SDK.zip',
            'credentials': 'https://secret@example.com/ego', 'malformed_url': 'https://['}[damage]
        row['link_review']['final_url'] = row['data']['url']
    bind(row)
    _, findings = validate_records(rows, SNAPSHOT)
    assert any(f['code'].startswith('link_') for f in findings)
    with pytest.raises(ValueError, match='link|URL|schema'):
        ReviewedKnowledge.from_manifest('a' * 64, manifest(rows))


def test_verified_link_returned_this_turn_only_to_current_authorized_role(tmp_path):
    rid = publish_release(tmp_path, SNAPSHOT, reviewed_rows(), None, RELEASE_REVIEW)
    activate_release(tmp_path, rid)
    view = ReviewedKnowledge.load_active(tmp_path)
    for role, expected in [('internal_fae', 'ok'), ('channel', 'not_found'), ('tmall_support', 'not_found')]:
        result = DaqToolBox(knowledge=view, role=role).dispatch('official_links', {'query': 'EGO'})
        assert result.status == expected
        from src.agent.loop.runtime import _verified_urls_from_payload
        assert _verified_urls_from_payload(result.content) == ({'https://example.com/ego'} if expected == 'ok' else set())
        assert ('https://example.com/ego' in str(result.content)) == (expected == 'ok')
    assert DaqToolBox(knowledge=view, role='internal_fae').dispatch('search_knowledge', {'query': 'https://example.com/ego'}).status == 'not_found'


def test_readme_candidate_never_delivered():
    rows = reviewed_rows(); row = rows[-1]
    row['status'] = 'candidate'; row['answerable'] = False
    m = manifest(rows); m['records'][-1]['answerable'] = False; m['answerable_count'] -= 1
    view = ReviewedKnowledge.from_manifest('a' * 64, m)
    result = DaqToolBox(knowledge=view, role='internal_fae').dispatch('official_links', {'query': 'EGO'})
    assert result.status == 'not_found'
    assert 'https://' not in str(result.content)


def test_page_snapshot_change_invalidates_existing_signatures():
    rows = reviewed_rows()
    rows[-1]['data']['page_evidence']['snapshot_sha256'] = 'd' * 64
    _, findings = validate_records(rows, SNAPSHOT)
    assert {'fact_review_stale', 'access_review_stale', 'link_review_stale'} <= {f['code'] for f in findings}
    with pytest.raises(ValueError, match='review'):
        ReviewedKnowledge.from_manifest('a' * 64, manifest(rows))


def test_long_lived_view_stops_delivering_expired_links(monkeypatch):
    from datetime import date
    from daq_fae.knowledge import records
    view = ReviewedKnowledge.from_manifest('a' * 64, manifest(reviewed_rows()))
    class Later(date):
        @classmethod
        def today(cls):
            return cls(2100, 1, 1)
    monkeypatch.setattr(records, 'date', Later)
    result = DaqToolBox(knowledge=view, role='internal_fae').dispatch('official_links', {'query': 'EGO'})
    assert result.status == 'not_found'
    assert 'https://' not in str(result.content)


def test_view_without_forward_permission_is_not_link_delivery():
    rows = reviewed_rows()
    rows[-1]['access_review']['forward_roles'] = []
    bind(rows[-1])
    view = ReviewedKnowledge.from_manifest('a' * 64, manifest(rows))
    result = DaqToolBox(knowledge=view, role='internal_fae').dispatch('official_links', {'query': 'EGO'})
    assert result.status == 'not_found'
    assert result.sources == []
