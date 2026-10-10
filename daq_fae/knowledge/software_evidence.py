"""Exact software support boundaries; evidence tiers never imply each other."""

REQUIRED_CONDITIONS = (
    'entity', 'software', 'platform', 'version', 'hardware_revision',
    'connection_mode', 'capability',
)
EVIDENCE_LEVELS = frozenset({
    'package_present', 'documented', 'source_supported',
    'device_tested', 'end_to_end_verified',
})
TEST_LEVELS = frozenset({'device_tested', 'end_to_end_verified'})


def missing_conditions(arguments: dict) -> list[str]:
    return [key for key in REQUIRED_CONDITIONS
            if not isinstance(arguments.get(key), str) or not arguments[key].strip()]


def support_gap(row: dict, arguments: dict, entity_names: set[str],
                topologies: dict | None = None) -> str | None:
    """Called only on role-filtered reviewed records. None means exact support evidence."""
    if row['kind'] != 'software' or row['status'] not in {'verified', 'unsupported'}:
        return 'reviewed_evidence_unavailable'
    if missing_conditions(arguments):
        return 'software_conditions_required'
    data = row['data']
    if arguments['entity'].casefold() not in entity_names or any(
        arguments[key] != data.get(key) for key in REQUIRED_CONDITIONS if key != 'entity'
    ):
        return 'reviewed_evidence_unavailable'
    scope = row['scope']
    conditions = arguments.get('conditions', {})
    declared = data.get('conditions', {})
    if not isinstance(conditions, dict) or not isinstance(declared, dict):
        return 'software_conditions_required'
    known = {**scope, **data, **declared}
    if any(key in arguments and arguments[key] != value for key, value in conditions.items()) or any(
        key not in known or known[key] != value for key, value in conditions.items()
    ) or any(conditions.get(key) != value for key, value in declared.items()) or any(
        key not in conditions or conditions[key] != scope.get(key)
        for key in scope.get('required_selectors', [])
    ):
        return 'software_conditions_required'
    expected_versions = data.get('software_versions', {})
    versions = arguments.get('software_versions', {})
    if not isinstance(versions, dict) or not isinstance(expected_versions, dict) or \
            versions != expected_versions or any(
                not isinstance(v, str) or not v.strip() for v in expected_versions.values()):
        return 'software_versions_unconfirmed'
    if software_version_conditions(data) is None:
        return 'software_versions_unconfirmed'
    topology = data.get('topology_id')
    if arguments.get('topology_id') != topology:
        return 'software_topology_unconfirmed'
    if data.get('evidence_level') not in TEST_LEVELS:
        return 'device_test_missing'
    if topology and data['evidence_level'] != 'end_to_end_verified':
        return 'end_to_end_test_missing'
    if topology:
        topology_row = (topologies or {}).get(topology, {})
        topology_data = topology_row.get('data', {})
        if topology_row.get('status') != 'verified' or data['entity_id'] not in topology_data.get('members', []) or topology_data.get('platform') != data['platform']:
            return 'software_topology_unconfirmed'
        topology_scope = topology_row.get('scope', {})
        selectors = topology_scope.get('required_selectors', [])
        if not isinstance(selectors, list) or any(
            not isinstance(key, str) or key not in topology_scope or
            key not in conditions or conditions[key] != topology_scope[key] or
            known.get(key) != topology_scope[key] or any(
                key in container and container[key] != topology_scope[key]
                for container in (scope, data, declared)
            ) for key in selectors
        ):
            return 'software_topology_conditions_required'
    if not row.get('source_refs') or all(
        ref.get('locator', {}).get('kind') == 'file' for ref in row['source_refs']
    ):
        return 'device_test_missing'
    return None


def candidate_matrix(packet: dict) -> list[dict]:
    """Prepare private review relations without interpreting archive names as facts.

    K3 text and metadata remain separate rows. Selectors await source-specific
    human adjudication; the issue question is a review prompt, never a claim.
    """
    from copy import deepcopy
    from hashlib import sha256
    import json

    rows = []
    for case in packet['cases']:
        if case['group'] not in {'package_inventory', 'firmware_inventory', 'capability_boundary'}:
            continue
        for evidence in case.get('evidence', []):
            ref = evidence['source_ref']
            identity = json.dumps([case['id'], ref, evidence.get('selector_index')],
                                  sort_keys=True, ensure_ascii=False)
            rows.append({
                'id': 'software-candidate:' + sha256(identity.encode()).hexdigest()[:24],
                'case_id': case['id'], 'review_question': case['question'],
                'status': 'candidate', 'online_eligible': False,
                'evidence_level': ('package_present' if evidence.get('evidence_basis') ==
                                   'asset_metadata_only' else 'documented'),
                'conditions': {key: None for key in (*REQUIRED_CONDITIONS, 'topology_id',
                              'viewer_version', 'sdk_version', 'firmware_version')},
                'source_refs': [deepcopy(ref)],
                'document_excerpt': evidence.get('excerpt'),
                'fact_review': 'pending', 'permission_review': 'pending',
                'view_roles': [], 'forward_roles': [],
                'gaps': ['identity_and_conditions_unconfirmed', 'versions_unconfirmed',
                         'device_test_missing', 'end_to_end_test_missing'],
            })
    return rows


def software_version_conditions(data: dict) -> dict | None:
    """Project reviewed dependency versions into coverage without overwriting conflicts."""
    projected = {}
    software = data['software'].casefold()
    for kind in ('viewer', 'sdk', 'firmware'):
        if kind in software or (kind == 'firmware' and '固件' in software):
            projected[kind + '_version'] = data['version']
    dependencies = data.get('software_versions', {})
    if not isinstance(dependencies, dict):
        return None
    for kind, version in dependencies.items():
        field = {'viewer': 'viewer_version', 'sdk': 'sdk_version',
                 'firmware': 'firmware_version'}.get(kind)
        if field is None:
            continue
        if field in projected and projected[field] != version:
            return None
        projected[field] = version
    return projected
