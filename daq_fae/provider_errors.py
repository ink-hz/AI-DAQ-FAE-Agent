"""Map buffered Anthropic transport diagnostics to visible DAQ outcomes."""

import httpx

from src.agent.anthropic_transport import AnthropicTransportError


def anthropic_failure(exc: AnthropicTransportError) -> dict:
    status = exc.telemetry.http_status
    reason = exc.reason
    gateway_code = exc.telemetry.gateway_error_code
    if status in {400, 401, 403, 404, 422}:
        outcome = "provider_configuration_error"
    elif status == 429:
        outcome = "provider_rate_limited"
    elif status is not None and status >= 500:
        outcome = "provider_unavailable"
    elif reason in {"connect_error", "read_error", "write_error"}:
        outcome = "provider_unavailable"
    else:
        outcome = "provider_protocol_error"
    fallback_reason = {
        "model_not_found": "provider_model_not_found",
        "forced_tool_choice_unsupported": "provider_tool_choice_unsupported",
    }.get(gateway_code)
    if fallback_reason is None:
        fallback_reason = "provider_bad_request" if status == 400 else f"anthropic_transport_{reason}"
    return {
        "type": "done",
        "answer": "数采模型或网关本轮未返回完整可用的结果，请稍后重试或检查配置。",
        "outcome": outcome,
        "sources": [],
        "tool_calls": [],
        "provider_status_code": status,
        "transport_reason": reason,
        "transport_retry_count": exc.telemetry.retry_count,
        "provider_protocol_status": exc.telemetry.provider_protocol_status,
        "gateway_error_code": gateway_code,
        "gateway_request_id": exc.telemetry.gateway_request_id,
        "fallback_used": True,
        "fallback_reason": fallback_reason,
    }


def provider_exception_done(exc: Exception) -> dict:
    if isinstance(exc, AnthropicTransportError):
        return anthropic_failure(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        outcome = ('provider_configuration_error' if status in {400, 401, 403, 404, 422}
                   else 'provider_rate_limited' if status == 429
                   else 'provider_unavailable' if status >= 500 else 'provider_error')
        return {'answer': '数采模型请求失败，未生成答案。', 'outcome': outcome,
                'sources': [], 'tool_calls': [], 'provider_status_code': status,
                'fallback_used': True, 'fallback_reason': f'provider_http_{status}'}
    if isinstance(exc, httpx.TransportError):
        return {'answer': '数采模型连接失败，未生成答案。', 'outcome': 'provider_unavailable',
                'sources': [], 'tool_calls': [], 'error_type': type(exc).__name__,
                'fallback_used': True, 'fallback_reason': 'provider_transport_error'}
    raise TypeError('unsupported_provider_exception')


def vision_failure_reason(done: dict) -> str | None:
    """Classify a failed image provider call from safe trace diagnostics."""
    for failure in done.get("attachment_failures") or []:
        if failure.get("error_code") != "vision_http_error":
            continue
        diagnostics = failure.get("vision_diagnostics") or {}
        status = diagnostics.get("http_status")
        if diagnostics.get("gateway_error_code") in {
            "model_not_found", "forced_tool_choice_unsupported",
        }:
            return "vision_provider_configuration_error"
        if status in {400, 401, 403, 404, 422}:
            return "vision_provider_configuration_error"
        if status == 429:
            return "vision_provider_rate_limited"
        if isinstance(status, int) and status >= 500:
            return "vision_provider_unavailable"
        return "vision_provider_protocol_error"
    return None
