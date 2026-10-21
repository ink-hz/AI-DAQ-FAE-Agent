"""Offline D3 release gate and explicit local rehearsal operations.

Trusted operators supply approval verification and Dev health/trace observation.
This is not an identity provider, deployment client, or a replacement for D4.
Public release APIs and application loads require this gate for nonempty knowledge.
"""
from collections import Counter
from copy import deepcopy
from contextlib import contextmanager
import fcntl
import os
import re
from pathlib import Path

from .adjudication import digest
from . import releases
from .records import validate_records
from .reviewed_view import ReviewedKnowledge
from .section_release import compile_sections, section_indices

SHA = re.compile(r'^[0-9a-f]{64}$')


def _hash(value):
    return isinstance(value, str) and bool(SHA.fullmatch(value))


def _text(value):
    return isinstance(value, str) and bool(value.strip())



def is_local_dev_bundle(bundle):
    return bundle.get('scope', {}).get('execution_scope') == 'loopback_dev_internal_fae_only'


def _local_archive_findings(bundle):
    """Verify the existing local archive without claiming independent custody."""
    reasons = []
    a = bundle.get('archive', {})
    if a.get('custody_mode') != 'local_verified' or a.get('authorization_ref') != \
            'project-owner-direction-2026-10-12' or a.get('independent_durable') is not False:
        return ['local_archive_authorization']
    try:
        from scripts.archive_candidate_snapshot import verify_archive
        root = Path(a['custody_ref'])
        verify_archive(root, bundle['snapshot']['archive_manifest_sha256'])
        import json
        original = json.loads((root / 'manifest.json').read_text())['files']
        inventory = lambda rows: sorted((r['path'], r['sha256'], r['size']) for r in rows)
        if inventory(original) != inventory(bundle['snapshot']['sources']):
            reasons.append('local_archive_inventory')
    except (OSError, ValueError, KeyError, TypeError):
        reasons.append('local_archive_inventory')
    # The local waiver only covers the already authorized internal Dev role.
    rows = bundle.get('records', [])
    sections = bundle.get('sections', [])
    if any(r.get('answerable', r.get('status') in {'verified', 'unsupported'}) and
           (r.get('access_review', {}).get('view_roles') != ['internal_fae'] or
            set(r.get('access_review', {}).get('forward_roles', [])) - {'internal_fae'})
           for r in rows) or any(s.get('view_roles') != ['internal_fae'] or
           set(s.get('forward_roles', [])) - {'internal_fae'} for s in sections):
        reasons.append('local_dev_roles')
    return reasons

def _manifest(bundle, *, require_current_links=True):
    snapshot = bundle['snapshot']
    records, findings = validate_records(bundle['records'], snapshot, require_current_links=require_current_links)
    if findings:
        raise ValueError('record validation failed')
    sections = compile_sections(bundle['sections'], bundle['bodies'], records, snapshot,
                                require_current_links=require_current_links)
    manifest = {
        'format_version': 2, 'archive_manifest_sha256': snapshot['archive_manifest_sha256'],
        'source_date': snapshot.get('source_date'),
        'sources': [{k: s[k] for k in ('path', 'sha256', 'size', 'kind')}
                    for s in snapshot['sources']],
        'source_count': len(snapshot['sources']), 'record_count': len(records),
        'status_counts': dict(sorted(Counter(r['status'] for r in records).items())),
        'answerable_count': sum(r['answerable'] for r in records),
        'records': sorted(records, key=lambda r: r['id']),
        'previous_release': bundle['previous_release'],
        'review': {**bundle['review'], 'readiness': deepcopy(bundle)},
        'sections': sections, 'runtime_contract': 'daq-reviewed-sections-v2',
        'source_locations': releases.source_locations(snapshot, bundle['sections']),
        **section_indices(sections, records),
    }
    ReviewedKnowledge.from_manifest('0'*64, manifest)
    return manifest


def audit_readiness(bundle, *, verify_approval=None, _require_current_links=True, allow_local_dev=False):
    """Read-only enumerated rejection; signatures bind every input and exclusion.

    verify_approval(reviewer, canonical_payload_sha256, signature) must be supplied
    by the trusted control plane. No production key or accepting default exists.
    Attested references need independently checked originals/ACL/custody evidence.
    """
    reasons = []
    def check(ok, code):
        if not ok and code not in reasons:
            reasons.append(code)
    try:
        b = deepcopy(bundle)
        a, r, s = b.get('archive', {}), b.get('runtime', {}), b.get('scope', {})
        review, impact, batch = b.get('reviews', {}), b.get('impact', {}), b.get('dev_batch', {})
        snapshot = b.get('snapshot', {})
        local = is_local_dev_bundle(b)
        check((a.get('independent_durable') is True or (local and allow_local_dev))
              and all(_text(a.get(k)) for k in ('custody_ref', 'permissions_ref', 'retrieval_ref')),
              'archive_custody')
        if local:
            check(allow_local_dev, 'local_dev_explicit_opt_in')
            reasons.extend(code for code in _local_archive_findings(b) if code not in reasons)
        elif a.get('custody_mode') == 'local_verified':
            check(False, 'local_dev_scope')
        check(_hash(a.get('manifest_sha256')) and a.get('manifest_sha256') ==
              snapshot.get('archive_manifest_sha256'), 'archive_manifest')
        check(bool(snapshot.get('sources')) and a.get('sources_sha256') == digest(snapshot.get('sources'))
              and a.get('retrieved_sources_sha256') == a.get('sources_sha256'), 'archive_retrieval')
        check(type(review.get('unsigned_count')) is int and review['unsigned_count'] == 0, 'unsigned_reviews')
        check(all(_hash(review.get(k)) for k in ('review_packet_sha256', 'review_decisions_sha256')), 'review_digests')
        check(_hash(impact.get('plan_sha256')) and impact.get('open_obligations') == [], 'impact_obligations')
        check(_text(r.get('release')) and isinstance(r.get('upstream_sha'), str)
              and re.fullmatch('[0-9a-f]{40}', r['upstream_sha'])
              and r.get('contract') == 'daq-reviewed-sections-v2'
              and _hash(r.get('contract_tests_sha256')), 'runtime_compatibility')
        check(r.get('tasks_enabled') is False and r.get('generic_trace_enabled') is False,
              'unsupported_entrypoints')
        check(_hash(s.get('identity_review_sha256')), 'identity_review')
        check(isinstance(s.get('excluded_topics'), list) and isinstance(s.get('gaps'), list)
              and all(_text(v) for v in s['excluded_topics'] + s['gaps']), 'scope_gaps')
        excluded = s.get('excluded_topics', [])
        topic_reviews = s.get('topic_reviews', {})
        topics = {'software', 'links', 'module_projection'}
        check(isinstance(topic_reviews, dict) and set(topic_reviews) <= topics
              and all(_hash(v) for v in topic_reviews.values())
              and not set(topic_reviews).intersection(excluded)
              and topics <= set(excluded) | set(topic_reviews)
              and 'module_projection' in excluded, 'topic_dispositions')
        excluded_kinds = {'software' if t == 'software' else 'link'
                          for t in excluded if t in {'software', 'links'}}
        check(not any(row.get('kind') in excluded_kinds for row in b.get('records', [])),
              'excluded_topic_content')
        check(batch.get('environment') == 'dev' and batch.get('frozen') is True
              and _text(batch.get('id')) and batch.get('id') == b.get('review', {}).get('dev_batch'), 'dev_batch')
        questions = batch.get('questions', [])
        check(isinstance(questions, list) and bool(questions) and batch.get('questions_sha256') == digest(questions), 'dev_batch_digest')
        ids = [q['id'] for q in questions if isinstance(q, dict) and _text(q.get('id')) and _text(q.get('question'))]
        check(len(ids) == len(questions) and len(set(ids)) == len(ids), 'dev_question_identity')
        required = impact.get('required_question_ids')
        check(isinstance(required, list) and bool(required) and set(required) <= set(ids), 'impact_batch_coverage')
        check(b.get('blockers') == [], 'declared_blockers')
        check(b.get('previous_release') is None or _hash(b.get('previous_release')), 'previous_release')
        check(releases._review_ok(b.get('review')), 'release_review')
        approval = b.get('approval', {})
        payload = digest({k: v for k, v in b.items() if k != 'approval'})
        approved = False
        if callable(verify_approval) and approval.get('payload_sha256') == payload:
            try:
                approved = verify_approval(approval.get('reviewer'), payload, approval.get('signature')) is True
            except Exception:
                approved = False
        check(approved, 'independent_approval')
        try:
            manifest = _manifest(b, require_current_links=_require_current_links)
            expected = sorted(row['id'] for row in manifest['records'] if row['answerable'])
            check(isinstance(s.get('answerable_ids'), list) and sorted(s['answerable_ids']) == expected
                  and bool(expected), 'answerable_scope')
        except (ValueError, TypeError, KeyError, AttributeError):
            check(False, 'content_contract')
    except (ValueError, TypeError, KeyError, AttributeError):
        check(False, 'readiness_schema')
    return {'ready': not reasons, 'reasons': reasons, 'online_eligible': False,
            'gate': 'daq-dev-readiness/v1', 'answer_quality': 'pending_D4'}


def _require(bundle, verify, *, require_current_links=True, allow_local_dev=False):
    report = audit_readiness(bundle, verify_approval=verify, _require_current_links=require_current_links,
                             allow_local_dev=allow_local_dev)
    if not report['ready']:
        raise ValueError('release readiness rejected: ' + ', '.join(report['reasons']))


def stage_release(root, bundle, *, verify_approval, allow_local_dev=False):
    b = deepcopy(bundle)
    _require(b, verify_approval, allow_local_dev=allow_local_dev)
    return releases._publish_release(root, b['snapshot'], b['records'], b['previous_release'],
                                    {**b['review'], 'readiness': b},
                                    sections=b['sections'], bodies=b['bodies'])


@contextmanager
def _lock(root):
    # The parent directory must be trusted and all D3 operators use this lock.
    releases._prepare_root(root)
    fd = os.open(root/'.readiness.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _checked(root, rid, verify, *, require_current_links=True, allow_local_dev=False):
    manifest, _ = releases._read_release(root, rid)
    bundle = manifest['review'].get('readiness')
    _require(bundle, verify, require_current_links=require_current_links,
             allow_local_dev=allow_local_dev)
    if releases._digest(releases._json_bytes(_manifest(bundle, require_current_links=require_current_links))) != rid:
        raise ValueError('release readiness content binding mismatch')
    return manifest


def _observation(observe, rid, manifest):
    observation = observe(rid)
    release_evidence = manifest['review'].get('readiness') or manifest['review'].get('operator_release')
    runtime = release_evidence['runtime']['release']
    for name in ('health', 'trace'):
        row = observation.get(name, {})
        if row.get('agent_id') != 'ai-daq-fae-agent' or row.get('knowledge_release') != rid \
                or row.get('runtime_release') != runtime:
            raise ValueError('Dev observation release identity mismatch')
    if not _text(observation['trace'].get('trace_id')):
        raise ValueError('Dev observation trace missing')
    return observation


def _switch(root, rid, manifest, previous, observe):
    phase = 'pointer transition'
    try:
        releases._activate_release(root, rid)
        phase = 'observation'
        return _observation(observe, rid, manifest)
    except Exception as failure:
        # Restore pointer before reporting observation failure. Restore errors are
        # deliberately not hidden; a caller must not claim successful rollback.
        current = releases.read_active_release(root)
        if current == previous:
            raise
        if previous:
            releases._restore_active_release(root, previous)
        else:
            (root/'active.json').unlink()
            fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        raise ValueError(f'Dev {phase} failed; previous pointer restored') from failure


def activate_checked(root, release_id, *, verify_approval, observe, allow_local_dev=False):
    with _lock(root):
        manifest = _checked(root, release_id, verify_approval, allow_local_dev=allow_local_dev)
        active = releases.read_active_release(root)
        if manifest['previous_release'] != (active['release_id'] if active else None):
            raise ValueError('previous release does not match active pointer')
        return _switch(root, release_id, manifest, active, observe)


def rollback_checked(root, *, verify_approval, observe, allow_local_dev=False):
    with _lock(root):
        active = releases.read_active_release(root)
        if not active or not active['manifest'].get('previous_release'):
            raise ValueError('previous release unavailable')
        # The current approval authorizes this exact prior ID. Validate it even
        # when the target is empty; an empty package has no standalone D3 grant.
        current = _checked(root, active['release_id'], verify_approval, require_current_links=False,
                           allow_local_dev=allow_local_dev)
        target = current['previous_release']
        manifest, _ = releases._read_release(root, target)
        if not any(manifest.get(key) for key in ('sources', 'records', 'sections', 'source_locations')):
            # Knowledge-only rollback retains the reviewed current runtime.
            # _switch observes the exact empty target ID and compensates failures.
            return _switch(root, target, current, active, observe)
        manifest = _checked(root, target, verify_approval, allow_local_dev=allow_local_dev)
        return _switch(root, target, manifest, active, observe)
