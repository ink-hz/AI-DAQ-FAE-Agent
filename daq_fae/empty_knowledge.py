"""Evidence interface for the intentionally empty Dev knowledge release."""

from src.agent.loop.tools import ToolResult


class EmptyKnowledgeToolBox:
    attachment_source_ids: list[str] = []
    attachment_vision_model = None
    device_support = None

    def with_request_context(self, question: str) -> "EmptyKnowledgeToolBox":
        return EmptyKnowledgeToolBox()

    def tool_schemas(self) -> list[dict]:
        return [{
            "type": "function",
            "function": {
                "name": "search_knowledge",
                "description": "Search the governed DAQ knowledge release. This Dev release is empty.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }]

    def dispatch(self, name: str, arguments: dict) -> ToolResult:
        if name != "search_knowledge":
            return ToolResult(status="tool_error", content={"error": "unknown_tool"})
        return ToolResult(
            status="not_found",
            content={"reason": "empty_knowledge_release", "matches": []},
        )

    def redact_tool_input(self, name: str, arguments: dict) -> dict:
        return dict(arguments)
