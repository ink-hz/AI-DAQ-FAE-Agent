from daq_fae.provider_errors import vision_failure_reason


def test_vision_http_rejection_is_classified_from_preserved_status():
    def done(status, code=None):
        return {'attachment_failures': [{
            'error_code': 'vision_http_error',
            'vision_diagnostics': {'http_status': status, 'gateway_error_code': code},
        }]}

    assert vision_failure_reason(done(400)) == 'vision_provider_configuration_error'
    assert vision_failure_reason(done(503, 'model_not_found')) == 'vision_provider_configuration_error'
    assert vision_failure_reason(done(503, 'forced_tool_choice_unsupported')) == 'vision_provider_configuration_error'
    assert vision_failure_reason(done(429)) == 'vision_provider_rate_limited'
    assert vision_failure_reason(done(503)) == 'vision_provider_unavailable'
    assert vision_failure_reason(done(299)) == 'vision_provider_protocol_error'
    assert vision_failure_reason({'attachment_failures': []}) is None
