#!/usr/bin/env python3
"""Reproducible private KQ-07 update proposals; never publishes or activates.

Input is a JSON bundle (or directory containing bundle.json) with snapshot,
records, sections, bodies, coverage, questions and optional governed definitions.
Original source bytes are read from --source-root or bundle.source_root. Every
mutation is explicitly simulated, unapproved and requires normal compilation and
independent fact/permission review. Rollback must use the existing release CLI.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_daq_material_coverage import write_private_report
from daq_fae.knowledge.records import ROLES, access_fingerprint, record_fingerprint
from daq_fae.knowledge.section_release import section_fingerprint
from daq_fae.knowledge.update_impact import plan_update


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':')).encode())


def _pointer(release_root):
    if release_root is None:
        return None
    path = Path(release_root) / 'active.json'
    return _sha(path.read_bytes()) if path.is_file() else 'absent'


def _guard(before, release_root):
    after = _pointer(release_root)
    return {'supplied': release_root is not None, 'before': before,
            'after': after, 'unchanged': before == after}


def _target(bundle):
    sources = {s['path']: s for s in bundle['snapshot']['sources']}
    chapter_dependencies = {rid for section in bundle['sections']
                            for rid in section.get('dependency_claim_ids', [])}
    ordered = sorted(bundle['records'], key=lambda r: r['id'] not in chapter_dependencies)
    for row in ordered:
        if row.get('kind') == 'claim' and row.get('status') == 'verified' and row.get('source_refs'):
            source = sources.get(row['source_refs'][0]['path'])
            if source and source.get('kind') != 'asset' and 'value' in row.get('data', {}):
                return row, source
    raise ValueError('bundle needs a verified source-bound parameter claim')


def _source_bytes(source_root, source):
    root = Path(source_root).resolve(strict=True)
    path = (root / source['path']).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('source must be a regular file below source root')
    raw = path.read_bytes()
    if _sha(raw) != source['sha256']:
        raise ValueError('source hash differs from approved snapshot')
    return raw


def _replace_hash(value, path, old, new):
    if isinstance(value, list):
        for part in value:
            _replace_hash(part, path, old, new)
    elif isinstance(value, dict):
        if value.get('path') == path and value.get('sha256') == old:
            value['sha256'] = new
        if value.get('source_path') == path and value.get('source_sha256') == old:
            value['source_sha256'] = new
        for part in value.values():
            _replace_hash(part, path, old, new)


def _fingerprints(previous, current, plan):
    old = {r['id']: r for r in previous['records']}
    new = {r['id']: r for r in current['records']}
    facts, accesses, stale = {}, {}, {}
    for rid in plan['affected']['record_ids']:
        if rid not in old or rid not in new:
            continue
        facts[rid] = {'old': record_fingerprint(old[rid]), 'new': record_fingerprint(new[rid])}
        accesses[rid] = {'old': access_fingerprint(old[rid]), 'new': access_fingerprint(new[rid])}
        findings = []
        if (new[rid].get('fact_review') or {}).get('record_sha256') != facts[rid]['new']:
            findings.append('fact_review_stale')
        if (new[rid].get('access_review') or {}).get('record_sha256') != accesses[rid]['new']:
            findings.append('access_review_stale')
        stale[rid] = findings
    sections = {}
    old_sections = {s['section_id']: s for s in previous['sections']}
    new_sections = {s['section_id']: s for s in current['sections']}
    for sid in plan['affected']['section_ids']:
        if sid in old_sections and sid in new_sections:
            sections[sid] = {
                'old': section_fingerprint(old_sections[sid], previous['bodies'][sid], previous['records']),
                'new': section_fingerprint(new_sections[sid], current['bodies'][sid], current['records'])}
    return facts, accesses, stale, sections


def _scenario(previous, current, details):
    plan = plan_update(previous, current)
    facts, accesses, stale, sections = _fingerprints(previous, current, plan)
    affected = set(plan['affected']['record_ids'])
    old = {r['id']: r for r in previous['records']}
    new = {r['id']: r for r in current['records']}
    unaffected = sorted(rid for rid in old.keys() & new.keys()
                        if rid not in affected and old[rid] == new[rid])
    entities = {}
    for row in previous['records']:
        entity = row.get('data', {}).get('entity_id')
        if entity:
            ids = [r['id'] for r in previous['records'] if r.get('data', {}).get('entity_id') == entity]
            if not set(ids) & affected:
                entities[entity] = {'record_ids': sorted(ids),
                                    'unchanged': all(rid in unaffected for rid in ids)}
    candidate = deepcopy(current)
    # The report retains original review comparisons above; proposed candidates
    # contain no approvals for the transitive review closure.
    for row in candidate['records']:
        if row['id'] in affected:
            row.update(status='candidate', fact_review=None, access_review=None)
    for section in candidate['sections']:
        if section['section_id'] in plan['affected']['section_ids']:
            section.update(review_status='candidate', fact_review=None, permission_review=None)
            for unit in section.get('narrative_units', []):
                unit.update(review_status='candidate', fact_review=None, permission_review=None)
    candidate['_drill'] = {'simulation_only': True, 'online_eligible': False,
        'proposed_withdrawal_record_ids': plan['withdraw_positive_record_ids'],
        'source_reextraction_required': plan['changes']['changed'] + plan['changes']['added'],
        'independent_fact_and_permission_review_required': True}
    return {'details': details, 'old_bundle_sha256': _digest(previous),
            'new_bundle_sha256': _digest(current), 'candidate_sha256': _digest(candidate),
            'plan': plan, 'record_fingerprints': facts, 'access_fingerprints': accesses,
            'section_fingerprints': sections, 'stale_review_findings': stale,
            'invalidated_reviews': {'record_ids': plan['affected']['record_ids'],
                                    'section_ids': plan['affected']['section_ids'],
                                    'review_types': ['fact', 'permission']},
            'proposed_withdrawal_record_ids': plan['withdraw_positive_record_ids'],
            'runtime_withdrawal_performed': False, 'unaffected_record_ids': unaffected,
            'unrelated_entities': entities, 'candidate': candidate}


def _added_source_scenario(bundle):
    def dimensions(row):
        scope = deepcopy(row.get('impact_scope', {}))
        for plural, singular in (('entity_ids', 'entity_id'), ('field_ids', 'field_id'),
                                 ('topology_ids', 'topology_id'), ('capabilities', 'capability')):
            values = list(scope.get(plural, [])) + list(row.get(plural, []))
            if row.get(singular):
                values.append(row[singular])
            scope[plural] = sorted(set(values))
        if row.get('field'):
            scope['field_ids'] = sorted(set(scope['field_ids'] + [row['field']]))
        return {key: values for key, values in scope.items() if values}

    gaps = [(c, dimensions(c)) for c in bundle['coverage'] if c.get('status') == 'audited_no_source']
    gaps = [(c, scope) for c, scope in gaps if scope.get('entity_ids') and scope.get('field_ids')]
    if not gaps:
        raise ValueError('bundle needs typed audited_no_source coverage for added-source rehearsal')
    gap, scope = gaps[0]
    payload = b'KQ-07 SIMULATION ONLY: new relevant source, no authoritative claims\n'
    added_path = 'kq-07-simulated-added-source.md'
    if any(s['path'] == added_path for s in bundle['snapshot']['sources']):
        raise ValueError('simulated source identity already exists')
    changed = deepcopy(bundle)
    changed['snapshot']['sources'].append({'path': added_path, 'sha256': _sha(payload),
        'kind': 'markdown', 'impact_scope': scope, 'extraction_status': 'not_extracted_simulation'})
    scenario = _scenario(bundle, changed, {
        'source_path': added_path, 'old_sha256': None, 'new_sha256': _sha(payload),
        'impact_scope': scope, 'target_coverage_id': gap['coverage_id'],
        'positive_claim_created': False, 'absence_judgment_requires_independent_recheck': True})
    return scenario, payload, added_path


def rehearse(bundle, source_root, output, *, release_root=None, private_root=None):
    """Write one new private audit batch, preserving caller inputs and originals."""
    private = Path(private_root or ROOT / 'tmp').resolve(strict=True)
    output = Path(output)
    resolved = output.resolve(strict=False)
    if output.is_symlink() or resolved == private or not resolved.is_relative_to(private):
        raise ValueError('output must be below private root')
    if resolved.exists():
        raise ValueError('new output batch required; existing batches are immutable')
    # Disallow writing through input source/release directories, even if these
    # happen to reside below the configured private root.
    for protected in (source_root, release_root):
        if protected and resolved.is_relative_to(Path(protected).resolve()):
            raise ValueError('private output overlaps protected source/release root')
    resolved.mkdir(mode=0o700, parents=True, exist_ok=False)
    pointer_before = _pointer(release_root)
    original_digest = _digest(bundle)
    try:
        for key in ('snapshot', 'records', 'sections', 'bodies', 'coverage', 'questions'):
            if key not in bundle:
                raise ValueError('bundle missing required ' + key)
        if {s['section_id'] for s in bundle['sections']} != set(bundle['bodies']):
            raise ValueError('section/body inventory mismatch')
        # Validate dependencies before any scenario; no unknown IDs are invented.
        plan_update(bundle, bundle)
        claim, source = _target(bundle)
        raw = _source_bytes(source_root, source)
        old_hash = source['sha256']
        simulated_bytes = raw + b'\nKQ-07 SIMULATION ONLY: non-authoritative byte-change marker\n'
        new_hash = _sha(simulated_bytes)
        (resolved / 'simulated-source.bin').write_bytes(simulated_bytes)
        (resolved / 'simulated-source.bin').chmod(0o600)
        scenarios = {}

        changed = deepcopy(bundle)
        _replace_hash(changed, source['path'], old_hash, new_hash)
        scenarios['source_bytes_changed'] = _scenario(bundle, changed, {
            'source_path': source['path'], 'old_sha256': old_hash, 'new_sha256': new_hash,
            'original_bytes_verified': True, 'simulation_file': 'simulated-source.bin',
            'extraction_status': 'requires_reextraction_not_a_new_approved_source'})

        write_private_report(resolved / 'source_bytes_changed-impact.json', scenarios['source_bytes_changed'], private)

        changed = deepcopy(bundle)
        changed_claim = next(r for r in changed['records'] if r['id'] == claim['id'])
        value = changed_claim['data']['value']
        changed_claim['data']['value'] = value + 1 if isinstance(value, (int, float)) and not isinstance(value, bool) \
            else str(value) + ' [KQ-07 SIMULATED CHANGE]'
        scenarios['table_value_changed'] = _scenario(bundle, changed, {
            'record_id': claim['id'], 'old_value': value, 'new_value': changed_claim['data']['value'],
            'source_path': source['path'], 'locator': claim['source_refs'][0]['locator'],
            'source_value_recheck_required': True})

        write_private_report(resolved / 'table_value_changed-impact.json', scenarios['table_value_changed'], private)

        changed = deepcopy(bundle)
        changed['snapshot']['sources'] = [s for s in changed['snapshot']['sources'] if s['path'] != source['path']]
        changed['snapshot']['chunks'] = [c for c in changed['snapshot'].get('chunks', [])
                                         if c['source_path'] != source['path']]
        scenarios['source_removed'] = _scenario(bundle, changed, {
            'source_path': source['path'], 'old_sha256': old_hash, 'new_sha256': None,
            'historical_tombstones_retained_in_audit': True,
            'next_compiler_must_remove_all_proposed_withdrawals': True})

        write_private_report(resolved / 'source_removed-impact.json', scenarios['source_removed'], private)

        for name, expand in (('permission_expanded', True), ('permission_contracted', False)):
            changed = deepcopy(bundle)
            changed_claim = next(r for r in changed['records'] if r['id'] == claim['id'])
            roles = list(changed_claim['access_review']['view_roles'])
            available = sorted(ROLES - set(roles))
            if expand and available:
                field, before, after = 'view_roles', roles, roles + [available[0]]
            elif expand:
                field = 'forward_roles'
                before = list(changed_claim['access_review'].get(field, []))
                available = sorted(set(roles) - set(before))
                if not available:
                    raise ValueError('selected claim has no permission expansion to rehearse')
                after = before + [available[0]]
            else:
                field, before, after = 'view_roles', roles, roles[:-1]
                if not roles:
                    raise ValueError('selected claim has no view permission to contract')
            changed_claim['access_review'][field] = after
            if not expand:
                changed_claim['access_review']['forward_roles'] = [
                    r for r in changed_claim['access_review'].get('forward_roles', []) if r in after]
            changed_source = next(s for s in changed['snapshot']['sources'] if s['path'] == source['path'])
            changed_source[field] = after
            scenarios[name] = _scenario(bundle, changed, {
                'record_id': claim['id'], 'source_path': source['path'], 'acl_field': field,
                'old_roles': before, 'new_roles': after, 'permission_grant_performed': False})
            write_private_report(resolved / (name + '-impact.json'), scenarios[name], private)

        scenarios['source_added'], payload, added_path = _added_source_scenario(bundle)
        (resolved / added_path).write_bytes(payload)
        (resolved / added_path).chmod(0o600)
        write_private_report(resolved / 'source_added-impact.json', scenarios['source_added'], private)

        if _sha(_source_bytes(source_root, source)) != old_hash or _digest(bundle) != original_digest:
            raise ValueError('drill input changed during rehearsal')
        pointer_guard = _guard(pointer_before, release_root)
        if not pointer_guard['unchanged']:
            raise ValueError('active pointer changed during rehearsal')
        report = {'format_version': 'daq-knowledge-update-drill-v1', 'online_eligible': False,
            'input_bundle_sha256': original_digest, 'scenarios': scenarios, 'pointer_guard': pointer_guard,
            'external_bindings': {'observed': bundle.get('release_bindings', {}),
                'main_link_record_ids': sorted(r['id'] for r in bundle['records'] if r.get('kind') == 'link'),
                'main_software_record_ids': sorted(r['id'] for r in bundle['records'] if r.get('kind') == 'software'),
                'action': 'rebind_and_recheck_required_before_publication',
                'reason': 'SDK/link releases have separate approval and graph contracts; main graph alone cannot authorize reuse'},
            'rollback': {'performed': False, 'owner': 'existing release workflow',
                         'target': bundle.get('release_bindings', {}).get('main_release_id'),
                         'status': 'requires_actual_stage_activate_load_and_rollback_evidence'},
            'review_status': 'unapproved_simulation_requires_independent_fact_and_permission_review'}
        for name, scenario in scenarios.items():
            write_private_report(resolved / (name + '.json'), scenario['candidate'], private)
        write_private_report(resolved / 'report.json', report, private)
        return report
    except Exception as error:
        write_private_report(resolved / 'failure.json', {
            'format_version': 'daq-knowledge-update-drill-failure-v1', 'online_eligible': False,
            'error_type': type(error).__name__, 'error': str(error),
            'pointer_guard': _guard(pointer_before, release_root)}, private)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--release-root', type=Path)
    args = parser.parse_args(argv)
    path = args.bundle / 'bundle.json' if args.bundle.is_dir() else args.bundle
    bundle = json.loads(path.read_text(encoding='utf-8'))
    source_root = args.source_root or bundle.get('source_root')
    if not source_root:
        parser.error('--source-root or bundle.source_root is required for exact byte verification')
    # Both repository-private artifact trees remain ignored; resolving first also
    # supports a worktree sharing the repository's private data/tmp directories.
    resolved = args.output.resolve(strict=False)
    private = next((root.resolve() for root in (ROOT / 'tmp', ROOT / 'data/knowledge/curated')
                    if root.is_dir() and resolved.is_relative_to(root.resolve())), None)
    if private is None:
        parser.error('--output must be below private tmp or data/knowledge/curated')
    ignored = subprocess.run(['git', 'check-ignore', '--quiet', '--no-index',
                              str(args.output.resolve(strict=False))],
                             cwd=private.parent, check=False)
    if ignored.returncode != 0:
        parser.error('--output must be Git-ignored')
    report = rehearse(bundle, source_root, args.output, release_root=args.release_root,
                      private_root=private)
    print(json.dumps({'status': 'private_unapproved_simulation',
                      'input_bundle_sha256': report['input_bundle_sha256'],
                      'scenario_count': len(report['scenarios']),
                      'pointer_unchanged': report['pointer_guard']['unchanged']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
