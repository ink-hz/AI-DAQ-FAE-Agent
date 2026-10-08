"""Extend a DAQ toolbox with request-scoped, session-authorized user evidence.

This factory does not load product facts, configure a provider, publish knowledge,
or grant identity. Callers must supply an already authorized session and store.
Build a fresh extension per request so image-call budgets do not span turns.
"""
from __future__ import annotations

import copy
import json

from src.agent.attachment_tools import AttachmentTools, _VISION_TOOL_SPEC
from src.agent.loop.tools import ToolResult
from src.attachments.models import AttachmentDescriptor, AttachmentError

_ATTACHMENT_NAMES = frozenset({'search_attachments', 'read_attachment', 'analyze_image'})


def _locator_key(source_id, locator):
    return source_id, json.dumps(locator, sort_keys=True)


class AttachmentEvidenceToolBox:
    def __init__(self, base, *, session, store, vision=None):
        self._base = base
        self._session = session
        self._store = store
        self._vision = vision
        self._descriptors = self._validate_session()
        self._by_source = {item.source_id: item for item in self._descriptors}
        self._tools = AttachmentTools(self._descriptors, store, vision)
        self._exposed_locators = set()
        if _ATTACHMENT_NAMES & {item['function']['name'] for item in base.tool_schemas()}:
            raise ValueError('attachment_tool_name_collision')

    def _validate_session(self):
        descriptors = []
        for aid in self._session.active_attachment_ids:
            original = self._session.attachments.get(aid)
            if original is None:
                raise AttachmentError('attachment_not_visible')
            manifest = self._store.get(aid)
            if manifest.bound_session_id != self._session.session_id:
                raise AttachmentError('attachment_session_mismatch')
            if manifest.owner_subject_id != self._session.owner_subject_id:
                raise AttachmentError('attachment_owner_mismatch')
            if manifest.status != 'ready':
                raise AttachmentError('attachment_not_ready')
            current = AttachmentDescriptor.from_manifest(manifest)
            if original != current:
                raise AttachmentError('attachment_descriptor_mismatch')
            descriptors.append(current)
        return descriptors

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_base'), name)

    @property
    def attachment_source_ids(self):
        return self._tools.source_ids

    @property
    def attachment_vision_model(self):
        return self._tools.vision_model

    def with_request_context(self, question):
        boxed = copy.copy(self)
        boxed._base = self._base.with_request_context(question)
        return boxed

    def with_session(self, session, loop_state=None):
        # A Session carries binding/owner identity; SessionContext alone cannot
        # authorize attachments and is intentionally insufficient here.
        return extend_with_attachments(self._base, session=session, store=self._store, vision=self._vision)

    def tool_schemas(self):
        specs = self._tools.schemas()
        if any(item.kind == 'image' for item in self._descriptors) and not self._vision:
            # Expose the capability even without a configured provider, so callers
            # get explicit vision_unavailable evidence rather than unknown_tool.
            specs = [*specs, _VISION_TOOL_SPEC]
        return self._base.tool_schemas() + [
            {'type': 'function', 'function': {
                'name': spec['name'], 'description': spec['description'],
                'parameters': copy.deepcopy(spec['input_schema']),
            }} for spec in specs
        ]

    def dispatch(self, name, arguments):
        if name not in _ATTACHMENT_NAMES:
            return self._base.dispatch(name, arguments)
        try:
            if not isinstance(arguments, dict):
                return ToolResult('tool_error', {'error': 'invalid_arguments'})
            allowed = {
                'search_attachments': {'query', 'source_ids', 'limit'},
                'read_attachment': {'source_id', 'locator'},
                'analyze_image': {'source_id', 'question'},
            }[name]
            if set(arguments) - allowed:
                return ToolResult('tool_error', {'error': 'invalid_arguments'})
            if name == 'search_attachments':
                limit = arguments.get('limit', 8)
                if (not isinstance(arguments.get('query'), str)
                        or type(limit) is not int or not 1 <= limit <= 8):
                    return ToolResult('tool_error', {'error': 'invalid_arguments'})
            if name == 'analyze_image':
                question = arguments.get('question')
                if not isinstance(question, str) or not question.strip() or len(question) > 2000:
                    return ToolResult('tool_error', {'error': 'invalid_arguments'})
            current = self._validate_session()
            if {item.source_id for item in current} != set(self._by_source):
                raise AttachmentError('attachment_not_visible')
            if name == 'search_attachments':
                ids = arguments.get('source_ids')
                if ids is not None:
                    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
                        return ToolResult('tool_error', {'error': 'invalid_arguments'})
                    if any(item not in self._by_source for item in ids):
                        raise AttachmentError('attachment_not_visible')
            else:
                source = arguments.get('source_id')
                if source not in self._by_source:
                    raise AttachmentError('attachment_not_visible')
                if name == 'read_attachment':
                    locator = arguments.get('locator')
                    if not isinstance(locator, dict):
                        return ToolResult('tool_error', {'error': 'invalid_arguments'})
                    if _locator_key(source, locator) not in self._exposed_locators:
                        raise AttachmentError('attachment_locator_not_exposed')
            result = self._tools.dispatch(name, arguments)
            if name == 'search_attachments' and result.status == 'ok':
                for hit in result.content['hits']:
                    self._exposed_locators.add(_locator_key(hit['source_id'], hit['locator']))
            return result
        except AttachmentError as exc:
            return ToolResult('tool_error', {'error': exc.code})
        except (TypeError, ValueError):
            return ToolResult('tool_error', {'error': 'invalid_arguments'})

    def redact_tool_input(self, name, arguments):
        if name not in _ATTACHMENT_NAMES:
            return self._base.redact_tool_input(name, arguments)
        if not isinstance(arguments, dict):
            return {'invalid_arguments': True}
        safe = {}
        source = arguments.get('source_id')
        if isinstance(source, str) and source in self._by_source:
            safe['source_id'] = source
        ids = arguments.get('source_ids')
        if isinstance(ids, list):
            safe['source_ids'] = [item for item in ids if isinstance(item, str) and item in self._by_source]
        for field in ('query', 'question'):
            if isinstance(arguments.get(field), str):
                safe[field + '_length'] = len(arguments[field])
        locator = arguments.get('locator')
        if isinstance(locator, dict) and isinstance(source, str):
            if _locator_key(source, locator) in self._exposed_locators:
                safe['locator'] = locator
        return safe


def extend_with_attachments(base, *, session, store, vision=None):
    return AttachmentEvidenceToolBox(base, session=session, store=store, vision=vision)
