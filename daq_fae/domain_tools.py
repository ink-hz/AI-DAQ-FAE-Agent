"""DAQ domain tools over an optional, role-filtered reviewed Dev release."""

from __future__ import annotations

import ast
import json
import re
import unicodedata

from src.agent.loop.tools import ToolResult

from daq_fae.knowledge.reviewed_view import ReviewedKnowledge, reviewed_name_in_query as _reviewed_name_in_query
from daq_fae.knowledge.source_paths import contains_source_path
from daq_fae.knowledge.software_evidence import missing_conditions, support_gap, software_version_conditions
from daq_fae.knowledge.section_search import (
    EXCERPT_CHARS, MAX_BODY_CHARS, MAX_QUERY_CHARS, body_text, rank_sections,
)


_TOOLS: dict[str, tuple[str, dict[str, dict], tuple[str, ...]]] = {
    "catalog": ("List governed DAQ products, kits, and release scope.", {"query": {"type": "string"}}, ("query",)),
    "selection": ("Evaluate a DAQ configuration against all stated acquisition constraints.", {"query": {"type": "string"}}, ("query",)),
    "resolve_entity": ("Resolve a DAQ product, variant, kit, or component. Returns reviewed spec field coverage without values; use lookup_spec before answering a field.", {"text": {"type": "string"}}, ("text",)),
    "lookup_spec": ("Look up a governed DAQ claim. Use a canonical field such as rgb_resolution or rgb_camera_count; conditions must be a JSON object, for example {\"resolution_variant\":\"1600x1200\"}. A generic product name cannot select a variant.", {"entity": {"type": "string"}, "field": {"type": "string"}, "conditions": {"type": "object"}}, ("entity", "field")),
    "inspect_topology": ("Inspect a verified acquisition system topology.", {"query": {"type": "string"}}, ("query",)),
    "lookup_procedure": ("Find an applicable, reviewed acquisition procedure.", {"task": {"type": "string"}, "entity": {"type": "string"}}, ("task",)),
    "check_software_support": ("Check exact product, revision, connection, platform, software version and capability; include variant selectors in conditions.", {"entity": {"type": "string"}, "software": {"type": "string"}, "platform": {"type": "string"}, "version": {"type": "string"}, "hardware_revision": {"type": "string"}, "connection_mode": {"type": "string"}, "capability": {"type": "string"}, "conditions": {"type": "object"}}, ("entity", "software", "platform", "version", "hardware_revision", "connection_mode", "capability")),
    "read_doc": ("Read one authorized reviewed section by section_id; preserve its scope and conditions.", {"section_id": {"type": "string"}}, ("section_id",)),
    "search_knowledge": ("Find authorized reviewed sections and uncovered records by natural-language query; use read_doc for a full section. Retrieval relevance does not certify a typed fact.", {"query": {"type": "string"}}, ("query",)),
    "sdk_evidence": ("Find source-checked SDK API and example usage. Usage evidence does not certify a device or firmware combination; exact support requires all software selectors and device tests.", {"query": {"type": "string"}}, ("query",)),
    "official_links": ("Find authorized and verified official DAQ links.", {"query": {"type": "string"}}, ("query",)),
    "experience": ("Find reviewed field cases and diagnostic experience; not product specifications.", {"query": {"type": "string"}}, ("query",)),
    "risk": ("Find reviewed operating and data integrity risks for an acquisition setup.", {"query": {"type": "string"}}, ("query",)),
    "session_state": ("Read user-provided context in the current DAQ session.", {}, ()),
}


def _identity_text(value: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).casefold().replace('×', 'x'))


def _sdk_usage_descriptor_matches(value: str, row: dict) -> bool:
    """Match a descriptive SDK goal against source reviewed tags and API names."""
    labels = [str(item) for key in ("usage_tasks", "topics", "symbols")
              for item in row.get(key, []) if isinstance(item, str)]
    def normalized(text: str) -> str:
        return re.sub(r"\bsd[_\s-]?card(?![a-z])", "sdcard", text.casefold())
    if _identity_text(normalized(value)) in {
            _identity_text(normalized(label)) for label in labels}:
        return True
    aliases = {
        "initialization": "init", "initialize": "init", "initialise": "init",
        "initializing": "init", "startup": "start", "starting": "start",
        "stopping": "stop", "configuration": "config", "configure": "config",
        "compilation": "build", "compile": "build",
    }
    ignored = {"and", "the", "of", "order", "sequence", "how", "to", "example", "sample"}
    def tokens(text: str) -> set[str]:
        return {aliases.get(token, token) for token in re.findall(r"[a-z0-9]+", normalized(text))
                if token not in ignored}
    wanted = tokens(value)
    known = tokens(" ".join(labels))
    overlap = wanted & known
    return len(wanted) >= 2 and len(overlap) >= 2 and len(overlap) * 5 >= len(wanted) * 3


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


def _catalog_summary(rows: list[dict], *, candidate_preview: bool) -> dict:
    """Count the whole role-visible directory and label its publication state."""
    by_type: dict[str, int] = {}
    product_names: set[str] = set()
    for row in rows:
        entity_type = row['data']['entity_type']
        by_type[entity_type] = by_type.get(entity_type, 0) + 1
        if entity_type in {'device', 'variant', 'hub', 'kit'}:
            product_names.add(row['data']['name'])
    names = sorted(product_names)
    return {
        'entry_count': len(rows),
        'distinct_product_name_count': len(names),
        'product_names': names,
        'by_entity_type': dict(sorted(by_type.items())),
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

    def __init__(self, *, question: str = "", context: dict | None = None,
                 knowledge: ReviewedKnowledge | None = None, role: str | None = None,
                 requirements: list[dict] | None = None, access_guard=None,
                 allowed_tools: frozenset[str] | None = None):
        if knowledge is not None and role is None:
            raise ValueError("knowledge role required")
        self.question = question
        self.context = dict(context or {})
        self.knowledge = knowledge
        self.role = role
        self.requirements = list(requirements or [])
        self.access_guard = access_guard
        self.allowed_tools = allowed_tools

    def with_request_context(self, question: str) -> "DaqToolBox":
        return DaqToolBox(question=question, context=self.context,
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
        if name == "sdk_evidence" and getattr(self.knowledge, "sdk_catalog", None) is not None \
                and not all(arguments.get(key) for key in (
                    "entity", "software", "platform", "version", "hardware_revision",
                    "connection_mode", "capability")):
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
                return ToolResult(status="tool_error", content={"error": "invalid_tool_arguments"})
            matches = self.knowledge.sdk_catalog.search(self.question or query, self.role)
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
                "matches": [{key: row[key] for key in (
                    "kb_id", "title", "answer", "sdk_layer", "doc_type", "version",
                    "build", "platform", "symbols", "code_tokens", "confidence", "last_verified")}
                    for row in matches],
                "matched_requirement_ids": self._sdk_usage_requirement_ids(matches),
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
        conflict_notices = (self.knowledge.conflict_notices_for(self.role)
                            if name == 'lookup_spec' else [])
        entities = {row["id"]: row["data"]["name"] for row in rows
                    if row["kind"] == "entity"}
        entity_records = {row["id"]: row for row in rows if row["kind"] == "entity"}
        topologies = {row["id"]: row for row in rows if row["kind"] == "topology"}
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
        matches = [row for row in rows if self._matches(
            name, arguments, row, entities, topologies, entity_records,
            candidate_preview=candidate_preview)]
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
                mismatched = {}
                for row in scoped:
                    for key, value in row['data']['conditions'].items():
                        if key in supplied and supplied[key] != value:
                            mismatched.setdefault(key, {})[
                                json.dumps(value, ensure_ascii=False, sort_keys=True)] = value
                if mismatched:
                    return ToolResult(status='not_found', content={
                        'reason': 'claim_conditions_mismatch', 'claim_status': 'unknown',
                        'available_claim_conditions': {
                            key: [value for _, value in sorted(values.items())]
                            for key, values in sorted(mismatched.items())},
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
                return ToolResult(status="not_found", content={
                    "reason": "variant_selector_required", "claim_status": "unknown",
                    ("source_checked_variant_scopes" if getattr(
                        self.knowledge, "is_candidate_preview", False)
                     else "verified_variant_scopes"): choices,
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
                self.knowledge, 'is_candidate_preview', False))) if name == 'catalog' else None
        if name != 'catalog':
            matches = matches[:20]
        field_coverage = {}
        if name == 'resolve_entity':
            for entity_row in matches:
                fields = sorted({str(row['data']['field']) for row in rows
                                 if row['kind'] == 'claim'
                                 and row['status'] == 'verified'
                                 and row['data'].get('entity_id') == entity_row['id']})
                field_coverage[entity_row['id']] = fields
        matched_requirements = sorted({
            requirement["id"] for requirement in self.requirements
            if isinstance(requirement, dict) and isinstance(requirement.get("id"), str)
            and requirement.get("capability") == name
            and any(self._requirement_matches(requirement, row, entities, topologies,
                                              entity_records)
                    and (name != 'lookup_spec' or self._positive_spec_covers_requirement(
                        requirement, row, arguments, rows, entity_records,
                        conflict_notices))
                    for row in matches)
        })
        return ToolResult(status="ok", content={
            "release_id": self.knowledge.release_id,
            **({'catalog_summary': catalog_summary} if catalog_summary is not None else {}),
            **({'available_spec_fields': field_coverage,
                'available_spec_fields_are_values': False}
               if name == 'resolve_entity' else {}),
            **({'query_conditions_completed_from_unique_claim': completed_claim_conditions}
               if completed_claim_conditions else {}),
            **({'question_targets_not_claim_conditions': question_targets}
               if question_targets else {}),
            **({"query_scope_normalized": scope_normalized} if
               getattr(self.knowledge, "is_candidate_preview", False) else {}),
            "evidence_level": ("dev_source_checked" if
                               getattr(self.knowledge, "is_candidate_preview", False)
                               else "reviewed"),
            "matches": [{**{key: row[key] for key in ("id", "kind", "status", "scope", "data")},
                         **({"status": "source_checked"} if
                            getattr(self.knowledge, "is_candidate_preview", False) else {})}
                        for row in matches],
            "matched_requirement_ids": matched_requirements,
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
        candidates = [row for row in rows if row['id'] not in covered
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
        candidate_preview = bool(getattr(self.knowledge, "is_candidate_preview", False))
        for section in matches:
            item = {key: section[key] for key in (
                "section_id", "title", "knowledge_type", "scope")}
            item.update(evidence_layer=("source_candidate" if candidate_preview else "reviewed_section"),
                        status=("candidate" if candidate_preview else "verified"))
            for key in ("entity_id", "entity_ids", "topology_id", "conditions",
                        "hardware_revision", "software_versions", "link_ids"):
                if key in section:
                    item[key] = section[key]
            if name == "read_doc":
                item["body"] = section["body"]
            else:
                text = body_text(section["body"])
                item.update(excerpt=text[:EXCERPT_CHARS],
                            excerpt_truncated=len(text) > EXCERPT_CHARS)
            projected.append(item)
        return ToolResult(status="ok", content={
            "release_id": self.knowledge.release_id, "matches": projected,
            # Retrieval relevance cannot certify typed facts or exact conditions.
            "matched_requirement_ids": [],
        }, sources=[{
            "type": "daq_source_candidate" if candidate_preview else "daq_governed_section",
            "verification_status": "candidate" if candidate_preview else "verified",
            "release_id": self.knowledge.release_id, "source_id": s["section_id"],
            "scope": s["scope"], "source_refs": [{
                "source_id": "source:" + ref["sha256"], "sha256": ref["sha256"],
                "locator": ref["locator"],
            } for ref in s["source_refs"]],
        } for s in matches])

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

    def _sdk_usage_requirement_ids(self, matches: list[dict]) -> list[str]:
        """Certify only exact SDK usage scopes, never device support selectors."""
        ids = []
        for requirement in self.requirements:
            if not isinstance(requirement, dict) or requirement.get("capability") != "sdk_evidence":
                continue
            if requirement.get("conditions_authority") == "platform_context_unverified" or \
                    requirement.get("reason") == "intent_or_entity_not_grounded":
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
                    "sdk", "task", "api", "export_files", "feature",
            }:
                continue
            entities = requirement.get("entities") or []
            if not isinstance(entities, list):
                continue
            for row in matches:
                labels = ({"ego", "orbbecsdk", "orbbecsdk2.9.0"}
                          if row["sdk_layer"] == "host_cpp" else
                          {"四目ego", "libobkit", "rk3576"})
                if any(_identity_text(str(entity)) not in labels for entity in entities):
                    continue
                if ("sdk_version" in conditions and
                        str(conditions["sdk_version"]) != row["version"] and
                        re.sub(r"\D", "", str(conditions["sdk_version"])) != row["build"]):
                    continue
                if ("sdk_release_date" in conditions and
                        re.sub(r"\D", "", str(conditions["sdk_release_date"])) != row["build"]):
                    continue
                if ("platform" in conditions and
                        _identity_text(str(conditions["platform"])) not in
                        _identity_text(row["platform"]) and not (
                            str(conditions["platform"]) == "device_side" and
                            row["sdk_layer"] == "device_c") and not (
                            str(conditions["platform"]) == "host_side" and
                            row["sdk_layer"] == "host_cpp")):
                    continue
                expected_sdk = "orbbecsdk" if row["sdk_layer"] == "host_cpp" else "libobkit"
                sdk_name = _identity_text(str(conditions.get("sdk", "")))
                if sdk_name and sdk_name != expected_sdk:
                    continue
                if ("api" in conditions and
                        _identity_text(str(conditions["api"])) not in {
                            _identity_text(symbol) for symbol in row.get("symbols", [])}):
                    continue
                if ("feature" in conditions and not _sdk_usage_descriptor_matches(
                        str(conditions["feature"]), row)):
                    continue
                export_files = conditions.get("export_files", [])
                if (not isinstance(export_files, list) or
                        any(not isinstance(name, str) or name not in row.get("outputs", [])
                            for name in export_files)):
                    continue
                requested_tasks = conditions.get("task", [])
                requested_tasks = ([requested_tasks] if isinstance(requested_tasks, str)
                                   else requested_tasks)
                if (not isinstance(requested_tasks, list) or
                        any(not isinstance(task, str) or
                            not _sdk_usage_descriptor_matches(task, row)
                            for task in requested_tasks)):
                    continue
                language = _identity_text(str(conditions.get("language", "")))
                if language in {"c++", "cxx", "cplusplus"}:
                    language = "cpp"
                if language and language != ("cpp" if row["sdk_layer"] == "host_cpp" else "c"):
                    continue
                if ("sample_type" in conditions and
                        (conditions["sample_type"] != "official_example"
                         or row["doc_type"] != "example")):
                    continue
                if isinstance(requirement.get("id"), str):
                    ids.append(requirement["id"])
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
            return kind == "entity"
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
            return kind == "topology" and bool(query) and query in text
        if name == "lookup_procedure":
            task = str(arguments.get("task") or "").casefold()
            entity = str(arguments.get("entity") or "").casefold()
            return kind == "procedure" and bool(task and entity) and \
                task == str(data["task"]).casefold() and \
                entity in cls._entity_names(row, entities, topologies)
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
                             entity_records: dict[str, dict] | None = None) -> bool:
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
                labels = [str(entity['data']['name']),
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
            return any(entity['id'].casefold() in names
                       for entity, length in candidates if length == longest)

        if required_entities and (not isinstance(required_entities, list) or not all(
            isinstance(item, str) and reviewed_entity_matches(item)
            for item in required_entities
        )):
            return False
        conditions = requirement.get("conditions") or {}
        if not isinstance(conditions, dict) or not cls._scope_selectors_match(
            row["scope"], conditions
        ):
            return False
        known = {**row["scope"], **row["data"].get("conditions", {})}
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
        if any(
            not (cls._condition_supported(row, key, value, entity_records or {})
                 if key == 'scope' else known.get(key) == value)
            for key, value in conditions.items()
            if not (row["kind"] == "software" and key == "task")
        ):
            return False
        return True

    def redact_tool_input(self, _name: str, arguments: dict) -> dict:
        # This release has no credentials in tool arguments; keep bounded audit data.
        return {str(key): str(value)[:256] for key, value in arguments.items()}
