"""Wire-level contracts against a strict local preserved-thinking gateway."""
import copy
import json
from pathlib import Path

import httpx
import pytest

from daq_fae import provider_adapter
from src.agent.anthropic_transport import AnthropicTransportError
from src.agent.loop.runtime import LoopConfig, LoopRuntime
from src.agent.loop.tools import ToolResult


class Box:
    attachment_source_ids = set()
    attachment_vision_model = None

    def __init__(self):
        self.calls = []

    def with_request_context(self, question):
        return self

    def tool_schemas(self):
        return [{'type': 'function', 'function': {'name': 'probe',
            'description': 'Protocol fixture only',
            'parameters': {'type': 'object', 'properties': {}}}}]

    def redact_tool_input(self, name, arguments):
        return arguments

    def dispatch(self, name, arguments):
        self.calls.append(name)
        return ToolResult(status='ok', content={'fixture': True})


def sse_response(index, name, arguments, extra_calls=()):
    blocks = [
        {'type': 'thinking', 'thinking': '', 'signature': f'opaque-{index}'},
        {'type': 'tool_use', 'id': f'call-{index}', 'name': name, 'input': arguments},
    ]
    blocks.extend(extra_calls)
    events = [('message_start', {'type': 'message_start', 'message': {
        'id': f'msg-{index}', 'type': 'message', 'role': 'assistant',
        'model': 'claude-opus-5-5', 'content': [],
        'usage': {'input_tokens': 1, 'output_tokens': 1}}})]
    for i, block in enumerate(blocks):
        events.extend([
            ('content_block_start', {'type': 'content_block_start', 'index': i,
                                     'content_block': block}),
            ('content_block_stop', {'type': 'content_block_stop', 'index': i}),
        ])
    events.extend([
        ('message_delta', {'type': 'message_delta',
                           'delta': {'stop_reason': 'tool_use'},
                           'usage': {'output_tokens': 2}}),
        ('message_stop', {'type': 'message_stop'}),
    ])
    body = ''.join(f'event: {name}\ndata: {json.dumps(value)}\n\n'
                   for name, value in events)
    return blocks, httpx.Response(200, text=body,
                                 headers={'content-type': 'text/event-stream'})


class StrictGateway:
    def __init__(self, *, disobey=False, fail_status=None, parallel_first=False):
        self.requests = []
        self.expected = None
        self.disobey = disobey
        self.fail_status = fail_status
        self.parallel_first = parallel_first

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        if self.fail_status:
            return httpx.Response(self.fail_status, json={
                'error': {'type': 'invalid_request_error', 'message': 'unrelated bad request'}})
        if self.expected is not None:
            prefix = {k: body.get(k) for k in ('system', 'tools')}
            prefix['messages'] = body['messages'][:len(self.expected['messages'])]
            if prefix != self.expected:
                return httpx.Response(400, json={'error': {
                    'type': 'invalid_request_error',
                    'message': 'Invalid signature: thinking prefix changed'}})
        index = len(self.requests)
        if index == 1:
            name, args = 'submit_answer', {'outcome': 'resolved'}  # requires correction
        elif self.disobey and index == 2:
            name, args = 'probe', {}
        else:
            name, args = 'submit_answer', {'outcome': 'resolved', 'conclusion': 'Protocol verified.'}
        extra = []
        if self.parallel_first and index == 1:
            name, args = 'probe', {}
            extra = [{'type': 'tool_use', 'id': 'parallel-1', 'name': 'probe', 'input': {}}]
        blocks, response = sse_response(index, name, args, extra)
        self.expected = {k: copy.deepcopy(body.get(k)) for k in ('system', 'tools')}
        self.expected['messages'] = copy.deepcopy(body['messages']) + [
            {'role': 'assistant', 'content': blocks}]
        return response


def adapter_for(gateway):
    adapter = provider_adapter.DaqAnthropicAdapter(
        api_key='fixture', model='claude-opus-5-5', thinking_mode='adaptive',
        effort='high', tool_choice_strategy='submit_only_auto',
        base_url='https://strict.invalid', retry_attempts=0)
    adapter._client.close()
    adapter._client = httpx.Client(transport=httpx.MockTransport(gateway))
    return adapter


def runtime_for(adapter, box):
    # The old kernel is the RED baseline until DAQ exports its thin delegate.
    runtime_type = getattr(provider_adapter, 'DaqLoopRuntime', LoopRuntime)
    return runtime_type(adapter=adapter, toolbox=box,
                        config=LoopConfig(max_tool_calls=8),
                        system_prompt_path=Path('prompts/reviewed_knowledge_system.md'))


@pytest.mark.parametrize('history', [None, [
    {'role': 'user', 'content': [
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                   'data': 'opaque-image-bytes'}},
        {'type': 'text', 'text': 'English image continuation fixture'},
    ]},
]])
def test_contract_correction_keeps_signed_prefix_and_images(history):
    gateway = StrictGateway()
    original = copy.deepcopy(history)
    box = Box()
    done = list(runtime_for(adapter_for(gateway), box).run(
        'Protocol fixture only.', history=history))[-1]
    assert done['outcome'] == 'resolved'
    assert len(gateway.requests) == 2
    assert gateway.requests[0]['tools'] == gateway.requests[1]['tools']
    assert history == original
    assert done['anthropic_prefix']['tool_scope_changes'] == 1
    assert done['anthropic_prefix']['thinking_blocks_removed'] == 0


def test_wrapup_guard_never_executes_a_tool_excluded_by_the_loop():
    gateway = StrictGateway(disobey=True)
    box = Box()
    done = list(runtime_for(adapter_for(gateway), box).run('Protocol fixture only.'))[-1]
    assert box.calls == []
    assert done['anthropic_prefix']['blocked_tool_calls'] == 1


def test_other_400_errors_remain_visible_and_are_not_retried():
    gateway = StrictGateway(fail_status=400)
    with pytest.raises(AnthropicTransportError) as failure:
        list(runtime_for(adapter_for(gateway), Box()).run('Protocol fixture only.'))
    assert failure.value.telemetry.http_status == 400
    assert len(gateway.requests) == 1


def prefix_conversation():
    from daq_fae.thinking_prefix import _PrefixSession, _PrefixAdapter
    gateway = StrictGateway()
    session = _PrefixSession(adapter_for(gateway))
    adapter = _PrefixAdapter(session)
    tools = Box().tool_schemas()
    messages = [{'role': 'system', 'content': 'Protocol fixture'},
                {'role': 'user', 'content': 'Only verify protocol.'}]
    events = list(adapter.chat(messages, tools))
    messages.extend([
        {'role': 'assistant', '_provider_blocks': events[-1]['_provider_blocks']},
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'original tool result'},
    ])
    return gateway, adapter, tools, messages


@pytest.mark.parametrize('edit', ['system', 'image_or_user', 'thinking'])
def test_edited_prefix_is_a_local_nonretryable_protocol_failure(edit):
    from daq_fae.provider_errors import provider_exception_done
    gateway, adapter, tools, messages = prefix_conversation()
    if edit == 'thinking':
        messages[2]['_provider_blocks'].pop(0)
    else:
        messages[0 if edit == 'system' else 1]['content'] = 'edited'
    with pytest.raises(AnthropicTransportError) as failure:
        list(adapter.chat(messages, tools))
    assert not failure.value.retryable
    assert failure.value.reason == 'client_thinking_prefix_changed'
    done = provider_exception_done(failure.value)
    assert done['outcome'] == 'provider_protocol_error'
    assert done['provider_status_code'] is None
    assert len(gateway.requests) == 1


def test_tool_schema_edits_are_rejected_without_an_upstream_call():
    gateway, adapter, tools, messages = prefix_conversation()
    tools[0]['function']['description'] = 'new description'
    with pytest.raises(AnthropicTransportError) as failure:
        list(adapter.chat(messages, tools))
    assert failure.value.reason == 'client_tool_catalog_changed'
    assert len(gateway.requests) == 1


def test_already_sent_tool_results_cannot_be_trimmed():
    gateway, adapter, tools, messages = prefix_conversation()
    events = list(adapter.chat(messages, tools))
    messages.append({'role': 'assistant', '_provider_blocks': events[-1]['_provider_blocks']})
    messages[3]['content'] = '[trimmed]'
    with pytest.raises(AnthropicTransportError) as failure:
        list(adapter.chat(messages, tools))
    assert failure.value.reason == 'client_thinking_prefix_changed'
    assert len(gateway.requests) == 2


def test_shared_adapter_reused_runtime_has_a_fresh_session_per_run():
    gateways = {name: StrictGateway() for name in ('run-A', 'run-B')}

    def route(request):
        text = json.loads(request.content)['messages'][0]['content']
        return gateways['run-A' if 'run-A' in text else 'run-B'](request)

    runtime = runtime_for(adapter_for(route), Box())
    for name in gateways:
        done = list(runtime.run(f'Protocol {name}.'))[-1]
        assert done['outcome'] == 'resolved'
        assert done['anthropic_prefix']['tool_scope_changes'] == 1
    assert all(len(g.requests) == 2 for g in gateways.values())


def test_mixed_tool_result_batches_do_not_mutate_caller_or_duplicate_results():
    from daq_fae.thinking_prefix import _PrefixSession, _PrefixAdapter
    gateway = StrictGateway(parallel_first=True)
    adapter = _PrefixAdapter(_PrefixSession(adapter_for(gateway)))
    tools = Box().tool_schemas()
    messages = [{'role': 'user', 'content': 'Parallel tool result fixture'}]
    events = list(adapter.chat(messages, tools))
    first_result = {'type': 'tool_result', 'tool_use_id': 'call-1', 'content': 'A'}
    messages.extend([
        {'role': 'assistant', '_provider_blocks': events[-1]['_provider_blocks']},
        {'role': 'user', 'content': [first_result]},
        {'role': 'tool', 'tool_call_id': 'parallel-1', 'content': 'B'},
    ])
    events = list(adapter.chat(messages, tools))
    assert messages[2]['content'] == [first_result]
    messages.extend([
        {'role': 'assistant', '_provider_blocks': events[-1]['_provider_blocks']},
        {'role': 'tool', 'tool_call_id': 'call-2', 'content': 'continue'},
    ])
    list(adapter.chat(messages, tools))
    assert len(gateway.requests) == 3
    assert gateway.requests[1]['messages'][2]['content'] == [
        first_result, {'type': 'tool_result', 'tool_use_id': 'parallel-1', 'content': 'B'}]
