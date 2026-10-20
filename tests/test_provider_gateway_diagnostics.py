from daq_fae.provider_errors import anthropic_failure
from src.agent.anthropic_transport import AnthropicTransportError, AnthropicTransportTelemetry


def _failure(code: str | None) -> dict:
    telemetry = AnthropicTransportTelemetry(
        mode="buffered", attempts=1, retry_count=0, retry_reasons=("http_400",),
        first_event_ms=None, complete_message_ms=None, message_stop_received=False,
        discarded_incomplete_attempts=1, http_status=400,
        gateway_error_code=code, gateway_request_id="provider-request-123",
    )
    return anthropic_failure(AnthropicTransportError(
        "http_400", retryable=False, telemetry=telemetry,
    ))


def test_provider_failure_preserves_safe_gateway_diagnostics():
    done = _failure("context_length_exceeded")
    assert done["outcome"] == "provider_configuration_error"
    assert done["provider_status_code"] == 400
    assert done["gateway_error_code"] == "context_length_exceeded"
    assert done["gateway_request_id"] == "provider-request-123"
    assert done["fallback_reason"] == "provider_bad_request"


def test_known_gateway_configuration_errors_match_camera_fae():
    assert _failure("model_not_found")["fallback_reason"] == "provider_model_not_found"
    assert _failure("forced_tool_choice_unsupported")["fallback_reason"] == "provider_tool_choice_unsupported"
