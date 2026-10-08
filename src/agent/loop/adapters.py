"""LLM 传输 adapters (M2):统一事件接口,runtime 与具体厂商协议的边界。

统一事件:
    {"type": "text_delta", "text": str}
    {"type": "tool_call", "id": str, "name": str, "arguments": dict}
    {"type": "stop", "stop_reason": str, "usage": {"input_tokens": int, "output_tokens": int} | None}

usage 取自厂商响应(openai 兼容通道需 stream_options.include_usage;部分网关
不回传,置 None——评测按缺失处理,不许拿工具调用数冒充 token 成本)。

runtime 内部转录使用 openai 消息格式;AnthropicAdapter 负责格式转换。
限流/网关错误(429/5xx)和安全的传输错误指数退避重试。Anthropic 整轮缓冲到
message_stop 后才映射统一事件,因此断流 attempt 可完整丢弃且不会重复工具副作用。
"""
from __future__ import annotations

import copy
import json
import time
from typing import Iterator

import httpx

from src.agent.anthropic_transport import request_buffered_anthropic_message
from src.agent.provider_identity import normalize_provider_model

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _parse_arguments(raw: str) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pick_usage(raw: dict) -> dict | None:
    """归一化厂商 usage:openai 用 prompt/completion_tokens,anthropic 用
    input/output_tokens。两者都缺 → None(不编造 0)。"""
    result: dict = {}
    input_tokens = raw.get("input_tokens", raw.get("prompt_tokens"))
    output_tokens = raw.get("output_tokens", raw.get("completion_tokens"))
    if input_tokens is not None:
        result["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        result["output_tokens"] = int(output_tokens)
    for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        if raw.get(key) is not None:
            result[key] = int(raw[key])
    output_details = raw.get("output_tokens_details")
    if isinstance(output_details, dict):
        thinking_tokens = output_details.get("thinking_tokens")
        if thinking_tokens is not None:
            result["output_tokens_details"] = {
                "thinking_tokens": int(thinking_tokens),
            }
    return result or None


def _require_known_tool(tools: list[dict] | None, required_tool: str | None) -> None:
    if not required_tool:
        return
    names = {
        str(tool.get("function", {}).get("name") or "")
        for tool in (tools or [])
    }
    if required_tool not in names:
        raise ValueError(f"required tool is not available: {required_tool}")


class OpenAICompatAdapter:
    """OpenAI 兼容通道(GLM 等):SSE 流式 + tool-call 增量拼装。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 max_tokens: int = 8192, timeout: float = 120.0,
                 retry_attempts: int = 4, retry_backoff_s: float = 1.5):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.retry_attempts = retry_attempts
        self.retry_backoff_s = retry_backoff_s
        self._client = httpx.Client(
            headers={"authorization": f"Bearer {api_key}",
                     "content-type": "application/json"},
            timeout=timeout,
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             required_tool: str | None = None) -> Iterator[dict]:
        _require_known_tool(tools, required_tool)
        payload: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "stream": True,
        }
        # usage 落盘:GLM/DeepSeek 等在最后一个 chunk 回传 usage;不主动加
        # stream_options.include_usage——严格校验 payload 的网关会 400,
        # 换取 usage 不值得赌请求失败;网关不回传就记 None。
        if tools is not None:
            payload["tools"] = tools
        if required_tool:
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": required_tool},
            }
        for attempt in range(self.retry_attempts + 1):
            emitted = False
            try:
                for ev in self._chat_once(payload):
                    emitted = True
                    yield ev
                return
            except httpx.HTTPStatusError as exc:
                # raise_for_status 在任何事件产出之前发生,重试不会重复事件
                if (exc.response.status_code not in _RETRYABLE_STATUS
                        or attempt >= self.retry_attempts):
                    raise
                time.sleep(self.retry_backoff_s * (2 ** attempt))
            except httpx.TransportError:
                # 首个事件前的超时、断连、协议错误可安全重试；流中断已产出
                # 事件时重试会重复内容，必须直接抛出。
                if emitted or attempt >= self.retry_attempts:
                    raise
                time.sleep(self.retry_backoff_s * (2 ** attempt))

    def _chat_once(self, payload: dict) -> Iterator[dict]:
        pending: dict[int, dict] = {}
        finish: str | None = None
        usage: dict | None = None
        provider_models: set[str] = set()
        with self._client.stream(
            "POST", f"{self.base_url}/chat/completions", json=payload,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                provider_model = normalize_provider_model(chunk.get("model"))
                if provider_model:
                    provider_models.add(provider_model)
                if chunk.get("usage"):
                    usage = _pick_usage(chunk["usage"])
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield {"type": "text_delta", "text": delta["content"]}
                for tc in delta.get("tool_calls") or []:
                    slot = pending.setdefault(
                        int(tc.get("index", 0)),
                        {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        for idx in sorted(pending):
            slot = pending[idx]
            yield {
                "type": "tool_call",
                "id": slot["id"] or f"call_{idx}",
                "name": slot["name"],
                "arguments": _parse_arguments(slot["arguments"]),
            }
        # Q0(20260708):正常收尾必有 finish_reason;缺失说明服务端断流,
        # 标 incomplete 交由 runtime 续写——半截输出不许伪装正常结束。
        provider_model = (
            next(iter(provider_models)) if len(provider_models) == 1 else None
        )
        provider_model_source = (
            "openai_chunk.model"
            if provider_model
            else "conflict"
            if provider_models
            else "unavailable"
        )
        yield {
            "type": "stop",
            "stop_reason": finish or "incomplete",
            "usage": usage,
            "provider_model": provider_model,
            "provider_model_source": provider_model_source,
        }


class AnthropicAdapter:
    """Anthropic /v1/messages 通道:转录后按整条消息缓冲 SSE。"""

    DEFAULT_BASE_URL = "https://api.anthropic.com"

    def __init__(self, api_key: str, model: str,
                 max_tokens: int = 8192, timeout: float = 120.0,
                 retry_attempts: int = 2, retry_backoff_s: float = 1.5,
                 retry_max_delay_s: float = 15.0,
                 base_url: str = "", auth_token: str = "",
                 thinking_mode: str = "legacy", effort: str | None = None,
                 tool_choice_strategy: str = "forced"):
        if thinking_mode not in {"legacy", "adaptive"}:
            raise ValueError("thinking_mode must be 'legacy' or 'adaptive'")
        if thinking_mode == "legacy" and effort is not None:
            raise ValueError("effort is only valid with adaptive thinking")
        if thinking_mode == "adaptive" and effort is None:
            raise ValueError("effort is required with adaptive thinking")
        if tool_choice_strategy not in {"forced", "submit_only_auto"}:
            raise ValueError(
                "tool_choice_strategy must be 'forced' or 'submit_only_auto'"
            )
        self.model = model
        self.max_tokens = max_tokens
        self.retry_attempts = retry_attempts
        self.retry_backoff_s = retry_backoff_s
        self.retry_max_delay_s = retry_max_delay_s
        self.thinking_mode = thinking_mode
        self.effort = effort
        self.tool_choice_strategy = tool_choice_strategy
        self.api_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/") + "/v1/messages"
        self._fold_system_into_user = bool(base_url)
        headers = {"anthropic-version": "2023-06-01",
                   "content-type": "application/json",
                   # 部分 Claude Code 网关按 UA 白名单放行
                   "user-agent": "claude-cli/2.0.0 (external, cli)"}
        if auth_token:
            # Claude Code 网关形态:ANTHROPIC_AUTH_TOKEN → Bearer 头
            headers["authorization"] = f"Bearer {auth_token}"
        else:
            headers["x-api-key"] = api_key
        self._headers = headers
        self._client = httpx.Client(headers=headers, timeout=timeout)

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             required_tool: str | None = None) -> Iterator[dict]:
        _require_known_tool(tools, required_tool)
        system, converted = _to_anthropic_messages(messages)
        if self._fold_system_into_user:
            converted = _fold_system_into_user_message(system, converted)
            system = ""
        payload: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": converted,
        }
        if self.thinking_mode == "adaptive":
            payload["thinking"] = {"type": "adaptive", "display": "omitted"}
            payload["output_config"] = {"effort": self.effort}
        if system:
            payload["system"] = system
        if tools is not None:
            payload["tools"] = [{
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {"type": "object"}),
            } for t in tools]
        if required_tool and self.tool_choice_strategy == "forced":
            payload["tool_choice"] = {"type": "tool", "name": required_tool}
        message = request_buffered_anthropic_message(
            client=self._client,
            url=self.api_url,
            payload=payload,
            retry_attempts=self.retry_attempts,
            retry_backoff_s=self.retry_backoff_s,
            retry_max_delay_s=self.retry_max_delay_s,
        )
        for text in message.text_blocks:
            if text:
                yield {"type": "text_delta", "text": text}
        for call in message.tool_calls:
            yield {
                "type": "tool_call",
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
            }
        yield {
            "type": "stop",
            "stop_reason": message.stop_reason,
            "usage": _pick_usage(message.usage or {}),
            "transport": message.transport.as_dict(),
            "_provider_blocks": [
                block.as_dict() for block in message.provider_blocks
            ],
            "response_model": message.response_model,
            "stop_details": message.stop_details,
            "thinking_block_count": message.transport.thinking_block_count,
            "provider_model": message.provider_model,
            "provider_model_source": (
                "anthropic_message_start.model"
                if message.provider_model
                else "unavailable"
            ),
        }


def _fold_system_into_user_message(system: str, messages: list[dict]) -> list[dict]:
    if not system:
        return messages
    if not messages:
        return [{"role": "user", "content": system}]
    first = dict(messages[0])
    content = first.get("content") or ""
    if first.get("role") == "user":
        if isinstance(content, list):
            first["content"] = [{"type": "text", "text": system}] + content
        else:
            first["content"] = f"{system}\n\n{content}"
        return [first] + messages[1:]
    return [{"role": "user", "content": system}] + messages


def _to_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system = ""
    out: list[dict] = []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            system = str(msg.get("content") or "")
        elif role == "user":
            out.append({"role": "user", "content": msg.get("content") or ""})
        elif role == "assistant":
            private_blocks = msg.get("_provider_blocks")
            if private_blocks is not None:
                if not isinstance(private_blocks, (list, tuple)) or not all(
                    isinstance(block, dict) for block in private_blocks
                ):
                    raise ValueError("private Anthropic provider blocks are malformed")
                blocks = copy.deepcopy(list(private_blocks))
            else:
                blocks = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg.get("tool_calls") or []:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": _parse_arguments(tc["function"].get("arguments", "")),
                    })
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {"type": "tool_result",
                     "tool_use_id": msg.get("tool_call_id", ""),
                     "content": msg.get("content") or ""}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return system, out
