"""Explicit local smoke adapter. It never calls a model provider."""


class OfflineAdapter:
    model = "offline-bootstrap"
    tool_choice_strategy = "submit_only_auto"

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             required_tool: str | None = None):
        searched = any(message.get("role") == "tool" for message in messages)
        if not searched and required_tool is None:
            yield {
                "type": "tool_call",
                "id": "offline-search",
                "name": "search_knowledge",
                "arguments": {"query": str(messages[-1].get("content") or "")},
            }
        else:
            yield {
                "type": "tool_call",
                "id": "offline-answer",
                "name": "submit_answer",
                "arguments": {
                    "outcome": "safe_abstained",
                    "missing": "当前数采知识库尚无已审核资料。",
                    "next_steps": "请补充设备型号、版本和经审核的产品资料。",
                },
            }
        yield {"type": "stop", "stop_reason": "tool_use", "usage": None}
