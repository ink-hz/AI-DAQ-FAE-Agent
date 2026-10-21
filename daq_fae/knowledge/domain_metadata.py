"""Reviewed lookup metadata retained when records acquire readable sections."""
from __future__ import annotations

from .records import _entity_label

CONTRACT = 'daq-reviewed-operating-scope/v1'


def validate_domain_metadata(value, records):
    """Validate typed targets; factual alias decisions remain approval-bound."""
    from .operator_release import PRO_VETO_ENTITIES
    if not isinstance(value, dict) or set(value) != {
            'contract', 'field_aliases', 'scoped_field_aliases', 'veto_entities'} or \
            value.get('contract') != CONTRACT or value.get('veto_entities') != PRO_VETO_ENTITIES:
        raise ValueError('reviewed domain metadata invalid')
    fields = {r['data']['field'] for r in records if r['kind'] == 'claim'}
    entities = {r['id'] for r in records if r['kind'] == 'entity'}
    entity_fields = {entity: {r['data']['field'] for r in records
                             if r['kind'] == 'claim' and r['data']['entity_id'] == entity}
                     for entity in entities}
    aliases, scoped = value['field_aliases'], value['scoped_field_aliases']
    if not isinstance(aliases, dict) or not isinstance(scoped, dict):
        raise ValueError('reviewed field aliases invalid')
    seen = {}
    for field, names in aliases.items():
        if field not in fields or not isinstance(names, list) or any(
                not isinstance(n, str) or not n.strip() for n in names):
            raise ValueError('reviewed field alias target invalid')
        for name in [field, *names]:
            key = _entity_label(name)
            if key in seen and seen[key] != field:
                raise ValueError('reviewed field alias ambiguous')
            seen[key] = field
    for entity, names in scoped.items():
        if entity not in entities or not isinstance(names, dict):
            raise ValueError('reviewed scoped alias entity invalid')
        seen_scoped = {}
        for name, field in names.items():
            if not isinstance(name, str) or not name.strip() or field not in entity_fields[entity]:
                raise ValueError('reviewed scoped alias target invalid')
            key = _entity_label(name)
            if key in seen_scoped and seen_scoped[key] != field:
                raise ValueError('reviewed scoped alias ambiguous')
            seen_scoped[key] = field
