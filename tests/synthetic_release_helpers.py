"""Offline fixture primitives. Never imported by application or CLI code."""
from daq_fae.knowledge.releases import read_active_release
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge


def load_fixture_active(root):
    if root.is_symlink():
        raise ValueError('knowledge release root must not be a symlink')
    active = read_active_release(root)
    if active is None:
        return None
    return ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])


def approve_fixture_for_app(root, *, release_id=None):
    """Sign synthetic data with a test-only key; the real app still runs every gate."""
    from copy import deepcopy
    import json
    from pathlib import Path
    from daq_fae.app import RUNTIME_RELEASE
    from daq_fae.knowledge import releases, release_readiness
    from test_release_readiness import candidate, sign, verify
    old = (releases._read_release(root, release_id)[0] if release_id else
           releases.read_active_release(root)['manifest'])
    bundle = candidate()
    bundle['snapshot'] = {
        'archive_manifest_sha256': old['archive_manifest_sha256'],
        'source_date': old.get('source_date'), 'sources': deepcopy(old['sources']),
        'chunks': [{'source_path': ref['path'], 'source_sha256': ref['sha256'],
                    'locator': deepcopy(ref['locator']), 'text': 'synthetic fixture'}
                   for row in old['records'] for ref in row['source_refs']],
    }
    # Deduplicate locators before comparison with manifest reconstruction.
    bundle['snapshot']['chunks'] = list({json.dumps(c, sort_keys=True): c
                                         for c in bundle['snapshot']['chunks']}.values())
    bundle['records'] = deepcopy(old['records'])
    bundle['sections'] = [{k: deepcopy(v) for k, v in section.items() if k != 'body'}
                          for section in old.get('sections', [])]
    bundle['bodies'] = {s['section_id']: s['body'] for s in old.get('sections', [])}
    bundle['scope']['answerable_ids'] = sorted(r['id'] for r in old['records'] if r['answerable'])
    bundle['scope']['topic_reviews'] = {}
    for kind, topic in [('software', 'software'), ('link', 'links')]:
        if any(r['kind'] == kind for r in old['records']):
            bundle['scope']['excluded_topics'].remove(topic)
            bundle['scope']['topic_reviews'][topic] = 'd'*64
    source_hash = release_readiness.digest(bundle['snapshot']['sources'])
    bundle['archive'].update(sources_sha256=source_hash, retrieved_sources_sha256=source_hash)
    bundle['runtime']['release'] = RUNTIME_RELEASE
    bundle['runtime']['upstream_sha'] = json.loads(
        (Path(__file__).resolve().parents[1]/'upstream-source.json').read_text())['revision']
    rid = release_readiness.stage_release(root, sign(bundle), verify_approval=verify)
    releases._activate_release(root, rid)
    return rid, verify
