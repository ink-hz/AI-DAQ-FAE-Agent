"""Provider-agnostic LLM 薄封装。

支持两种 provider:
  - "anthropic"     → Anthropic SDK (claude-sonnet-4-6 / haiku-4-5)
  - "openai_compat" → OpenAI SDK 走自定义 base_url(GLM 等 OpenAI 兼容协议)

非流式 + JSON 解析 + 流式三类。
"""
import json
import re
import time
from collections.abc import Callable, Iterator

import httpx
from anthropic import Anthropic
from openai import OpenAI

from src.agent.anthropic_transport import (
    AnthropicTransportError,
    request_buffered_anthropic_message,
)
from src.agent.provider_identity import normalize_provider_model
from src.agent.tracing import current_trace_ctx

_JSON_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_FENCE_INLINE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_JSON_TRANSPORT_RETRY_BACKOFF_S = 1.5
_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}

# T4(20260713):Claude Code 网关形态(auth-token-only)的 Bearer 凭证。
# 凭证是进程级配置,server 启动时注入一次;不给 7 个模块的每个调用点穿参。
# replay84 实证:loop adapter 支持 Bearer 而本模块只发 x-api-key,
# schema/clarify/planner 语义面 84/84 401,且被误分类为 llm_json_invalid
# 穿过 O1a 豁免——传输错误分类见 LLMTransportError。
_ANTHROPIC_AUTH_TOKEN = ""


def set_anthropic_auth_token(token: str) -> None:
    """注入网关 Bearer token(空串=清除,回落 x-api-key)。"""
    global _ANTHROPIC_AUTH_TOKEN
    _ANTHROPIC_AUTH_TOKEN = token or ""


def _anthropic_auth_headers(api_key: str) -> dict:
    if _ANTHROPIC_AUTH_TOKEN:
        return {"authorization": f"Bearer {_ANTHROPIC_AUTH_TOKEN}"}
    return {"x-api-key": api_key}


def _anthropic_messages_and_system(
    *, system: str, user: str, custom_base_url: bool
) -> tuple[list[dict], str | None]:
    if custom_base_url and system:
        return [{"role": "user", "content": f"{system}\n\n{user}"}], None
    return [{"role": "user", "content": user}], system


def _complete_anthropic_custom(
    *,
    api_key: str,
    model: str,
    system: str,
    user: str,
    base_url: str,
    max_tokens: int,
    temperature: float,
    transport_observer: Callable[[dict], None] | None = None,
    provider_model_observer: Callable[[dict[str, str]], None] | None = None,
) -> str:
    messages, _ = _anthropic_messages_and_system(
        system=system,
        user=user,
        custom_base_url=True,
    )
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    # Anthropic-compatible gateways may route through Bedrock models that
    # reject the optional temperature field. Provider defaults are sufficient
    # here; JSON validation/retry remains the determinism boundary.
    headers = {
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "user-agent": "claude-cli/2.0.0 (external, cli)",
        **_anthropic_auth_headers(api_key),
    }
    try:
        with httpx.Client(headers=headers, timeout=120.0) as client:
            message = request_buffered_anthropic_message(
                client=client,
                url=f"{base_url.rstrip('/')}/v1/messages",
                payload=payload,
                retry_attempts=0,
                retry_backoff_s=0,
            )
    except AnthropicTransportError as exc:
        if transport_observer is not None:
            transport_observer(exc.telemetry.as_dict())
        raise
    if transport_observer is not None:
        transport_observer(message.transport.as_dict())
    if message.provider_model and provider_model_observer is not None:
        provider_model_observer({
            "model": message.provider_model,
            "source": "anthropic_message_start.model",
        })
    return "".join(message.text_blocks)


class LLMJsonError(Exception):
    """LLM 多次重试后仍未返回合法 JSON。"""

    def __init__(self, last_text: str, last_exc: Exception):
        self.last_text = last_text
        self.last_exc = last_exc
        super().__init__(f"LLM did not return valid JSON after retries: {last_exc}")


class LLMTransportError(Exception):
    """LLM 调用全部死于传输层(HTTP 错误/超时/连接),一次都没拿到文本。

    T4(20260713):必须与 LLMJsonError 区分——JSON 抖动是模型特性,loop 路径
    O1a 豁免(_INTAKE_SOFT_FAILURES);传输失败是基础设施故障,必须如实计
    fallback(schema_transport_error)。replay84 实证:网关 401 被归为
    llm_json_invalid 后穿过豁免通道,84/84 降级仅 3 条进 done.fallback_used。
    """

    def __init__(self, last_exc: Exception):
        self.last_exc = last_exc
        super().__init__(f"LLM transport failed on all attempts: {last_exc}")


def _backoff_retryable_transport(
    exc: Exception, *, attempt: int, max_retries: int
) -> None:
    """Space retryable transport attempts so a transient gateway can recover."""
    if attempt >= max_retries:
        return
    retryable = isinstance(exc, httpx.TransportError)
    if isinstance(exc, AnthropicTransportError):
        retryable = exc.retryable
    if isinstance(exc, httpx.HTTPStatusError):
        retryable = exc.response.status_code in _RETRYABLE_HTTP_STATUS
    if retryable:
        time.sleep(_JSON_TRANSPORT_RETRY_BACKOFF_S * (2 ** attempt))


def _terminal_transport_error(exc: Exception) -> bool:
    if isinstance(exc, AnthropicTransportError):
        return not exc.retryable
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code not in _RETRYABLE_HTTP_STATUS
    return False


def _repair_json(text: str) -> str | None:
    """轻量 JSON 修复:strip 围栏 + 找第一个平衡 {…} / […] 块。

    返回处理后字符串(保证至少 parse 试过一次),或 None 表示无法修复。

    边界:
    - 空 / 纯空白 → None
    - JSON 字符串内的 { } 字符不算结构括号(in_string 状态机识别 \\")
    - 截断(无闭合)→ None
    """
    if not text or not text.strip():
        return None
    # 先剥围栏(支持开头 / 中间出现的 ``` ... ```)
    stripped = text.strip()
    m = _JSON_FENCE.match(stripped)
    if m:
        stripped = m.group(1).strip()
    else:
        inline = _FENCE_INLINE.search(stripped)
        if inline:
            stripped = inline.group(1).strip()
    # 找第一个平衡块,优先 {,其次 [
    candidate = _find_balanced_block(stripped, "{", "}")
    if candidate is None:
        candidate = _find_balanced_block(stripped, "[", "]")
    return candidate


def _find_balanced_block(text: str, open_ch: str, close_ch: str) -> str | None:
    """在 text 中找第一个开括号到对应闭括号的平衡子串。

    忽略 JSON string 内的括号字符。string 用 " 包围,转义用 \\。
    截断(无对应闭括号)→ None。
    """
    start = text.find(open_ch)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    # 走完都没闭合
    return None


def complete(
    *,
    provider: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    base_url: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.2,
    _skip_span: bool = False,
    _transport_observer: Callable[[dict], None] | None = None,
    _provider_model_observer: Callable[[dict[str, str]], None] | None = None,
) -> str:
    """非流式文本生成。返回纯文本。"""
    ctx = current_trace_ctx() if not _skip_span else None
    if ctx is None:
        return _complete_impl(
            provider=provider, api_key=api_key, model=model,
            system=system, user=user, base_url=base_url,
            max_tokens=max_tokens, temperature=temperature,
            _transport_observer=_transport_observer,
            _provider_model_observer=_provider_model_observer,
        )
    t0 = time.time()
    with ctx.span("llm_call", input_summary={}, metadata={
        "fn": "complete",
        "provider": provider,
        "model": model,
        "base_url_present": bool(base_url),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }) as h:
        def observe_transport(telemetry: dict) -> None:
            h.set_metadata(provider_transport=telemetry)
            if _transport_observer is not None:
                _transport_observer(telemetry)

        def observe_provider_model(observation: dict[str, str]) -> None:
            h.set_metadata(provider_model=observation)
            if _provider_model_observer is not None:
                _provider_model_observer(observation)

        result = _complete_impl(
            provider=provider, api_key=api_key, model=model,
            system=system, user=user, base_url=base_url,
            max_tokens=max_tokens, temperature=temperature,
            _transport_observer=observe_transport,
            _provider_model_observer=observe_provider_model,
        )
        h.set_metadata(
            output_text_len=len(result),
            duration_ms=int((time.time() - t0) * 1000),
        )
        h.set_output({"text_len": len(result)})
        return result


def _complete_impl(
    *,
    provider: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    base_url: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.2,
    _transport_observer: Callable[[dict], None] | None = None,
    _provider_model_observer: Callable[[dict[str, str]], None] | None = None,
) -> str:
    """实际 LLM 调用逻辑(无 span)。"""
    if provider == "anthropic":
        if base_url:
            return _complete_anthropic_custom(
                api_key=api_key,
                model=model,
                system=system,
                user=user,
                base_url=base_url,
                max_tokens=max_tokens,
                temperature=temperature,
                transport_observer=_transport_observer,
                provider_model_observer=_provider_model_observer,
            )
        client = Anthropic(api_key=api_key or None,
                           auth_token=_ANTHROPIC_AUTH_TOKEN or None,
                           base_url=base_url or None)
        messages, anthropic_system = _anthropic_messages_and_system(
            system=system,
            user=user,
            custom_base_url=bool(base_url),
        )
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if anthropic_system is not None:
            kwargs["system"] = anthropic_system
        resp = client.messages.create(**kwargs)
        provider_model = normalize_provider_model(getattr(resp, "model", None))
        if provider_model and _provider_model_observer is not None:
            _provider_model_observer({
                "model": provider_model,
                "source": "anthropic_response.model",
            })
        return resp.content[0].text
    if provider == "openai_compat":
        client = OpenAI(api_key=api_key, base_url=base_url or None)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        provider_model = normalize_provider_model(getattr(resp, "model", None))
        if provider_model and _provider_model_observer is not None:
            _provider_model_observer({
                "model": provider_model,
                "source": "openai_response.model",
            })
        text = resp.choices[0].message.content or ""
        if (
            not text
            and getattr(resp.choices[0], "finish_reason", None) == "length"
            and max_tokens < 1024
        ):
            resp = client.chat.completions.create(
                model=model,
                max_tokens=max(1024, max_tokens * 4),
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            provider_model = normalize_provider_model(getattr(resp, "model", None))
            if provider_model and _provider_model_observer is not None:
                _provider_model_observer({
                    "model": provider_model,
                    "source": "openai_response.model",
                })
            text = resp.choices[0].message.content or ""
        return text
    raise ValueError(f"unknown llm provider: {provider!r}")


def complete_stream(
    *,
    provider: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    base_url: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.2,
) -> Iterator[str]:
    """流式文本生成。逐块 yield text chunk。"""
    ctx = current_trace_ctx()
    if ctx is None:
        yield from _complete_stream_with_empty_fallback(
            provider=provider, api_key=api_key, model=model,
            system=system, user=user, base_url=base_url,
            max_tokens=max_tokens, temperature=temperature,
        )
        return

    t0 = time.time()
    first_chunk_ms: int | None = None
    chunk_count = 0
    fallback_to_complete = False
    fallback_text_len = 0
    with ctx.span("llm_call", input_summary={}, metadata={
        "fn": "complete_stream",
        "provider": provider,
        "model": model,
        "base_url_present": bool(base_url),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }) as h:
        try:
            for chunk in _complete_stream_impl(
                provider=provider, api_key=api_key, model=model,
                system=system, user=user, base_url=base_url,
                max_tokens=max_tokens, temperature=temperature,
            ):
                if first_chunk_ms is None:
                    first_chunk_ms = int((time.time() - t0) * 1000)
                chunk_count += 1
                yield chunk
            if chunk_count == 0:
                fallback_to_complete = True
                fallback_text = _complete_impl(
                    provider=provider, api_key=api_key, model=model,
                    system=system, user=user, base_url=base_url,
                    max_tokens=max_tokens, temperature=temperature,
                )
                fallback_text_len = len(fallback_text)
                if fallback_text:
                    if first_chunk_ms is None:
                        first_chunk_ms = int((time.time() - t0) * 1000)
                    yield fallback_text
        finally:
            duration_ms = int((time.time() - t0) * 1000)
            h.set_metadata(
                duration_ms=duration_ms,
                stream_chunks=chunk_count,
                first_chunk_ms=first_chunk_ms,
                fallback_to_complete=fallback_to_complete,
                fallback_text_len=fallback_text_len,
            )
            h.set_output({"stream_chunks": chunk_count})


def _complete_stream_with_empty_fallback(
    *,
    provider: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    base_url: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.2,
) -> Iterator[str]:
    chunk_count = 0
    for chunk in _complete_stream_impl(
        provider=provider, api_key=api_key, model=model,
        system=system, user=user, base_url=base_url,
        max_tokens=max_tokens, temperature=temperature,
    ):
        chunk_count += 1
        yield chunk
    if chunk_count == 0:
        fallback_text = _complete_impl(
            provider=provider, api_key=api_key, model=model,
            system=system, user=user, base_url=base_url,
            max_tokens=max_tokens, temperature=temperature,
        )
        if fallback_text:
            yield fallback_text


def _complete_stream_impl(
    *,
    provider: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    base_url: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.2,
) -> Iterator[str]:
    """实际流式调用逻辑(无 span)。"""
    if provider == "anthropic":
        if base_url:
            text = _complete_anthropic_custom(
                api_key=api_key,
                model=model,
                system=system,
                user=user,
                base_url=base_url,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if text:
                yield text
            return
        client = Anthropic(api_key=api_key or None,
                           auth_token=_ANTHROPIC_AUTH_TOKEN or None,
                           base_url=base_url or None)
        messages, anthropic_system = _anthropic_messages_and_system(
            system=system,
            user=user,
            custom_base_url=bool(base_url),
        )
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if anthropic_system is not None:
            kwargs["system"] = anthropic_system
        with client.messages.stream(**kwargs) as stream:
            yield from stream.text_stream
        return
    if provider == "openai_compat":
        client = OpenAI(api_key=api_key, base_url=base_url or None)
        stream = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        return
    raise ValueError(f"unknown llm provider: {provider!r}")


def complete_json(
    *,
    provider: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    base_url: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.0,
    max_retries: int = 2,
) -> dict:
    """要求 LLM 输出纯 JSON 时用。

    流程(每轮):
    1. complete() 拿文本,strip
    2. 若开头匹配 ```...``` 围栏,取围栏内
    3. json.loads;失败 → 试 _repair_json → 再 loads
    4. 仍失败 → 进入下一轮 retry
    重试 max_retries 次仍全部失败 → 抛 LLMJsonError(last_text, last_exc);
    若所有 attempt 都死在传输层(一次都没拿到文本)→ 抛 LLMTransportError,
    调用方不得把它当 JSON 抖动豁免。

    JSON 格式错误立即重试；429/5xx 和网络传输异常指数退避后重试。
    每个 attempt 开一个 llm_call span(若有 trace ctx)。
    """
    last_text = ""
    last_exc: Exception | None = None
    got_text = False
    ctx = current_trace_ctx()
    for _attempt in range(max_retries + 1):
        t0 = time.time()
        if ctx is None:
            try:
                text = complete(
                    provider=provider, api_key=api_key, model=model,
                    system=system, user=user, base_url=base_url,
                    max_tokens=max_tokens, temperature=temperature,
                    _skip_span=True,
                ).strip()
            except Exception as e:
                last_exc = e
                if _terminal_transport_error(e):
                    break
                _backoff_retryable_transport(
                    e, attempt=_attempt, max_retries=max_retries
                )
                continue
            got_text = True
            last_text = text
            m = _JSON_FENCE.match(text)
            candidate = m.group(1) if m else text
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                last_exc = e
                repaired = _repair_json(text)
                if repaired is not None:
                    try:
                        return json.loads(repaired)
                    except json.JSONDecodeError as e2:
                        last_exc = e2
        else:
            with ctx.span("llm_call", input_summary={}, metadata={
                "fn": "complete_json",
                "provider": provider,
                "model": model,
                "base_url_present": bool(base_url),
                "max_tokens": max_tokens,
                "temperature": temperature,
                "retry_attempt": _attempt,
            }) as h:
                try:
                    text = complete(
                        provider=provider, api_key=api_key, model=model,
                        system=system, user=user, base_url=base_url,
                        max_tokens=max_tokens, temperature=temperature,
                        _skip_span=True,
                        _transport_observer=lambda telemetry: h.set_metadata(
                            provider_transport=telemetry
                        ),
                    ).strip()
                except Exception as e:
                    last_exc = e
                    h.set_metadata(
                        duration_ms=int((time.time() - t0) * 1000),
                        output_text_len=0,
                        json_parse_success=False,
                    )
                    h.set_error(repr(e))
                    if _terminal_transport_error(e):
                        break
                    _backoff_retryable_transport(
                        e, attempt=_attempt, max_retries=max_retries
                    )
                    continue
                got_text = True
                last_text = text
                m = _JSON_FENCE.match(text)
                candidate = m.group(1) if m else text
                try:
                    result = json.loads(candidate)
                    h.set_metadata(
                        duration_ms=int((time.time() - t0) * 1000),
                        output_text_len=len(text),
                        json_parse_success=True,
                    )
                    return result
                except json.JSONDecodeError as e:
                    last_exc = e
                    repaired = _repair_json(text)
                    if repaired is not None:
                        try:
                            result = json.loads(repaired)
                            h.set_metadata(
                                duration_ms=int((time.time() - t0) * 1000),
                                output_text_len=len(text),
                                json_parse_success=True,
                            )
                            return result
                        except json.JSONDecodeError as e2:
                            last_exc = e2
                h.set_metadata(
                    duration_ms=int((time.time() - t0) * 1000),
                    output_text_len=len(text),
                    json_parse_success=False,
                )
        # 进入下一轮 retry
    if not got_text:
        raise LLMTransportError(last_exc or RuntimeError("no attempt made"))
    raise LLMJsonError(last_text, last_exc or RuntimeError("no attempt made"))
