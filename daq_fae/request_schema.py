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

from daq_fae.knowledge.reviewed_view import reviewed_name_in_query


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
    'recording_format', 'storage', 'simultaneous_port_count',
    'scope', 'sensor', 'camera_type', 'camera_config', 'stereo_pair',
    'lens_type', 'port',
}
_IDENTITY_SELECTOR_CONDITIONS = frozenset({
    'variant', 'resolution_variant', 'hardware_revision',
})
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


_RESOLUTION_PAIR = re.compile(r'(?<!\d)(\d{3,4}x\d{3,4})(?!\d)')
_RESOLUTION_VERSION = re.compile(r'(?<!\d)(\d{3,4}x\d{3,4})(?:版本|变体|版)')


def _explicit_resolution_versions(text: str) -> set[str]:
    return {match.group(1) for match in _RESOLUTION_VERSION.finditer(_normalized(text))}


_PORT_COUNT_WORDS = {
    '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
}


def _port_counts(text: str) -> set[int]:
    counts = set()
    for word in re.findall(r'([一二两三四五六七八九十\d]+)\s*(?:个)?\s*(?:下游)?\s*口', text):
        count = int(word) if word.isdigit() else _PORT_COUNT_WORDS.get(word)
        if count is None:
            raise SchemaExtractionError('schema_aggregate_power_count_ambiguous')
        counts.add(count)
    for word in re.findall(r'\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*'
                           r'(?:downstream\s+)?ports?\b', text, re.I):
        counts.add(int(word) if word.isdigit() else _PORT_COUNT_WORDS[word.casefold()])
    return counts


def _simultaneous_port_counts(text: str) -> set[int]:
    """Separate a device's port capacity from ports used simultaneously."""
    if '中' in text:
        after_capacity = _port_counts(text.rsplit('中', 1)[-1])
        if after_capacity:
            return after_capacity
    return _port_counts(text)


def _other_product_nearest(clause: str, marker_at: int,
                           other_entities: tuple[str, ...]) -> bool:
    hub_end = max((match.end() for match in re.finditer(
        r'\bHUB\b|集线', clause[:marker_at], re.I)), default=-1)
    other_end = max((match.end() for entity in other_entities
                     for match in re.finditer(re.escape(entity),
                                              clause[:marker_at], re.I)), default=-1)
    return other_end > hub_end


def _explicit_aggregate_port_power(
        question: str, prior_user_context: str = '',
        other_entities: tuple[str, ...] = ()) -> tuple[bool, tuple[int, ...]]:
    """Recognize an explicit multi-port total, only as a schema obligation.

    This extracts a user's condition; it never computes an electrical result.
    Multiple explicit counts each require their own evidence goal.
    """
    total_pattern = (r'总供电|总输出|总功率|合计(?:供电|输出|功率)|'
                     r'total.{0,18}(?:power|current|output)')
    hub_pattern = r'\bHUB\b|集线'
    prior_hub = bool(re.search(hub_pattern, prior_user_context, re.I))
    prior_counts = _port_counts(prior_user_context) if prior_hub else set()
    prior_unique_count = next(iter(prior_counts)) if len(prior_counts) == 1 else None
    active_hub = False
    current_counts: set[int] = set()
    counts = set()
    asked = False
    for clause in re.split(r'[?？。!！；;，,\n]+', question):
        has_hub = bool(re.search(hub_pattern, clause, re.I))
        clause_counts = _simultaneous_port_counts(clause)
        has_downstream = bool(re.search(r'下游(?:端)?口|downstream\s+ports?', clause, re.I))
        total_match = re.search(total_pattern, clause, re.I)
        other_product = bool(total_match and _other_product_nearest(
            clause, total_match.start(), other_entities))
        if total_match and not other_product:
            if has_hub or has_downstream or (clause_counts and (active_hub or prior_hub)):
                asked = True
                counts.update(clause_counts)
                if not clause_counts and active_hub:
                    counts.update(current_counts)
            elif (active_hub and current_counts and
                  re.search(r'总|total', clause, re.I)):
                asked = True
                counts.update(current_counts)
            elif (prior_hub and prior_unique_count is not None and
                  re.search(r'那|那么|它|这些|这|then|those|what about', clause, re.I)):
                asked = True
                counts.add(prior_unique_count)
        if has_hub:
            active_hub = True
        if clause_counts and (active_hub or prior_hub):
            current_counts = clause_counts
        if other_product:
            active_hub = False
            current_counts = set()
    return asked, tuple(sorted(counts))


def _explicit_per_port_power(question: str, prior_user_context: str,
                             other_entities: tuple[str, ...]) -> bool:
    active_hub = False
    prior_hub = bool(re.search(r'\bHUB\b|集线', prior_user_context, re.I))
    for clause in re.split(r'[?？。!！；;，,\n]+', question):
        has_hub = bool(re.search(r'\bHUB\b|集线', clause, re.I))
        per_port_match = re.search(r'每口|每个(?:下游)?口|单口|'
                                   r'\bper[- ]?port\b|\beach\s+port\b',
                                   clause, re.I)
        other_product = bool(per_port_match and _other_product_nearest(
            clause, per_port_match.start(), other_entities))
        if (not other_product and (has_hub or active_hub or prior_hub)
                and per_port_match
                and re.search(r'供电|电源|功率|\bpower\b', clause, re.I)):
            return True
        if has_hub:
            active_hub = True
        elif other_product:
            active_hub = False
            prior_hub = False
    return False


def _reviewed_power_targets(question: str, knowledge, role: str) -> set[str]:
    """Bind each power ask to named reviewed subjects, including conjunctions."""
    rows = [row for row in knowledge.records_for(role) if row['kind'] == 'entity']
    targets = set()
    for clause in re.split(r'[?？。!！；;，,\n]+', question):
        asks = list(re.finditer(r'每口|单口|总供电|总功率|供电(?:输出)?|电源输出|'
                               r'\b(?:total\s+)?power(?:\s+output)?\b',
                               clause, re.I))
        if not asks:
            continue
        mentions: set[tuple[int, int, str]] = set()
        for row in rows:
            for label in (row['data']['name'], *row['data'].get('aliases', [])):
                if not reviewed_name_in_query(label, clause):
                    continue
                pattern = re.compile(r'(?<![A-Za-z0-9])' +
                                     re.escape(label).replace(r'\ ', r'\s*') +
                                     r'(?![A-Za-z0-9])', re.I)
                for match in pattern.finditer(clause):
                    mentions.add((match.start(), match.end(),
                                  str(row['scope'].get('product', ''))))
        for ask in asks:
            prior = sorted((item for item in mentions if item[1] <= ask.start()),
                           key=lambda item: (-item[1], item[0]))
            if not prior:
                continue
            nearest = prior[0]
            targets.add(nearest[2])
            start = nearest[0]
            for mention in prior[1:]:
                if mention[1] > start:
                    continue
                between = clause[mention[1]:start]
                if not re.fullmatch(r'\s*(?:和|与|及|、|and|&)\s*',
                                    between, re.I):
                    break
                targets.add(mention[2])
                start = mention[0]
    return targets


def _requested_frame_rate_modes(question: str, versions: set[str]) -> tuple[tuple[str, str | None, str | None], ...]:
    """Find explicit mode-conditioned fps questions, without asserting support.

    A mode belongs to a question clause, not merely to the same user turn:
    a background native fps value followed by a software question must not
    create a new fps obligation.
    """
    normalized = _normalized(question)
    modes = set(_RESOLUTION_PAIR.findall(normalized)) - versions
    if not modes:
        return ()
    ordered_versions = list(dict.fromkeys(match.group(1) for match in
                           _RESOLUTION_VERSION.finditer(normalized)))
    if not ordered_versions and len(versions) == 1:
        ordered_versions = list(versions)
    only_version = ordered_versions[0] if len(set(ordered_versions)) == 1 else None
    global_channels = {name for name, pattern in (
        ('rgb', r'rgb|彩色'), ('mono', r'mono|单色|黑白'), ('depth', r'depth|深度'))
        if re.search(pattern, normalized, re.I)}
    only_channel = next(iter(global_channels)) if len(global_channels) == 1 else None
    requirements: list[tuple[str, str | None, str | None]] = []
    previous_fps_question = False
    for sentence in re.split(r'[?？。!！；;\n]+', normalized):
        if not sentence:
            continue
        sentence_versions = _explicit_resolution_versions(sentence)
        sentence_version = (next(iter(sentence_versions)) if len(sentence_versions) == 1
                            else only_version)
        pending_modes: set[str] = set()
        had_mode_fps = False
        sentence_asked_fps = False
        for clause in re.split(r'[，,]+', sentence):
            local_modes = set()
            for mode in modes:
                mode_context = (r'(?:在|以|切到|切换到|切换为|改用|改为|改成).{0,12}'
                                + re.escape(mode))
                if (re.search(mode_context, clause) or
                        re.search(re.escape(mode) + r'(?:下|时|模式)', clause) or
                        ('输出模式' in clause and mode in clause)):
                    local_modes.add(mode)
                height = mode.split('x')[1]
                if re.search(r'(?:切到|切换到|切换为|改用|改为|改成).{0,12}'
                             + re.escape(height) + r'(?:后|下|时)', clause):
                    local_modes.add(mode)
            pending_modes.update(local_modes)
            asks_fps = bool(re.search(r'(?:fps|帧率|帧)', clause, re.I)
                            and re.search(r'多少|几|最高|能到|达到|可达|能否|能跑|吗|上限', clause))
            asks_fps = asks_fps or bool(previous_fps_question and
                                         re.search(r'又(?:是)?多少|那(?:又)?是多少', clause))
            if not asks_fps:
                continue
            sentence_asked_fps = True
            clause_versions = _explicit_resolution_versions(clause)
            target_version = (next(iter(clause_versions)) if len(clause_versions) == 1
                              else sentence_version)
            if '前者' in clause and ordered_versions:
                target_version = ordered_versions[0]
            elif '后者' in clause and len(ordered_versions) >= 2:
                target_version = ordered_versions[1]
            channels = {name for name, pattern in (
                ('rgb', r'rgb|彩色'), ('mono', r'mono|单色|黑白'),
                ('depth', r'depth|深度')) if re.search(pattern, clause, re.I)}
            if not channels:
                channels = {only_channel}
            modes_for_question = set(pending_modes)
            if (had_mode_fps and target_version and
                    re.search(r'(?:回到|切回|返回).{0,8}原生分辨率', clause)):
                modes_for_question.add(target_version)
            for mode in modes_for_question:
                for channel in channels:
                    requirements.append((mode, target_version, channel))
            had_mode_fps = had_mode_fps or bool(modes_for_question)
            pending_modes.clear()
        previous_fps_question = sentence_asked_fps
    return tuple(dict.fromkeys(requirements))


def _is_frame_rate_field(field: str | None) -> bool:
    return isinstance(field, str) and ('frame_rate' in field or field.endswith('fps'))


def _frame_rate_channel_matches(field: str | None, channel: str | None) -> bool:
    return _is_frame_rate_field(field) and (channel is None or field.startswith(channel + '_'))


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
        proposed_hub_goal = any(
            isinstance(item, dict) and item.get('kind') == 'product' and
            any(isinstance(entity, str) and
                re.search(r'\bHUB\b|集线', entity, re.I)
                for entity in (item.get('entities') if isinstance(
                    item.get('entities'), list) else []))
            for item in raw_goals)
        prior_power_context = (prior_user_context
                               if proposed_hub_goal and
                               candidate.get('topic_switch') is not True else '')
        other_power_entities = tuple(
            entity for item in raw_goals if isinstance(item, dict)
            for entity in (item.get('entities') if isinstance(
                item.get('entities'), list) else [])
            if isinstance(entity, str) and
            not re.search(r'\bHUB\b|集线', entity, re.I))
        aggregate_power_asked, aggregate_port_counts = _explicit_aggregate_port_power(
            question, prior_power_context, other_power_entities)
        per_port_power_asked = _explicit_per_port_power(
            question, prior_power_context, other_power_entities)
        power_goal_count = sum(isinstance(item, dict) and
                               item.get('kind') == 'product' and
                               item.get('capability') == 'lookup_spec' and
                               item.get('field') in {
                                   'downstream_port_power_output',
                                   'total_downstream_port_power_output'}
                               for item in raw_goals)
        accepted_scope = _normalized(question + ' ' + prior_user_context)
        explicit_versions = (_explicit_resolution_versions(question)
                             or _explicit_resolution_versions(prior_user_context))
        requested_modes = _requested_frame_rate_modes(question, explicit_versions)
        goals: list[DaqGoal] = []
        ids: set[str] = set()
        raw_by_id: dict[str, str] = {}
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
            try:
                raw_signature = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError) as exc:
                raise SchemaExtractionError('schema_goal_invalid') from exc
            conditions = dict(conditions)
            if capability == 'lookup_spec' and field in {
                    'downstream_port_power_output',
                    'total_downstream_port_power_output'}:
                count_keys = {'simultaneous_port_count', 'port_count'} & set(conditions)
                if len(count_keys) > 1 and conditions['simultaneous_port_count'] != \
                        conditions['port_count']:
                    raise SchemaExtractionError('schema_aggregate_power_count_conflict')
                model_count = conditions[next(iter(count_keys))] if count_keys else None
                if model_count is not None and (type(model_count) is not int or
                                                model_count < 1 or model_count > 100):
                    raise SchemaExtractionError('schema_aggregate_power_count_invalid')
                if conditions.get('per_port') is True and (
                        model_count is not None or
                        field == 'total_downstream_port_power_output'):
                    raise SchemaExtractionError('schema_aggregate_power_single_port_conflict')
                if model_count is not None and not aggregate_power_asked:
                    raise SchemaExtractionError('schema_aggregate_power_unasked')
                if aggregate_power_asked and model_count is not None and \
                        model_count not in aggregate_port_counts:
                    raise SchemaExtractionError('schema_aggregate_power_count_mismatch')
                if aggregate_power_asked and conditions.get('per_port') is not True and (
                        field == 'total_downstream_port_power_output' or model_count is not None
                        or power_goal_count == 1):
                    field = 'total_downstream_port_power_output'
                    conditions.pop('port_count', None)
                    if conditions.get('per_port') is False:
                        conditions.pop('per_port')
                    if model_count is not None:
                        conditions['simultaneous_port_count'] = model_count
                    elif len(aggregate_port_counts) == 1:
                        conditions['simultaneous_port_count'] = aggregate_port_counts[0]
                elif field == 'total_downstream_port_power_output' and not aggregate_power_asked:
                    raise SchemaExtractionError('schema_aggregate_power_unasked')
                if field == 'downstream_port_power_output' and per_port_power_asked:
                    conditions['per_port'] = True
            if capability == 'resolve_entity' and \
                    set(conditions) - _IDENTITY_SELECTOR_CONDITIONS:
                # Identity only proves which reviewed product/variant this is.
                # Task, software, connection and other context need their own
                # goal; keeping them here creates an impossible evidence debt.
                raise SchemaExtractionError('schema_identity_context_condition')
            variant = conditions.get('resolution_variant')
            variant_values = ([variant] if isinstance(variant, str) else
                              variant if isinstance(variant, list) else [])
            current_resolutions = set(_RESOLUTION_PAIR.findall(_normalized(question)))
            if (kind == 'product' and explicit_versions and
                    any(isinstance(value, str) and _normalized(value) in current_resolutions
                        and _normalized(value) not in explicit_versions
                        for value in variant_values)):
                raise SchemaExtractionError('resolution_variant_not_user_variant')
            selector_phrases = [
                re.compile(re.escape(_normalized(value)) + r'(?:版|版本|变体)?')
                for value in variant_values if isinstance(value, str)
                and re.fullmatch(r'\d{3,4}x\d{3,4}', _normalized(value))
            ]
            if kind == 'product' and selector_phrases:
                # The extractor can repeat "1920x1200 版本" as a second
                # entity even while correctly recording it as a selector.
                # It is a scope phrase, not another product in a topology.
                product_entities = [entity for entity in entities
                                    if not any(pattern.fullmatch(_normalized(entity))
                                               for pattern in selector_phrases)]
                if product_entities:
                    entities = product_entities
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
            # A product named "双目" is not evidence that a question about its
            # RGB output asks for the Mono/stereo output field. Keep modality
            # anchored to the user's explicit words even if the model picked
            # a plausible but wrong field name.
            if kind == 'product' and capability == 'lookup_spec' and has_rgb and not has_mono:
                if field in {'mono_resolution', 'stereo_mono_resolution',
                             'stereo_resolution', 'stereo_resolution_options',
                             'resolution_options'}:
                    field = 'rgb_resolution'
                elif field in {'mono_frame_rate', 'stereo_mono_frame_rate',
                               'stereo_mono_max_frame_rate'}:
                    field = 'rgb_max_frame_rate'
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
                if (key == 'simultaneous_port_count' and
                        value in aggregate_port_counts):
                    continue
                if key == 'per_port' or isinstance(value, bool):
                    continue
                values = value if isinstance(value, list) else [value]
                if any(not isinstance(item, (str, int)) or
                       _normalized(str(item)) not in accepted_scope for item in values):
                    unmapped.add(key)
            variants = conditions.get('resolution_variant')
            if kind == 'product' and capability == 'lookup_spec' and isinstance(variants, list):
                if not variants or any(not isinstance(value, str) or not value for value in variants):
                    raise SchemaExtractionError('variant_selector_invalid')
                scoped_conditions = [
                    {**conditions, 'resolution_variant': value}
                    for value in dict.fromkeys(variants)
                ]
            else:
                scoped_conditions = [conditions]
            unique_entities = tuple(dict.fromkeys(entities))
            if kind == 'product' and capability in {'lookup_spec', 'resolve_entity'} and \
                    len(unique_entities) > 1 and conditions:
                # Even scope or measurement conditions may apply to only one
                # member. Split automatically only when no condition can be
                # copied across products.
                reason = ('schema_multi_entity_identity_conditions_ambiguous'
                          if capability == 'resolve_entity' else
                          'schema_multi_entity_selector_ambiguous' if any(
                    key in conditions for key in
                    ('variant', 'resolution_variant', 'hardware_revision'))
                    else 'schema_multi_entity_conditions_ambiguous')
                raise SchemaExtractionError(reason)
            for scoped in scoped_conditions:
                entity_scopes = ([(entity,) for entity in unique_entities]
                                 if kind == 'product' and capability in {
                                     'lookup_spec', 'resolve_entity'}
                                 and len(unique_entities) > 1 else [unique_entities])
                for entity_scope in entity_scopes:
                    payload = [kind, capability, sorted(entity_scope), field, scoped]
                    digest = hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                    goal_id = 'goal_' + digest
                    if goal_id in ids:
                        if raw_by_id.get(goal_id) == raw_signature:
                            # An exact repetition is one evidence obligation.
                            continue
                        if (aggregate_power_asked and
                                field == 'downstream_port_power_output'):
                            raise SchemaExtractionError('schema_aggregate_power_goal_missing')
                        # Normalization can erase a distinct raw field or an
                        # unmapped condition. Do not silently lose that goal.
                        raise SchemaExtractionError('schema_goal_normalization_collision')
                    ids.add(goal_id)
                    raw_by_id[goal_id] = raw_signature
                    goals.append(DaqGoal(goal_id, kind, capability, entity_scope,
                                         field, tuple(sorted(scoped.items())),
                                         tuple(sorted(unmapped & set(scoped)))))
                    if len(goals) > 16:
                        raise SchemaExtractionError('schema_goals_expansion_limit')
        # A short follow-up can name one version in the user's context while
        # the model scopes only some of several requested fields to it. A
        # single grounded version shared by peer goals applies to the same
        # entity's remaining fields; it is a product selector, never a claim
        # measurement condition. Keep comparisons and other entities separate.
        current_versions = _explicit_resolution_versions(question)
        prior_versions = _explicit_resolution_versions(prior_user_context)
        if not current_versions and len(prior_versions) == 1:
            explicit_variant = next(iter(prior_versions))
            peer_variants: dict[tuple[str, ...], set[str]] = {}
            for goal in goals:
                if goal.kind == 'product' and goal.capability == 'lookup_spec' \
                        and 'resolution_variant' not in goal.unmapped_condition_keys:
                    variant = goal.condition_map().get('resolution_variant')
                    if isinstance(variant, str):
                        peer_variants.setdefault(goal.entities, set()).add(variant)
            scoped_by_peer = []
            for goal in goals:
                if goal.kind == 'product' and goal.capability == 'lookup_spec' \
                        and 'resolution_variant' not in goal.condition_map() \
                        and peer_variants.get(goal.entities) == {explicit_variant}:
                    scoped = {**goal.condition_map(), 'resolution_variant': explicit_variant}
                    payload = [goal.kind, goal.capability, sorted(goal.entities),
                               goal.field, scoped]
                    digest = hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                    goal = DaqGoal('goal_' + digest, goal.kind, goal.capability,
                                   goal.entities, goal.field,
                                   tuple(sorted(scoped.items())),
                                   goal.unmapped_condition_keys, goal.critical)
                scoped_by_peer.append(goal)
            if len({goal.id for goal in scoped_by_peer}) != len(scoped_by_peer):
                raise SchemaExtractionError('schema_goal_normalization_collision')
            goals = scoped_by_peer
            ids = {goal.id for goal in goals}
        for goal in goals:
            if goal.kind == 'product' and goal.capability == 'lookup_spec' \
                    and 'resolution_variant' not in goal.condition_map() \
                    and any(peer.kind == 'product' and peer.capability == 'lookup_spec'
                            and peer.entities == goal.entities
                            and isinstance(peer.condition_map().get('resolution_variant'), str)
                            for peer in goals):
                # Multiple product-version clauses can refer to different
                # versions even when the entity name is the same. Ask the
                # model to scope the remaining goal instead of guessing.
                raise SchemaExtractionError('schema_missing_variant_scope')
        if aggregate_power_asked:
            total_goals = [goal for goal in goals
                           if goal.kind == 'product' and
                           goal.capability == 'lookup_spec' and
                           goal.field == 'total_downstream_port_power_output' and
                           len(goal.entities) == 1 and
                           re.search(r'\bHUB\b|集线', goal.entities[0], re.I)]
            if not total_goals or any(not any(
                    goal.condition_map().get('simultaneous_port_count') == count
                    for goal in total_goals) for count in aggregate_port_counts):
                raise SchemaExtractionError('schema_aggregate_power_goal_missing')
        if per_port_power_asked and not any(
                goal.kind == 'product' and goal.capability == 'lookup_spec' and
                goal.field == 'downstream_port_power_output' and
                goal.condition_map().get('per_port') is True and
                len(goal.entities) == 1 and
                re.search(r'\bHUB\b|集线', goal.entities[0], re.I)
                for goal in goals):
            raise SchemaExtractionError('schema_aggregate_power_goal_missing')
        # A bare family identity cannot resolve to one entity when the user
        # compares variants. Keep identity as governed evidence, but scope it
        # to each explicitly requested variant instead of demanding one
        # unqualified family identity.
        variant_scopes: dict[tuple[str, ...], set[str]] = {}
        for goal in goals:
            if goal.kind == 'product' and goal.capability == 'lookup_spec' \
                    and 'resolution_variant' not in goal.unmapped_condition_keys:
                variant = goal.condition_map().get('resolution_variant')
                if isinstance(variant, str):
                    variant_scopes.setdefault(goal.entities, set()).add(variant)
        scoped_goals = []
        for goal in goals:
            variants = variant_scopes.get(goal.entities, set())
            if goal.kind == 'product' and goal.capability == 'resolve_entity' \
                    and not goal.conditions and len(variants) >= 2:
                for variant in sorted(variants):
                    conditions = {'resolution_variant': variant}
                    payload = [goal.kind, goal.capability, sorted(goal.entities),
                               goal.field, conditions]
                    digest = hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                    scoped_id = 'goal_' + digest
                    if scoped_id in ids:
                        continue
                    ids.add(scoped_id)
                    scoped_goals.append(DaqGoal(
                        scoped_id, goal.kind, goal.capability, goal.entities,
                        goal.field, tuple(conditions.items()),
                        goal.unmapped_condition_keys, goal.critical))
            else:
                scoped_goals.append(goal)
        if len(scoped_goals) > 16:
            raise SchemaExtractionError('schema_goals_expansion_limit')
        goals = scoped_goals
        kinds = {goal.kind for goal in goals if goal.kind != 'unknown'}
        for requested_mode, requested_variant, requested_channel in requested_modes:
            if not any(goal.capability == 'lookup_spec'
                       and _frame_rate_channel_matches(goal.field, requested_channel)
                       and goal.condition_map().get('resolution') == requested_mode
                       and (requested_variant is None or
                            goal.condition_map().get('resolution_variant') == requested_variant)
                       for goal in goals):
                raise SchemaExtractionError('schema_missing_measurement_mode')
        request_kind = 'mixed' if len(kinds) > 1 else next(iter(kinds), 'unknown')
        topic_switch = candidate.get('topic_switch', False)
        if not isinstance(topic_switch, bool):
            raise SchemaExtractionError('topic_switch_invalid')
        native_context = question if topic_switch else question + '\n' + prior_user_context
        if re.search(r'原生分辨率|native\s+(?:rgb\s+)?resolution', native_context, re.I):
            for goal in tuple(goals):
                if goal.kind != 'product' or goal.capability != 'lookup_spec' \
                        or goal.field not in {'rgb_max_frame_rate', 'rgb_max_fps'}:
                    continue
                selectors = {key: value for key, value in goal.condition_map().items()
                             if key in {'variant', 'resolution_variant',
                                        'hardware_revision', 'scope'}}
                payload = ['product', 'lookup_spec', sorted(set(goal.entities)),
                           'rgb_resolution', selectors]
                digest = hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                goal_id = 'goal_' + digest
                if goal_id not in ids:
                    ids.add(goal_id)
                    goals.append(DaqGoal(goal_id, 'product', 'lookup_spec',
                                         goal.entities, 'rgb_resolution',
                                         tuple(sorted(selectors.items()))))
                    if len(goals) > 16:
                        raise SchemaExtractionError('schema_goals_expansion_limit')
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
                           selected_answer_language: str | None = None,
                           knowledge=None, role: str = 'internal_fae') -> DaqRequestSchema:
    prompt = (_ROOT / 'prompts/daq_schema_extraction.md').read_text(encoding='utf-8')
    messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps({
        'question': question, 'prior_user_context': prior_user_context,
        'attachment_present': attachment_present, 'image_present': image_present,
        'selected_answer_language': selected_answer_language,
    }, ensure_ascii=False)}]
    calls = [event for event in adapter.chat(
        messages, tools=[_SCHEMA_TOOL], required_tool='submit_daq_request_schema')
        if event.get('type') == 'tool_call']
    def validated(candidate_calls: list[dict]) -> DaqRequestSchema:
        candidates = [event.get('arguments') for event in candidate_calls
                      if event.get('name') == 'submit_daq_request_schema']
        if len(candidate_calls) != 1 or len(candidates) != 1:
            raise SchemaExtractionError('model_schema_missing_or_ambiguous')
        raw = candidates[0]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SchemaExtractionError('model_schema_json_invalid') from exc
        schema = DaqRequestSchema.from_candidate(
            question, raw, prior_user_context=prior_user_context,
            attachment_present=attachment_present, image_present=image_present,
            selected_answer_language=selected_answer_language,
        )
        if (knowledge is not None and
                not getattr(knowledge, 'is_candidate_preview', False) and
                hasattr(knowledge, 'ground_question') and
                hasattr(knowledge, 'grounding_matches_goals') and
                not re.search(r'\bHUB\b|集线', question, re.I)):
            current_grounding = knowledge.ground_question(question, role=role)
            if (current_grounding.get('entities') and
                    any(goal.kind == 'product' and goal.field in {
                        'downstream_port_power_output',
                        'total_downstream_port_power_output'} and
                        not knowledge.grounding_matches_goals(
                            current_grounding, (goal,), role=role)
                        for goal in schema.goals)):
                # A currently named reviewed product outranks an old HUB
                # setup. Retry the schema instead of certifying the old
                # product's valid claim for a different current question.
                raise SchemaExtractionError('schema_current_entity_conflict')
        if (knowledge is not None and
                not getattr(knowledge, 'is_candidate_preview', False) and
                hasattr(knowledge, 'records_for') and
                any(goal.kind == 'product' and goal.field in {
                    'downstream_port_power_output',
                    'total_downstream_port_power_output'}
                    for goal in schema.goals)):
            targets = _reviewed_power_targets(question, knowledge, role)
            if targets and 'hub' not in targets:
                raise SchemaExtractionError('schema_current_entity_conflict')
            if targets and hasattr(knowledge, 'grounding_matches_goals'):
                power_goals = [goal for goal in schema.goals
                               if goal.kind == 'product' and
                               goal.capability == 'lookup_spec' and goal.field and
                               'power' in goal.field]
                covered = {
                    str(row['scope'].get('product', ''))
                    for row in knowledge.records_for(role) if row['kind'] == 'entity'
                    if any(knowledge.grounding_matches_goals(
                        {'entities': [row['id']]}, (goal,), role=role)
                        for goal in power_goals)
                }
                if targets - covered:
                    raise SchemaExtractionError('schema_missing_explicit_spec_goal')
        if (schema.goals and all(goal.kind == 'product' and goal.capability in {
                'resolve_entity', 'catalog'} for goal in schema.goals)
                and re.search(r'[?？]|多少|几(?:个|颗|路|帧)', question)):
            # The model can return a syntactically valid identity-only plan
            # for an explicit value question, leaving lookup_spec unavailable.
            # Restrict this check to identity-only answers to a question;
            # setup statements, service, procedure and software goals are
            # valid even when they mention a specification field as context.
            # The conservative planner only flags a missing obligation.
            from daq_fae.task_context import prepare_turn
            baseline = prepare_turn(question)
            if any(requirement.get('capability') == 'lookup_spec'
                   and requirement.get('field') for requirement in baseline.requirements):
                raise SchemaExtractionError('schema_missing_explicit_spec_goal')
        return schema

    try:
        return validated(calls)
    except SchemaExtractionError as exc:
        # Retry once for either a missing call or an invalid schema. Never
        # pass the untrusted candidate back as facts or hide provider errors.
        correction = ('上一次回复没有调用要求的工具。现在必须只调用 '
                      '`submit_daq_request_schema` 一次；不要输出正文。'
                      if not calls else
                      '上一次结构化结果漏掉明确规格目标（schema_missing_explicit_spec_goal）。'
                      '请按产品与变体逐项补充 `lookup_spec` goal，保留原有其他目标；'
                      '不要把分辨率版本当成测量条件或第二个产品。'
                      if str(exc) == 'schema_missing_explicit_spec_goal' else
                      '上一次沿用了历史中的 HUB 目标，但本轮明确提到了另一个已核实产品。'
                      '请按本轮产品重新列取证目标；若每口含义不明，应澄清，'
                      '不要用历史 HUB 的单口或总量规格回答。现在只调用结构化工具一次。'
                      if str(exc) == 'schema_current_entity_conflict' else
                      '上一次多个目标规范化后指向同一证据义务，但原始目标不同。'
                      '非规格目标不要填写 field；软件与固件兼容用 '
                      '`check_software_support`，SDK 证据用 `sdk_evidence`，'
                      '官方下载地址用 `official_links`。同一能力、实体和条件只列一次；'
                      '不同版本或平台条件须分别保留，不要省略用户询问的部分。'
                      '现在只调用结构化工具一次。'
                      if str(exc) == 'schema_goal_normalization_collision' else
                      '上一次同一产品的规格目标有的填写了产品版本、有的没有。'
                      '请逐个字段判断它指的是本轮还是历史中的哪个版本，'
                      '为每个明确版本的规格目标填写对应 `resolution_variant`；'
                      '不要把切换后的新版本复制给切换前的追问，也不要把产品版本'
                      '写成帧率的测量 `resolution`。现在只调用结构化工具一次。'
                      if str(exc) == 'schema_missing_variant_scope' else
                      '上一次把采集任务、软件、连接或其他非身份条件放进了 '
                      '`resolve_entity` 目标。产品身份目标只保留 variant、'
                      'resolution_variant 或 hardware_revision 选择器；'
                      '如果用户还询问该场景的能力、兼容或流程，请另列对应目标，'
                      '不要把背景使用场景当成已核实能力。现在只调用结构化工具一次。'
                      if str(exc) == 'schema_identity_context_condition' else
                      '上一次把单口供电与多个下游口同时使用的总供电混为一个目标，'
                      '或漏掉了用户明确询问的总量目标（' + str(exc) + '）。'
                      '若用户问 N 个口同时使用时的总供电，独立列 '
                      '`field=total_downstream_port_power_output`，'
                      '`conditions.simultaneous_port_count=N` 必须与用户原话一致；'
                      '单口 `downstream_port_power_output` 另列，不能与总量条件合并。'
                      '仅问每口供电时不要附加同时口数。现在只调用结构化工具一次。'
                      if str(exc).startswith('schema_aggregate_power') else
                      '上一次把输出分辨率模式与产品版本混淆，或漏了明确切换后的帧率测量条件（' +
                      str(exc) + '）。保留用户明确说的产品版本为 `resolution_variant`；'
                      '将切换后的取流分辨率写成帧率目标的 `resolution`。'
                      '分辨率选项目标只按原产品版本查询。现在只调用结构化工具一次。'
                      if str(exc) in {'resolution_variant_not_user_variant',
                                      'schema_missing_measurement_mode'} else
                      '上一次把多个产品放在同一规格目标并附带可能只适用于其中一项的条件（' +
                      str(exc) + '）。请按每个产品分别列 `lookup_spec` goal，'
                      '仅给确实适用的产品填写版本、范围或测量条件，不能把一个产品的条件复制给另一个。'
                      '现在只调用结构化工具一次。'
                      if str(exc) in {'schema_multi_entity_selector_ambiguous',
                                      'schema_multi_entity_conditions_ambiguous'} else
                      '上一次把多个产品放在同一个身份目标并附带条件（' + str(exc) + '）。'
                      '请按每个产品分别列 `resolve_entity` goal，只保留该产品实际适用的版本条件。'
                      '现在只调用结构化工具一次。'
                      if str(exc) == 'schema_multi_entity_identity_conditions_ambiguous' else
                      '上一次结构化结果未通过校验（' + str(exc) + '）。请仅重新调用 '
                      '`submit_daq_request_schema` 一次。产品实体只能逐字来自本轮问题或'
                      '提供的用户历史；无法接地的实体请省略。不要补产品事实。')
        retry = [{**messages[0], 'content': messages[0]['content'] + '\n' + correction},
                 messages[1]]
        retry_calls = [event for event in adapter.chat(
            retry, tools=[_SCHEMA_TOOL], required_tool='submit_daq_request_schema')
            if event.get('type') == 'tool_call']
        return validated(retry_calls)
