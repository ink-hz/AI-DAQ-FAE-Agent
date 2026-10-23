"""DAQ domain tools over an optional, role-filtered reviewed Dev release."""

from __future__ import annotations

import ast
import json
import re
import unicodedata

from src.agent.loop.tools import ToolResult

from daq_fae.knowledge.reviewed_view import ReviewedKnowledge, reviewed_name_in_query as _reviewed_name_in_query
from daq_fae.knowledge.sdk_catalog import (
    audio_playback_subjects, calibration_export_known_scope,
    calibration_export_row_matches_question,
    device_sdk_scope_matches_question,
    sdk_cpu_architectures,
    sdk_usage_descriptor_matches as _sdk_usage_descriptor_matches,
)
from daq_fae.knowledge.release_notes import (
    _build_tokens, _firmware_identity, _firmware_requirement_identity,
    _firmware_scopes, _mentioned_families, _selector_scopes, _versions,
)
from daq_fae.knowledge.source_paths import contains_source_path
from daq_fae.request_schema import firmware_versions_compared_without_current
from daq_fae.knowledge.software_evidence import missing_conditions, support_gap, software_version_conditions
from daq_fae.knowledge.section_search import (
    EXCERPT_CHARS, MAX_BODY_CHARS, MAX_QUERY_CHARS, body_text, rank_sections,
)
from daq_fae.supporting_evidence import supporting_specs
from daq_fae.document_references import is_document_statement, section_documents
from daq_fae.knowledge.entity_families import mentions_variant


_TOOLS: dict[str, tuple[str, dict[str, dict], tuple[str, ...]]] = {
    "catalog": ("List governed DAQ products, kits, and release scope.", {"query": {"type": "string"}}, ("query",)),
    "selection": ("Evaluate a DAQ configuration against all stated acquisition constraints.", {"query": {"type": "string"}}, ("query",)),
    "resolve_entity": ("Resolve a DAQ product, variant, kit, or component. Returns reviewed spec field coverage without values; use lookup_spec before answering a field.", {"text": {"type": "string"}}, ("text",)),
    "lookup_spec": ("Look up a governed DAQ claim. For a detailed product specification overview, use field all_reviewed_specs to return every verified field for one exact product or variant with its individual conditions and sources. Otherwise use a canonical field such as rgb_resolution; conditions must be a JSON object. A generic product name cannot select a variant.", {"entity": {"type": "string"}, "field": {"type": "string"}, "conditions": {"type": "object"}}, ("entity", "field")),
    "inspect_topology": ("Inspect a verified acquisition system topology.", {"query": {"type": "string"}}, ("query",)),
    "lookup_procedure": ("Find an applicable, reviewed acquisition procedure.", {"task": {"type": "string"}, "entity": {"type": "string"}}, ("task",)),
    "check_software_support": ("Check exact product, revision, connection, platform, software version and capability; include variant selectors in conditions.", {"entity": {"type": "string"}, "software": {"type": "string"}, "platform": {"type": "string"}, "version": {"type": "string"}, "hardware_revision": {"type": "string"}, "connection_mode": {"type": "string"}, "capability": {"type": "string"}, "conditions": {"type": "object"}}, ("entity", "software", "platform", "version", "hardware_revision", "connection_mode", "capability")),
    "read_doc": ("Read one authorized reviewed section by section_id; preserve its scope and conditions.", {"section_id": {"type": "string"}}, ("section_id",)),
    "search_knowledge": ("Find authorized reviewed sections and uncovered records by natural-language query; use read_doc for a full section. Retrieval relevance does not certify a typed fact.", {"query": {"type": "string"}}, ("query",)),
    "sdk_evidence": ("Find source-checked SDK API and example usage. Packaged source or CMake targets do not prove compilation. Usage evidence does not certify a device or firmware combination; exact support requires all software selectors and device tests.", {"query": {"type": "string"}}, ("query",)),
    "official_links": ("Find authorized and verified official DAQ links.", {"query": {"type": "string"}}, ("query",)),
    "experience": ("Find reviewed field cases and diagnostic experience; not product specifications.", {"query": {"type": "string"}}, ("query",)),
    "risk": ("Find reviewed operating and data integrity risks for an acquisition setup.", {"query": {"type": "string"}}, ("query",)),
    "session_state": ("Read user-provided context in the current DAQ session.", {}, ()),
}


def _identity_text(value: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).casefold().replace('×', 'x'))


def _sdk_api_selector_matches(value: str, row: dict) -> bool:
    """A named API or a getter's named value must exist in this exact record."""
    wanted = _identity_text(value)
    return bool(wanted) and any(
        isinstance(symbol, str) and (
            (name := _identity_text(symbol)) == wanted
            or name == 'get' + wanted
        ) for symbol in row.get('symbols', [])
    )


def _release_build_matches(selector: object, build: str) -> bool:
    wanted = re.sub(r'\D', '', str(selector))
    return (len(wanted) == 8 and build.startswith(wanted) or
            len(wanted) in {10, 12} and build == wanted)


def _release_note_probe_grounded(probe: str, user_text: str) -> bool:
    """A narrower tool probe may improve recall but cannot add selectors."""
    probe_families = set(_mentioned_families(probe))
    if not probe_families or not probe_families <= set(_mentioned_families(user_text)):
        return False
    probe_scopes = _selector_scopes(probe)
    user_scopes = _selector_scopes(user_text)
    if any(family not in user_scopes or any(
            not selectors <= user_scopes[family][selector]
            for selector, selectors in scope.items())
            for family, scope in probe_scopes.items()):
        return False
    if not _versions(probe) <= _versions(user_text):
        return False
    if not _build_tokens(probe) <= _build_tokens(user_text):
        return False
    for platform in ('android', 'desktop', 'linux', 'windows', 'ios'):
        if re.search(r'\b' + platform + r'\b', probe, re.I) and not re.search(
                r'\b' + platform + r'\b|桌面' if platform == 'desktop' else
                r'\b' + platform + r'\b', user_text, re.I):
            return False
    probe_fw = _firmware_scopes(probe)
    user_fw = _firmware_scopes(user_text)
    return all(identity in user_fw and
               scope['versions'] <= user_fw[identity]['versions']
               for identity, scope in probe_fw.items())


def _user_scoped_log_probes(question: str) -> list[str]:
    """Use the user's dated log clause without package conditions beside it."""
    families = set(_mentioned_families(question))
    if len(families) != 1 or 'firmware' in families:
        return []
    family = next(iter(families))
    label = {'orbbecsdk': 'OrbbecSDK', 'egoviewer': 'EgoViewer'}[family]
    probes = []
    for clause in re.split(r'[;；。？！?\n]+', question):
        if ('日志' not in clause and 'changelog' not in clause.casefold()
                and 'release note' not in clause.casefold()):
            continue
        if not (_build_tokens(clause) or _versions(clause)):
            continue
        mentioned = set(_mentioned_families(clause))
        if mentioned and mentioned != families:
            continue
        probes.append((label + ' ' if not mentioned else '') + clause)
    return probes


def _ambiguous_firmware_choice(question: str) -> bool:
    token = r'(双目|四目|\bEGO\s*Pro\b|\bEGO\s*Dual\b|\bPro\b|\bDual\b)'
    for match in re.finditer(token + r'[^。？！?]{0,12}(?:还是|或者|或|/)'
                             r'[^。？！?]{0,12}' + token, question, re.I):
        left = _firmware_identity(match.group(1)) or match.group(1).casefold()
        right = _firmware_identity(match.group(2)) or match.group(2).casefold()
        if left != right:
            return True
    return False


def _procedure_task_paraphrase_matches(task: str, row: dict) -> bool:
    """Accept source-scoped recording paraphrases when the record names that mode."""
    data = row.get('data', {})
    conditions = data.get('evidence_conditions', {})
    if (row.get('status') != 'verified' or
            conditions.get('recording_mode') != 'local_tf_card' or
            conditions.get('storage') != 'tf_card' or
            'local_recording' not in data.get('task_intents', [])):
        return False
    compact = _identity_text(task)
    if any(selector in compact for selector in ('ssd', 'nvme', '硬盘', 'emmc', 'sd卡')):
        return False
    recording = '录制' in compact or 'record' in compact
    local_storage = any(value in compact for value in
                        ('tf卡', 'tfcard', '本机', '本地', '单机', '独立',
                         'local', 'standalone'))
    reviewed_checks = all(value in conditions.get('indicators', [])
                          for value in ('voice_prompt', 'green_led'))
    check_sequence = (reviewed_checks and '步骤' in compact and
                      '语音' in compact and '绿灯' in compact)
    return recording and (local_storage or check_sequence)


def _entity_identity_labels(row: dict) -> set[str]:
    labels = {_identity_text(str(value)) for value in (
        row['id'], row['data']['name'], row['scope'].get('product', ''),
        *row['data'].get('aliases', [])) if value}
    selectors = row['scope'].get('required_selectors', [])
    if len(selectors) == 1:
        selector = _identity_text(str(row['scope'][selectors[0]]))
        labels.update(label + selector + suffix for label in tuple(labels)
                      for suffix in ('', '版', '版本', '变体'))
    return labels


def _with_identity_vetoes(entities: dict[str, dict], vetoes: list[dict]) -> dict[str, dict]:
    """Keep outside-identity vetoes without shadowing now-published entities."""
    labels = {label for row in entities.values() for label in _entity_identity_labels(row)}
    return {**{row['id']: row for row in vetoes if row['id'] not in entities
               and _identity_text(row['data']['name']) not in labels}, **entities}


def _catalog_summary(rows: list[dict], *, candidate_preview: bool,
                     category_counts_requested: bool = False) -> dict:
    """Count the whole role-visible directory and label its publication state."""
    by_type: dict[str, int] = {}
    products: dict[str, list[dict]] = {}
    product_types = {'device', 'variant', 'hub', 'kit'}
    for row in rows:
        entity_type = row['data']['entity_type']
        by_type[entity_type] = by_type.get(entity_type, 0) + 1
        if entity_type in product_types:
            products.setdefault(str(row['scope'].get('product') or row['data']['name']), []).append(row)
    names = []
    for product, variants in products.items():
        labels = {row['data']['name'] for row in variants}
        if len(labels) == 1:
            names.append(next(iter(labels)))
            continue
        shared_aliases = set(variants[0]['data'].get('aliases', []))
        for row in variants[1:]:
            shared_aliases &= set(row['data'].get('aliases', []))
        names.append(sorted(shared_aliases, key=lambda value: (len(value), value))[0]
                     if shared_aliases else product)
    names.sort()
    return {
        'entry_count': len(rows),
        'distinct_product_name_count': len(names),
        'product_or_variant_entry_count': sum(by_type.get(kind, 0)
                                              for kind in product_types),
        'component_entry_count': by_type.get('component', 0),
        'entry_count_includes_components': True,
        'product_names': names,
        **({'by_entity_type': dict(sorted(by_type.items()))}
           if category_counts_requested else {}),
        'scope': ('source_checked_dev_preview' if candidate_preview
                  else 'released_role_visible_knowledge'),
        'exhaustive_product_range': False,
    }


def _resolution_output_field(value: str) -> bool:
    return value.casefold().endswith(('resolution', 'resolution_options'))


def _candidate_name_in_query(name: str, query: str) -> bool:
    """Keep the explicit Dev source-check preview's former loose discovery."""
    normalized_name = unicodedata.normalize('NFKC', name)
    normalized_query = unicodedata.normalize('NFKC', query)
    pattern = re.escape(normalized_name).replace(r'\ ', r'\s*')
    return bool(re.search(r'(?<![A-Za-z0-9])' + pattern + r'(?![A-Za-z0-9])',
                          normalized_query, re.I))


def _candidate_query_scope_safe(row: dict, query: str) -> bool:
    """A descriptive alias cannot fill in a missing variant or version."""
    surface = unicodedata.normalize('NFKC', query).replace('×', 'x')
    scope = row['scope']
    if (row.get('data', {}).get('evidence_conditions', {}).get('host_connection') == 'none'
            or scope.get('mode') == 'standalone'):
        if re.search(r'组合|多机|多设备|配对|同步组|组开流|联机|multi[-\s]?device|pairing',
                     surface, re.I):
            return False
        # Any positively mentioned host conflicts with a standalone record.
        # Looking only beside a connection verb misses “通过 USB 与 PC 通信”.
        for host in re.finditer(r'电脑|手机|主机|(?<![A-Za-z])PC(?![A-Za-z])|\bcomputer\b'
                                r'|\bphone\b|\bhost\b', surface, re.I):
            before = surface[max(0, host.start() - 20):host.start()]
            if not re.search(
                    r'(?:不(?:连接|接入|接|使用|用|通过|经)|无(?:需|须|连接)?'
                    r'|未连接|免(?:接|连|用))[^，。；;!?？!]{0,12}$', before):
                return False
    resolutions = {match.group().casefold() for match in re.finditer(
        r'(?<!\d)\d{3,4}x\d{3,4}(?!\d)', surface, re.I)}
    scoped_resolutions = {
        _identity_text(value) for key, value in scope.items()
        if isinstance(value, str) and ('resolution' in key or 'variant' in key)
        and re.fullmatch(r'\d{3,4}x\d{3,4}', _identity_text(value))}
    if not resolutions <= scoped_resolutions:
        return False
    version_labels = (
        ('viewer_version', r'viewer|查看器|浏览器'),
        ('sdk_version', r'\bsdk\b'),
        ('firmware_version', r'固件|firmware|\bfw\b'),
        ('hardware_revision', r'硬件修订|硬件版本|hardware\s*revision'),
        ('build', r'构建|\bbuild\b'),
    )
    for match in re.finditer(r'(?<![\d.])v?(\d+(?:\.\d+){1,3})(?![\d.])',
                             surface, re.I):
        value = match.group(1)
        prefix = surface[max(0, match.start() - 32):match.start()]
        labels = [(hit.end(), key) for key, pattern in version_labels
                  for hit in re.finditer(pattern, prefix, re.I)]
        requested_key = max(labels)[1] if labels else None
        scoped_versions = {
            key: token.group(1) for key, item in scope.items()
            if isinstance(item, str) and (key.endswith('_version') or
                                          key in {'hardware_revision', 'build'})
            if (token := re.fullmatch(r'v?(\d+(?:\.\d+){1,3})', item, re.I))}
        if (requested_key is not None and scoped_versions.get(requested_key) != value
                or requested_key is None and value not in scoped_versions.values()):
            return False
    return True


def _candidate_alias_match(row: dict, query: str) -> bool:
    if row['status'] != 'verified' or not _candidate_query_scope_safe(row, query):
        return False
    aliases = row['data'].get('retrieval_aliases', [])
    normalized = _identity_text(query)
    return isinstance(aliases, list) and any(
        isinstance(alias, str) and len(_identity_text(alias)) >= (
            2 if re.search(r'[\u3400-\u9fff]', alias) else 3) and
        _identity_text(alias) in normalized for alias in aliases)


def _topology_entity_mentions(row: dict, query: str,
                              entity_records: dict[str, dict]) -> tuple[set[str], set[str]]:
    hits = []
    normalized = _identity_text(query)
    negated = set()
    for entity_id, entity in entity_records.items():
        scope = entity.get('scope', {})
        family_id = entity['data'].get('family_entity_id')
        family_member = family_id in row['data']['members']
        if not family_member and any(_identity_text(str(scope[key])) not in normalized
                                     for key in scope.get('required_selectors', [])):
            continue
        target_id = (family_id if family_member and not mentions_variant(entity, query)
                     else entity_id)
        for label in [entity['data']['name'], scope.get('product', ''),
                      *entity['data'].get('aliases', [])]:
            if _candidate_name_in_query(label, query):
                compact_label = _identity_text(label)
                hits.append((target_id, compact_label))
                if (re.search(
                        r'(?:不(?:接|连接|带|要|用|使用|含|包含|需要|配)'
                        r'|未(?:接入|连接|配)|无(?:需)?|没有|排除|去掉|without)'
                        r'[^，。；;!?？!]{0,4}' + re.escape(compact_label),
                        normalized, re.I)
                        or re.search(re.escape(compact_label) +
                                     r'(?:不参与|不使用|已移除|被排除)', normalized)):
                    negated.add(target_id)
    longest_hits = {entity_id for entity_id, label in hits if not any(
        label != other and label in other for _, other in hits)}
    return longest_hits, negated


def _candidate_topology_entities_match(row: dict, query: str,
                                       entity_records: dict[str, dict]) -> bool:
    longest_hits, negated = _topology_entity_mentions(row, query, entity_records)
    members = set(row['data']['members'])
    # A documented configuration may add prerequisite components the user has
    # not yet specified. Discovery is conditional on retaining those explicit
    # prerequisites; a negated or foreign member still vetoes the configuration.
    query_members = set(row['data'].get('query_members', members))
    return (bool(members) and query_members <= longest_hits <= members
            and not (negated & members))


def _formal_user_operating_scope_matches(row: dict, topology: dict,
                                         question: str,
                                         entity_records: dict[str, dict]) -> bool:
    """Keep independent user clauses separate when judging operating scope."""
    clauses = re.split(r'[?？。；;\n]+', question)
    members = set(topology['data']['members'])
    for clause in clauses:
        mentioned, excluded = _topology_entity_mentions(topology, clause, entity_records)
        if excluded & members and not mentioned - members:
            # A global setup statement can follow the question in another
            # sentence. A shortened model probe must not drop it. A clause
            # explicitly naming a different product belongs to that other
            # goal, not automatically to this configuration.
            return False
    for clause in clauses:
        if not clause.strip():
            continue
        if (_candidate_query_scope_safe(row, clause)
                and _candidate_query_scope_safe(topology, clause)
                and _candidate_topology_entities_match(
                    topology, clause, entity_records)):
            return True
    return False


def _field_intent_matches(intent: str, field: str) -> bool:
    port_power_fields = {
        'downstream_port_power_output',
        'total_downstream_port_power_output',
    }
    if intent.casefold() in port_power_fields or field.casefold() in port_power_fields:
        return (intent.casefold() == field.casefold() or
                intent.casefold() == 'power_output' and
                field.casefold() == 'downstream_port_power_output')
    if intent.casefold().endswith("frame_rate") and field.casefold().endswith("_fps"):
        prefix = intent.casefold().removesuffix("frame_rate").rstrip("_").split('_')
        actual = field.casefold().removesuffix("_fps").split('_')
        prefix = [part for part in prefix if part]
        return not prefix or any(actual[index:index + len(prefix)] == prefix
                                 for index in range(len(actual) - len(prefix) + 1))
    wanted = intent.casefold().split('_')
    actual = field.casefold().split('_')
    return any(actual[index:index + len(wanted)] == wanted
               for index in range(len(actual) - len(wanted) + 1))


# Keep legacy query-only SDK callers valid, but require structured conditions
# before either software tool can return affirmative support evidence.
for _software_tool in ('check_software_support', 'sdk_evidence'):
    _description, _properties, _required = _TOOLS[_software_tool]
    _TOOLS[_software_tool] = (
        _description + ' Support requires exact selectors and reviewed device/combination tests.',
        {**_TOOLS['check_software_support'][1], **_properties,
         'software_versions': {'type': 'object'}, 'topology_id': {'type': 'string'}},
        _required,
    )


class DaqToolBox:
    attachment_source_ids: list[str] = []
    attachment_vision_model = None
    device_support = None

    def __init__(self, *, question: str = "", current_question: str | None = None,
                 context: dict | None = None,
                 knowledge: ReviewedKnowledge | None = None, role: str | None = None,
                 requirements: list[dict] | None = None, access_guard=None,
                 allowed_tools: frozenset[str] | None = None):
        if knowledge is not None and role is None:
            raise ValueError("knowledge role required")
        self.question = question
        self.current_question = question if current_question is None else current_question
        self.context = dict(context or {})
        self.knowledge = knowledge
        self.role = role
        self.requirements = list(requirements or [])
        self.access_guard = access_guard
        self.allowed_tools = allowed_tools

    def with_request_context(self, question: str) -> "DaqToolBox":
        return DaqToolBox(question=question,
                          current_question=self.current_question or question,
                          context=self.context,
                          knowledge=self.knowledge, role=self.role,
                          requirements=self.requirements, access_guard=self.access_guard,
                          allowed_tools=self.allowed_tools)

    def tool_schemas(self) -> list[dict]:
        return [{
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object", "properties": properties,
                    "required": list(required), "additionalProperties": False,
                },
            },
        } for name, (description, properties, required) in _TOOLS.items()
            if self.allowed_tools is None or name in self.allowed_tools]

    def dispatch(self, name: str, arguments: dict) -> ToolResult:
        if self.access_guard is not None:
            self.access_guard()
        result = self._dispatch(name, arguments)
        if self.access_guard is not None:
            self.access_guard()
        return result

    def _dispatch(self, name: str, arguments: dict) -> ToolResult:
        if name not in _TOOLS:
            return ToolResult(status="tool_error", content={"error": "unknown_tool"})
        if self.allowed_tools is not None and name not in self.allowed_tools:
            return ToolResult(status="tool_error", content={"error": "tool_outside_request_goals"})
        original_query_entity = (arguments.get('entity') if name == 'lookup_spec'
                                 else None)
        if name == 'lookup_spec' and 'conditions' in arguments:
            raw_conditions = arguments['conditions']
            if isinstance(raw_conditions, str) and len(raw_conditions) <= 2048:
                try:
                    try:
                        raw_conditions = json.loads(raw_conditions)
                    except ValueError:
                        raw_conditions = ast.literal_eval(raw_conditions)
                except (ValueError, SyntaxError, TypeError, RecursionError, MemoryError):
                    raw_conditions = None
            if not isinstance(raw_conditions, dict) or any(
                not isinstance(key, str) or not isinstance(value, (str, int, float, bool))
                for key, value in raw_conditions.items()
            ):
                return ToolResult(status='tool_error', content={
                    'error': 'conditions_must_be_object',
                })
            arguments = {**arguments, 'conditions': raw_conditions}
        if name == "session_state":
            return ToolResult(status="ok", content={
                "question": self.question, "user_context": dict(self.context),
                "authority": "user_supplied_only",
            })
        if self.knowledge is None:
            return ToolResult(status="not_found", content={
                "reason": "empty_knowledge_release", "query": dict(arguments),
                "claim_status": "unknown", "matches": [],
            })
        if (name == 'check_software_support' and self.question
                and firmware_versions_compared_without_current(self.question)
                and not self.context.get('firmware_version')):
            # Historical log versions are not evidence of the installed FW.
            # Ignore a model-selected version rather than validating a
            # fictional device/SDK combination.
            return ToolResult(status='not_found', content={
                'reason': 'current_firmware_version_unverified',
                'claim_status': 'unknown', 'matches': [],
                'missing_conditions': {'firmware_version': 'current_device'},
                'matched_requirement_ids': self._attempted_requirement_ids(
                    name, arguments),
            })
        if name == "sdk_evidence" and getattr(self.knowledge, "sdk_catalog", None) is not None:
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
                return ToolResult(status="tool_error", content={"error": "invalid_tool_arguments"})
            catalog = self.knowledge.sdk_catalog
            sdk_requirements = [item for item in self.requirements
                                if isinstance(item, dict)
                                and item.get('capability') == 'sdk_evidence']
            matches = catalog.search(query, self.role)
            partial_requirement_rows = {}
            if sdk_requirements:
                matches = [row for row in matches
                           if self._sdk_usage_requirement_ids([row])]
                # Recover every still-uncovered typed SDK goal separately. A
                # Viewer clause or another SDK build cannot erase its evidence.
                covered = set(self._sdk_usage_requirement_ids(matches))
                for requirement in sdk_requirements:
                    if requirement.get('id') in covered:
                        continue
                    conditions = requirement.get('conditions') or {}
                    if not isinstance(conditions, dict):
                        continue
                    selectors = (
                        conditions.get('sdk') or conditions.get('sdk_name') or
                        conditions.get('software') or '',
                        conditions.get('platform') or '',
                        conditions.get('api') or conditions.get('task') or
                        conditions.get('feature') or '',
                        conditions.get('sdk_release_date') or
                        conditions.get('sdk_build') or '',
                        ' '.join(conditions.get('source_files', []))
                        if isinstance(conditions.get('source_files'), list) else '',
                    )
                    scoped = ' '.join(value.replace('_', ' ') for value in selectors
                                      if isinstance(value, str) and value)
                    if not scoped:
                        continue
                    for row in catalog.search(
                            scoped, self.role,
                            include_all=bool(conditions.get('source_files'))):
                        row_ids = self._sdk_usage_requirement_ids([row])
                        if requirement.get('id') in row_ids and row not in matches:
                            matches.append(row)
                            covered.update(row_ids)
                    if requirement.get('id') not in covered:
                        # The model may paraphrase a source task into search
                        # terms that rank a different sample first. Recover
                        # from the user's words only through the same typed
                        # requirement check, never through relevance alone.
                        for row in catalog.search(
                                self.question, self.role,
                                include_all=bool(conditions.get('source_files'))):
                            row_ids = self._sdk_usage_requirement_ids([row])
                            if requirement.get('id') in row_ids and row not in matches:
                                matches.append(row)
                                covered.update(row_ids)
                # An unsupported action must remain unknown, but it must not
                # hide the documented meaning of two APIs explicitly named by
                # the user. Return that exact source row as partial context;
                # only the strict matcher above may satisfy a requirement.
                for requirement in sdk_requirements:
                    requirement_id = requirement.get('id')
                    unmapped = requirement.get('unmapped_condition_keys') or []
                    if (requirement_id in covered or not isinstance(unmapped, list)
                            or not unmapped or not set(unmapped) <= {'task', 'feature'}):
                        continue
                    for row in catalog.search(self.question, self.role, include_all=True):
                        named_symbols = sum(bool(re.search(
                            r'(?<![A-Za-z0-9_])' + re.escape(symbol)
                            + r'(?![A-Za-z0-9_])', self.question, re.I))
                            for symbol in row.get('callable_symbols', [])
                            if isinstance(symbol, str) and len(symbol) >= 6)
                        if (named_symbols < 2 or requirement_id not in
                                self._sdk_usage_requirement_ids(
                                    [row], ignore_descriptors=True)):
                            continue
                        if row not in matches:
                            matches.append(row)
                        partial_requirement_rows[requirement_id] = row
                        break
                # A build or API goal can be paired with a separate question
                # about the package's image/firmware scope. Preserve the
                # reviewed package-scope record as context, without letting it
                # satisfy either an API task or device support requirement.
                if matches and re.search(
                        r'镜像|固件|兼容|搭配|配套|image|firmware|compatib',
                        self.question, re.I):
                    packages = {row.get('package') for row in matches}
                    for row in catalog.search(self.question, self.role):
                        if (row.get('package') in packages
                                and 'sdk_scope' in row.get('usage_tasks', [])
                                and row not in matches):
                            matches.append(row)
                matches = matches[:6]
            # Goal recovery may search the whole user question to repair a
            # weak model query. It must not return a different version/build
            # when this particular tool call explicitly narrowed its scope.
            if re.search(r'(?<![A-Za-z0-9_])(?:orbbecsdk|egolowble)'
                         r'[_\s-]*v?\d+\.\d+\.\d+|'
                         r'(?<!\d)20\d{2}[-/]?\d{2}[-/]?\d{2}(?!\d)',
                         query, re.I):
                query_scope_ids = {row['kb_id'] for row in catalog.search(
                    query, self.role, include_all=True)}
                matches = [row for row in matches
                           if row['kb_id'] in query_scope_ids]
            asked_os = {name for name, pattern in (
                ('linux', r'\b(?:linux|ubuntu)\b'),
                ('windows', r'\b(?:windows|win\s*(?:10|11))\b'),
                ('android', r'\bandroid\b'), ('ios', r'\bios\b'),
                ('macos', r'\bmac(?:\s*os)?\b|\bdarwin\b'))
                if re.search(pattern, query, re.I)}
            asked_arch = set(sdk_cpu_architectures(query))
            # In a comparison, an architecture next to Linux does not
            # constrain the separate Android clause. Per-goal matching below
            # still checks each requested platform and architecture.
            named_sdk_families = {name for name in (
                'orbbecsdk', 'libobkit', 'egolowble')
                if re.search(r'(?<![A-Za-z0-9_])' + name
                             + r'(?![A-Za-z0-9_])', query, re.I)}
            scoped_arch = (asked_arch if len(asked_os) <= 1
                           and len(named_sdk_families) <= 1 else set())
            if asked_os or asked_arch:
                matches = [row for row in matches if
                           (not asked_os or any(
                               name in str(row.get('platform', '')).casefold()
                               for name in asked_os))
                           and (not scoped_arch or bool(scoped_arch & set(
                               sdk_cpu_architectures(str(row.get('platform', ''))))))]
            named_audio_sdks = {name.casefold() for name in
                                audio_playback_subjects(query)}
            if named_audio_sdks:
                layer_sdk = {
                    'host_cpp': 'orbbecsdk', 'android_java': 'orbbecsdk',
                    'ios_framework': 'orbbecsdk', 'device_c': 'libobkit',
                    'ble_cpp': 'egolowble',
                }
                matches = [row for row in matches
                           if layer_sdk.get(row.get('sdk_layer')) in named_audio_sdks]
            partial_requirement_ids = sorted(
                requirement_id for requirement_id, row in
                partial_requirement_rows.items() if row in matches)
            if not matches:
                return ToolResult(status="not_found", content={
                    "release_id": self.knowledge.release_id,
                    "reason": "source_checked_sdk_usage_unavailable",
                    "claim_status": "unknown", "device_support": "unknown",
                    "matches": [], "matched_requirement_ids": [],
                })
            return ToolResult(status="ok", content={
                "release_id": self.knowledge.release_id,
                "sdk_catalog_sha256": self.knowledge.sdk_catalog.manifest["records_sha256"],
                "evidence_scope": "sdk_usage_only", "device_support": "unknown",
                "package_inventory_scope": (
                    "returned_source_checked_records_are_a_subset_not_the_full_package"
                    if re.search(r'包内容|包里有什么|package\s+(?:contents?|files?)',
                                 query, re.I) else None),
                "example_build_status": (
                    "unknown" if any(row.get("doc_type") in {"example", "build"}
                                     for row in matches) else "not_applicable"),
                "matched_requirement_ids": self._sdk_usage_requirement_ids(matches),
                "requirement_source_ids": {
                    requirement_id: [row['kb_id'] for row in matches
                                     if requirement_id in self._sdk_usage_requirement_ids([row])]
                    for requirement_id in self._sdk_usage_requirement_ids(matches)
                },
                "matches": [{key: row[key] for key in (
                    "kb_id", "title", "answer", "sdk_layer", "doc_type", "version",
                    "build", "platform", "symbols", "code_tokens", "confidence", "last_verified")}
                    for row in matches],
                "partial_requirement_ids": sorted(set(partial_requirement_ids)),
            }, sources=[{
                "type": "daq_governed_sdk" if getattr(
                    self.knowledge.sdk_catalog, "published", False)
                    else "daq_sdk_source_checked",
                "verification_status": "verified" if getattr(
                    self.knowledge.sdk_catalog, "published", False)
                    else "source_checked",
                "release_id": getattr(self.knowledge.sdk_catalog, "release_id",
                                      self.knowledge.release_id),
                "source_id": row["kb_id"], "source_refs": row["source_refs"],
            } for row in matches])
        if name == "search_knowledge" and getattr(
                getattr(self.knowledge, "sdk_catalog", None), "release_notes", None) is not None:
            query = arguments.get("query")
            if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
                return ToolResult(status="tool_error", content={"error": "invalid_tool_arguments"})
            text = self.question or query
            lowered = text.casefold()
            explicit_note = ("变更日志" in text or "更新说明" in text or
                             "发布说明" in text or "changelog" in lowered or
                             "release note" in lowered)
            dated_builds = {
                re.sub(r'\D', '', match.group())[:8] for match in re.finditer(
                    r'(?<!\d)20\d{2}[-/]?\d{2}[-/]?\d{2}(?:\d{2,4})?(?!\d)', text)
            }
            dated_comparison = bool(
                len(dated_builds) >= 2 and
                re.search(r'与|和|差异|变化|相同|一致|区别|compare|different',
                          text, re.I))
            scoped_log_probe = bool(
                dated_builds and re.search(r'\.[ch]pp\b|\.c\b|\.h\b', text, re.I) and
                re.search(r'能确定|仍可用|是否可用|已恢复|还能', text) and
                re.search(r'\bSDK\b|OrbbecSDK', text, re.I))
            colloquial_log = bool('日志' in text and _mentioned_families(text)
                                 and (_versions(text) or dated_builds))
            typed_note_goal = any(
                isinstance(requirement, dict)
                and requirement.get('capability') == 'search_knowledge'
                and isinstance(requirement.get('conditions'), dict)
                and any(key in requirement['conditions'] for key in (
                    'viewer_release_date', 'sdk_release_date', 'firmware_version'))
                for requirement in self.requirements)
            historical_support_goal = bool(
                re.search(r'这个示例|该示例|this example', text, re.I)
                and re.search(r'仍(?:然)?可用|还能用|still available', text, re.I)
                and any(isinstance(item, dict)
                        and item.get('capability') == 'search_knowledge'
                        and isinstance(item.get('conditions'), dict)
                        and item['conditions'].get('sdk') == 'OrbbecSDK'
                        and item['conditions'].get('feature')
                        and item['conditions'].get('sdk_version')
                        for item in self.requirements))
            note_intent = (explicit_note or dated_comparison or scoped_log_probe
                           or colloquial_log or typed_note_goal
                           or historical_support_goal)
            if note_intent:
                # A tool query cannot supply a product the user did not name.
                # Report the identity gap, rather than implying that neither
                # product has a release note for the requested version.
                firmware_goals = [requirement for requirement in self.requirements
                                  if isinstance(requirement, dict)
                                  and requirement.get('capability') == 'search_knowledge'
                                  and 'firmware_version' in (
                                      requirement.get('conditions') or {})]
                typed_identity = (all(
                    _firmware_identity(' '.join(str(entity) for entity in
                                               requirement.get('entities', [])))
                    in {'dual', 'pro'} for requirement in firmware_goals)
                    if firmware_goals else False)
                disclaimed_identity = bool(re.search(
                    r'(?:没|未|不)(?:说明|明确|确定|清楚|知道)[^。？！]*'
                    r'(?:双目|四目|ego\s*(?:pro|dual))', text, re.I))
                alternative_identity = _ambiguous_firmware_choice(text)
                identity_confirmed = ((typed_identity or
                                       bool(_firmware_scopes(text))) and
                                      not disclaimed_identity and
                                      not alternative_identity)
                if (_mentioned_families(text) == ['firmware']
                        and not identity_confirmed):
                    return ToolResult(status='not_found', content={
                        'release_id': self.knowledge.release_id,
                        'reason': 'firmware_product_identity_required',
                        'availability_checked': False,
                        'claim_status': 'unknown', 'matches': [],
                        'matched_requirement_ids': [],
                    })
                notes = self.knowledge.sdk_catalog.release_notes
                matches = notes.search(text, self.role)
                if not matches:
                    for probe in _user_scoped_log_probes(text):
                        for row in notes.search(probe, self.role):
                            if row not in matches:
                                matches.append(row)
                if (not matches and query != text and
                        _release_note_probe_grounded(query, text)):
                    # A package platform in one clause can overconstrain a
                    # separate dated log request. The model's narrower probe
                    # is usable only after every family/version/build selector
                    # is grounded in the user's own question.
                    matches = notes.search(query, self.role)
                if scoped_log_probe and len(dated_builds) == 1:
                    # When a dated source sample is used to infer a feature is
                    # *still* available, include the earlier statements for
                    # that source topic. Their log scope remains distinct from
                    # both API usage and tested device support.
                    source_topics = {match.group(1).casefold() for match in
                                     re.finditer(r'\b([a-z][a-z0-9_-]*)\.[ch]pp\b',
                                                 text, re.I)}
                    if len(source_topics) == 1:
                        latest = next(iter(dated_builds))
                        for row in notes.records:
                            if (self.role in row.get('view_roles', [])
                                    and row.get('family', '').casefold() == 'orbbecsdk'
                                    and row.get('build', '') < latest
                                    and source_topics & set(row.get('topics', []))
                                    and row not in matches):
                                matches.append(row)
                if re.search(r'这个示例|该示例|this example', text, re.I) and \
                        re.search(r'仍(?:然)?可用|还能用|still available', text, re.I):
                    # A typed historical-note goal can be added after a
                    # source-checked SDK example in a prior turn. Compare its
                    # product/topic with the separate support goal and use
                    # the latter's current build only as a chronology bound.
                    # The note remains a statement about a past change, not
                    # device support for the requested build.
                    note_goals = [item for item in self.requirements
                                  if isinstance(item, dict)
                                  and item.get('capability') == 'search_knowledge'
                                  and isinstance(item.get('conditions'), dict)]
                    support_goals = [item for item in self.requirements
                                     if isinstance(item, dict)
                                     and item.get('capability') == 'check_software_support'
                                     and isinstance(item.get('conditions'), dict)]
                    for note_goal in note_goals:
                        note_scope = note_goal['conditions']
                        feature = note_scope.get('feature')
                        if (not isinstance(feature, str)
                                or note_scope.get('sdk') != 'OrbbecSDK'
                                or not isinstance(note_scope.get('sdk_version'), str)
                                or len(note_goal.get('entities') or []) != 1):
                            continue
                        entity = note_goal['entities'][0]
                        for support_goal in support_goals:
                            support_scope = support_goal['conditions']
                            if (entity not in (support_goal.get('entities') or [])
                                    or support_scope.get('feature') != feature
                                    or support_scope.get('sdk_version') !=
                                    note_scope['sdk_version']):
                                continue
                            current_build = re.sub(
                                r'\D', '', str(support_scope.get('sdk_release_date', '')))[:8]
                            if len(current_build) != 8:
                                continue
                            for row in notes.records:
                                if (self.role in row.get('view_roles', [])
                                        and row.get('family', '').casefold() == 'orbbecsdk'
                                        and row.get('version') == note_scope['sdk_version']
                                        and feature in row.get('topics', [])
                                        and _identity_text(entity) in _identity_text(
                                            str(row.get('title', '')) + ' ' +
                                            str(row.get('answer', '')))
                                        and re.sub(r'\D', '', str(row.get('build', '')))[:8]
                                        < current_build and row not in matches):
                                    matches.append(row)
                # A software version mentioned only in a support question is
                # not a request for that software's change log. Keep source
                # attribution within the families of typed log goals.
                log_families = set()
                for requirement in self.requirements:
                    if (not isinstance(requirement, dict)
                            or requirement.get('capability') != 'search_knowledge'):
                        continue
                    conditions = requirement.get('conditions') or {}
                    if not isinstance(conditions, dict):
                        continue
                    if 'firmware_version' in conditions:
                        log_families.add('firmware')
                    if ('sdk_release_date' in conditions or
                            'sdk_version' in conditions or
                            conditions.get('sdk') == 'OrbbecSDK'):
                        log_families.add('orbbecsdk')
                    if ('viewer_release_date' in conditions or
                            'viewer_version' in conditions):
                        log_families.add('egoviewer')
                if log_families:
                    matches = [row for row in matches if any(
                        family in row['family'].casefold()
                        for family in log_families)]
                if matches:
                    return ToolResult(status="ok", content={
                        "release_id": self.knowledge.release_id,
                        "release_notes_sha256": notes.manifest["records_sha256"],
                        "evidence_scope": "release_note_statement_only",
                        "device_support": "unknown",
                        "matched_requirement_ids": self._release_note_requirement_ids(matches),
                        "requirement_source_ids": {
                            requirement_id: [row['kb_id'] for row in matches
                                             if requirement_id in
                                             self._release_note_requirement_ids([row])]
                            for requirement_id in self._release_note_requirement_ids(matches)
                        },
                        "matches": [{key: row[key] for key in (
                            "title", "answer", "family", "version", "build", "platform")}
                            for row in matches],
                    }, sources=[{
                        "type": ("daq_governed_release_note" if getattr(
                            self.knowledge.sdk_catalog, "published", False)
                            else "daq_release_note_source_checked"),
                        "verification_status": ("verified" if getattr(
                            self.knowledge.sdk_catalog, "published", False)
                            else "source_checked"),
                        "release_id": getattr(self.knowledge.sdk_catalog, "release_id",
                                              self.knowledge.release_id),
                        "source_id": row["kb_id"], "source_refs": row["source_refs"],
                    } for row in matches])
        scope_normalized = False
        completed_claim_conditions = {}
        question_targets = {}
        if name == "lookup_spec" and hasattr(self.knowledge, "ground_question"):
            arguments = dict(arguments)
            visible_entities = [row for row in self.knowledge.records_for(self.role)
                                if row['kind'] == 'entity']
            requested_key = _identity_text(str(arguments.get('entity') or ''))
            eligible_entities = [row for row in visible_entities if requested_key and
                                 (requested_key in _entity_identity_labels(row) or
                                  (getattr(self.knowledge, 'is_candidate_preview', False)
                                       and _candidate_name_in_query(
                                           str(row['data']['name']),
                                           str(arguments.get('entity') or ''))))]
            if not getattr(self.knowledge, 'is_candidate_preview', False) and len({
                (_identity_text(row['data']['name']),
                 _identity_text(str(row['scope'].get('product', ''))))
                for row in eligible_entities
            }) > 1:
                return ToolResult(status='not_found', content={
                    'reason': 'ambiguous_entity_identity', 'claim_status': 'unknown',
                    'matches': [], 'matched_requirement_ids': [],
                })
            eligible_ids = {row['id'] for row in eligible_entities}
            raw_conditions = arguments.get('conditions')
            if isinstance(raw_conditions, dict):
                conditions = dict(raw_conditions)
                field_name = str(arguments.get('field') or '').casefold()
                for key in list(conditions):
                    if key == field_name or (field_name.endswith(
                            ('fps', 'frame_rate')) and key == 'frame_rate_target'):
                        question_targets[key] = conditions.pop(key)
                if field_name.endswith(('fps', 'frame_rate')) and 'rgb_resolution' in conditions:
                    if 'resolution' not in conditions:
                        conditions['resolution'] = conditions.pop('rgb_resolution')
                    elif conditions['resolution'] == conditions['rgb_resolution']:
                        conditions.pop('rgb_resolution')
                if conditions != raw_conditions:
                    arguments['conditions'] = conditions
                    scope_normalized = True
            target = (self.knowledge.ground_question(self.question)
                      if getattr(self.knowledge, "is_candidate_preview", False)
                      else self.knowledge.ground_question(self.question, role=self.role))
            supplied = arguments.get('conditions') or {}
            if (len(eligible_entities) > 1 and isinstance(supplied, dict)
                    and not any(key in supplied for key in (
                        'variant', 'resolution_variant', 'hardware_revision'))
                    and any(requirement.get('capability') == 'lookup_spec'
                            and not any(key in requirement.get('conditions', {}) for key in (
                                'variant', 'resolution_variant', 'hardware_revision'))
                            and any(_identity_text(str(name)) == requested_key
                                    for name in requirement.get('entities', []))
                            for requirement in self.requirements)):
                # A phrase can mention a variant while rejecting an exclusive
                # claim about it. The generic schema goal, not that mention,
                # controls whether a variant was actually selected.
                target = {}
            entities = target.get("entities", [])
            field = arguments.get("field")
            if isinstance(field, str):
                canonical = self.knowledge.resolve_field(
                    field, entities[0] if len(entities) == 1
                    and entities[0] in eligible_ids else None)
                if canonical and canonical != field:
                    arguments["field"] = canonical
                    scope_normalized = True
            if len(entities) == 1:
                entity_row = next((row for row in visible_entities
                                   if row["id"] == entities[0]), None)
                if entity_row and entity_row['id'] in eligible_ids:
                    if arguments["entity"] != entity_row["id"]:
                        arguments["entity"] = entity_row["id"]
                        scope_normalized = True
                    raw = arguments.get("conditions")
                    conditions = dict(raw) if isinstance(raw, dict) else {}
                    if not isinstance(raw, dict) and raw is not None:
                        scope_normalized = True
                    for selector, value in target.get("conditions", {}).items():
                        if selector in conditions and conditions[selector] != value:
                            break
                        for alias in ("variant", "resolution_variant"):
                            if alias != selector and alias in conditions and \
                                    _identity_text(str(value)) in _identity_text(str(conditions[alias])):
                                conditions.pop(alias)
                                scope_normalized = True
                        if selector not in conditions:
                            conditions[selector] = value
                            scope_normalized = True
                    arguments["conditions"] = conditions
            else:
                # A family phrase can identify a reviewed variant only when
                # the caller supplies every selector explicitly. This lets a
                # user compare known variants without treating the family as
                # one product or guessing a selector.
                requested = arguments.get('entity')
                conditions = arguments.get('conditions') or {}
                if isinstance(requested, str) and isinstance(conditions, dict):
                    candidates = eligible_entities
                    if candidates:
                        longest = max(len(_identity_text(row['data']['name']))
                                      for row in candidates)
                        candidates = [row for row in candidates if
                                      len(_identity_text(row['data']['name'])) == longest and
                                      self._scope_selectors_match(row['scope'], conditions)]
                        if len(candidates) == 1:
                            arguments['entity'] = candidates[0]['id']
                            scope_normalized = True
                            field = arguments.get('field')
                            if isinstance(field, str):
                                canonical = self.knowledge.resolve_field(
                                    field, candidates[0]['id'])
                                if canonical and canonical != field:
                                    arguments['field'] = canonical
                                    scope_normalized = True
            # A canonical entity ID needs the same scoped field aliases as a
            # display name. The earlier context-only resolution has no ID when
            # the user question does not identify a unique entity.
            entity_id = arguments.get('entity')
            canonical_entity = next((row['id'] for row in visible_entities
                                     if isinstance(entity_id, str)
                                     and row['id'].casefold() == entity_id.casefold()), None)
            if canonical_entity:
                if arguments['entity'] != canonical_entity:
                    arguments['entity'] = canonical_entity
                    scope_normalized = True
                field = arguments.get('field')
                if isinstance(field, str):
                    canonical = self.knowledge.resolve_field(field, canonical_entity)
                    if canonical and canonical != field:
                        arguments['field'] = canonical
                        scope_normalized = True
            if any(
                requirement.get("capability") == "lookup_spec"
                and _field_intent_matches(str(requirement.get("field") or ""),
                                          str(arguments.get("field") or ""))
                and self._requirement_targets_entity(
                    requirement, arguments.get('entity'), visible_entities)
                and requirement.get("conditions", {}).get("per_port") is True
                for requirement in self.requirements
            ):
                raw = arguments.get("conditions")
                conditions = dict(raw) if isinstance(raw, dict) else {}
                if "per_port" not in conditions:
                    conditions["per_port"] = True
                    arguments["conditions"] = conditions
                    scope_normalized = True
            supplied_selectors = (arguments.get('conditions')
                                  if isinstance(arguments.get('conditions'), dict) else {})
            matching_spec_needs = [
                requirement for requirement in self.requirements
                if requirement.get("capability") == "lookup_spec"
                and _field_intent_matches(str(requirement.get("field") or ""),
                                          str(arguments.get("field") or ""))
                and self._requirement_targets_entity(
                    requirement, arguments.get('entity'), visible_entities)
                and isinstance(requirement.get("conditions"), dict)
                and all(
                    key not in requirement['conditions'] or
                    requirement['conditions'][key] == supplied_selectors.get(key)
                    for key in ('variant', 'resolution_variant', 'hardware_revision')
                )
            ]
            # A number the user wants to compare with a specification is not
            # a measurement condition on the published claim. Keep every key
            # used by a visible claim for this field and entity, including
            # measurement selectors unknown to the current request planner.
            raw = arguments.get('conditions')
            if isinstance(raw, dict):
                conditions = dict(raw)
                claim_condition_keys = set()
                governed_claims = list(self.knowledge.records_for(self.role))
                governed_claims.extend(self.knowledge.conflict_notices_for(self.role))
                for row in governed_claims:
                    if row['kind'] == 'claim' and row['status'] in {'verified', 'conflict'} and \
                            row['data'].get('entity_id') == arguments.get('entity') and \
                            _field_intent_matches(str(arguments.get('field') or ''),
                                                  str(row['data'].get('field') or '')):
                        claim_condition_keys.update(row['scope'])
                        claim_condition_keys.update(row['data']['conditions'])
                if _resolution_output_field(str(arguments.get('field') or '')) and \
                        'resolution' in conditions and 'resolution' not in claim_condition_keys:
                    question_targets['resolution'] = conditions.pop('resolution')
                for requirement in matching_spec_needs:
                    for key in (requirement.get('question_constraints') or {}):
                        if key in conditions and key not in claim_condition_keys:
                            question_targets[key] = conditions.pop(key)
                if conditions != raw:
                    arguments['conditions'] = conditions
                    scope_normalized = True
            resolutions = {requirement["conditions"]["resolution"]
                           for requirement in matching_spec_needs
                           if isinstance(requirement["conditions"].get("resolution"), str)}
            if len(resolutions) == 1 and not _resolution_output_field(
                    str(arguments.get('field') or '')):
                resolution = next(iter(resolutions))
                raw = arguments.get("conditions")
                conditions = dict(raw) if isinstance(raw, dict) else {}
                plan_has_variant = any("resolution_variant" in requirement["conditions"]
                                       for requirement in matching_spec_needs)
                if not plan_has_variant and _identity_text(str(
                        conditions.get("resolution_variant", ""))) == _identity_text(resolution):
                    conditions.pop("resolution_variant")
                    scope_normalized = True
                if "resolution" not in conditions:
                    conditions["resolution"] = resolution
                    scope_normalized = True
                arguments["conditions"] = conditions
        # Attribution uses the same deterministic alias/selector normalization
        # as retrieval, before any unique published claim fills a condition.
        attempt_arguments = dict(arguments)
        if isinstance(arguments.get('conditions'), dict):
            attempt_arguments['conditions'] = dict(arguments['conditions'])
        if name == "search_knowledge" and self.knowledge.sections_for(self.role):
            return self._hybrid_search(arguments)
        if name == "read_doc":
            return self._section_result(name, arguments)
        rows = self.knowledge.records_for(self.role)
        if name == 'lookup_spec':
            # Source passages are consumed through read_doc, not typed spec values.
            rows = [row for row in rows if not is_document_statement(row)]
        conflict_notices = (self.knowledge.conflict_notices_for(self.role)
                            if name == 'lookup_spec' else [])
        entities = {row["id"]: row["data"]["name"] for row in rows
                    if row["kind"] == "entity"}
        entity_records = {row["id"]: row for row in rows if row["kind"] == "entity"}
        topologies = {row["id"]: row for row in rows if row["kind"] == "topology"}
        if name == 'lookup_spec' and arguments.get('field') == 'all_reviewed_specs':
            # An overview is a collection of separate source-bound claims.
            # Select one visible SKU by exact reviewed identity; never join
            # sibling variants merely because they share a family name.
            wanted = _identity_text(str(arguments.get('entity') or ''))
            def overview_identity(row):
                if wanted in _entity_identity_labels(row):
                    return True
                selectors = row['scope'].get('required_selectors', [])
                if not selectors:
                    return False
                labels = [row['data']['name'], *row['data'].get('aliases', [])]
                return any(wanted in {
                    _identity_text(label + str(row['scope'][selector])),
                    _identity_text(label + str(row['scope'][selector]).split('x')[0]),
                } for label in labels for selector in selectors
                    if selector == 'resolution_variant')
            selected = [row for row in entity_records.values() if wanted and
                        overview_identity(row)]
            supplied = arguments.get('conditions') or {}
            requested_conditions = supplied
            selected = [row for row in selected if isinstance(supplied, dict)
                        and all(row['scope'].get(key) == value for key, value in
                                supplied.items() if key in row['scope'])]
            if len(selected) != 1:
                return ToolResult(status='not_found', content={
                    'reason': 'entity_variant_required', 'claim_status': 'unknown',
                    'matches': [], 'matched_requirement_ids': [],
                })
            target = selected[0]['id']
            selectors = selected[0]['scope'].get('required_selectors', [])
            if selectors and any(key not in supplied for key in selectors):
                generic = {_identity_text(label) for label in
                           [selected[0]['data']['name'],
                            *selected[0]['data'].get('aliases', [])]}
                if wanted in generic:
                    return ToolResult(status='not_found', content={
                        'reason': 'variant_selector_required',
                        'claim_status': 'unknown', 'matches': [],
                        'matched_requirement_ids': [],
                    })
                supplied = {**supplied, **{key: selected[0]['scope'][key]
                                          for key in selectors if key not in supplied}}
            overview = sorted((row for row in rows if row['kind'] == 'claim'
                               and row['status'] == 'verified'
                               and row['data'].get('entity_id') == target
                               and row['data'].get('field') not in {
                                   'source_text', 'risk', 'experience'}
                               and self._scope_selectors_match(row['scope'], supplied)),
                              key=lambda row: row['id'])
            if not overview:
                return ToolResult(status='not_found', content={
                    'reason': 'reviewed_field_unavailable', 'claim_status': 'unknown',
                    'matches': [], 'matched_requirement_ids': [],
                })
            matched = [item['id'] for item in self.requirements
                       if isinstance(item, dict)
                       and item.get('capability') == 'lookup_spec'
                       and item.get('field') == 'all_reviewed_specs'
                       and item.get('conditions', {}) == requested_conditions
                       and isinstance(item.get('entities'), list)
                       and len(item['entities']) == 1
                       and _identity_text(item['entities'][0]) in {
                           wanted, _identity_text(str(original_query_entity or ''))}
                       and all(self._requirement_matches(
                           {**item, 'field': row['data']['field']}, row,
                           entities, topologies, entity_records)
                           and self._positive_spec_covers_requirement(
                               {**item, 'field': row['data']['field']}, row,
                               arguments, rows, entity_records, conflict_notices)
                           for row in overview)]
            return ToolResult(status='ok', content={
                'release_id': self.knowledge.release_id,
                'query_entity': str(original_query_entity or ''),
                'evidence_level': 'reviewed',
                'overview_scope': selected[0]['scope'],
                'matched_requirement_ids': sorted(matched),
                'requirement_source_ids': {rid: [row['id'] for row in overview]
                                           for rid in matched},
                'required_delivery_source_ids': {rid: [row['id'] for row in overview]
                                                for rid in matched},
                'supporting_evidence': supporting_specs(
                    overview, self.requirements, entity_records,
                    {row['id'] for row in overview} if matched else set()),
                'matches': [{key: row[key] for key in
                             ('id', 'kind', 'status', 'scope', 'data')}
                            for row in overview],
            }, sources=[{
                'type': 'daq_governed_claim', 'verification_status': 'verified',
                'release_id': self.knowledge.release_id, 'source_id': row['id'],
                'source_refs': row['source_refs'],
            } for row in overview])
        if name == 'lookup_spec' and isinstance(arguments.get('conditions'), dict):
            supplied = arguments['conditions']
            field = str(arguments.get('field') or '')
            entity = str(arguments.get('entity') or '').casefold()
            candidates = [row for row in rows if row['kind'] == 'claim'
                          and row['status'] == 'verified'
                          and _field_intent_matches(field, str(row['data'].get('field') or ''))
                          and entity in self._entity_names(row, entities, topologies)
                          and self._scope_selectors_match(row['scope'], supplied)
                          and all(self._condition_supported(row, key, value, entity_records)
                                  for key, value in supplied.items())]
            missing_sets = {
                json.dumps({key: value for key, value in row['data']['conditions'].items()
                            if key not in supplied}, ensure_ascii=False, sort_keys=True)
                for row in candidates
            }
            if candidates and len(missing_sets) == 1:
                missing = json.loads(next(iter(missing_sets)))
                # The selected variant and published measurement resolution
                # must agree. Never infer other dimensions such as per-port
                # power, which change the meaning of a generic question.
                if missing == {'resolution': supplied.get('resolution_variant')} \
                        and isinstance(missing['resolution'], str):
                    arguments = {**arguments, 'conditions': {**supplied, **missing}}
                    completed_claim_conditions = missing
        if name in {'check_software_support', 'sdk_evidence'}:
            missing = missing_conditions(arguments)
            gaps = [support_gap(row, arguments, self._entity_names(row, entities, topologies), topologies)
                    for row in rows if row['kind'] == 'software']
            if missing or not any(gap is None for gap in gaps):
                reason = ('software_conditions_required' if missing else next(
                    (gap for gap in gaps if gap != 'reviewed_evidence_unavailable'),
                    'reviewed_evidence_unavailable'))
                return ToolResult(status='not_found', content={
                    'release_id': self.knowledge.release_id, 'reason': reason,
                    'claim_status': 'unknown', 'missing_conditions': missing,
                    'matches': [], 'matched_requirement_ids': self._attempted_requirement_ids(name, attempt_arguments),
                })
        candidate_preview = bool(getattr(self.knowledge, 'is_candidate_preview', False))
        governed_operating = candidate_preview or bool(getattr(
            self.knowledge, 'is_operator_pro_v2', False))
        topology_identity_records = entity_records
        if name in {'inspect_topology', 'lookup_procedure', 'selection'}:
            if candidate_preview:
                topology_identity_records = {**entity_records, **{
                    row['id']: row for row in self.knowledge.candidate_entity_records()}}
            elif getattr(self.knowledge, 'is_operator_pro_v2', False):
                topology_identity_records = _with_identity_vetoes(
                    entity_records, self.knowledge.operating_veto_entity_records())
        def formal_question_safe(row):
            if not getattr(self.knowledge, 'is_operator_pro_v2', False) or row['kind'] not in {
                    'topology', 'procedure'}:
                return True
            topology = (row if row['kind'] == 'topology' else
                        topologies.get(row['data']['topology_id']))
            question = self.question or str(arguments.get('query') or '') or (
                str(arguments.get('task') or '') + ' ' + str(arguments.get('entity') or ''))
            return bool(topology and _formal_user_operating_scope_matches(
                row, topology, question, topology_identity_records))
        matches = [row for row in rows if self._matches(
            name, arguments, row, entities, topologies, topology_identity_records,
            candidate_preview=(governed_operating if name in {
                'inspect_topology', 'lookup_procedure', 'selection'} else candidate_preview))
                   and formal_question_safe(row)]
        if (not matches and name == 'inspect_topology'
                and getattr(self.knowledge, 'is_operator_pro_v2', False)
                and self.question):
            # A model-written tool query can paraphrase a reviewed Chinese
            # topology into English. Retry against the user's actual question;
            # the same entity, scope, and source checks still apply.
            original_arguments = {**arguments, 'query': self.question}
            matches = [row for row in rows if self._matches(
                name, original_arguments, row, entities, topologies,
                topology_identity_records, candidate_preview=True)
                and formal_question_safe(row)]
        attempted = self._attempted_requirement_ids(name, attempt_arguments)
        if name in {'check_software_support', 'sdk_evidence'} and len({
            row['status'] for row in matches
        }) > 1:
            return ToolResult(status='not_found', content={
                'release_id': self.knowledge.release_id,
                'reason': 'reviewed_evidence_unavailable', 'claim_status': 'unknown',
                'matches': [], 'matched_requirement_ids': attempted,
            })
        matching_conflicts = ([notice for notice in conflict_notices
                               if self._matches(name, arguments, notice, entities,
                                                topologies, entity_records,
                                                candidate_preview=candidate_preview)]
                              if name == 'lookup_spec' else [])
        if matching_conflicts:
            conflict_ids = sorted({requirement['id'] for requirement in self.requirements
                                   if isinstance(requirement, dict)
                                   and isinstance(requirement.get('id'), str)
                                   and requirement.get('capability') == 'lookup_spec'
                                   and any(self._requirement_matches(
                                       requirement, notice, entities, topologies,
                                       entity_records) for notice in matching_conflicts)})
            return ToolResult(status="not_found", content={
                "reason": "reviewed_conflict_notice", "claim_status": "conflict",
                "matches": [], "matched_requirement_ids": conflict_ids,
            })
        if name == "lookup_spec" and len({
            json.dumps([row["status"], row["data"].get("value"), row["data"].get("unit")],
                       ensure_ascii=False, sort_keys=True) for row in matches
        }) > 1:
            return ToolResult(status="not_found", content={
                "reason": "scope_or_conditions_required", "claim_status": "unknown",
                "matches": [], "matched_requirement_ids": [],
            })
        if not matches and name == 'lookup_spec':
            supplied = arguments.get('conditions') or {}
            if isinstance(supplied, dict):
                field = str(arguments.get('field') or '').casefold()
                entity = str(arguments.get('entity') or '').casefold()
                candidates = [row for row in rows if row['kind'] == 'claim'
                              and row['data']['field'].casefold() == field
                              and entity in self._entity_names(row, entities, topologies)
                              and self._scope_selectors_match(row['scope'], supplied)
                              and all({**row['scope'], **row['data']['conditions']}.get(key) == value
                                      for key, value in supplied.items())]
                missing = {}
                for row in candidates:
                    for key, value in row['data']['conditions'].items():
                        if key not in supplied:
                            missing.setdefault(key, {})[
                                json.dumps(value, ensure_ascii=False, sort_keys=True)] = value
                if missing:
                    return ToolResult(status='not_found', content={
                        'reason': 'claim_conditions_required', 'claim_status': 'unknown',
                        'available_claim_conditions': {
                            key: [value for _, value in sorted(values.items())]
                            for key, values in sorted(missing.items())},
                        'matches': [], 'matched_requirement_ids': [],
                    })
                scoped = [row for row in rows if row['kind'] == 'claim'
                          and row['status'] == 'verified'
                          and _field_intent_matches(field, str(row['data']['field']))
                          and entity in self._entity_names(row, entities, topologies)
                          and self._scope_selectors_match(row['scope'], supplied)]
                mismatched_scopes = {}
                for row in scoped:
                    for key, value in row['data']['conditions'].items():
                        if key in supplied and supplied[key] != value:
                            scope = {
                                'entity_id': row['data']['entity_id'],
                                'resolution_variant': row['scope'].get('resolution_variant'),
                                'conditions': row['data']['conditions'],
                            }
                            mismatched_scopes[json.dumps(
                                scope, ensure_ascii=False, sort_keys=True)] = scope
                if mismatched_scopes:
                    return ToolResult(status='not_found', content={
                        'reason': 'claim_conditions_mismatch', 'claim_status': 'unknown',
                        'available_claim_condition_scopes': [
                            scope for _, scope in sorted(mismatched_scopes.items())],
                        'matches': [], 'matched_requirement_ids': attempted,
                    })
        if not matches and name == "lookup_spec" and hasattr(self.knowledge, "ground_question"):
            choices = (self.knowledge.source_checked_variant_scopes(
                str(arguments.get("entity") or ""), str(arguments.get("field") or ""))
                if getattr(self.knowledge, "is_candidate_preview", False)
                else self.knowledge.verified_variant_scopes(
                    str(arguments.get("entity") or ""), str(arguments.get("field") or ""),
                    role=self.role))
            if choices:
                entity_scopes = {}
                for row in visible_entities:
                    if str(arguments.get('entity') or '').casefold() not in self._entity_names(
                            row, entities, topologies):
                        continue
                    for selector in row['scope'].get('required_selectors', []):
                        entity_scopes.setdefault(selector, set()).add(
                            str(row['scope'][selector]))
                return ToolResult(status="not_found", content={
                    "reason": "variant_selector_required", "claim_status": "unknown",
                    ("source_checked_variant_scopes" if getattr(
                        self.knowledge, "is_candidate_preview", False)
                     else "verified_variant_scopes"): choices,
                    "visible_entity_variant_scopes": {
                        key: sorted(values) for key, values in sorted(entity_scopes.items())},
                    "variant_scopes_exhaustive": False,
                    "matches": [], "matched_requirement_ids": attempted,
                })
        if not matches and name == 'lookup_spec' and not getattr(
                self.knowledge, 'is_candidate_preview', False):
            requested_entity = str(arguments.get('entity') or '').casefold()
            requested_field = str(arguments.get('field') or '').casefold()
            identified = [row for row in rows if row['kind'] == 'entity'
                          and requested_entity in {
                              row['id'].casefold(), str(row['data']['name']).casefold()}]
            if requested_field and len(identified) == 1:
                entity_row = identified[0]
                entity_claims = [row for row in rows if row['kind'] == 'claim'
                                 and row['data'].get('entity_id') == entity_row['id']]
                has_selector = bool(entity_row['scope'].get('required_selectors'))
                has_conflict = any(
                    notice['data'].get('entity_id') == entity_row['id']
                    and _field_intent_matches(
                        requested_field, str(notice['data'].get('field') or ''))
                    for notice in self.knowledge.conflict_notices_for(self.role))
                if not has_selector and not has_conflict and not any(
                        _field_intent_matches(requested_field,
                                              str(row['data']['field']))
                        for row in entity_claims):
                    return ToolResult(status='not_found', content={
                        'reason': 'reviewed_field_unavailable',
                        'claim_status': 'unknown',
                        'available_fields': sorted({str(row['data']['field'])
                                                    for row in entity_claims
                                                    if row['status'] == 'verified'}),
                        'matches': [], 'matched_requirement_ids': attempted,
                    })
        if not matches:
            return ToolResult(status="not_found", content={
                "reason": "reviewed_evidence_unavailable", "claim_status": "unknown",
                "matches": [], "matched_requirement_ids": attempted,
            })
        catalog_summary = _catalog_summary(
            matches, candidate_preview=bool(getattr(
                self.knowledge, 'is_candidate_preview', False)),
            category_counts_requested=bool(
                re.search(r'设备|HUB|变体|部件|类别|类型', self.question, re.I)
                and re.search(r'各|分别|多少条|几条|按类别|按类型|类型分布',
                              self.question, re.I))) if name == 'catalog' else None
        if name != 'catalog':
            matches = matches[:20]
        field_coverage = {}
        if name == 'resolve_entity':
            for entity_row in matches:
                fields = sorted({str(row['data']['field']) for row in rows
                                 if row['kind'] == 'claim'
                                 and row['status'] == 'verified'
                                 and not is_document_statement(row)
                                 and row['data'].get('entity_id') == entity_row['id']})
                field_coverage[entity_row['id']] = fields
        requirement_source_ids = {}
        for requirement in self.requirements:
            if (not isinstance(requirement, dict)
                    or not isinstance(requirement.get('id'), str)
                    or requirement.get('capability') != name):
                continue
            source_ids = [row['id'] for row in matches
                          if self._requirement_matches(
                              requirement, row, entities, topologies,
                              topology_identity_records,
                              candidate_preview=(governed_operating if row['kind'] in {
                                  'topology', 'procedure'} else candidate_preview))
                          and (name != 'lookup_spec' or
                               self._positive_spec_covers_requirement(
                                   requirement, row, arguments, rows,
                                   entity_records, conflict_notices))]
            if source_ids:
                requirement_source_ids[requirement['id']] = source_ids
        matched_requirements = sorted(requirement_source_ids)
        return ToolResult(status="ok", content={
            "release_id": self.knowledge.release_id,
            **({'query_entity': str(original_query_entity or '')}
               if name == 'lookup_spec' else {}),
            **({'catalog_summary': catalog_summary} if catalog_summary is not None else {}),
            **({'catalog_by_requirement': {
                rid: _catalog_summary([row for row in matches if row['id'] in ids],
                                      candidate_preview=candidate_preview)
                for rid, ids in requirement_source_ids.items()}}
               if name == 'catalog' else {}),
            **({'available_spec_fields': field_coverage,
                'available_spec_fields_are_values': False}
               if name == 'resolve_entity' else {}),
            **({'query_conditions_completed_from_unique_claim': completed_claim_conditions}
               if completed_claim_conditions else {}),
            **({'question_targets_not_claim_conditions': question_targets}
               if question_targets else {}),
            **({'variant_scopes_exhaustive': False}
               if name in {'lookup_spec', 'resolve_entity'} and any(
                   row['scope'].get('required_selectors') for row in matches) else {}),
            **({"query_scope_normalized": scope_normalized} if
               getattr(self.knowledge, "is_candidate_preview", False) else {}),
            "evidence_level": ("dev_source_checked" if
                               getattr(self.knowledge, "is_candidate_preview", False)
                               else "reviewed"),
            "matched_requirement_ids": matched_requirements,
            "requirement_source_ids": requirement_source_ids,
            **({'required_delivery_source_ids': requirement_source_ids}
               if name == 'catalog' else {}),
            **({'supporting_evidence': supporting_specs(
                matches, self.requirements, entity_records,
                {source_id for ids in requirement_source_ids.values() for source_id in ids})}
               if name == 'lookup_spec' else {}),
            "matches": [{**{key: row[key] for key in ("id", "kind", "status", "scope", "data")},
                         **({"status": "source_checked"} if
                            getattr(self.knowledge, "is_candidate_preview", False) else {})}
                        for row in matches],
        }, sources=[{
            "type": ("daq_dev_source_checked_claim" if
                     getattr(self.knowledge, "is_candidate_preview", False)
                     else "daq_governed_claim"),
            "verification_status": ("source_checked" if
                                    getattr(self.knowledge, "is_candidate_preview", False)
                                    else "verified"),
            "release_id": self.knowledge.release_id, "source_id": row["id"],
            "source_refs": row["source_refs"],
        } for row in matches])

    def _hybrid_search(self, arguments: dict) -> ToolResult:
        """Search both published layers; a section's dependency is shown once."""
        section_result = self._section_result('search_knowledge', arguments)
        if section_result.status == 'tool_error':
            return section_result
        sections = [section for section in self.knowledge.sections_for(self.role)
                    if not contains_source_path(section)]
        covered = {
            record_id for section in sections
            for record_id in (
                [section.get('entity_id'), *section.get('entity_ids', []),
                 *section.get('dependency_claim_ids', [])]
                + [item.get('id') for item in section.get('record_assertions', [])]
            ) if isinstance(record_id, str)
        }
        rows = self.knowledge.records_for(self.role)
        entities = {row['id']: row['data']['name'] for row in rows
                    if row['kind'] == 'entity'}
        topologies = {row['id']: row for row in rows if row['kind'] == 'topology'}
        identity_records = {row['id']: row for row in rows if row['kind'] == 'entity'}
        if getattr(self.knowledge, 'is_operator_pro_v2', False):
            identity_records = _with_identity_vetoes(
                identity_records, self.knowledge.operating_veto_entity_records())
        query = str(arguments.get('query') or '')
        def operating_search_safe(row):
            if not getattr(self.knowledge, 'is_operator_pro_v2', False) or row['kind'] not in {
                    'topology', 'procedure'}:
                return True
            topology = (row if row['kind'] == 'topology' else
                        topologies.get(row['data']['topology_id']))
            return bool(topology and _formal_user_operating_scope_matches(
                row, topology, self.question or query, identity_records))
        candidates = [row for row in rows if row['id'] not in covered
                      and operating_search_safe(row)
                      and self._matches('search_knowledge', arguments, row,
                                        entities, topologies)][:20]
        if not candidates:
            return section_result
        candidate_preview = bool(getattr(self.knowledge, 'is_candidate_preview', False))
        records = [{**{key: row[key] for key in ('id', 'kind', 'status', 'scope', 'data')},
                    'evidence_layer': ('source_candidate' if candidate_preview
                                       else 'reviewed_record')}
                   for row in candidates]
        sources = [{
            'type': ('daq_dev_source_checked_claim' if candidate_preview
                     else 'daq_governed_claim'),
            'verification_status': ('source_checked' if candidate_preview else 'verified'),
            'release_id': self.knowledge.release_id,
            'source_id': row['id'], 'source_refs': row['source_refs'],
        } for row in candidates]
        return ToolResult(status='ok', content={
            'release_id': self.knowledge.release_id,
            'matches': (section_result.content['matches'] if section_result.status == 'ok'
                        else []) + records,
            # Retrieval relevance cannot certify a typed product requirement.
            'matched_requirement_ids': [],
        }, sources=(section_result.sources if section_result.status == 'ok' else [])
           + sources)

    def _section_result(self, name: str, arguments: dict) -> ToolResult:
        key = "section_id" if name == "read_doc" else "query"
        value = arguments.get(key)
        if not isinstance(value, str) or len(value) > MAX_QUERY_CHARS:
            return ToolResult(status="tool_error", content={"error": "invalid_tool_arguments"})
        sections = [s for s in self.knowledge.sections_for(self.role)
                    if not contains_source_path(s)]
        if name == "read_doc":
            matches = [s for s in sections if s["section_id"] == value]
        else:
            matches = rank_sections(sections, self.knowledge.records_for(self.role), value)
            if getattr(self.knowledge, "is_candidate_preview", False):
                matches = matches[:8]
        if not matches:
            return ToolResult(status="not_found", content={
                "release_id": self.knowledge.release_id,
                "reason": "reviewed_evidence_unavailable", "claim_status": "unknown",
                "matches": [], "matched_requirement_ids": [],
            })
        if name == "read_doc" and len(matches[0]["body"]) > MAX_BODY_CHARS:
            return ToolResult(status="tool_error", content={
                "release_id": self.knowledge.release_id, "error": "section_too_large",
                "matches": [], "matched_requirement_ids": [],
            })
        projected = []
        records = {r['id']: r for r in self.knowledge.records_for(self.role)}
        document_rows = ([r for s in matches for r in section_documents(s, records)]
                         if name == 'read_doc' and not getattr(
                             self.knowledge, 'is_candidate_preview', False) else [])
        entities = {key: row for key, row in records.items() if row['kind'] == 'entity'}
        candidate_preview = bool(getattr(self.knowledge, "is_candidate_preview", False))
        for section in matches:
            item = {key: section[key] for key in (
                "section_id", "title", "knowledge_type", "scope")}
            item.update(evidence_layer=("source_candidate" if candidate_preview else "reviewed_section"),
                        status=("candidate" if candidate_preview else "verified"))
            for key in ("entity_id", "entity_ids", "topology_id", "conditions",
                        "hardware_revision", "software_versions", "link_ids",
                        "evidence_scope", "source_limits"):
                if key in section:
                    item[key] = section[key]
            if name == "read_doc":
                item["body"] = section["body"]
            else:
                passages = section_documents(section, records)
                text = ('\n\n'.join(row['data']['value'] for row in passages)
                        if passages else body_text(section["body"]))
                item.update(excerpt=text[:EXCERPT_CHARS],
                            excerpt_truncated=len(text) > EXCERPT_CHARS)
            projected.append(item)
        return ToolResult(status="ok", content={
            "release_id": self.knowledge.release_id, "matches": projected,
            # Retrieval relevance cannot certify typed facts or exact conditions.
            "matched_requirement_ids": [],
            **({'supporting_evidence': supporting_specs(
                document_rows, self.requirements, entities, set())}
               if document_rows else {}),
        }, sources=[{
            "type": "daq_source_candidate" if candidate_preview else "daq_governed_section",
            "verification_status": "candidate" if candidate_preview else "verified",
            "release_id": self.knowledge.release_id, "source_id": s["section_id"],
            "scope": s["scope"], "source_refs": [{
                "source_id": "source:" + ref["sha256"], "sha256": ref["sha256"],
                "locator": ref["locator"],
            } for ref in s["source_refs"]],
        } for s in matches] + [{
            'type': 'daq_governed_claim', 'verification_status': 'verified',
            'evidence_scope': 'document_statement_only',
            'scope': row['scope'],
            'release_id': self.knowledge.release_id, 'source_id': row['id'],
            'source_refs': row['source_refs'],
        } for row in document_rows])

    @staticmethod
    def _record_text(row: dict) -> str:
        return json.dumps({"data": row["data"], "scope": row["scope"]},
                          ensure_ascii=False, sort_keys=True).casefold()

    @staticmethod
    def _scope_selectors_match(scope: dict, conditions: dict) -> bool:
        selectors = scope.get("required_selectors", [])
        return isinstance(selectors, list) and all(
            isinstance(key, str) and key in scope and
            key in conditions and conditions[key] == scope[key]
            for key in selectors
        )

    @staticmethod
    def _condition_supported(row: dict, key: str, value,
                             entity_records: dict[str, dict]) -> bool:
        if key == 'scope' and row['kind'] == 'claim':
            explicit = {**row['scope'], **row['data'].get('conditions', {})}
            if key in explicit:
                # A reviewed claim's own range is narrower than its parent
                # product type. Never widen that range via entity identity.
                return explicit[key] == value
            entity = entity_records.get(row['data'].get('entity_id'))
            if entity:
                entity_type = entity['data'].get('entity_type')
                if value == 'device' and entity_type == 'device':
                    return True
                if entity_type == 'component':
                    if value == 'component':
                        return True
                    if value == 'module' and re.search(
                            r'模块|\bmodule\b', entity['data'].get('name', ''), re.I):
                        return True
        return {**row['scope'], **row['data'].get('conditions', {})}.get(key) == value

    def _attempted_requirement_ids(self, name: str, arguments: dict) -> list[str]:
        ids = []
        for requirement in self.requirements:
            if not isinstance(requirement, dict) or requirement.get("capability") != name or \
                    not isinstance(requirement.get("id"), str):
                continue
            field = requirement.get("field")
            if field is not None and not _field_intent_matches(
                    str(field), str(arguments.get("field") or "")):
                continue
            if name == 'lookup_spec':
                required_conditions = requirement.get('conditions') or {}
                supplied_conditions = arguments.get('conditions') or {}
                if not isinstance(required_conditions, dict) or not isinstance(
                        supplied_conditions, dict) or supplied_conditions != required_conditions:
                    # A narrower or conflicting failed query does not prove
                    # absence for the requested condition set.
                    continue
            software = requirement.get("software")
            if software is not None and str(arguments.get("software") or "").casefold() != str(software).casefold():
                continue
            required_entities = requirement.get("entities") or []
            if name == 'resolve_entity' and (
                not required_entities or _identity_text(str(arguments.get('text') or ''))
                not in {_identity_text(str(item)) for item in required_entities}
            ):
                continue
            if required_entities and name in {"lookup_spec", "check_software_support"} and \
                    str(arguments.get("entity") or "").casefold() not in {
                        str(item).casefold() for item in required_entities
                    } and not (name == 'lookup_spec' and self.knowledge is not None
                               and self._requirement_targets_entity(
                                   requirement, arguments.get('entity'),
                                   [row for row in self.knowledge.records_for(self.role)
                                    if row['kind'] == 'entity'],
                                   arguments.get('conditions') or {})):
                continue
            if name in {"lookup_spec", "check_software_support", "lookup_procedure"} or \
                    str(arguments.get("query") or arguments.get("text") or "").strip():
                ids.append(requirement["id"])
        return sorted(set(ids))

    def _sdk_usage_requirement_ids(self, matches: list[dict], *,
                                   ignore_descriptors: bool = False) -> list[str]:
        """Certify only exact SDK usage scopes, never device support selectors."""
        product_names = tuple(
            record['data']['name'] for record in (
                self.knowledge.records_for(self.role)
                if self.knowledge is not None
                and callable(getattr(self.knowledge, 'records_for', None))
                else [])
            if isinstance(record, dict) and record.get('kind') == 'entity'
            and isinstance(record.get('data'), dict)
            and isinstance(record['data'].get('name'), str))
        ids = []
        for requirement in self.requirements:
            if not isinstance(requirement, dict) or requirement.get("capability") != "sdk_evidence":
                continue
            if requirement.get("conditions_authority") == "platform_context_unverified" or \
                    requirement.get("reason") == "intent_or_entity_not_grounded":
                continue
            if (not ignore_descriptors and (
                    requirement.get('reason') == 'unmapped_condition_unverified'
                    or requirement.get('unmapped_condition_keys'))):
                # A row may be returned as partial context for named APIs,
                # but cannot certify a goal with an unreviewed selector.
                continue
            raw_conditions = requirement.get("conditions") or {}
            if not isinstance(raw_conditions, dict):
                continue
            aliases = {
                "sdk_name": "sdk", "software": "sdk",
                "language_binding": "language", "sdk_language": "language",
                "binding_language": "language", "sdk_build": "sdk_release_date",
                "build_date": "sdk_release_date",
            }
            conditions = {}
            for key, value in raw_conditions.items():
                canonical = aliases.get(key, key)
                if canonical in conditions and conditions[canonical] != value:
                    conditions = None
                    break
                conditions[canonical] = value
            if conditions is None or set(conditions) - {
                    "sdk_version", "sdk_release_date", "platform", "language", "sample_type",
                    "sdk", "task", "api", "export_files", "source_files",
                    "feature", "connection",
            }:
                continue
            entities = requirement.get("entities") or []
            if not isinstance(entities, list):
                continue
            for row in matches:
                if (self.question
                        and 'calibration_export' in row.get('usage_tasks', [])
                        and not calibration_export_row_matches_question(
                            row, calibration_export_known_scope(self.question),
                            known_product_names=product_names)):
                    continue
                layer = row["sdk_layer"]
                labels_by_layer = {
                    "host_cpp": {"ego", "entity:ego-1600", "entity:ego-1920",
                                 "orbbecsdk", "orbbecsdk2.9.0"},
                    "device_c": {"四目ego", "egopro", "entity:ego-pro",
                                 "libobkit", "rk3576"},
                    "android_java": {"ego", "entity:ego-1600", "entity:ego-1920",
                                     "orbbecsdk", "orbbecsdkandroidwrapper"},
                    "ble_cpp": {"egolowble", "ego", "entity:ego-1600",
                                "entity:ego-1920"},
                    "ios_framework": {"orbbecsdk", "ego", "entity:ego-1600",
                                      "entity:ego-1920"},
                }
                labels = labels_by_layer.get(layer, set())
                example_subjects = {
                    _identity_text(subject) for subject in row.get('example_subjects', [])
                    if isinstance(subject, str)
                }
                if any(_identity_text(str(entity)) not in labels | example_subjects
                       for entity in entities):
                    continue
                if ("sdk_version" in conditions and
                        str(conditions["sdk_version"]) != row["version"] and
                        re.sub(r"\D", "", str(conditions["sdk_version"])) != row["build"]):
                    continue
                if ("sdk_release_date" in conditions and
                        re.sub(r"\D", "", str(conditions["sdk_release_date"])) != row["build"]):
                    continue
                if (conditions.get('connection') and not (
                        conditions['connection'] == 'network'
                        and any('network' in str(item).casefold()
                                for item in [*row.get('usage_tasks', []),
                                             *row.get('topics', [])]))):
                    continue
                if ("platform" in conditions and
                        _identity_text({"linux_x64": "Linux x86_64",
                                        "linux_x86_64": "Linux x86_64",
                                        "windows_x64": "Windows x64"}.get(
                                            str(conditions["platform"]),
                                            str(conditions["platform"]))) not in
                        _identity_text(row["platform"]) and not (
                            str(conditions["platform"]) == "device_side" and
                            row["sdk_layer"] == "device_c") and not (
                            str(conditions["platform"]) == "host_side" and
                            row["sdk_layer"] == "host_cpp")):
                    continue
                sdk_name = _identity_text(str(conditions.get("sdk", ""))).replace('_', '')
                sdk_aliases = {
                    "host_cpp": {"orbbecsdk"}, "device_c": {"libobkit"},
                    "android_java": {"orbbecsdk", "orbbecsdkandroidwrapper",
                                     "orbbecsdkandroidjavawrapper", "androidwrapper",
                                     "androidjavawrapper", "orbbecsdkjavawrapper"},
                    "ble_cpp": {"egolowble"}, "ios_framework": {"orbbecsdk"},
                }
                if sdk_name and sdk_name not in sdk_aliases.get(layer, set()):
                    continue
                apis = conditions.get('api', [])
                apis = [apis] if isinstance(apis, str) else apis
                if (not isinstance(apis, list) or any(
                        not isinstance(api, str) or
                        not _sdk_api_selector_matches(api, row)
                        for api in apis)):
                    continue
                features = conditions.get('feature', [])
                features = [features] if isinstance(features, str) else features
                if (not isinstance(features, list) or any(
                        not isinstance(feature, str) or
                        not _sdk_usage_descriptor_matches(feature, row)
                        for feature in features)) and not ignore_descriptors:
                    continue
                export_files = conditions.get("export_files", [])
                if (not isinstance(export_files, list) or
                        any(not isinstance(name, str) or name not in row.get("outputs", [])
                            for name in export_files)):
                    continue
                source_files = conditions.get("source_files", [])
                if (not isinstance(source_files, list) or any(
                        not isinstance(name, str) or not re.fullmatch(
                            r'[A-Za-z][A-Za-z0-9_.-]*\.(?:cpp|c|h|hpp|java|swift)', name)
                        or not any(
                            str(ref.get("member", "")).replace("\\", "/").rsplit("/", 1)[-1] == name
                            and re.fullmatch(r'[0-9a-f]{64}', str(ref.get("member_sha256", "")))
                            for ref in row.get("source_refs", []))
                        for name in source_files)):
                    continue
                requested_tasks = conditions.get("task", [])
                requested_tasks = ([requested_tasks] if isinstance(requested_tasks, str)
                                   else requested_tasks)
                if (self.current_question and isinstance(requested_tasks, list)
                        and 'sdk_scope' in requested_tasks
                        and layer == 'device_c'
                        and 'image_binding' in row.get('topics', [])
                        and not device_sdk_scope_matches_question(
                            self.current_question)):
                    continue
                if (not isinstance(requested_tasks, list) or
                        any(not isinstance(task, str) or
                            not _sdk_usage_descriptor_matches(task, row)
                            for task in requested_tasks)) and not ignore_descriptors:
                    continue
                language = _identity_text(str(conditions.get("language", "")))
                if language in {"c++", "cxx", "cplusplus"}:
                    language = "cpp"
                language_by_layer = {
                    "host_cpp": "cpp", "device_c": "c", "android_java": "java",
                    "ble_cpp": "cpp", "ios_framework": None,
                }
                if language and language != language_by_layer.get(layer):
                    continue
                sample_type = _identity_text(str(conditions.get("sample_type", "")))
                if sample_type == "officialexample":
                    sample_type = "example"
                if sample_type and sample_type != _identity_text(row["doc_type"]):
                    continue
                if isinstance(requirement.get("id"), str):
                    ids.append(requirement["id"])
                break
        return sorted(set(ids))

    def _release_note_requirement_ids(self, matches: list[dict]) -> list[str]:
        """Attribute a log statement to its dated search goal, never to support."""
        feature_terms = {
            'magnetometer': ('磁力计', 'magnetometer'),
            'network_device_enumeration': ('网络设备枚举',),
            'master_slave_sync': ('主从', '同步'),
        }
        ids = []
        for requirement in self.requirements:
            if (not isinstance(requirement, dict)
                    or requirement.get('capability') != 'search_knowledge'
                    or not isinstance(requirement.get('id'), str)
                    or requirement.get('conditions_authority') ==
                    'platform_context_unverified'):
                continue
            conditions = requirement.get('conditions') or {}
            entities = requirement.get('entities') or []
            if (not isinstance(conditions, dict) or not isinstance(entities, list)
                    or set(conditions) - {
                        'platform', 'sdk', 'sdk_release_date',
                        'viewer_release_date', 'viewer_version',
                        'firmware_version', 'feature', 'sdk_version'}):
                continue
            for row in matches:
                body = ' '.join(str(row.get(key, '')) for key in
                                ('title', 'answer', 'family'))
                family = _identity_text(str(row.get('family', '')))
                build = re.sub(r'\D', '', str(row.get('build', '')))
                if ('sdk_release_date' in conditions and
                        not family.startswith('orbbecsdk')):
                    continue
                if ('viewer_release_date' in conditions and
                        not family.startswith('egoviewer')):
                    continue
                if ('viewer_version' in conditions and
                        not family.startswith('egoviewer')):
                    continue
                if ('sdk_version' in conditions and
                        (not family.startswith('orbbecsdk') or
                         str(conditions['sdk_version']).lstrip('vV') !=
                         str(row.get('version', '')).lstrip('vV'))):
                    continue
                if ('firmware_version' in conditions and
                        'firmware' not in family):
                    continue
                if 'firmware' in family:
                    target = _firmware_requirement_identity(
                        self.question, entities, conditions.get('firmware_version'))
                    if target is None or target not in family:
                        continue
                if any(_identity_text(str(entity)) not in _identity_text(body)
                       for entity in entities):
                    continue
                if (conditions.get('sdk') and
                        _identity_text(str(conditions['sdk'])) != family):
                    continue
                desktop_viewer = (
                    str(conditions.get('platform', '')).casefold() == 'desktop'
                    and family.startswith('egoviewerdesktop')
                    and 'linux' in str(row.get('platform', '')).casefold()
                    and 'windows' in str(row.get('platform', '')).casefold())
                if (conditions.get('platform') and not desktop_viewer and
                        _identity_text(str(conditions['platform'])) not in
                        _identity_text(str(row.get('platform', '')))):
                    continue
                if any(not _release_build_matches(conditions[key], build)
                       for key in ('sdk_release_date', 'viewer_release_date')
                       if key in conditions):
                    continue
                if (conditions.get('viewer_version') and
                        str(conditions['viewer_version']).lstrip('vV') !=
                        str(row.get('version', '')).lstrip('vV')):
                    continue
                if conditions.get('firmware_version'):
                    wanted_version = str(conditions['firmware_version']).lstrip('vV')
                    current_version = str(row.get('version', '')).lstrip('vV')
                    if (wanted_version != current_version and not re.search(
                            r'(?<![0-9.])v?' + re.escape(wanted_version) +
                            r'(?![0-9.])', str(row.get('answer', '')), re.I)):
                        continue
                feature = conditions.get('feature')
                if feature and not any(term.casefold() in body.casefold()
                                       for term in feature_terms.get(
                                           str(feature), (str(feature),))):
                    continue
                ids.append(requirement['id'])
                break
        return sorted(set(ids))

    @staticmethod
    def _requirement_targets_entity(requirement: dict, entity: object,
                                    visible_entities: list[dict],
                                    tool_conditions: dict | None = None) -> bool:
        """Attribute a tool target only when both labels select one visible entity."""
        if not isinstance(entity, str):
            return False
        names = requirement.get('entities')
        if not isinstance(names, list) or len(names) != 1 or not isinstance(names[0], str):
            return False
        wanted = _identity_text(names[0])
        target = _identity_text(entity)
        selectors = ('variant', 'resolution_variant', 'hardware_revision')
        tool_conditions = tool_conditions or {}
        target_ids = {row['id'] for row in visible_entities
                      if target in _entity_identity_labels(row)
                      and all(row['scope'].get(key) == value for key, value in
                              tool_conditions.items() if key in selectors)}
        if len(target_ids) != 1:
            return False
        conditions = requirement.get('conditions')
        matches = []
        for row in visible_entities:
            if wanted not in _entity_identity_labels(row):
                continue
            if isinstance(conditions, dict) and any(
                key in conditions and _identity_text(str(conditions[key])) !=
                _identity_text(str(row['scope'].get(key, '')))
                for key in selectors
            ):
                continue
            matches.append(row['id'])
        return matches == [next(iter(target_ids))]

    @classmethod
    def _positive_spec_covers_requirement(cls, requirement: dict, row: dict,
                                          arguments: dict, rows: list[dict],
                                          entity_records: dict[str, dict],
                                          conflict_notices: list[dict]) -> bool:
        """A tool's extra mode filter cannot certify a broader multi-mode goal."""
        if row['kind'] != 'claim':
            return False
        required = requirement.get('conditions') or {}
        supplied = arguments.get('conditions') or {}
        if not isinstance(required, dict) or not isinstance(supplied, dict):
            return False
        if requirement.get('field') == 'total_downstream_port_power_output':
            claim_count = {**row['scope'], **row['data'].get('conditions', {})}.get(
                'simultaneous_port_count')
            if claim_count != required.get('simultaneous_port_count'):
                # Both a narrower claim and a broader claim fail the same
                # total-output obligation when their simultaneous counts differ.
                return False
        if any(notice['kind'] == 'claim'
               and notice['data'].get('entity_id') == row['data'].get('entity_id')
               and _field_intent_matches(str(requirement.get('field') or ''),
                                         str(notice['data'].get('field') or ''))
               and cls._scope_selectors_match(notice['scope'], required)
               and all(cls._condition_supported(notice, key, value, entity_records)
                       for key, value in required.items())
               for notice in conflict_notices):
            return False
        extra = {key: value for key, value in supplied.items() if key not in required}
        if (requirement.get('field') == 'total_downstream_port_power_output'
                and 'simultaneous_port_count' in extra):
            # A total measured for N simultaneously used ports cannot certify
            # an unqualified total for a different or unspecified port count.
            return False
        if not extra:
            return True
        peers = [peer for peer in rows if peer['kind'] == 'claim'
                 and peer['status'] == row['status']
                 and peer['data'].get('entity_id') == row['data'].get('entity_id')
                 and _field_intent_matches(str(requirement.get('field') or ''),
                                           str(peer['data'].get('field') or ''))
                 and cls._scope_selectors_match(peer['scope'], required)
                 and all(cls._condition_supported(peer, key, value, entity_records)
                         for key, value in required.items())]
        return bool(peers) and all(
            cls._condition_supported(peer, key, value, entity_records)
            for peer in peers for key, value in extra.items())

    @staticmethod
    def _entity_names(row: dict, entities: dict[str, str],
                      topologies: dict[str, dict]) -> set[str]:
        data = row["data"]
        ids = ([row["id"]] if row["kind"] == "entity" else
               data.get("members", []) if row["kind"] == "topology" else
               topologies.get(data.get("topology_id"), {}).get("data", {}).get("members", [])
               if row["kind"] == "procedure" else
               [data.get("entity_id")] if data.get("entity_id") else [])
        return {str(item).casefold() for entity_id in ids if entity_id
                for item in (entity_id, entities.get(entity_id, "")) if item}

    @classmethod
    def _matches(cls, name: str, arguments: dict, row: dict,
                 entities: dict[str, str], topologies: dict[str, dict],
                 entity_records: dict[str, dict] | None = None,
                 *, candidate_preview: bool = False) -> bool:
        kind = row["kind"]
        data = row["data"]
        text = cls._record_text(row)
        raw_query = str(arguments.get("query") or arguments.get("text") or "").strip()
        query = raw_query.casefold()
        if name not in {"catalog", "resolve_entity", "lookup_spec",
                        "check_software_support", "sdk_evidence",
                        "search_knowledge"} and not cls._scope_selectors_match(
            row["scope"], {}
        ):
            return False
        if name == "catalog":
            # The catalog is a directory scan. Natural-language overview
            # questions are not literal substrings of individual records.
            return kind == 'entity' and not (
                data.get('entity_type') == 'family' and any(
                    entity['data'].get('family_entity_id') == row['id']
                    for entity in (entity_records or {}).values()))
        if name == "resolve_entity":
            if kind != "entity" or not query:
                return False
            identity = _identity_text(query)
            name_text = _identity_text(str(data["name"]))
            named = {}
            family_selectors = {
                entity_id: [peer['scope'][key]
                            for peer in (entity_records or {}).values()
                            if peer['kind'] == 'entity'
                            and peer['scope'].get('product') == entity_row['scope'].get('product')
                            for key in peer['scope'].get('required_selectors', [])]
                for entity_id, entity_row in (entity_records or {}).items()
                if entity_row['kind'] == 'entity'
            }
            for entity_id, entity_name in entities.items():
                entity_row = (entity_records or {}).get(entity_id, {})
                labels = [entity_name, *entity_row.get('data', {}).get('aliases', [])]
                hits = [_identity_text(label) for label in labels
                        if (_candidate_name_in_query(label, raw_query) if candidate_preview
                            else _reviewed_name_in_query(
                                label, raw_query,
                                selector_values=family_selectors.get(entity_id, ())))]
                if _identity_text(raw_query) == _identity_text(entity_id):
                    hits.append(_identity_text(entity_id))
                if hits:
                    named[entity_id] = max(map(len, hits))
            if named:
                longest = max(named.values())
                if named.get(row['id']) != longest:
                    return False
                peers = [candidate for candidate in (entity_records or {}).values()
                         if candidate['kind'] == 'entity'
                         and _identity_text(candidate['data']['name']) == name_text]
                selected = any(
                    _identity_text(str(candidate['scope'][key])) in identity
                    for candidate in peers
                    for key in candidate['scope'].get('required_selectors', [])
                )
                return not selected or all(
                    _identity_text(str(row['scope'][key])) in identity
                    for key in row['scope'].get('required_selectors', []))
            # A reviewed product key may be a short public alias (HUB), but
            # matching it never outranks a longer reviewed entity name.
            alias = str(row['scope'].get('product', ''))
            alias_token = ((
                _candidate_name_in_query(alias, raw_query) if candidate_preview
                else _reviewed_name_in_query(
                    alias, raw_query,
                    selector_values=family_selectors.get(row['id'], ())))
                if alias else False)
            suffix = name_text[len(identity):] if name_text.startswith(identity) else ''
            reviewed_selector_suffix = bool(suffix) and any(
                _identity_text(str(value)) == suffix
                for key, value in row['scope'].items()
                if key not in {'product', 'required_selectors'}
                and isinstance(value, (str, int)))
            return bool(alias_token or (len(identity) >= 3 and reviewed_selector_suffix))
        if name == "lookup_spec":
            if kind != "claim":
                return False
            field = str(arguments.get("field") or "").casefold()
            entity = str(arguments.get("entity") or "").casefold()
            conditions = arguments.get("conditions") or {}
            if not isinstance(conditions, dict) or any(
                not cls._condition_supported(row, key, value, entity_records or {})
                for key, value in conditions.items()
            ) or any(key not in conditions for key in data["conditions"]) or \
                    not cls._scope_selectors_match(row["scope"], conditions):
                return False
            return kind == "claim" and bool(field and entity) and \
                data["field"].casefold() == field and entity in cls._entity_names(row, entities, topologies)
        if name in {"inspect_topology", "selection"}:
            if kind != 'topology' or not query:
                return False
            if candidate_preview and name in {'inspect_topology', 'selection'}:
                return (_candidate_query_scope_safe(row, raw_query) and
                        _candidate_topology_entities_match(
                            row, raw_query, entity_records or {}) and
                        (query in text or _candidate_alias_match(row, raw_query)))
            return query in text
        if name == "lookup_procedure":
            task = str(arguments.get("task") or "").casefold()
            entity = str(arguments.get("entity") or "").casefold()
            topology = topologies.get(data.get('topology_id')) if kind == 'procedure' else None
            scoped_query = task + ' ' + entity
            if candidate_preview and (
                    topology is None or
                    not _candidate_query_scope_safe(row, scoped_query) or
                    not _candidate_topology_entities_match(
                        topology, scoped_query, entity_records or {})):
                return False
            return kind == "procedure" and bool(task and entity) and \
                (task == str(data["task"]).casefold() or (candidate_preview and
                 (_candidate_alias_match(row, task) or
                  row['status'] == 'verified' and
                  task in data.get('task_intents', []) and
                  _candidate_query_scope_safe(row, task) or
                  task in data.get('evidence_conditions', {}).get('steps', []) or
                  _procedure_task_paraphrase_matches(task, row)))) and \
                (entity in cls._entity_names(row, entities, topologies)
                 or candidate_preview and topology is not None and
                 _candidate_topology_entities_match(
                     topology, entity, entity_records or {}))
        if name in {'check_software_support', 'sdk_evidence'}:
            return support_gap(row, arguments, cls._entity_names(row, entities, topologies), topologies) is None
        if name == "official_links":
            return kind == "link" and row["status"] == "verified" and bool(query) and query in text
        if name in {"experience", "risk"}:
            return kind == "claim" and data.get("field") == name and bool(query) and query in text
        if name == "search_knowledge":
            return kind != "link" and bool(query) and query in text and \
                (kind == "entity" or cls._scope_selectors_match(row["scope"], {}))
        return False

    @classmethod
    def _requirement_matches(cls, requirement: dict, row: dict,
                             entities: dict[str, str], topologies: dict[str, dict],
                             entity_records: dict[str, dict] | None = None,
                             *, candidate_preview: bool = False) -> bool:
        if is_document_statement(row):
            return False
        if requirement.get("reason") in {"intent_or_entity_not_grounded",
                                          "unmapped_condition_unverified"} or \
                requirement.get("conditions_authority") == "platform_context_unverified":
            return False
        field = requirement.get("field")
        if requirement.get("capability") == "lookup_spec" and not field:
            return False
        if field is not None and (row["kind"] != "claim" or not _field_intent_matches(
                str(field), str(row["data"].get("field", "")))):
            return False
        software = requirement.get("software")
        if software is not None and (row["kind"] != "software" or
                                    str(software).casefold() not in
                                    row["data"].get("software", "").casefold()):
            return False
        names = cls._entity_names(row, entities, topologies)
        required_entities = requirement.get("entities") or []
        if (requirement.get('capability') == 'catalog' and required_entities and any(
                not isinstance(name, str) or not any(
                    _identity_text(name) in _entity_identity_labels(entity)
                    for entity in (entity_records or {}).values())
                for name in required_entities)):
            # An OR over directory rows must not certify an unresolved member
            # of the user's requested set merely because another member exists.
            return False
        if not required_entities and row["kind"] not in {"entity", "link"}:
            return False
        if requirement.get("capability") in {
            "lookup_spec", "lookup_procedure", "check_software_support",
            "inspect_topology", "selection",
        } and not required_entities:
            return False
        def reviewed_entity_matches(item: str) -> bool:
            if item.casefold() in names:
                return True
            candidates = []
            for entity in (entity_records or {}).values():
                labels = [entity['id'], str(entity['data']['name']),
                          *entity['data'].get('aliases', []),
                          str(entity['scope'].get('product', ''))]
                hits = [_identity_text(label) for label in labels if label and (
                    _reviewed_name_in_query(label, item)
                    or _identity_text(label) == _identity_text(item))]
                if hits:
                    candidates.append((entity, max(map(len, hits))))
            if not candidates:
                return False
            longest = max(length for _, length in candidates)
            def generic_family_label(entity):
                peers = [peer for peer in (entity_records or {}).values()
                         if peer['data'].get('family_entity_id')
                         and peer['data'].get('family_entity_id') ==
                         entity['data'].get('family_entity_id')]
                return len(peers) >= 2 and all(
                    _identity_text(item) in _entity_identity_labels(peer) for peer in peers)
            return any((entity['id'].casefold() in names or (
                           requirement.get('capability') == 'catalog'
                           and row['kind'] == 'entity'
                           and row['data'].get('family_entity_id') == entity['id']) or (
                           row['kind'] in {'topology', 'procedure'}
                           and generic_family_label(entity)
                           and str(entity['data'].get('family_entity_id', '')).casefold() in names))
                       for entity, length in candidates if length == longest)

        entity_matches = [isinstance(item, str) and reviewed_entity_matches(item)
                          for item in required_entities]
        if required_entities and (not isinstance(required_entities, list) or not (
                any(entity_matches) if requirement.get('capability') == 'catalog'
                else all(entity_matches))):
            return False
        conditions = requirement.get("conditions") or {}
        if not isinstance(conditions, dict) or (
            not (row['kind'] == 'entity' and (
                requirement.get('capability') == 'catalog'
                or (requirement.get('capability') == 'resolve_entity' and not conditions)))
            and not cls._scope_selectors_match(row["scope"], conditions)
        ):
            return False
        known = {**row["scope"], **row["data"].get("conditions", {})}
        if candidate_preview and row['status'] == 'verified' and row['kind'] in {
                'topology', 'procedure'}:
            known.update(row['data'].get('evidence_conditions', {}))
        if row["kind"] == "software":
            data = row["data"]
            task = conditions.get("task")
            if not task:
                return False
            tasks = task if isinstance(task, list) else [task]
            normalized_tasks = {
                {"recording": "record", "storage": "save", "startup": "start"}.get(
                    str(item).casefold(), str(item).casefold()
                ) for item in tasks
            }
            if data["capability"].casefold() not in normalized_tasks:
                return False
            known.update({"platform": data["platform"], "variant": data["hardware_revision"],
                          "connection": data["connection_mode"]})
            versions = software_version_conditions(data)
            if versions is None or any(
                key in known and known[key] != value for key, value in versions.items()
            ):
                return False
            known.update(versions)
            if not {"platform", "variant", "connection"} <= set(conditions):
                return False
        elif row["kind"] == "procedure":
            topology = topologies.get(row["data"]["topology_id"])
            if topology is None:
                return False
            known.update({"platform": topology["data"]["platform"],
                          "task": row["data"]["task"]})
        def condition_matches(key, value):
            if key == 'task' and isinstance(value, str) and row['kind'] in {'topology', 'procedure'}:
                return value == known.get(key) or value in row['data'].get('task_intents', [])
            if candidate_preview and key == 'topology_aspects':
                available = known.get(key)
                return (isinstance(value, list) and isinstance(available, list)
                        and bool(value) and set(value) <= set(available))
            if candidate_preview and key == 'indicators':
                available = known.get(key)
                return (isinstance(value, list) and isinstance(available, list)
                        and bool(value) and all(
                            item in available or
                            (item == 'led_state' and any(
                                indicator.endswith('_led') for indicator in available))
                            for item in value))
            if candidate_preview and key == 'steps':
                available = known.get(key)
                if not isinstance(value, list) or not isinstance(available, list) or not value:
                    return False
                remaining = iter(available)
                return all(any(candidate == step for candidate in remaining)
                           for step in value)
            if candidate_preview and row['kind'] == 'procedure' and \
                    row['status'] == 'verified' and key == 'task':
                tasks = value if isinstance(value, list) else [value]
                intents = row['data'].get('task_intents', [])
                if isinstance(intents, list) and intents:
                    return bool(tasks) and all(
                        str(item).casefold() in intents or
                        _procedure_task_paraphrase_matches(str(item), row)
                        for item in tasks)
            return (cls._condition_supported(row, key, value, entity_records or {})
                    if key == 'scope' else known.get(key) == value)

        return not any(
            not condition_matches(key, value)
            for key, value in conditions.items()
            if not (row["kind"] == "software" and key == "task")
        )

    def redact_tool_input(self, _name: str, arguments: dict) -> dict:
        # This release has no credentials in tool arguments; keep bounded audit data.
        return {str(key): str(value)[:256] for key, value in arguments.items()}
