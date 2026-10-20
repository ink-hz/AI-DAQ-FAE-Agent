"""DAQ transport boundary for provider tool-continuation compatibility."""

from collections.abc import Iterator

from src.agent.loop.adapters import AnthropicAdapter


def normalize_unsigned_thinking(event: dict) -> dict:
    """Drop provider thinking blocks that cannot pass Anthropic signature validation.

    Some compatible gateways emit an unsigned thinking block alongside valid
    tool calls. Replaying that block makes the next request fail with HTTP 400.
    The block is private model output, so it must never be reconstructed or
    exposed as text. Preserve the remaining opaque provider blocks in order.
    """
    if event.get("type") != "stop":
        return event
    blocks = event.get("_provider_blocks")
    if not isinstance(blocks, list) or not all(isinstance(block, dict) for block in blocks):
        return event
    safe_blocks = [
        block for block in blocks
        if block.get("type") != "thinking"
        or (isinstance(block.get("signature"), str) and bool(block["signature"]))
    ]
    dropped = len(blocks) - len(safe_blocks)
    if not dropped:
        return event
    normalized = dict(event)
    normalized["_provider_blocks"] = safe_blocks
    normalized["transport"] = {
        **(event.get("transport") or {}),
        "unsigned_thinking_blocks_dropped": dropped,
    }
    return normalized


class DaqAnthropicAdapter(AnthropicAdapter):
    """Keep the shared camera adapter unchanged while normalizing DAQ replay."""

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             required_tool: str | None = None) -> Iterator[dict]:
        for event in super().chat(messages, tools, required_tool=required_tool):
            yield normalize_unsigned_thinking(event)
