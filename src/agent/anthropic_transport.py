"""Strict, whole-message buffering for Anthropic-compatible SSE responses.

The transport deliberately exposes no text or tool call until the provider has
closed every content block and emitted ``message_stop``.  That makes retrying an
interrupted provider attempt safe for callers that execute tools as side effects.
"""

from __future__ import annotations

import copy
import json
import re
import math
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.agent.provider_identity import normalize_provider_model

_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
_MODE = "anthropic_sse_buffered"
_SAFE_PROTOCOL_TOKEN = re.compile(r"[a-z0-9_]{1,64}")
_SAFE_GATEWAY_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,80}")
_MESSAGE_REQUEST_ID = re.compile(r"request id[:：]\s*([A-Za-z0-9_-]{8,80})", re.I)
_MAX_GATEWAY_ERROR_BYTES = 8192
_PROTOCOL_ERROR_KINDS = frozenset({
    "unsupported_block_type",
    "delta_block_mismatch",
    "duplicate_block_index",
    "missing_block_start",
    "unclosed_block",
    "invalid_field_shape",
})
_INDEX_STATES = frozenset({"valid", "missing", "duplicate", "unknown"})


@dataclass(frozen=True)
class AnthropicToolCall:
    id: str
    name: str
    arguments: dict


def _safe_protocol_token(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and _SAFE_PROTOCOL_TOKEN.fullmatch(value):
        return value
    return "invalid_token"


def _is_block_index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class AnthropicProtocolDiagnostic:
    protocol_error_kind: str | None = None
    observed_event_type: str | None = None
    observed_block_type: str | None = None
    observed_delta_type: str | None = None
    open_block_type: str | None = None
    index_state: str = "unknown"
    ignored_unknown_event_type_count: int = 0

    def __post_init__(self) -> None:
        if (
            self.protocol_error_kind is not None
            and self.protocol_error_kind not in _PROTOCOL_ERROR_KINDS
        ):
            raise ValueError("invalid Anthropic protocol error kind")
        if self.index_state not in _INDEX_STATES:
            raise ValueError("invalid Anthropic protocol index state")
        if (
            isinstance(self.ignored_unknown_event_type_count, bool)
            or not isinstance(self.ignored_unknown_event_type_count, int)
            or self.ignored_unknown_event_type_count < 0
        ):
            raise ValueError("invalid ignored Anthropic event count")

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "protocol_error_kind": self.protocol_error_kind,
            "observed_event_type": self.observed_event_type,
            "observed_block_type": self.observed_block_type,
            "observed_delta_type": self.observed_delta_type,
            "open_block_type": self.open_block_type,
            "index_state": self.index_state,
            "ignored_unknown_event_type_count": (
                self.ignored_unknown_event_type_count
            ),
        }


def _protocol_diagnostic(
    *,
    error_kind: str | None = None,
    event_type: object = None,
    block_type: object = None,
    delta_type: object = None,
    open_block_type: object = None,
    index_state: str = "unknown",
    ignored_unknown_event_type_count: int = 0,
) -> AnthropicProtocolDiagnostic:
    return AnthropicProtocolDiagnostic(
        protocol_error_kind=error_kind,
        observed_event_type=_safe_protocol_token(event_type),
        observed_block_type=_safe_protocol_token(block_type),
        observed_delta_type=_safe_protocol_token(delta_type),
        open_block_type=_safe_protocol_token(open_block_type),
        index_state=index_state,
        ignored_unknown_event_type_count=ignored_unknown_event_type_count,
    )


@dataclass(frozen=True, repr=False)
class AnthropicProviderBlock:
    """Immutable, process-local provider block with a non-leaking repr."""

    block_type: str
    _payload_json: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AnthropicProviderBlock:
        block_type = payload.get("type")
        if not isinstance(block_type, str):
            raise ValueError("provider block type must be a string")
        return cls(
            block_type=block_type,
            _payload_json=json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a fresh JSON-equivalent block for the next provider request."""
        payload = json.loads(self._payload_json)
        if not isinstance(payload, dict):
            raise AssertionError("frozen provider block is not an object")
        return payload

    def __repr__(self) -> str:
        return f"AnthropicProviderBlock(block_type={self.block_type!r})"


@dataclass(frozen=True)
class AnthropicTransportTelemetry:
    mode: str
    attempts: int
    retry_count: int
    retry_reasons: tuple[str, ...]
    first_event_ms: int | None
    complete_message_ms: int | None
    message_stop_received: bool
    discarded_incomplete_attempts: int
    thinking_block_count: int = 0
    provider_protocol_status: str = "complete"
    protocol_diagnostic: AnthropicProtocolDiagnostic = AnthropicProtocolDiagnostic()
    http_status: int | None = None
    gateway_error_code: str | None = None
    gateway_request_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "attempts": self.attempts,
            "retry_count": self.retry_count,
            "retry_reasons": list(self.retry_reasons),
            "first_event_ms": self.first_event_ms,
            "complete_message_ms": self.complete_message_ms,
            "message_stop_received": self.message_stop_received,
            "discarded_incomplete_attempts": self.discarded_incomplete_attempts,
            "thinking_block_count": self.thinking_block_count,
            "provider_protocol_status": self.provider_protocol_status,
            "http_status": self.http_status,
            "gateway_error_code": self.gateway_error_code,
            "gateway_request_id": self.gateway_request_id,
            **self.protocol_diagnostic.as_dict(),
        }


@dataclass(frozen=True)
class AnthropicBufferedMessage:
    text_blocks: tuple[str, ...]
    tool_calls: tuple[AnthropicToolCall, ...]
    provider_blocks: tuple[AnthropicProviderBlock, ...]
    response_model: str | None
    stop_reason: str
    stop_details: dict[str, str] | None
    usage: dict[str, Any] | None
    provider_model: str | None
    transport: AnthropicTransportTelemetry


class AnthropicTransportError(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        retryable: bool,
        telemetry: AnthropicTransportTelemetry,
        cause: Exception | None = None,
    ):
        self.reason = reason
        self.retryable = retryable
        self.telemetry = telemetry
        self.cause = cause
        super().__init__(f"Anthropic buffered stream failed: {reason}")


class _AttemptError(Exception):
    def __init__(
        self,
        reason: str,
        *,
        retryable: bool,
        cause: Exception | None = None,
        first_event_ms: int | None = None,
        protocol_status: str = "incomplete",
        protocol_diagnostic: AnthropicProtocolDiagnostic | None = None,
        retry_after_s: float | None = None,
        http_status: int | None = None,
        gateway_error_code: str | None = None,
        gateway_request_id: str | None = None,
    ):
        self.reason = reason
        self.retryable = retryable
        self.cause = cause
        self.first_event_ms = first_event_ms
        self.protocol_status = protocol_status
        self.protocol_diagnostic = (
            protocol_diagnostic or AnthropicProtocolDiagnostic()
        )
        self.retry_after_s = retry_after_s
        self.http_status = http_status
        self.gateway_error_code = gateway_error_code
        self.gateway_request_id = gateway_request_id
        super().__init__(reason)


@dataclass
class _OpenBlock:
    kind: str
    payload: dict[str, Any]
    partial_json: str = ""


@dataclass(frozen=True)
class _AttemptMessage:
    text_blocks: tuple[str, ...]
    tool_calls: tuple[AnthropicToolCall, ...]
    provider_blocks: tuple[AnthropicProviderBlock, ...]
    response_model: str | None
    stop_reason: str
    stop_details: dict[str, str] | None
    usage: dict[str, Any] | None
    provider_model: str | None
    first_event_ms: int | None
    complete_message_ms: int
    protocol_diagnostic: AnthropicProtocolDiagnostic = AnthropicProtocolDiagnostic()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _merge_usage(target: dict[str, Any], raw: object) -> None:
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        if isinstance(value, int) and not isinstance(value, bool):
            target[str(key)] = value
        elif isinstance(value, dict):
            numeric_details = {
                str(detail_key): detail_value
                for detail_key, detail_value in value.items()
                if isinstance(detail_value, int) and not isinstance(detail_value, bool)
            }
            if numeric_details:
                target[str(key)] = numeric_details


def _structural_error(
    *,
    first_event_ms: int | None,
    protocol_error_kind: str,
    event_type: object = None,
    block_type: object = None,
    delta_type: object = None,
    open_block_type: object = None,
    index_state: str = "unknown",
    ignored_unknown_event_type_count: int = 0,
    reason: str = "protocol_error",
    cause: Exception | None = None,
) -> _AttemptError:
    return _AttemptError(
        reason,
        retryable=False,
        cause=cause,
        first_event_ms=first_event_ms,
        protocol_status="structural_error",
        protocol_diagnostic=_protocol_diagnostic(
            error_kind=protocol_error_kind,
            event_type=event_type,
            block_type=block_type,
            delta_type=delta_type,
            open_block_type=open_block_type,
            index_state=index_state,
            ignored_unknown_event_type_count=ignored_unknown_event_type_count,
        ),
    )


def _transport_reason(exc: httpx.TransportError) -> str:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return "connect_error"
    if isinstance(exc, (httpx.ReadError, httpx.ReadTimeout)):
        return "read_error"
    if isinstance(exc, (httpx.WriteError, httpx.WriteTimeout)):
        return "write_error"
    return "protocol_error"


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _retry_delay(
    *, attempt: int, retry_backoff_s: float, retry_max_delay_s: float,
    retry_after_s: float | None, random_fn,
) -> float:
    if retry_after_s is not None:
        return min(retry_after_s, retry_max_delay_s)
    jitter = 0.5 + 0.5 * min(1.0, max(0.0, float(random_fn())))
    return min(retry_backoff_s * (2**attempt), retry_max_delay_s) * jitter


def _parse_event(raw: str, *, first_event_ms: int | None) -> dict:
    try:
        event = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise _AttemptError(
            "invalid_sse_json",
            retryable=True,
            cause=exc,
            first_event_ms=first_event_ms,
        ) from exc
    if not isinstance(event, dict):
        raise _AttemptError(
            "protocol_error",
            retryable=True,
            first_event_ms=first_event_ms,
        )
    return event


def _finish_tool(block: _OpenBlock, *, first_event_ms: int | None) -> AnthropicToolCall:
    if block.partial_json:
        try:
            arguments = json.loads(block.partial_json)
        except (TypeError, ValueError) as exc:
            raise _AttemptError(
                "invalid_tool_arguments",
                retryable=True,
                cause=exc,
                first_event_ms=first_event_ms,
            ) from exc
    else:
        arguments = block.payload.get("input") or {}
    if not isinstance(arguments, dict):
        raise _AttemptError(
            "invalid_tool_arguments",
            retryable=True,
            first_event_ms=first_event_ms,
        )
    return AnthropicToolCall(
        id=str(block.payload.get("id") or ""),
        name=str(block.payload.get("name") or ""),
        arguments=arguments,
    )


def _gateway_error_diagnostic(response: httpx.Response) -> tuple[str | None, str | None]:
    """Extract bounded machine diagnostics without retaining gateway prose."""
    request_id = next(
        (
            value for key in ("x-request-id", "request-id")
            if (value := response.headers.get(key))
            and _SAFE_GATEWAY_REQUEST_ID.fullmatch(value)
        ),
        None,
    )
    if "application/json" not in response.headers.get("content-type", "").lower():
        return None, request_id
    length = response.headers.get("content-length")
    if length and length.isdecimal() and int(length) > _MAX_GATEWAY_ERROR_BYTES:
        return None, request_id
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > _MAX_GATEWAY_ERROR_BYTES:
            return None, request_id
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks))
    except (ValueError, UnicodeDecodeError):
        return None, request_id
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return None, request_id
    error = body["error"]
    raw_code = error.get("code")
    code = (
        raw_code if isinstance(raw_code, str)
        and _SAFE_PROTOCOL_TOKEN.fullmatch(raw_code) else None
    )
    message = error.get("message")
    if isinstance(message, str):
        lowered = message.lower()
        if "forced tool_choice" in lowered and "does not support" in lowered:
            code = "forced_tool_choice_unsupported"
        if request_id is None:
            match = _MESSAGE_REQUEST_ID.search(message)
            if match:
                request_id = match.group(1)
    body_request_id = error.get("request_id")
    if (request_id is None and isinstance(body_request_id, str)
            and _SAFE_GATEWAY_REQUEST_ID.fullmatch(body_request_id)):
        request_id = body_request_id
    return code, request_id


def _request_once(
    *,
    client: httpx.Client,
    url: str,
    payload: dict,
) -> _AttemptMessage:
    started = time.monotonic()
    first_event_ms: int | None = None
    open_blocks: dict[int, _OpenBlock] = {}
    block_order: list[int] = []
    completed_blocks: dict[int, AnthropicProviderBlock] = {}
    completed_text: dict[int, str] = {}
    completed_tools: dict[int, AnthropicToolCall] = {}
    usage: dict[str, Any] = {}
    response_model: str | None = None
    stop_reason = ""
    stop_details: dict[str, str] | None = None
    ignored_unknown_event_type_count = 0
    last_unknown_event_type: str | None = None
    provider_model: str | None = None

    try:
        with client.stream("POST", url, json=payload) as response:
            if response.status_code >= 400:
                reason = f"http_{response.status_code}"
                gateway_error_code, gateway_request_id = _gateway_error_diagnostic(
                    response
                )
                raise _AttemptError(
                    reason,
                    retryable=(
                        response.status_code in _RETRYABLE_HTTP_STATUS
                        and gateway_error_code != "model_not_found"
                    ),
                    retry_after_s=_retry_after_seconds(response),
                    http_status=response.status_code,
                    gateway_error_code=gateway_error_code,
                    gateway_request_id=gateway_request_id,
                )
            for line in response.iter_lines():
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                if first_event_ms is None:
                    first_event_ms = _elapsed_ms(started)
                raw = line[len("data:") :].strip()
                if not raw:
                    continue
                event = _parse_event(raw, first_event_ms=first_event_ms)
                event_type = event.get("type")

                if event_type == "error":
                    raise _AttemptError(
                        "provider_error_event",
                        retryable=True,
                        first_event_ms=first_event_ms,
                    )
                if event_type == "message_start":
                    message = event.get("message") or {}
                    if isinstance(message, dict):
                        raw_model = message.get("model")
                        if isinstance(raw_model, str):
                            response_model = raw_model
                        _merge_usage(usage, message.get("usage"))
                        provider_model = normalize_provider_model(
                            message.get("model")
                        )
                    continue
                if event_type == "content_block_start":
                    index = event.get("index")
                    block = event.get("content_block")
                    if not _is_block_index(index) or not isinstance(block, dict):
                        raise _structural_error(
                            first_event_ms=first_event_ms,
                            protocol_error_kind="invalid_field_shape",
                            event_type=event_type,
                            block_type=(block.get("type") if isinstance(block, dict) else None),
                            index_state="unknown",
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                        )
                    if index in open_blocks or index in completed_blocks:
                        existing_type = (
                            open_blocks[index].kind
                            if index in open_blocks
                            else completed_blocks[index].block_type
                        )
                        raise _structural_error(
                            first_event_ms=first_event_ms,
                            protocol_error_kind="duplicate_block_index",
                            event_type=event_type,
                            block_type=block.get("type"),
                            open_block_type=existing_type,
                            index_state="duplicate",
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                        )
                    block_type = block.get("type")
                    if block_type not in {
                        "text", "tool_use", "thinking", "redacted_thinking",
                    }:
                        raise _structural_error(
                            first_event_ms=first_event_ms,
                            protocol_error_kind="unsupported_block_type",
                            event_type=event_type,
                            block_type=block_type,
                            index_state="valid",
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                        )
                    payload = copy.deepcopy(block)
                    if block_type in {"text", "thinking"}:
                        content_key = "text" if block_type == "text" else "thinking"
                        content = payload.get(content_key, "")
                        if not isinstance(content, str):
                            raise _structural_error(
                                first_event_ms=first_event_ms,
                                protocol_error_kind="invalid_field_shape",
                                event_type=event_type,
                                block_type=block_type,
                                index_state="valid",
                                ignored_unknown_event_type_count=(
                                    ignored_unknown_event_type_count
                                ),
                            )
                        payload[content_key] = content
                        if "signature" in payload and not isinstance(
                            payload["signature"], str,
                        ):
                            raise _structural_error(
                                first_event_ms=first_event_ms,
                                protocol_error_kind="invalid_field_shape",
                                event_type=event_type,
                                block_type=block_type,
                                index_state="valid",
                                ignored_unknown_event_type_count=(
                                    ignored_unknown_event_type_count
                                ),
                            )
                    elif block_type == "tool_use":
                        initial_input = payload.get("input")
                        if initial_input is not None and not isinstance(initial_input, dict):
                            raise _structural_error(
                                first_event_ms=first_event_ms,
                                protocol_error_kind="invalid_field_shape",
                                event_type=event_type,
                                block_type=block_type,
                                index_state="valid",
                                ignored_unknown_event_type_count=(
                                    ignored_unknown_event_type_count
                                ),
                                reason="invalid_tool_arguments",
                            )
                    elif "data" in payload and not isinstance(payload["data"], str):
                        raise _structural_error(
                            first_event_ms=first_event_ms,
                            protocol_error_kind="invalid_field_shape",
                            event_type=event_type,
                            block_type=block_type,
                            index_state="valid",
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                        )
                    open_blocks[index] = _OpenBlock(
                        kind=block_type,
                        payload=payload,
                    )
                    block_order.append(index)
                    continue
                if event_type == "content_block_delta":
                    index = event.get("index")
                    delta = event.get("delta")
                    block = open_blocks.get(index) if _is_block_index(index) else None
                    if not _is_block_index(index) or not isinstance(delta, dict):
                        raise _structural_error(
                            first_event_ms=first_event_ms,
                            protocol_error_kind="invalid_field_shape",
                            event_type=event_type,
                            delta_type=(delta.get("type") if isinstance(delta, dict) else None),
                            open_block_type=(block.kind if block is not None else None),
                            index_state=(
                                "unknown"
                                if not _is_block_index(index)
                                else "valid" if block is not None else "missing"
                            ),
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                        )
                    delta_type = delta.get("type")
                    if block is None:
                        raise _structural_error(
                            first_event_ms=first_event_ms,
                            protocol_error_kind="missing_block_start",
                            event_type=event_type,
                            delta_type=delta_type,
                            index_state="missing",
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                        )
                    if block.kind == "text" and delta_type == "text_delta":
                        fragment = delta.get("text", "")
                        if not isinstance(fragment, str):
                            raise _structural_error(
                                first_event_ms=first_event_ms,
                                protocol_error_kind="invalid_field_shape",
                                event_type=event_type,
                                block_type=block.kind,
                                delta_type=delta_type,
                                open_block_type=block.kind,
                                index_state="valid",
                                ignored_unknown_event_type_count=(
                                    ignored_unknown_event_type_count
                                ),
                            )
                        block.payload["text"] += fragment
                    elif block.kind == "tool_use" and delta_type == "input_json_delta":
                        fragment = delta.get("partial_json", "")
                        if not isinstance(fragment, str):
                            raise _structural_error(
                                first_event_ms=first_event_ms,
                                protocol_error_kind="invalid_field_shape",
                                event_type=event_type,
                                block_type=block.kind,
                                delta_type=delta_type,
                                open_block_type=block.kind,
                                index_state="valid",
                                ignored_unknown_event_type_count=(
                                    ignored_unknown_event_type_count
                                ),
                            )
                        block.partial_json += fragment
                    elif block.kind == "thinking" and delta_type == "thinking_delta":
                        fragment = delta.get("thinking", "")
                        if not isinstance(fragment, str):
                            raise _structural_error(
                                first_event_ms=first_event_ms,
                                protocol_error_kind="invalid_field_shape",
                                event_type=event_type,
                                block_type=block.kind,
                                delta_type=delta_type,
                                open_block_type=block.kind,
                                index_state="valid",
                                ignored_unknown_event_type_count=(
                                    ignored_unknown_event_type_count
                                ),
                            )
                        block.payload["thinking"] += fragment
                    elif block.kind == "thinking" and delta_type == "signature_delta":
                        fragment = delta.get("signature", "")
                        if not isinstance(fragment, str):
                            raise _structural_error(
                                first_event_ms=first_event_ms,
                                protocol_error_kind="invalid_field_shape",
                                event_type=event_type,
                                block_type=block.kind,
                                delta_type=delta_type,
                                open_block_type=block.kind,
                                index_state="valid",
                                ignored_unknown_event_type_count=(
                                    ignored_unknown_event_type_count
                                ),
                            )
                        signature = block.payload.get("signature", "")
                        if not isinstance(signature, str):
                            raise _structural_error(
                                first_event_ms=first_event_ms,
                                protocol_error_kind="invalid_field_shape",
                                event_type=event_type,
                                block_type=block.kind,
                                delta_type=delta_type,
                                open_block_type=block.kind,
                                index_state="valid",
                                ignored_unknown_event_type_count=(
                                    ignored_unknown_event_type_count
                                ),
                            )
                        block.payload["signature"] = signature + fragment
                    else:
                        raise _structural_error(
                            first_event_ms=first_event_ms,
                            protocol_error_kind="delta_block_mismatch",
                            event_type=event_type,
                            block_type=block.kind,
                            delta_type=delta_type,
                            open_block_type=block.kind,
                            index_state="valid",
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                        )
                    continue
                if event_type == "content_block_stop":
                    index = event.get("index")
                    if not _is_block_index(index):
                        raise _structural_error(
                            first_event_ms=first_event_ms,
                            protocol_error_kind="invalid_field_shape",
                            event_type=event_type,
                            index_state="unknown",
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                        )
                    block = open_blocks.pop(index, None)
                    if block is None:
                        raise _structural_error(
                            first_event_ms=first_event_ms,
                            protocol_error_kind="missing_block_start",
                            event_type=event_type,
                            index_state="missing",
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                        )
                    if block.kind == "text":
                        completed_text[index] = str(block.payload["text"])
                    elif block.kind == "tool_use":
                        tool_call = _finish_tool(
                            block,
                            first_event_ms=first_event_ms,
                        )
                        block.payload["input"] = copy.deepcopy(tool_call.arguments)
                        completed_tools[index] = tool_call
                    try:
                        completed_blocks[index] = AnthropicProviderBlock.from_dict(
                            block.payload,
                        )
                    except (TypeError, ValueError) as exc:
                        raise _structural_error(
                            first_event_ms=first_event_ms,
                            protocol_error_kind="invalid_field_shape",
                            event_type=event_type,
                            block_type=block.kind,
                            open_block_type=block.kind,
                            index_state="valid",
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                            cause=exc,
                        ) from exc
                    continue
                if event_type == "message_delta":
                    delta = event.get("delta") or {}
                    if isinstance(delta, dict) and delta.get("stop_reason"):
                        stop_reason = str(delta["stop_reason"])
                    raw_stop_details = (
                        delta.get("stop_details")
                        if isinstance(delta, dict)
                        else None
                    )
                    if not isinstance(raw_stop_details, dict):
                        raw_stop_details = event.get("stop_details")
                    if isinstance(raw_stop_details, dict):
                        safe_details = {
                            key: value
                            for key in ("type", "category")
                            if isinstance((value := raw_stop_details.get(key)), str)
                        }
                        stop_details = safe_details or None
                    _merge_usage(usage, event.get("usage"))
                    continue
                if event_type == "message_stop":
                    if open_blocks:
                        first_open_block = next(iter(open_blocks.values()))
                        raise _structural_error(
                            protocol_error_kind="unclosed_block",
                            event_type=event_type,
                            open_block_type=first_open_block.kind,
                            index_state="valid",
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                            reason=(
                                "unclosed_tool_block"
                                if any(
                                    block.kind == "tool_use"
                                    for block in open_blocks.values()
                                )
                                else "protocol_error"
                            ),
                            first_event_ms=first_event_ms,
                        )
                    provider_blocks = tuple(
                        completed_blocks[index] for index in block_order
                    )
                    return _AttemptMessage(
                        text_blocks=tuple(
                            completed_text[index]
                            for index in block_order
                            if index in completed_text
                        ),
                        tool_calls=tuple(
                            completed_tools[index]
                            for index in block_order
                            if index in completed_tools
                        ),
                        provider_blocks=provider_blocks,
                        response_model=response_model,
                        stop_reason=stop_reason or "end_turn",
                        stop_details=stop_details,
                        usage=usage or None,
                        provider_model=provider_model,
                        first_event_ms=first_event_ms,
                        complete_message_ms=_elapsed_ms(started),
                        protocol_diagnostic=_protocol_diagnostic(
                            event_type=last_unknown_event_type,
                            ignored_unknown_event_type_count=(
                                ignored_unknown_event_type_count
                            ),
                        ),
                    )
                if event_type == "ping":
                    continue
                ignored_unknown_event_type_count += 1
                last_unknown_event_type = _safe_protocol_token(event_type)
    except _AttemptError:
        raise
    except httpx.TransportError as exc:
        raise _AttemptError(
            _transport_reason(exc),
            retryable=True,
            cause=exc,
            first_event_ms=first_event_ms,
        ) from exc

    raise _AttemptError(
        "missing_message_stop",
        retryable=True,
        first_event_ms=first_event_ms,
        protocol_diagnostic=_protocol_diagnostic(
            event_type=last_unknown_event_type,
            ignored_unknown_event_type_count=ignored_unknown_event_type_count,
        ),
    )


def request_buffered_anthropic_message(
    *,
    client: httpx.Client,
    url: str,
    payload: dict,
    retry_attempts: int,
    retry_backoff_s: float,
    retry_max_delay_s: float = 15.0,
    sleep_fn=None,
    random_fn=None,
) -> AnthropicBufferedMessage:
    """Return one complete buffered message or raise AnthropicTransportError."""
    if retry_attempts < 0:
        raise ValueError("retry_attempts must be non-negative")
    if not math.isfinite(retry_backoff_s) or retry_backoff_s < 0:
        raise ValueError("retry_backoff_s must be non-negative")
    if not math.isfinite(retry_max_delay_s) or retry_max_delay_s <= 0:
        raise ValueError("retry_max_delay_s must be positive")
    sleep_fn = sleep_fn or time.sleep
    random_fn = random_fn or random.random
    request_payload = dict(payload)
    request_payload["stream"] = True
    retry_reasons: list[str] = []
    discarded = 0

    for attempt in range(retry_attempts + 1):
        try:
            message = _request_once(
                client=client,
                url=url,
                payload=request_payload,
            )
        except _AttemptError as exc:
            discarded += 1
            retry_reasons.append(exc.reason)
            telemetry = AnthropicTransportTelemetry(
                mode=_MODE,
                attempts=attempt + 1,
                retry_count=attempt,
                retry_reasons=tuple(retry_reasons),
                first_event_ms=exc.first_event_ms,
                complete_message_ms=None,
                message_stop_received=False,
                discarded_incomplete_attempts=discarded,
                provider_protocol_status=exc.protocol_status,
                protocol_diagnostic=exc.protocol_diagnostic,
                http_status=exc.http_status,
                gateway_error_code=exc.gateway_error_code,
                gateway_request_id=exc.gateway_request_id,
            )
            if not exc.retryable or attempt >= retry_attempts:
                error = AnthropicTransportError(
                    exc.reason,
                    retryable=exc.retryable,
                    telemetry=telemetry,
                    cause=exc.cause,
                )
                raise error from exc.cause
            sleep_fn(_retry_delay(
                attempt=attempt,
                retry_backoff_s=retry_backoff_s,
                retry_max_delay_s=retry_max_delay_s,
                retry_after_s=exc.retry_after_s,
                random_fn=random_fn,
            ))
            continue

        telemetry = AnthropicTransportTelemetry(
            mode=_MODE,
            attempts=attempt + 1,
            retry_count=attempt,
            retry_reasons=tuple(retry_reasons),
            first_event_ms=message.first_event_ms,
            complete_message_ms=message.complete_message_ms,
            message_stop_received=True,
            discarded_incomplete_attempts=discarded,
            thinking_block_count=sum(
                1
                for block in message.provider_blocks
                if block.block_type in {"thinking", "redacted_thinking"}
            ),
            provider_protocol_status="complete",
            protocol_diagnostic=message.protocol_diagnostic,
        )
        return AnthropicBufferedMessage(
            text_blocks=message.text_blocks,
            tool_calls=message.tool_calls,
            provider_blocks=message.provider_blocks,
            response_model=message.response_model,
            stop_reason=message.stop_reason,
            stop_details=message.stop_details,
            usage=message.usage,
            provider_model=message.provider_model,
            transport=telemetry,
        )

    raise AssertionError("unreachable")
