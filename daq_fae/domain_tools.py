"""DAQ domain tools over an optional, role-filtered reviewed Dev release."""

from __future__ import annotations

import json

from src.agent.loop.tools import ToolResult

from daq_fae.knowledge.reviewed_view import ReviewedKnowledge


_TOOLS: dict[str, tuple[str, dict[str, dict], tuple[str, ...]]] = {
    "catalog": ("List governed DAQ products, kits, and release scope.", {"query": {"type": "string"}}, ("query",)),
    "selection": ("Evaluate a DAQ configuration against all stated acquisition constraints.", {"query": {"type": "string"}}, ("query",)),
    "resolve_entity": ("Resolve a DAQ product, variant, kit, or component.", {"text": {"type": "string"}}, ("text",)),
    "lookup_spec": ("Look up a governed DAQ claim. Include required variant selectors in conditions; a generic product name cannot select a variant.", {"entity": {"type": "string"}, "field": {"type": "string"}, "conditions": {"type": "object"}}, ("entity", "field")),
    "inspect_topology": ("Inspect a verified acquisition system topology.", {"query": {"type": "string"}}, ("query",)),
    "lookup_procedure": ("Find an applicable, reviewed acquisition procedure.", {"task": {"type": "string"}, "entity": {"type": "string"}}, ("task",)),
    "check_software_support": ("Check exact product, revision, connection, platform, software version and capability; include variant selectors in conditions.", {"entity": {"type": "string"}, "software": {"type": "string"}, "platform": {"type": "string"}, "version": {"type": "string"}, "hardware_revision": {"type": "string"}, "connection_mode": {"type": "string"}, "capability": {"type": "string"}, "conditions": {"type": "object"}}, ("entity", "software", "platform", "version", "hardware_revision", "connection_mode", "capability")),
    "search_knowledge": ("Search the governed DAQ knowledge release.", {"query": {"type": "string"}}, ("query",)),
    "sdk_evidence": ("Find reviewed SDK evidence for DAQ products and combinations.", {"query": {"type": "string"}}, ("query",)),
    "official_links": ("Find authorized and verified official DAQ links.", {"query": {"type": "string"}}, ("query",)),
    "experience": ("Find reviewed field cases and diagnostic experience; not product specifications.", {"query": {"type": "string"}}, ("query",)),
    "risk": ("Find reviewed operating and data integrity risks for an acquisition setup.", {"query": {"type": "string"}}, ("query",)),
    "session_state": ("Read user-provided context in the current DAQ session.", {}, ()),
}


class DaqToolBox:
    attachment_source_ids: list[str] = []
    attachment_vision_model = None
    device_support = None

    def __init__(self, *, question: str = "", context: dict | None = None,
                 knowledge: ReviewedKnowledge | None = None, role: str | None = None,
                 requirements: list[dict] | None = None):
        if knowledge is not None and role is None:
            raise ValueError("knowledge role required")
        self.question = question
        self.context = dict(context or {})
        self.knowledge = knowledge
        self.role = role
        self.requirements = list(requirements or [])

    def with_request_context(self, question: str) -> "DaqToolBox":
        return DaqToolBox(question=question, context=self.context,
                          knowledge=self.knowledge, role=self.role,
                          requirements=self.requirements)

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
        } for name, (description, properties, required) in _TOOLS.items()]

    def dispatch(self, name: str, arguments: dict) -> ToolResult:
        if name not in _TOOLS:
            return ToolResult(status="tool_error", content={"error": "unknown_tool"})
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
        rows = self.knowledge.records_for(self.role)
        entities = {row["id"]: row["data"]["name"] for row in rows
                    if row["kind"] == "entity"}
        topologies = {row["id"]: row for row in rows if row["kind"] == "topology"}
        matches = [row for row in rows if self._matches(name, arguments, row, entities, topologies)]
        attempted = self._attempted_requirement_ids(name, arguments)
        if name == "lookup_spec" and len({
            json.dumps([row["status"], row["data"].get("value"), row["data"].get("unit")],
                       ensure_ascii=False, sort_keys=True) for row in matches
        }) > 1:
            return ToolResult(status="not_found", content={
                "reason": "scope_or_conditions_required", "claim_status": "unknown",
                "matches": [], "matched_requirement_ids": attempted,
            })
        if not matches:
            return ToolResult(status="not_found", content={
                "reason": "reviewed_evidence_unavailable", "claim_status": "unknown",
                "matches": [], "matched_requirement_ids": attempted,
            })
        matches = matches[:20]
        matched_requirements = sorted({
            requirement["id"] for requirement in self.requirements
            if isinstance(requirement, dict) and isinstance(requirement.get("id"), str)
            and requirement.get("capability") == name
            and any(self._requirement_matches(requirement, row, entities, topologies)
                    for row in matches)
        })
        return ToolResult(status="ok", content={
            "release_id": self.knowledge.release_id,
            "matches": [{key: row[key] for key in ("id", "kind", "status", "scope", "data")}
                        for row in matches],
            "matched_requirement_ids": matched_requirements,
        }, sources=[{
            "type": "daq_governed_claim", "verification_status": "verified",
            "release_id": self.knowledge.release_id, "source_id": row["id"],
            "source_refs": row["source_refs"],
        } for row in matches])

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

    def _attempted_requirement_ids(self, name: str, arguments: dict) -> list[str]:
        ids = []
        for requirement in self.requirements:
            if not isinstance(requirement, dict) or requirement.get("capability") != name or \
                    not isinstance(requirement.get("id"), str):
                continue
            field = requirement.get("field")
            if field is not None and str(arguments.get("field") or "").casefold() != str(field).casefold():
                continue
            software = requirement.get("software")
            if software is not None and str(arguments.get("software") or "").casefold() != str(software).casefold():
                continue
            required_entities = requirement.get("entities") or []
            if required_entities and name in {"lookup_spec", "check_software_support"} and \
                    str(arguments.get("entity") or "").casefold() not in {
                        str(item).casefold() for item in required_entities
                    }:
                continue
            if name in {"lookup_spec", "check_software_support", "lookup_procedure"} or \
                    str(arguments.get("query") or arguments.get("text") or "").strip():
                ids.append(requirement["id"])
        return sorted(set(ids))

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
                 entities: dict[str, str], topologies: dict[str, dict]) -> bool:
        kind = row["kind"]
        data = row["data"]
        text = cls._record_text(row)
        query = str(arguments.get("query") or arguments.get("text") or "").strip().casefold()
        if name not in {"catalog", "resolve_entity", "lookup_spec",
                        "check_software_support"} and not cls._scope_selectors_match(
            row["scope"], {}
        ):
            return False
        if name == "catalog":
            return kind == "entity" and (not query or query in text)
        if name == "resolve_entity":
            return kind == "entity" and bool(query) and query in text
        if name == "lookup_spec":
            if kind != "claim":
                return False
            field = str(arguments.get("field") or "").casefold()
            entity = str(arguments.get("entity") or "").casefold()
            conditions = arguments.get("conditions") or {}
            if not isinstance(conditions, dict) or any(
                {**row["scope"], **data["conditions"]}.get(key) != value
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
        if name == "check_software_support":
            if kind != "software":
                return False
            conditions = arguments.get("conditions") or {}
            if not isinstance(conditions, dict) or any(
                key in arguments and arguments[key] != value
                for key, value in conditions.items()
            ) or not cls._scope_selectors_match(
                row["scope"], {**arguments, **conditions}
            ):
                return False
            software = str(arguments.get("software") or "").casefold()
            entity = str(arguments.get("entity") or "").casefold()
            if not software or not entity or entity not in cls._entity_names(row, entities, topologies):
                return False
            return all(str(arguments.get(key) or "").casefold() == str(data[key]).casefold()
                       for key in ("platform", "version", "hardware_revision",
                                   "connection_mode", "capability")) and \
                software == data["software"].casefold()
        if name == "official_links":
            return kind == "link" and row["status"] == "verified" and bool(query) and query in text
        if name == "sdk_evidence":
            return kind == "software" and bool(query) and query in text
        if name in {"experience", "risk"}:
            return kind == "claim" and data.get("field") == name and bool(query) and query in text
        if name == "search_knowledge":
            return kind != "link" and bool(query) and query in text and \
                (kind == "entity" or cls._scope_selectors_match(row["scope"], {}))
        return False

    @classmethod
    def _requirement_matches(cls, requirement: dict, row: dict,
                             entities: dict[str, str], topologies: dict[str, dict]) -> bool:
        if requirement.get("reason") == "intent_or_entity_not_grounded" or \
                requirement.get("conditions_authority") == "platform_context_unverified":
            return False
        field = requirement.get("field")
        if requirement.get("capability") == "lookup_spec" and not field:
            return False
        if field is not None and (row["kind"] != "claim" or row["data"].get("field") != field):
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
        if required_entities and (not isinstance(required_entities, list) or not all(
            isinstance(item, str) and item.casefold() in names for item in required_entities
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
            software_name = data["software"].casefold()
            if "viewer" in software_name:
                known["viewer_version"] = data["version"]
            if "sdk" in software_name:
                known["sdk_version"] = data["version"]
            if "firmware" in software_name or "固件" in software_name:
                known["firmware_version"] = data["version"]
            if not {"platform", "variant", "connection"} <= set(conditions):
                return False
        elif row["kind"] == "procedure":
            topology = topologies.get(row["data"]["topology_id"])
            if topology is None:
                return False
            known.update({"platform": topology["data"]["platform"],
                          "task": row["data"]["task"]})
        if any(
            known.get(key) != value for key, value in conditions.items()
            if not (row["kind"] == "software" and key == "task")
        ):
            return False
        return True

    def redact_tool_input(self, _name: str, arguments: dict) -> dict:
        # This release has no credentials in tool arguments; keep bounded audit data.
        return {str(key): str(value)[:256] for key, value in arguments.items()}
