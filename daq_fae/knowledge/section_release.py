"""DAQ section compilation gates, shared by publication and immutable reads.

The format stores arbitrary UTF-8 body text. The current B2 consistency gate
only reconciles exact typed projections; human approval alone cannot waive it.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json

from .records import ROLES, _review_valid, record_fingerprint, access_fingerprint
from .section_consistency import audit_sections
from .source_paths import contains_source_path


def section_fingerprint(section: dict, body: str, records: list[dict]) -> str:
    """Bind both reviews to body, all metadata, roles and exact dependencies."""
    core = {k: v for k, v in section.items()
            if k not in {'fact_review', 'permission_review', 'body'}}
    ids = set(section.get('dependency_claim_ids', []) + section.get('link_ids', []))
    dependencies = {r['id']: {'record': record_fingerprint(r), 'access': access_fingerprint(r)}
                    for r in records if r['id'] in ids}
    payload = json.dumps([core, body, dependencies], ensure_ascii=False,
                         sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def _ids(value, label):
    if not isinstance(value, list) or any(not isinstance(i, str) or not i for i in value) \
            or len(set(value)) != len(value):
        raise ValueError('section ' + label + ' invalid')
    return value


def _same_roles(section, view, forward):
    return set(section['view_roles']) == set(view) and set(section['forward_roles']) == set(forward)


def compile_sections(sections, bodies, records, snapshot, *, require_current_links=True):
    # Late import avoids the releases/reviewed_view cycle.
    from .reviewed_view import _url_bearing_strings

    if not isinstance(sections, list) or not isinstance(bodies, dict):
        raise ValueError('section inventory invalid')
    by_record = {row['id']: row for row in records}
    by_section = {}
    candidates = []
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError('section schema invalid')
        sid = section.get('section_id')
        if not isinstance(sid, str) or sid in by_section:
            raise ValueError('section identity invalid')
        by_section[sid] = section
        body = bodies.get(sid)
        if not isinstance(body, str) or not body.strip():
            raise ValueError('section body missing')
        if _url_bearing_strings({k: v for k, v in section.items()
                                 if k not in {'source_refs', 'record_assertions',
                                              'fact_review', 'permission_review'}}) \
                or _url_bearing_strings(body):
            raise ValueError('section URL must use reviewed link IDs')
        if contains_source_path({**section, 'body': body}):
            raise ValueError('section local source path in content')
        if section.get('review_status') != 'verified':
            raise ValueError('section not reviewed')
        if any(not isinstance(section.get(k), str) or not section[k].strip()
               for k in ('title', 'knowledge_type')):
            raise ValueError('section title/type missing')
        views = _ids(section.get('view_roles'), 'view roles')
        forwards = _ids(section.get('forward_roles'), 'forward roles')
        if not views or set(views + forwards) - ROLES or set(forwards) - set(views):
            raise ValueError('section roles invalid')
        deps = _ids(section.get('dependency_claim_ids'), 'record dependencies')
        _ids(section.get('dependency_section_ids', []), 'section dependencies')
        links = _ids(section.get('link_ids', []), 'link IDs')
        for rid in deps + links:
            row = by_record.get(rid)
            if row is None or not row.get('answerable'):
                raise ValueError('section dependency/link not reviewed')
            access = row['access_review']
            if rid in links:
                if row['kind'] != 'link' or not set(views) <= set(access['forward_roles']):
                    raise ValueError('section link not deliverable')
            elif row['kind'] == 'link':
                raise ValueError('section link must use link IDs')
            elif not _same_roles(section, access['view_roles'], access['forward_roles']):
                raise ValueError('section mixed permissions; split sections')
        fingerprint = section_fingerprint(section, body, records)
        for field in ('fact_review', 'permission_review'):
            review = section.get(field)
            if not _review_valid(review) or review.get('section_sha256') != fingerprint:
                raise ValueError('section review missing or stale')
        candidate = deepcopy(section)
        candidate.update(review_status='candidate', fact_review=None, permission_review=None,
                         view_roles=[], forward_roles=[])
        candidates.append(candidate)
    if set(bodies) != set(by_section):
        raise ValueError('section body inventory mismatch')
    for section in sections:
        for dep in section.get('dependency_section_ids', []):
            target = by_section.get(dep)
            if target is None:
                raise ValueError('section dependency missing')
            if not _same_roles(section, target['view_roles'], target['forward_roles']):
                raise ValueError('section mixed dependency permissions; split sections')
    audit = audit_sections(candidates, bodies, records, snapshot, require_current_links=require_current_links)
    if not audit['consistent']:
        raise ValueError('section consistency failed: ' + ', '.join(sorted(
            {f['code'] for f in audit['findings']})))
    return sorted([{**deepcopy(s), 'body': bodies[s['section_id']]} for s in sections],
                  key=lambda s: s['section_id'])


def section_indices(sections, records):
    dependencies = {s['section_id']: {
        'records': sorted(s['dependency_claim_ids']),
        'sections': sorted(s.get('dependency_section_ids', [])),
        'links': sorted(s.get('link_ids', [])),
    } for s in sections}
    sources = {}
    for kind, rows, key in [('records', records, 'id'), ('sections', sections, 'section_id')]:
        for row in rows:
            for ref in row['source_refs']:
                entry = sources.setdefault(ref['path'], {'sha256': ref['sha256'],
                                                         'records': [], 'sections': []})
                if row[key] not in entry[kind]:
                    entry[kind].append(row[key])
    for entry in sources.values():
        entry['records'].sort()
        entry['sections'].sort()
    roles = {role: {
        'sections': sum(role in s['view_roles'] for s in sections),
        'records': sum(r['answerable'] and role in r['access_review']['view_roles'] for r in records),
    } for role in sorted(ROLES)}
    return {'section_count': len(sections),
            'status_counts': dict(sorted(Counter(r['status'] for r in records).items())),
            'section_status_counts': dict(Counter(s['review_status'] for s in sections)),
            'record_kind_counts': dict(sorted(Counter(r['kind'] for r in records).items())),
            'dependency_index': dependencies, 'source_index': sources, 'role_summary': roles}


def validate_section_manifest(manifest):
    sections = manifest.get('sections')
    if not isinstance(sections, list) or any(not isinstance(s, dict) for s in sections):
        raise ValueError('section inventory invalid')
    snapshot = {'sources': manifest['sources'], 'chunks': manifest.get('source_locations', [])}
    compiled = compile_sections(
        [{k: v for k, v in s.items() if k != 'body'} for s in sections],
        {s.get('section_id'): s.get('body') for s in sections}, manifest['records'], snapshot,
        require_current_links=False)
    for key, value in section_indices(compiled, manifest['records']).items():
        if manifest.get(key) != value:
            raise ValueError('section manifest index/count invalid: ' + key)
