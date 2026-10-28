"""Request-local preserved-thinking compatibility around the pinned FAE loop."""
from copy import deepcopy
import hashlib
import json

from src.agent.anthropic_transport import AnthropicTransportError, AnthropicTransportTelemetry
from src.agent.loop.adapters import _to_anthropic_messages, _fold_system_into_user_message
from src.agent.loop.runtime import LoopRuntime
from src.agent.loop.tools import ToolResult


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _reject(reason):
    raise AnthropicTransportError(reason, retryable=False,
        telemetry=AnthropicTransportTelemetry(
            mode='anthropic_prefix_guard', attempts=0, retry_count=0,
            retry_reasons=(reason,), first_event_ms=None, complete_message_ms=None,
            message_stop_received=False, discarded_incomplete_attempts=0,
            provider_protocol_status='incomplete'))


class _PrefixSession:
    def __init__(self, adapter):
        self.adapter = adapter
        self.catalog = None
        self.definitions = {}
        self.active = frozenset()
        self.expected_prefix = None
        self.expected_length = 0
        self.scope_changes = 0
        self.blocked_calls = 0

    def snapshot(self):
        return {'strategy': 'stable_catalog_append_only',
                'advertised_tool_count': len(self.definitions),
                'active_tool_count': len(self.active),
                'tool_scope_changes': self.scope_changes,
                'blocked_tool_calls': self.blocked_calls,
                'thinking_blocks_removed': 0}

    def wire(self, messages):
        # The same reviewed serializer/fold as the real adapter, on private data.
        system, converted = _to_anthropic_messages(deepcopy(messages))
        if getattr(self.adapter, '_fold_system_into_user', False):
            converted = _fold_system_into_user_message(system, converted)
            system = ''
        return {'system': system, 'messages': converted}

    def prepare(self, messages, tools):
        if not isinstance(tools, list):
            _reject('client_tool_catalog_invalid')
        definitions = {t['function']['name']: t for t in tools}
        if len(definitions) != len(tools):
            _reject('client_tool_catalog_invalid')
        if self.catalog is None:
            self.catalog = deepcopy(tools)
            self.definitions = deepcopy(definitions)
            self.active = frozenset(definitions)
        elif any(name not in self.definitions or self.definitions[name] != definition
                 for name, definition in definitions.items()):
            _reject('client_tool_catalog_changed')
        wire = self.wire(messages)
        if self.expected_prefix is not None:
            prefix = {'system': wire['system'],
                      'messages': wire['messages'][:self.expected_length]}
            if _digest(prefix) != self.expected_prefix:
                _reject('client_thinking_prefix_changed')
        active = frozenset(definitions)
        if active != self.active:
            self.active = active
            self.scope_changes += 1
            # Append after all pending tool results; never rewrite a prior turn.
            messages.append({'role': 'user', 'content': (
                'Tool availability has changed for this phase. Only call these tools: '
                + json.dumps(sorted(active))
                + '. Other tools are disabled. Preserve all existing evidence, '
                  'answer-contract, access and execution-budget requirements.')})
            wire = self.wire(messages)
        return wire


class _PrefixAdapter:
    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session.adapter, name)

    def chat(self, messages, tools=None, required_tool=None):
        session = self.session
        wire = session.prepare(messages, tools)
        if required_tool and required_tool not in session.active:
            _reject('client_required_tool_unavailable')
        for event in session.adapter.chat(deepcopy(messages), deepcopy(session.catalog),
                                          required_tool=required_tool):
            if event.get('type') == 'stop':
                blocks = event.get('_provider_blocks')
                expected = deepcopy(wire)
                if isinstance(blocks, list):
                    expected['messages'].append({'role': 'assistant',
                                                 'content': deepcopy(blocks)})
                session.expected_length = len(expected['messages'])
                session.expected_prefix = _digest(expected)
                event = {**event, 'transport': {
                    **(event.get('transport') or {}),
                    'anthropic_prefix': session.snapshot()}}
            yield event


class _PhaseToolbox:
    def __init__(self, base, session):
        self.base, self.session = base, session

    def __getattr__(self, name):
        return getattr(self.base, name)

    def with_request_context(self, question):
        return _PhaseToolbox(self.base.with_request_context(question), self.session)

    def dispatch(self, name, arguments):
        if name not in self.session.active:
            self.session.blocked_calls += 1
            return ToolResult(status='tool_error', content={
                'error': 'tool_unavailable_in_current_phase',
                'allowed_tools': sorted(self.session.active)})
        return self.base.dispatch(name, arguments)


class DaqLoopRuntime(LoopRuntime):
    """Delegate each execution to the exact pinned kernel with local guards."""
    def run(self, *args, **kwargs):
        if getattr(self.adapter, 'preserve_thinking_prefix', False) is not True:
            yield from super().run(*args, **kwargs)
            return
        session = _PrefixSession(self.adapter)
        runner = LoopRuntime(_PrefixAdapter(session), _PhaseToolbox(self.toolbox, session),
                             config=self.config, system_prompt_path=self.system_prompt_path,
                             evidence_policy=self.evidence_policy)
        for event in runner.run(*args, **kwargs):
            if event.get('type') == 'done':
                event = {**event, 'anthropic_prefix': session.snapshot()}
            yield event
