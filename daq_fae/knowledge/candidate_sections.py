"""Static validation for private candidate chapters; never authorizes publication.

Source equality is structural only. Callers must separately reread and hash the
original archive and independently review factual applicability.
"""
import hashlib
import re
from pathlib import PurePosixPath


def validate_section(section, body):
    """Reject accidental approvals, broken locators and cross-topology steps."""
    def require(condition, reason):
        if not condition:
            raise ValueError(reason)

    require(isinstance(section.get('section_id'), str) and section['section_id'], 'section_id')
    require(section.get('body_sha256') == hashlib.sha256(body.encode()).hexdigest(), 'body hash')
    require(section.get('review_status') == 'candidate', 'candidate status')
    for field in ('fact_review', 'permission_review'):
        require(field in section and section[field] is None, 'review must be empty')
    for field in ('view_roles', 'forward_roles'):
        require(section.get(field) == [], 'roles must be empty')
    scope = section.get('scope', {})
    require(not (scope.get('variant_unconfirmed') and scope.get('resolution_variant')), 'unresolved variant')
    refs = section.get('source_refs')
    require(isinstance(refs, list) and bool(refs), 'source refs')
    for ref in refs:
        path = ref.get('path', '')
        require(bool(path) and not PurePosixPath(path).is_absolute() and '..' not in PurePosixPath(path).parts, 'source path')
        require(bool(re.fullmatch('[0-9a-f]{64}', ref.get('sha256', ''))), 'source hash')
        loc = ref.get('locator', {})
        if loc.get('kind') == 'page':
            require(type(loc.get('page')) is int and loc['page'] > 0, 'page locator')
        elif loc.get('kind') == 'lines':
            require(type(loc.get('start')) is int and type(loc.get('end')) is int
                    and 0 < loc['start'] <= loc['end'], 'line locator')
        else:
            raise ValueError('locator kind')
    steps = section.get('steps', [])
    require(isinstance(steps, list), 'steps')
    seen = set()
    for step in steps:
        require(bool(step.get('step_id')) and step['step_id'] not in seen, 'step identity')
        seen.add(step['step_id'])
        require(bool(section.get('topology_id')) and step.get('topology_id') == section['topology_id'], 'step topology')
        require(bool(step.get('source_refs')) and all(ref in refs for ref in step['source_refs']), 'step source refs')


def validate_guidance_section(section, body):
    """Check candidate guidance structure, not factual truth or release eligibility.

    A documented procedure is not a test result. Stronger evidence labels need a
    separately identified record, whose authenticity remains a private audit gate.
    """
    validate_section(section, body)

    def require(condition, reason):
        if not condition:
            raise ValueError(reason)

    require(section.get('capability') in {'selection', 'troubleshoot', 'risk', 'experience'}, 'capability')
    entities = section.get('entity_ids')
    require(isinstance(entities, list) and bool(entities), 'section entities')
    items = section.get('evidence_items')
    gaps = section.get('gap_ids')
    require(isinstance(items, list) and isinstance(gaps, list) and bool(items or gaps), 'evidence or gap')
    seen = set()
    for item in items:
        identity = item.get('item_id')
        require(isinstance(identity, str) and bool(identity) and identity not in seen, 'item identity')
        seen.add(identity)
        text = item.get('text')
        require(isinstance(text, str) and bool(text) and text in body, 'item body')
        ids = item.get('entity_ids')
        require(isinstance(ids, list) and bool(ids) and all(i in entities for i in ids), 'item entities')
        require(bool(section.get('topology_id')) and item.get('topology_id') == section['topology_id'], 'item topology')
        require(isinstance(item.get('conditions'), dict) and bool(item['conditions']), 'item conditions')
        refs = item.get('source_refs')
        require(isinstance(refs, list) and bool(refs) and all(r in section['source_refs'] for r in refs), 'item source refs')
        level = item.get('evidence_level')
        require(level in {'product_specification', 'documented_procedure', 'combination_validation', 'field_experience'}, 'evidence level')
        require(item.get('conclusion_scope') in {'entity', 'topology'}, 'conclusion scope')
        require(type(item.get('actionable')) is bool, 'actionable')
        require(not (level == 'product_specification' and (item['conclusion_scope'] == 'topology' or item['actionable'])), 'specification cannot prove action or combination')
        if level in {'combination_validation', 'field_experience'}:
            record = item.get('evidence_record')
            require(isinstance(record, dict) and bool(record.get('record_id'))
                    and record.get('source_refs') == refs
                    and record.get('conditions') == item['conditions'], 'independent evidence record required')
