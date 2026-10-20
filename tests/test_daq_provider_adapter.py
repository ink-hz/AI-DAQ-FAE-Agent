from daq_fae.provider_adapter import normalize_unsigned_thinking


def test_unsigned_thinking_is_excluded_from_anthropic_continuation():
    original = {
        'type': 'stop', 'stop_reason': 'tool_use',
        '_provider_blocks': [
            {'type': 'thinking', 'thinking': '', 'signature': 'valid-opaque'},
            {'type': 'tool_use', 'id': 'one', 'name': 'search_knowledge', 'input': {}},
            {'type': 'thinking', 'thinking': ''},
            {'type': 'tool_use', 'id': 'two', 'name': 'sdk_evidence', 'input': {}},
        ],
        'transport': {'provider_protocol_status': 'complete'},
    }
    normalized = normalize_unsigned_thinking(original)
    assert [b['type'] for b in normalized['_provider_blocks']] == [
        'thinking', 'tool_use', 'tool_use',
    ]
    assert normalized['_provider_blocks'][0]['signature'] == 'valid-opaque'
    assert normalized['transport']['unsigned_thinking_blocks_dropped'] == 1
    assert len(original['_provider_blocks']) == 4
    assert 'unsigned_thinking_blocks_dropped' not in original['transport']


def test_signed_provider_blocks_pass_through_without_change():
    original = {'type': 'stop', '_provider_blocks': [
        {'type': 'thinking', 'thinking': '', 'signature': 'valid-opaque'},
        {'type': 'tool_use', 'id': 'one', 'name': 'search_knowledge', 'input': {}},
    ], 'transport': {'provider_protocol_status': 'complete'}}
    assert normalize_unsigned_thinking(original) == original


def test_unsigned_block_with_empty_signature_is_also_excluded():
    original = {'type': 'stop', '_provider_blocks': [
        {'type': 'thinking', 'thinking': '', 'signature': ''},
        {'type': 'tool_use', 'id': 'one', 'name': 'search_knowledge', 'input': {}},
    ]}
    assert normalize_unsigned_thinking(original)['_provider_blocks'] == [
        {'type': 'tool_use', 'id': 'one', 'name': 'search_knowledge', 'input': {}},
    ]
