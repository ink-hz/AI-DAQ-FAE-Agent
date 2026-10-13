"""Validated, model-proposed goals for one DAQ turn.

The model proposes semantics. This module validates scope and derives stable IDs;
it never promotes a model-proposed product entity into published evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parents[1]
_SERVICE = {'service_identity', 'service_capabilities', 'service_coverage'}
_PRODUCT = {
    'catalog', 'resolve_entity', 'lookup_spec', 'inspect_topology',
    'lookup_procedure', 'check_software_support', 'sdk_evidence',
    'official_links', 'experience', 'risk', 'selection', 'search_knowledge',
}
_ATTACHMENT = {'search_attachments', 'analyze_image'}
_CONDITIONS = {
    'variant', 'resolution_variant', 'hardware_revision', 'resolution',
    'platform', 'connection', 'connection_mode', 'viewer_version',
    'sdk_version', 'firmware_version', 'task', 'per_port', 'power',
    'recording_format', 'storage',
    'scope', 'sensor', 'camera_type', 'camera_config', 'stereo_pair',
    'lens_type', 'port',
}
_SENSITIVE_CONDITION_KEY = re.compile(r'(?:secret|token|password|api_key|credential)', re.I)
_KINDS = {'service', 'product', 'attachment', 'unknown'}
_CONFIDENCE = {'high', 'medium', 'low'}
_DEPENDENCY = {'unknown', 'relevant', 'required_for_answer', 'not_relevant'}
_SAFE_CODE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')


def _safe_condition_value(value: Any, depth: int = 0) -> bool:
    if depth > 4:
        return False
    if isinstance(value, str):
        return len(value) <= 128
    if type(value) in {int, bool}:
        return True
    if isinstance(value, list):
        return len(value) <= 8 and all(_safe_condition_value(item, depth + 1)
                                       for item in value)
    if isinstance(value, dict):
        return len(value) <= 8 and all(
            isinstance(key, str) and 0 < len(key) <= 64 and
            _safe_condition_value(item, depth + 1)
            for key, item in value.items())
    return False


class SchemaExtractionError(ValueError):
    """A schema candidate is missing or unsafe; provider transport errors propagate."""


def _normalized(value: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).casefold().replace('×', 'x'))


@dataclass(frozen=True)
class DaqGoal:
    id: str
    kind: str
    capability: str
    entities: tuple[str, ...] = ()
    field: str | None = None
    conditions: tuple[tuple[str, Any], ...] = ()
    unmapped_condition_keys: tuple[str, ...] = ()
    critical: bool = True

    def condition_map(self) -> dict[str, Any]:
        return dict(self.conditions)


@dataclass(frozen=True)
class DaqRequestSchema:
    original_question: str
    effective_question: str
    request_kind: str
    goals: tuple[DaqGoal, ...]
    topic_switch: bool
    attachment_dependency: str
    confidence: str
    answer_language: str
    fallback_reason: str | None = None

    @classmethod
    def from_candidate(cls, question: str, candidate: dict[str, Any], *,
                       prior_user_context: str = '', attachment_present: bool = False,
                       image_present: bool = False,
                       selected_answer_language: str | None = None) -> 'DaqRequestSchema':
        if not isinstance(question, str) or not question.strip() or not isinstance(candidate, dict):
            raise SchemaExtractionError('schema_candidate_invalid')
        confidence = candidate.get('confidence')
        if confidence not in _CONFIDENCE:
            raise SchemaExtractionError('schema_confidence_invalid')
        raw_goals = candidate.get('goals')
        if not isinstance(raw_goals, list) or not 1 <= len(raw_goals) <= 8:
            raise SchemaExtractionError('schema_goals_invalid')
        accepted_scope = _normalized(question + ' ' + prior_user_context)
        goals: list[DaqGoal] = []
        ids: set[str] = set()
        for raw in raw_goals:
            if not isinstance(raw, dict):
                raise SchemaExtractionError('schema_goal_invalid')
            kind, capability = raw.get('kind'), raw.get('capability')
            allowed = (_SERVICE if kind == 'service' else _PRODUCT if kind == 'product'
                       else _ATTACHMENT if kind == 'attachment' else {'clarify'} if kind == 'unknown'
                       else set())
            if kind not in _KINDS or capability not in allowed:
                raise SchemaExtractionError('schema_goal_capability_invalid')
            if kind == 'unknown':
                # Clarification never asserts an entity or a product field.
                # User-provided context is retained separately in the turn state.
                raw = {**raw, 'entities': [], 'conditions': {}}
                raw.pop('field', None)
            entities = raw.get('entities', [])
            if not isinstance(entities, list) or len(entities) > 6 or any(
                not isinstance(entity, str) or not entity.strip() or
                _normalized(entity) not in accepted_scope for entity in entities
            ):
                raise SchemaExtractionError('entity_not_grounded')
            if kind != 'product' and entities:
                raise SchemaExtractionError('schema_goal_scope_invalid')
            field = raw.get('field')
            if field is not None and (kind != 'product' or not isinstance(field, str)
                                      or not _SAFE_CODE.fullmatch(field)):
                raise SchemaExtractionError('schema_field_invalid')
            # Only a spec lookup can require a claim field. Entity, topology,
            # procedure and compatibility tools return other evidence kinds.
            if kind == 'product' and capability != 'lookup_spec':
                field = None
            conditions = raw.get('conditions', {})
            if not isinstance(conditions, dict) or len(conditions) > 16 or any(
                not isinstance(key, str) or not _SAFE_CODE.fullmatch(key) or
                _SENSITIVE_CONDITION_KEY.search(key) or
                not _safe_condition_value(value)
                for key, value in conditions.items()
            ) or len(json.dumps(conditions, ensure_ascii=False)) > 600:
                raise SchemaExtractionError('schema_conditions_invalid')
            conditions = dict(conditions)
            unmapped = set(conditions) - _CONDITIONS
            if kind != 'product' and conditions:
                raise SchemaExtractionError('schema_goal_scope_invalid')
            sensor = conditions.pop('sensor', None)
            camera = conditions.pop('camera_type', None)
            if any(value is not None and not isinstance(value, str)
                   for value in (sensor, camera)):
                raise SchemaExtractionError('camera_qualifier_invalid')
            if sensor is not None and camera is not None and sensor != camera:
                raise SchemaExtractionError('camera_qualifier_conflict')
            camera = camera or sensor
            stereo = conditions.pop('stereo_pair', None)
            camera_config = conditions.pop('camera_config', None)
            user_words = question + ' ' + prior_user_context
            has_fisheye = bool(re.search(r'鱼眼|fisheye', user_words, re.I))
            has_rgb = bool(re.search(r'\bRGB\b|彩色', user_words, re.I))
            has_mono = bool(re.search(r'\bMono\b|单色|黑白', user_words, re.I))
            has_stereo = bool(re.search(r'双目|stereo', user_words, re.I))
            if camera_config is not None:
                if camera_config != 'stereo' or not has_stereo:
                    raise SchemaExtractionError('camera_config_not_grounded')
                stereo = True
            if stereo is not None and (stereo is not True or not has_stereo):
                raise SchemaExtractionError('stereo_qualifier_not_grounded')
            if camera == 'fisheye_rgb':
                if not (has_fisheye and has_rgb):
                    raise SchemaExtractionError('camera_qualifier_not_grounded')
                conditions['lens_type'] = 'fisheye'
                if field in {'resolution', 'fisheye_rgb_resolution'}:
                    field = 'rgb_resolution'
            elif camera == 'rgb':
                if not has_rgb:
                    raise SchemaExtractionError('camera_qualifier_not_grounded')
                if field == 'resolution':
                    field = 'rgb_resolution'
                elif field == 'frame_rate':
                    field = 'rgb_frame_rate'
            elif camera in {'mono', 'stereo_mono'}:
                if not has_mono or (camera == 'stereo_mono' and not has_stereo):
                    raise SchemaExtractionError('camera_qualifier_not_grounded')
                if has_stereo and field in {
                        'baseline', 'mono_baseline', 'stereo_baseline',
                        'stereo_mono_baseline'}:
                    field = 'stereo_mono_baseline'
                elif has_stereo and field in {'resolution', 'mono_resolution', 'stereo_mono_resolution'}:
                    field = 'stereo_mono_resolution'
                else:
                    conditions['camera_type'] = camera
            elif camera is not None:
                raise SchemaExtractionError('camera_qualifier_invalid')
            port = conditions.get('port')
            if port == 'downstream' and field and field.startswith('downstream_') \
                    and re.search(r'下游|downstream', user_words, re.I):
                conditions.pop('port')
            if conditions.get('lens_type') == 'fisheye' and not has_fisheye:
                raise SchemaExtractionError('lens_qualifier_not_grounded')
            if 'scope' in conditions:
                if not isinstance(conditions['scope'], str):
                    raise SchemaExtractionError('condition_scope_invalid')
                explicit_scopes = {
                    name for name, pattern in (
                        ('whole_system', r'整机|整套系统|whole.system|system.wide'),
                        ('module', r'模块|\bmodule\b'),
                        ('component', r'部件|组件|\bcomponent\b'),
                        ('device', r'设备|\bdevice\b'),
                    ) if re.search(pattern, user_words, re.I)
                }
                if not explicit_scopes:
                    conditions.pop('scope')
                elif len(explicit_scopes) == 1:
                    conditions['scope'] = next(iter(explicit_scopes))
                elif conditions['scope'] not in explicit_scopes:
                    raise SchemaExtractionError('condition_scope_ambiguous')
            for key, value in conditions.items():
                if key == 'scope':
                    if not isinstance(value, str) or value not in {
                            'whole_system', 'module', 'component', 'device'}:
                        raise SchemaExtractionError('condition_scope_invalid')
                    continue
                if key == 'lens_type' and value == 'fisheye' and has_fisheye:
                    continue
                if key == 'per_port' or isinstance(value, bool):
                    continue
                values = value if isinstance(value, list) else [value]
                if any(not isinstance(item, (str, int)) or
                       _normalized(str(item)) not in accepted_scope for item in values):
                    unmapped.add(key)
            payload = [kind, capability, sorted(set(entities)), field, conditions]
            digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
            goal_id = 'goal_' + digest
            if goal_id in ids:
                raise SchemaExtractionError('schema_goal_duplicate')
            ids.add(goal_id)
            goals.append(DaqGoal(goal_id, kind, capability, tuple(dict.fromkeys(entities)),
                                 field, tuple(sorted(conditions.items())),
                                 tuple(sorted(unmapped & set(conditions)))))
        kinds = {goal.kind for goal in goals if goal.kind != 'unknown'}
        request_kind = 'mixed' if len(kinds) > 1 else next(iter(kinds), 'unknown')
        topic_switch = candidate.get('topic_switch', False)
        if not isinstance(topic_switch, bool):
            raise SchemaExtractionError('topic_switch_invalid')
        effective = candidate.get('effective_question', question)
        if not isinstance(effective, str) or not effective.strip() or len(effective) > 2000:
            raise SchemaExtractionError('effective_question_invalid')
        # The model rewrite is only a dependency signal. Compose the effective
        # question from actual user words so it cannot add product assertions.
        effective = (question + '\n此前用户原话（仅作上下文线索）：' + prior_user_context[-500:]
                     if prior_user_context and effective != question and not topic_switch
                     else question)
        dependency = candidate.get('attachment_dependency', 'unknown')
        if dependency not in _DEPENDENCY:
            raise SchemaExtractionError('attachment_dependency_invalid')
        if not attachment_present:
            dependency = 'unknown'
        elif image_present and any(goal.capability == 'analyze_image' for goal in goals):
            dependency = 'required_for_answer'
        language = selected_answer_language or candidate.get('answer_language')
        if language is None:
            language = ('en' if re.search(r'[A-Za-z]{2,}', question)
                        and not re.search(r'[\u3400-\u9fff]', question) else 'zh')
        if language not in {'zh', 'en'}:
            raise SchemaExtractionError('answer_language_invalid')
        return cls(question, effective, request_kind, tuple(goals), topic_switch,
                   dependency, confidence, language)


_SCHEMA_TOOL = {
    'type': 'function',
    'function': {
        'name': 'submit_daq_request_schema',
        'description': 'Submit composable DAQ request goals. Do not answer the question.',
        'parameters': {
            'type': 'object',
            'properties': {
                'confidence': {'type': 'string', 'enum': sorted(_CONFIDENCE)},
                'topic_switch': {'type': 'boolean'},
                'effective_question': {'type': 'string'},
                'attachment_dependency': {'type': 'string', 'enum': sorted(_DEPENDENCY)},
                'answer_language': {'type': 'string', 'enum': ['zh', 'en']},
                'goals': {'type': 'array', 'items': {
                    'type': 'object',
                    'properties': {
                        'kind': {'type': 'string', 'enum': sorted(_KINDS)},
                        'capability': {'type': 'string', 'enum': sorted(
                            _SERVICE | _PRODUCT | _ATTACHMENT | {'clarify'})},
                        'entities': {'type': 'array', 'items': {'type': 'string'}},
                        'field': {'type': 'string'},
                        'conditions': {'type': 'object'},
                    },
                    'required': ['kind', 'capability'],
                }},
            },
            'required': ['confidence', 'goals'],
        },
    },
}


def extract_request_schema(question: str, *, adapter, prior_user_context: str = '',
                           attachment_present: bool = False,
                           image_present: bool = False,
                           selected_answer_language: str | None = None) -> DaqRequestSchema:
    prompt = (_ROOT / 'prompts/daq_schema_extraction.md').read_text(encoding='utf-8')
    messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps({
        'question': question, 'prior_user_context': prior_user_context,
        'attachment_present': attachment_present, 'image_present': image_present,
        'selected_answer_language': selected_answer_language,
    }, ensure_ascii=False)}]
    calls = [event for event in adapter.chat(
        messages, tools=[_SCHEMA_TOOL], required_tool='submit_daq_request_schema')
        if event.get('type') == 'tool_call']
    if not calls:
        # The Dev gateway uses auto tool choice with adaptive thinking. A model
        # can end its turn without the required tool despite the prompt. Retry
        # once with an explicit correction, then expose a schema error if it
        # still omits the call. Never turn a provider error into a content gap.
        retry = [{**messages[0], 'content': messages[0]['content'] +
                  '\n上一次回复没有调用要求的工具。现在必须只调用 '
                  '`submit_daq_request_schema` 一次；不要输出正文。'},
                 messages[1]]
        calls = [event for event in adapter.chat(
            retry, tools=[_SCHEMA_TOOL], required_tool='submit_daq_request_schema')
            if event.get('type') == 'tool_call']
    candidates = [event.get('arguments') for event in calls
                  if event.get('name') == 'submit_daq_request_schema']
    if len(calls) != 1 or len(candidates) != 1:
        raise SchemaExtractionError('model_schema_missing_or_ambiguous')
    raw = candidates[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SchemaExtractionError('model_schema_json_invalid') from exc
    return DaqRequestSchema.from_candidate(
        question, raw, prior_user_context=prior_user_context,
        attachment_present=attachment_present, image_present=image_present,
        selected_answer_language=selected_answer_language,
    )
