"""DAQ domain tool contract backed by an empty, governed Dev release."""

from __future__ import annotations

from src.agent.loop.tools import ToolResult


_TOOLS: dict[str, tuple[str, dict[str, dict], tuple[str, ...]]] = {
    "resolve_entity": ("Resolve a DAQ product, variant, kit, or component.", {"text": {"type": "string"}}, ("text",)),
    "lookup_spec": ("Look up a governed DAQ claim with conditions and revision.", {"entity": {"type": "string"}, "field": {"type": "string"}, "conditions": {"type": "object"}}, ("entity", "field")),
    "inspect_topology": ("Inspect a verified acquisition system topology.", {"query": {"type": "string"}}, ("query",)),
    "lookup_procedure": ("Find an applicable, reviewed acquisition procedure.", {"task": {"type": "string"}, "entity": {"type": "string"}}, ("task",)),
    "check_software_support": ("Check product, revision, platform, and software compatibility.", {"entity": {"type": "string"}, "software": {"type": "string"}, "platform": {"type": "string"}, "version": {"type": "string"}}, ("entity", "software")),
    "search_knowledge": ("Search the governed DAQ knowledge release.", {"query": {"type": "string"}}, ("query",)),
    "sdk_evidence": ("Find reviewed SDK evidence for DAQ products and combinations.", {"query": {"type": "string"}}, ("query",)),
    "official_links": ("Find authorized and verified official DAQ links.", {"query": {"type": "string"}}, ("query",)),
    "session_state": ("Read user-provided context in the current DAQ session.", {}, ()),
}


class DaqToolBox:
    attachment_source_ids: list[str] = []
    attachment_vision_model = None
    device_support = None

    def __init__(self, *, question: str = "", context: dict | None = None):
        self.question = question
        self.context = dict(context or {})

    def with_request_context(self, question: str) -> "DaqToolBox":
        return DaqToolBox(question=question, context=self.context)

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
        return ToolResult(status="not_found", content={
            "reason": "empty_knowledge_release", "query": dict(arguments),
            "claim_status": "unknown", "matches": [],
        })

    def redact_tool_input(self, _name: str, arguments: dict) -> dict:
        # This release has no credentials in tool arguments; keep bounded audit data.
        return {str(key): str(value)[:256] for key, value in arguments.items()}
