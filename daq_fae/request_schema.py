"""Validated, model-proposed goals for one DAQ turn.

The model proposes semantics. This module validates scope and derives stable IDs;
it never promotes a model-proposed product entity into published evidence.
"""
from __future__ import annotations

import hashlib
from itertools import product
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daq_fae.knowledge.reviewed_view import reviewed_name_in_query
from daq_fae.knowledge.sdk_catalog import (
    SdkCatalog, calibration_export_known_scope,
    calibration_export_row_matches_question,
    audio_playback_subjects, device_sdk_scope_matches_question, sdk_cpu_architectures,
    sdk_usage_descriptor_matches,
)


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
    'viewer_release_date',
    'sdk', 'sdk_name', 'sdk_version', 'sdk_release_date', 'language', 'language_binding', 'api', 'feature', 'export_files', 'source_files', 'firmware_version', 'task', 'per_port', 'power',
    'recording_format', 'storage', 'simultaneous_port_count',
    'simultaneous_camera_count',
    'scope', 'sensor', 'camera_type', 'camera_config', 'stereo_pair',
    'lens_type', 'port', 'host_connection', 'standalone',
    'topology_aspects', 'recording_mode', 'indicators', 'steps',
}
_IDENTITY_SELECTOR_CONDITIONS = frozenset({
    'variant', 'resolution_variant', 'hardware_revision',
})
_SENSITIVE_CONDITION_KEY = re.compile(r'(?:secret|token|password|api_key|credential)', re.I)


def refers_to_prior_attachment(question: str) -> bool:
    """Recognize an explicit reference to evidence uploaded earlier in this session."""
    return bool(isinstance(question, str) and re.search(
        r'(?:刚才|前面|之前|上一轮|上次).{0,12}'
        r'(?:日志|附件|文件|图片|照片)|'
        r'(?:日志|附件|文件|图片|照片).{0,12}'
        r'(?:刚才|前面|之前|上一轮|上次)', question))


def stated_current_firmware_version(question: str) -> str | None:
    """Only an owned, directly stated version enters device setup memory."""
    match = re.search(
        r'(?:我(?:的|手上|当前|正在用|使用)|当前(?:设备|使用)|现用)'
        r'[^。？！?；;\n]{0,18}(?:固件|firmware)(?:版本)?'
        r'\s*(?:是|为|[:：=]|is)?\s*v?(\d+(?:\.\d+){1,3})',
        question, re.I)
    if match:
        return match.group(1)
    for pattern in (
        r'\b(?:my|our)\b[^.!?;\n]{0,48}\bfirmware(?:\s+version)?\s*'
        r'(?:is|runs?|=|:|on)\s*v?(\d+(?:\.\d+){1,3})',
        r'\b(?:i\s+am|we\s+are)\s+(?:currently\s+)?using\b'
        r'[^.!?;\n]{0,28}\bfirmware\s*v?(\d+(?:\.\d+){1,3})',
        r'\b(?:my|our)\b[^.!?;\n]{0,48}\b(?:runs?|is\s+on)\s+'
        r'firmware\s*v?(\d+(?:\.\d+){1,3})',
        r'\b(?:my|our)\b[^.!?;\n]{0,48}\b(?:is|are)\s+running\s+'
        r'firmware\s*v?(\d+(?:\.\d+){1,3})',
        r"\b(?:i['’]m|we['’]re|i\s+am|we\s+are)\s+"
        r'(?:currently\s+)?using\s+firmware\s*v?(\d+(?:\.\d+){1,3})',
        r'\b(?:i|we)\s+run\s+firmware\s*v?(\d+(?:\.\d+){1,3})',
    ):
        match = re.search(pattern, question, re.I)
        if match:
            return match.group(1)
    return None


def firmware_versions_compared_without_current(question: str) -> bool:
    """A version-history comparison must not become the user's installed FW."""
    version = r'v?\d+(?:\.\d+){1,3}'
    compared = re.search(
        r'(?:固件|firmware)[^。？！?；;\n]{0,28}?'
        + version + r'\s*(?:和|与|及|、|或|/|vs\.?|and|or)\s*'
        + version, question, re.I)
    if not compared:
        return False
    return stated_current_firmware_version(question) is None
_KINDS = {'service', 'product', 'attachment', 'unknown'}
_CONFIDENCE = {'high', 'medium', 'low'}
_DEPENDENCY = {'unknown', 'relevant', 'required_for_answer', 'not_relevant'}
_SAFE_CODE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
_PROCEDURE_STEP_CODES = (
    'insert_card_power_off', 'power_on', 'start_recording', 'stop_recording',
)


def _normalize_operating_conditions(capability: str,
                                    conditions: dict[str, Any],
                                    question: str = '') -> dict[str, Any]:
    """Map model synonyms to one inspectable condition without losing scope."""
    normalized = dict(conditions)
    # Tool arguments can serialize a boolean as text. Canonicalize the one
    # governed positive flag before the user-grounding check below; unknown
    # values remain evidence debt rather than becoming truthy by accident.
    standalone = normalized.get('standalone')
    if isinstance(standalone, str) and standalone.strip().casefold() == 'true':
        normalized['standalone'] = True
    aliases = {}
    if capability == 'inspect_topology':
        if normalized.get('usage_context') == 'standalone_no_host':
            normalized.pop('usage_context')
            aliases['standalone'] = True
            aliases['host_connection'] = 'none'
        if normalized.get('host') == 'none':
            aliases['host_connection'] = normalized.pop('host')
        if normalized.get('connection_mode') == 'standalone':
            normalized.pop('connection_mode')
            aliases['standalone'] = True
        aspects = normalized.get('aspects')
        aspect_names = {'power_supply': 'power', 'connectivity': 'connection',
                        'storage': 'storage'}
        if isinstance(aspects, list) and aspects and all(
                value in aspect_names for value in aspects):
            normalized.pop('aspects')
            aliases['topology_aspects'] = [aspect_names[value] for value in aspects]
    for key, value in aliases.items():
        if key in normalized and normalized[key] != value:
            raise SchemaExtractionError('schema_operating_alias_conflict')
        normalized[key] = value
    if capability == 'inspect_topology':
        requested = [key for key in ('power', 'connection', 'storage')
                     if normalized.get(key) is True]
        if requested:
            for key in requested:
                normalized.pop(key)
            existing = normalized.get('topology_aspects', [])
            if not isinstance(existing, list):
                raise SchemaExtractionError('schema_operating_alias_conflict')
            normalized['topology_aspects'] = list(dict.fromkeys(existing + requested))
    if capability == 'lookup_procedure' and isinstance(normalized.get('steps'), list):
        normalized['steps'] = [
            'insert_card_power_off' if value == 'power_off_insert_card' else value
            for value in normalized['steps']]
    if capability == 'lookup_procedure' and isinstance(normalized.get('indicators'), list):
        indicators = list(dict.fromkeys(
            'led_state' if value in {'led', 'led_status'} else value
            for value in normalized['indicators']))
        # Buttons are actions in a procedure, not observable indicators.  A
        # model may put "button" beside LED while also extracting the exact
        # action span.  Keep the action requirement on reviewed step evidence;
        # without that span, leave the unknown indicator in the evidence debt.
        steps = normalized.get('steps')
        if ('button' in indicators and isinstance(steps, list) and steps
                and all(step in _PROCEDURE_STEP_CODES for step in steps)
                and re.search(r'按键|按钮|电源键|\bbutton\b', question, re.I)):
            indicators = [item for item in indicators if item != 'button']
        explicit_led_colors = [name for name, pattern in (
            ('red_led', r'红灯|红色\s*(?:LED|指示灯)|red\s*(?:LED|lamp)'),
            ('green_led', r'绿灯|绿色\s*(?:LED|指示灯)|green\s*(?:LED|lamp)'),
            ('blue_led', r'蓝灯|蓝色\s*(?:LED|指示灯)|blue\s*(?:LED|lamp)'),
            ('yellow_led', r'黄灯|黄色\s*(?:LED|指示灯)|yellow\s*(?:LED|lamp)'),
        ) if re.search(pattern, question, re.I)]
        if 'led_state' in indicators and explicit_led_colors:
            indicators = [item for item in indicators if item != 'led_state']
            indicators.extend(explicit_led_colors)
        normalized['indicators'] = list(dict.fromkeys(indicators))
    if (capability == 'lookup_procedure'
            and normalized.get('task') == 'recording_completion_checkpoints'
            and normalized.get('standalone') is True
            and re.search(r'单机流程|单机录制流程|standalone.*procedure',
                          question, re.I)
            and re.search(r'完成检查点|保存完成.*检查点|completion.*checkpoint',
                          question, re.I)):
        # A completion-checkpoint request is a lookup of the reviewed stop
        # step. Do not absorb a separately observed red LED into that step.
        normalized.pop('task')
        normalized['steps'] = ['stop_recording']
    return normalized


def _operating_condition_grounded(key: str, value: Any, words: str) -> bool | None:
    """Accept canonical operating conditions only when the user said them."""
    if key == 'host_connection' and value == 'none':
        return bool(re.search(r'不(?:连接|接)(?:电脑|手机|主机|PC)|无需(?:电脑|手机|主机|PC)|脱机',
                              words, re.IGNORECASE))
    if key == 'standalone' and value is True:
        return bool(re.search(r'单机|独立|脱机|standalone', words, re.IGNORECASE))
    if key == 'topology_aspects' and isinstance(value, list):
        aspects = {
            'power': r'供电|电源|电池|power',
            'connection': r'连接|接线|拓扑|connection',
            'storage': r'存储|保存|TF\s*卡|SD\s*卡|storage',
        }
        return bool(value) and all(
            item in aspects and re.search(aspects[item], words, re.IGNORECASE)
            for item in value)
    if key == 'recording_mode' and value == 'local_tf_card':
        return bool(re.search(
            r'(?:本机|单机|独立|本地).{0,12}(?:TF\s*卡|SD\s*卡)'
            r'|(?:TF\s*卡|SD\s*卡).{0,12}(?:本机|单机|独立|本地)', words, re.IGNORECASE))
    if key == 'task' and value == ['recording']:
        return bool(re.search(r'录制|录像|record', words, re.IGNORECASE))
    if key == 'task' and value == 'standalone_capture':
        return bool(re.search(r'单机|独立|脱机', words, re.IGNORECASE)
                    and re.search(r'采集|录制|capture|record', words, re.IGNORECASE))
    if key == 'task' and value == 'local_recording':
        return bool(re.search(r'本机|本地|单机|独立', words, re.IGNORECASE)
                    and re.search(r'录制|录像|record', words, re.IGNORECASE))
    if key == 'storage' and value == 'tf_card':
        return bool(re.search(r'TF\s*卡|存储卡', words, re.IGNORECASE))
    if key == 'indicators' and isinstance(value, list):
        names = {'voice_prompt': r'语音|提示音|voice|没听见|未听到',
                 'green_led': r'绿灯|绿色指示灯|green.*led',
                 'red_led': r'红灯|红色\s*(?:LED|指示灯)|red\s*(?:LED|lamp)',
                 'blue_led': r'蓝灯|蓝色\s*(?:LED|指示灯)|blue\s*(?:LED|lamp)',
                 'yellow_led': r'黄灯|黄色\s*(?:LED|指示灯)|yellow\s*(?:LED|lamp)',
                 'led_state': r'灯态|指示灯|\bLED\b|lamp.state'}
        return bool(value) and all(
            item in names and re.search(names[item], words, re.IGNORECASE)
            for item in value)
    if key == 'steps' and isinstance(value, list):
        recording_context = bool(re.search(r'录制|录像|record', words, re.I))
        full_recording_span = bool(recording_context and re.search(
            r'开机.{0,8}(?:到|至).{0,8}停止|power.on.{0,20}stop', words, re.I))
        names = {
            'insert_card_power_off': r'断电.{0,8}(?:插卡|TF\s*卡|存储卡)',
            'power_on': r'开机|上电|power.on',
            'start_recording': (r'开始.{0,4}录制|启动.{0,4}录制|start.record'
                                + (r'|开始|启动' if recording_context else '')
                                + (r'|开机.{0,8}(?:到|至).{0,8}停止'
                                   if full_recording_span else '')),
            'stop_recording': (r'停止.{0,4}录制|结束.{0,4}录制|stop.record'
                               r'|录制.{0,50}(?:保存完成|完成检查点)'
                               + (r'|停止|结束' if recording_context else '')),
        }
        return bool(value) and all(
            item in names and re.search(names[item], words, re.IGNORECASE)
            for item in value)
    return None


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


def _sdk_platform_grounded(value: object, words: str) -> bool:
    if not isinstance(value, str):
        return False
    if value in {'Linux arm64', 'Linux x86_64'}:
        return bool(re.search(r'\b(?:linux|ubuntu)\b', words, re.I)
                    and value.split(' ', 1)[1] in sdk_cpu_architectures(words))
    if value.startswith('Windows '):
        version = re.search(r'\b(10|11)\b', value)
        asked_windows = re.search(r'\b(?:windows|win\s*(?:10|11))\b', words, re.I)
        if not asked_windows or (version and not re.search(
                r'\b(?:windows|win)\s*' + version.group(1) + r'\b', words, re.I)):
            return False
        asked_arch = sdk_cpu_architectures(words)
        value_arch = sdk_cpu_architectures(value)
        return bool(value_arch and value_arch == asked_arch)
    if value.startswith('macOS '):
        return bool(re.search(r'\bmac(?:\s*os)?\b|\bdarwin\b', words, re.I)
                    and sdk_cpu_architectures(value) == sdk_cpu_architectures(words))
    patterns = {
        'linux_x64': r'\blinux[\s_-]+(?:x64|x86_64)\b',
        'linux_x86_64': r'\blinux[\s_-]+x86_64\b',
        'windows_x64': r'\bwindows[\s_-]+x64\b',
    }
    return bool(value in patterns and re.search(patterns[value], words, re.I))


def _sdk_name_grounded(value: object, words: str) -> bool:
    if not isinstance(value, str):
        return False
    if value == 'android_wrapper':
        return bool(re.search(r'Android\s+(?:Java\s+)?Wrapper', words, re.I))
    if value == 'OrbbecSDK Android Java Wrapper':
        return bool(re.search(r'Android\s+Java\s+Wrapper', words, re.I))
    if value == 'android_java_wrapper':
        return bool(re.search(r'Android\s+Java\s+Wrapper', words, re.I))
    if value == 'orbbecsdk_java_wrapper':
        return bool(re.search(r'Android\s+Java\s+Wrapper', words, re.I))
    return False


def _normalize_encoded_sdk_selectors(capability: str, conditions: dict[str, Any],
                                     words: str) -> dict[str, Any]:
    """Recover typed, user-quoted selectors misplaced in a model's steps list."""
    if capability not in {'search_knowledge', 'sdk_evidence', 'check_software_support'}:
        return conditions
    steps = conditions.get('steps')
    if not isinstance(steps, list) or not steps:
        return conditions
    allowed = {'sdk', 'sdk_release_date', 'feature', 'source_files'}
    assignments = []
    for step in steps:
        if not isinstance(step, str):
            return conditions
        match = re.fullmatch(r'\s*([a-z_]+)\s*=\s*([^=\s][^=]*)\s*', step, re.I)
        if not match or match.group(1) not in allowed:
            return conditions
        key, value = match.group(1), match.group(2).strip()
        if key == 'source_files' and capability != 'sdk_evidence':
            return conditions
        if key == 'sdk_release_date' and capability == 'check_software_support':
            return conditions
        if value.casefold() not in words.casefold():
            # A typed selector invented by the model must not become evidence.
            return conditions
        assignments.append((key, value))
    normalized = dict(conditions)
    normalized.pop('steps')
    for key, value in assignments:
        if key in normalized and normalized[key] != value:
            raise SchemaExtractionError('schema_encoded_selector_conflict')
        normalized[key] = value
    return normalized


def _compared_audio_sdk_versions(question: str) -> tuple[str, ...]:
    if (not re.search(r'(?<![A-Za-z0-9_])OrbbecSDK(?![A-Za-z0-9_])',
                      question, re.I)
            or not re.search(r'音频|\baudio\b', question, re.I)
            or not re.search(r'和|与|对比|区别|相同|\b(?:vs|compare|same)\b',
                             question, re.I)
            or re.search(r'固件|firmware|\bviewer\b', question, re.I)):
        return ()
    versions = tuple(dict.fromkeys(re.findall(
        r'(?<!\d)(\d+\.\d+\.\d+)(?!\d)', question)))
    return versions if 1 < len(versions) <= 4 else ()


def _compared_orbbec_package_versions(question: str) -> tuple[str, ...]:
    """Recognize one package's versions, including an elided second SDK name."""
    if (not re.search(r'(?<![A-Za-z0-9_])OrbbecSDK(?![A-Za-z0-9_])',
                      question, re.I)
            or not re.search(r'包|package', question, re.I)
            or not re.search(r'和|与|分别|对比|\b(?:and|vs|compare)\b',
                             question, re.I)
            or re.search(r'固件|firmware|\bviewer\b', question, re.I)):
        return ()
    versions = tuple(dict.fromkeys(re.findall(
        r'(?<!\d)(\d+\.\d+\.\d+)(?!\d)', question)))
    return versions if 1 < len(versions) <= 4 else ()


_ORBBEC_OWNED_VERSION = re.compile(
    r'(?<![A-Za-z0-9_])OrbbecSDK[_\s-]*(?:的[_\s-]*)?'
    r'(?:版本[_\s-]*(?:是[_\s-]*)?)?v?'
    r'(\d+\.\d+\.\d+)(?![A-Za-z0-9_.])', re.I)


def _current_orbbec_package_version(question: str) -> str | None:
    """Bind a package subset to the last affirmative package ask."""
    clauses = [clause for clause in re.split(r'[，,。？！?；;\n]+', question)
               if re.search(r'包内容|包里有什么|package\s+(?:contents?|files?)',
                            clause, re.I)]
    if not clauses:
        return None
    clause = clauses[-1]
    if re.search(r'不要|不用|不查|取消|\b(?:not|cancel)\b',
                 clause, re.I):
        return None
    versions = _ORBBEC_OWNED_VERSION.findall(clause)
    if (not versions and not re.search(
            r'(?<![A-Za-z0-9_])OrbbecSDK(?![A-Za-z0-9_])', clause, re.I)):
        preceding = re.split(r'[，,。？！?；;\n]+', question)
        package_index = max((index for index, part in enumerate(preceding)
                             if part == clause), default=-1)
        if package_index > 0:
            versions = _ORBBEC_OWNED_VERSION.findall(preceding[package_index - 1])
    return versions[0] if len(set(versions)) == 1 and versions else None


def _reviewed_magnetometer_presence_only(
        question: str, conditions: dict[str, Any], catalog: SdkCatalog) -> bool:
    """Keep a reviewed example-presence ask apart from another platform's work."""
    clauses = [part for part in re.split(r'[?？。；;\n]+', question) if part.strip()]
    presence = [part for part in clauses if re.search(
        r'包(?:里|内|中)?.{0,30}磁力计.{0,15}示例|'
        r'package.{0,30}magnetometer.{0,20}(?:example|sample)',
        part, re.I) and catalog.grounds_selector(
            part, 'feature', 'magnetometer', scope=conditions)]
    if not presence:
        return False
    goal_platform = {
        'linux_x86_64': 'Linux x86_64',
        'linux_x64': 'Linux x86_64',
        'windows_x64': 'Windows x64',
    }.get(str(conditions.get('platform', '')).casefold(),
          str(conditions.get('platform', ''))).casefold()
    goal_os = next((name for name in ('linux', 'windows', 'android', 'ios', 'macos')
                    if name in goal_platform), '')
    goal_arch = set(sdk_cpu_architectures(goal_platform))
    for clause in clauses:
        if not re.search(r'运行|编译|适用|怎么|如何|调用|'
                         r'\b(?:build|run|compile|compatible)\b',
                         clause, re.I):
            continue
        asked_os = {name for name, pattern in (
            ('linux', r'\b(?:linux|ubuntu)\b'),
            ('windows', r'\b(?:windows|win\s*(?:10|11))\b'),
            ('android', r'\bandroid\b'), ('ios', r'\bios\b'),
            ('macos', r'\bmac(?:\s*os)?\b|\bdarwin\b'))
            if re.search(pattern, clause, re.I)}
        asked_arch = set(sdk_cpu_architectures(clause))
        if ((asked_os and goal_os not in asked_os)
                or (asked_arch and goal_arch and not asked_arch & goal_arch)):
            continue
        return False
    return True


def _normalize_sdk_conditions(capability: str, conditions: dict[str, Any],
                              words: str,
                              sdk_catalog: SdkCatalog | None = None, *,
                              current_question: str | None = None) -> dict[str, Any]:
    if capability not in {'sdk_evidence', 'search_knowledge'}:
        return conditions
    normalized = dict(conditions)
    version = normalized.get('sdk_version')
    if isinstance(version, str) and re.fullmatch(r'20\d{2}-\d{2}-\d{2}', version):
        existing = normalized.get('sdk_release_date')
        if existing is not None and existing != version:
            raise SchemaExtractionError('schema_sdk_build_date_conflict')
        normalized.pop('sdk_version')
        normalized['sdk_release_date'] = version
    if capability != 'sdk_evidence':
        return normalized
    if isinstance(current_question, str):
        if (not normalized.get('sdk')
                and re.search(r'(?<![A-Za-z0-9_])OrbbecSDK(?![A-Za-z0-9_])',
                              current_question, re.I)
                and 'OrbbecSDK' in audio_playback_subjects(current_question)):
            normalized['sdk'] = 'OrbbecSDK'
        if (normalized.get('sdk') == 'OrbbecSDK'
                and not any(normalized.get(key) for key in (
                    'feature', 'task', 'api', 'source_files'))
                and 'OrbbecSDK' in audio_playback_subjects(current_question)):
            # The user named the task even if the model emitted only a
            # generic SDK goal. Bind it before evidence matching and before
            # deciding whether device applicability needs another goal.
            normalized['feature'] = 'audio_playback'
        # The current turn owns explicit package selectors. A filename or
        # build mentioned only in history cannot replace a different one the
        # user just named, even when the model carries it into this goal.
        current_files = list(dict.fromkeys(re.findall(
            r'(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]*\.'
            r'(?:cpp|c|h|hpp|java|swift))(?![A-Za-z0-9_.-])',
            current_question, re.I)))
        proposed_files = normalized.get('source_files')
        proposed_files = ([proposed_files] if isinstance(proposed_files, str)
                          else proposed_files)
        if (current_files and isinstance(proposed_files, list)
                and not set(proposed_files) <= set(current_files)):
            normalized['source_files'] = current_files
        current_builds = list(dict.fromkeys(re.sub(r'\D', '', value)
            for value in re.findall(
                r'(?<!\d)20\d{2}[-/]?\d{2}[-/]?\d{2}(?!\d)',
                current_question)))
        if len(current_builds) == 1 and normalized.get('sdk_release_date'):
            build = current_builds[0]
            normalized['sdk_release_date'] = (
                f'{build[:4]}-{build[4:6]}-{build[6:]}' if len(build) == 8
                else build)
        sdk_version_question = current_question
        sdk_clauses = [clause for clause in re.split(
            r'[。？！?；;\n]+', current_question)
            if re.search(r'(?<![A-Za-z0-9_])OrbbecSDK(?![A-Za-z0-9_])',
                         clause, re.I)]
        if len(sdk_clauses) > 1:
            topic = (r'(?:SD\s*卡|\bsd.?card\b).{0,30}(?:下载|\bdownload\b)'
                     if (normalized.get('feature') == 'sd_card_file_download'
                         or normalized.get('task') in {
                             'sd_card_file_download', 'download', 'file_download'})
                     else r'包内容|包里有什么|package\s+(?:contents?|files?)'
                     if normalized.get('task') == 'inspect_package'
                     else r'磁力计|magnetometer'
                     if normalized.get('feature') == 'magnetometer'
                     else r'音频|\baudio\b'
                     if (normalized.get('feature') == 'audio_playback'
                         or normalized.get('task') == 'audio_playback')
                     else None)
            owning = [clause for clause in sdk_clauses
                      if topic and re.search(topic, clause, re.I)]
            sdk_version_question = owning[0] if len(owning) == 1 else ''
        current_sdk_versions = list(dict.fromkeys(
            _ORBBEC_OWNED_VERSION.findall(sdk_version_question)))
        if (normalized.get('sdk') in (None, 'OrbbecSDK')
                and not _compared_audio_sdk_versions(sdk_version_question)
                and not _compared_orbbec_package_versions(sdk_version_question)
                and len(current_sdk_versions) == 1):
            normalized['sdk_version'] = current_sdk_versions[0]
        if normalized.get('feature') == 'audio_playback' or normalized.get('task') == 'audio_playback':
            asked_platforms = [name for name, pattern in (
                ('Windows', r'\b(?:windows|win\s*(?:10|11))\b'),
                ('Linux', r'\blinux\b|\bubuntu\b'),
                ('macOS', r'\bmac(?:\s*os)?\b|\bdarwin\b'),
                ('Android', r'\bandroid\b'), ('iOS', r'\bios\b'))
                if re.search(pattern, current_question, re.I)]
            architectures = sdk_cpu_architectures(current_question)
            if len(asked_platforms) == 1:
                platform = asked_platforms[0]
                if platform == 'Windows':
                    win_version = re.search(r'\b(?:windows|win)\s*(10|11)\b',
                                            current_question, re.I)
                    if win_version:
                        platform = 'Windows ' + win_version.group(1)
                if len(architectures) == 1:
                    arch = architectures[0]
                    platform += ' ' + ('x64' if platform.startswith('Windows')
                                       and arch == 'x86_64' else arch)
                elif len(architectures) > 1:
                    platform += ' multiple architectures'
                normalized['platform'] = platform
            elif not asked_platforms and architectures:
                normalized['platform'] = ('unspecified OS ' + architectures[0]
                                          if len(architectures) == 1
                                          else 'unspecified OS multiple architectures')
    if isinstance(sdk_catalog, SdkCatalog):
        if (normalized.get('task') == 'inspect_package'
                and normalized.get('feature') in (None, 'magnetometer')
                and isinstance(current_question, str)
                and _reviewed_magnetometer_presence_only(
                    current_question, normalized, sdk_catalog)):
            # Existence of the uniquely reviewed example satisfies a
            # package-presence ask. Android and other unsupported scopes
            # retain inspect_package as unresolved, as do build/run asks.
            normalized.pop('task')
            normalized['feature'] = 'magnetometer'
        # A model may put its own explanation of the question into selectors
        # and even encode selectors as "steps". Two exact, source-reviewed
        # symbols in the user's words provide a safer retrieval anchor.
        def exact_symbol(symbol: str) -> bool:
            return bool(re.search(r'(?<![A-Za-z0-9_])' + re.escape(symbol)
                                  + r'(?![A-Za-z0-9_])', words, re.I))

        symbol_rows = [row for row in sdk_catalog.search(words, 'internal_fae', include_all=True)
                       if sum(exact_symbol(symbol)
                              for symbol in row.get('symbols', [])
                              if isinstance(symbol, str) and len(symbol) >= 4) >= 2]
        if symbol_rows:
            # Two named APIs establish which row to inspect. They do not
            # certify an additional task or feature requested by the user;
            # keep that selector until the reviewed row can prove it.
            steps = normalized.get('steps')
            if (isinstance(steps, list) and steps and all(
                    isinstance(step, str) and re.match(
                        r'^\s*[a-z_]+\s*=', step, re.I)
                    for step in steps)):
                normalized.pop('steps')
    if (normalized.get('standalone') is True
            and not re.search(r'单机|独立|单设备|standalone|single.device',
                              words, re.I)):
        # Device-side SDK use does not imply standalone acquisition. A model
        # default must not turn a package-usage goal into an impossible scope.
        normalized.pop('standalone')
    if normalized.get('standalone') is False:
        # A model default about device topology cannot narrow package API use.
        normalized.pop('standalone')
    task_aliases = {
        'framework_embedding': 'embed_framework',
        'demo_and_build_materials': 'inspect_package',
    }
    if (normalized.get('task') == 'integration'
            and _normalized(str(normalized.get('sdk', ''))) == 'egolowble'
            and re.search(r'\bDLL\b', words, re.I)
            and re.search(r'集成|integrat', words, re.I)
            and re.search(r'windows', words, re.I)):
        # The user's DLL clause is the reviewed Windows BLE install task;
        # the neighboring device/firmware comparison is a separate goal.
        task_aliases['integration'] = 'dll_integration'
    feature_aliases = {
        'simulator_artifact': 'simulator_artifacts',
        'master_slave_sync': 'multi_device_sync_mode_check_and_set',
        'prebuilt_demo_inclusion': 'inspect_package',
    }
    for key, aliases in (('task', task_aliases), ('feature', feature_aliases)):
        if isinstance(normalized.get(key), str):
            normalized[key] = aliases.get(normalized[key], normalized[key])
    platform_aliases = {
        'device': 'device_side',
        'host': 'host_side',
        'windows-x64': 'windows_x64',
        'linux-x64': 'linux_x64',
        'linux-x86_64': 'linux_x86_64',
    }
    if isinstance(normalized.get('platform'), str):
        normalized['platform'] = platform_aliases.get(
            normalized['platform'].casefold(), normalized['platform'])
    if (normalized.get('sample_type') == 'network' or
            normalized.get('connection_mode') == 'network') and re.search(
                r'网络|\bnetwork\b', words, re.I):
        normalized.pop('sample_type', None)
        normalized.pop('connection_mode', None)
        if normalized.get('connection') not in (None, 'network'):
            raise SchemaExtractionError('schema_network_sdk_scope_conflict')
        normalized['connection'] = 'network'
    if isinstance(sdk_catalog, SdkCatalog):
        # Normalize descriptive model labels only through a unique reviewed
        # SDK row; a user-mentioned concept is not automatically an API symbol
        # or a literal output filename.
        rows = sdk_catalog.search(words, 'internal_fae', include_all=True)
        platform = normalized.get('platform')
        if platform:
            if platform in {'host_side', 'device_side'}:
                layer = {'host_side': 'host_cpp', 'device_side': 'device_c'}[platform]
                rows = [row for row in rows if row.get('sdk_layer') == layer]
            else:
                platform_name = {'linux_x64': 'linux x86_64',
                                 'linux_x86_64': 'linux x86_64',
                                 'windows_x64': 'windows x64'}.get(platform, platform)
                rows = [row for row in rows if str(platform_name).casefold()
                        in str(row.get('platform', '')).casefold()]
        # Reconcile descriptive model tasks with a unique, source-checked
        # usage row. The question and requested SDK/build must agree with
        # that row; neighboring products and firmware support remain separate.
        scoped_rows = [row for row in rows
                       if (not normalized.get('sdk_release_date') or
                           re.sub(r'\D', '', str(normalized['sdk_release_date']))
                           == row.get('build'))
                       and (not normalized.get('sdk_version') or
                            normalized['sdk_version'] == row.get('version'))]
        # Model-generated task/feature labels are descriptors, not evidence.
        # Bind one only when the current question and SDK selectors identify a
        # unique checked example with the matching usage task.
        if (normalized.get('feature') == 'multi_device_sync'
                and normalized.get('sdk') == 'OrbbecSDK'
                and normalized.get('language') in (None, 'cpp')
                and re.search(r'多设备|多机|multi.device', words, re.I)
                and re.search(r'主从|PRIMARY|SECONDARY|sync', words, re.I)
                and re.search(r'示例|example|sample', words, re.I)):
            sync_rows = [row for row in scoped_rows
                         if row.get('sdk_layer') == 'host_cpp'
                         and row.get('doc_type') == 'example'
                         and 'multi_device_sync_mode_check_and_set'
                         in row.get('usage_tasks', [])]
            if len(sync_rows) == 1:
                normalized['feature'] = 'multi_device_sync_mode_check_and_set'
        if (normalized.get('task') in {'preset_switch', 'switch_preset'}
                and normalized.get('sdk') == 'OrbbecSDK'
                and re.search(r'EGO\s*Depth', words, re.I)
                and re.search(r'网络|network', words, re.I)
                and re.search(r'切换.{0,4}预设|switch.{0,12}preset', words, re.I)
                and re.search(r'\bStreamProfile\b', words, re.I)):
            preset_rows = [row for row in scoped_rows
                           if row.get('sdk_layer') == 'host_cpp'
                           and row.get('doc_type') == 'example'
                           and 'preset_profile' in row.get('usage_tasks', [])
                           and 'EGO Depth' in row.get('example_subjects', [])
                           and 'network' in row.get('topics', [])
                           and 'StreamProfile' in str(row.get('answer', ''))]
            if len(preset_rows) == 1:
                normalized['task'] = 'preset_profile'
        if (normalized.get('task') == 'simultaneous_camera_imu_audio_capture'
                and isinstance(current_question, str)
                and _normalized(str(normalized.get('sdk', ''))) == 'libobkit'
                and normalized.get('language') in (None, 'c')
                and re.search(r'相机|camera', current_question, re.I)
                and re.search(r'\bIMU\b', current_question, re.I)
                and re.search(r'音频|audio', current_question, re.I)
                and re.search(r'初始化|配置|启停|退出|lifecycle|'
                              r'init|config|start|stop', current_question, re.I)):
            capture_rows = [row for row in scoped_rows
                            if row.get('sdk_layer') == 'device_c'
                            and row.get('doc_type') == 'example'
                            and 'capture' in row.get('usage_tasks', [])
                            and {'camera', 'imu', 'audio', 'lifecycle'} <=
                            set(row.get('topics', []))]
            if len(capture_rows) == 1:
                normalized['task'] = 'capture'
        if (normalized.get('task') ==
                'export_alignment_and_imu_calibration_yaml'
                and isinstance(current_question, str)
                and normalized.get('language') in (None, 'cpp')
                and normalized.get('sdk') in (None, 'OrbbecSDK')
                and re.search(r'导出|export', current_question, re.I)
                and re.search(r'对齐|align', current_question, re.I)
                and re.search(r'\bIMU\b', current_question, re.I)
                and re.search(r'标定|calibrat', current_question, re.I)
                and re.search(r'\bYAML\b|\.ya?ml\b', current_question, re.I)):
            calib_rows = [row for row in scoped_rows
                          if row.get('sdk_layer') == 'host_cpp'
                          and row.get('doc_type') == 'example'
                          and calibration_export_row_matches_question(
                              row, current_question)
                          and 'calibration_export' in row.get('usage_tasks', [])]
            if len(calib_rows) == 1:
                normalized['task'] = 'calibration_export'
        if ((normalized.get('feature') == 'sd_card_file_download'
             and not normalized.get('task'))
                or normalized.get('task') == 'sd_card_file_download'):
            sdcard_paraphrase = True
        else:
            sdcard_paraphrase = False
        if (sdcard_paraphrase
                and isinstance(current_question, str)
                and re.search(r'SD\s*卡|\bsd.?card\b', current_question, re.I)
                and re.search(r'下载|\bdownload\b', current_question, re.I)):
            # Bind a descriptive model label to this scope's unique reviewed
            # download example, using that source's actual task vocabulary.
            build = re.sub(r'\D', '', str(normalized.get('sdk_release_date', '')))
            version = normalized.get('sdk_version')
            eligible = [row for row in rows
                        if row.get('sdk_layer') in {'host_cpp', 'android_java'}
                        and (not build or row.get('build') == build)
                        and (not version or row.get('version') == version)
                        and 'sdcard_file_service' in row.get('usage_tasks', [])
                        and any(task in row.get('usage_tasks', [])
                                for task in ('download', 'file_download'))]
            if len(eligible) == 1:
                if normalized.get('feature') == 'sd_card_file_download':
                    normalized.pop('feature')
                normalized['task'] = ('file_download' if
                    'file_download' in eligible[0]['usage_tasks'] else 'download')
        source_files = normalized.get('source_files')
        if (normalized.get('task') == 'inspect_package'
                and isinstance(current_question, str)
                and re.search(r'包|\bpackage\b', current_question, re.I)
                and re.search(r'示例|\b(?:example|sample)\b', current_question, re.I)
                and re.search(r'有|含|存在|带|\b(?:include|contain)\b',
                              current_question, re.I)
                and not re.search(r'编译|运行|可用|支持|兼容|\b(?:compile|run|'
                                  r'support|compatible)\b', current_question, re.I)
                and isinstance(source_files, list) and len(source_files) == 1
                and isinstance(source_files[0], str)
                and re.search(r'(?<![A-Za-z0-9_.-])'
                              + re.escape(source_files[0])
                              + r'(?![A-Za-z0-9_.-])', current_question, re.I)):
            # File presence is determined by a checked archive member, not
            # the package-inspection task label used by unrelated BLE rows.
            # Drop a model-generated label only for one exact reviewed file,
            # build and version; execution and compatibility remain separate.
            filename = source_files[0]
            build = re.sub(r'\D', '', str(normalized.get('sdk_release_date', '')))
            matching_files = [row for row in rows
                              if row.get('doc_type') == 'example'
                              and (not build or row.get('build') == build)
                              and (not normalized.get('sdk_version') or
                                   row.get('version') == normalized['sdk_version'])
                              and any(str(ref.get('member', '')).replace('\\', '/')
                                      .rsplit('/', 1)[-1] == filename
                                      for ref in row.get('source_refs', []))]
            if len(matching_files) == 1:
                normalized.pop('task')
        libobkit_image_scope = (
            isinstance(current_question, str)
            and _normalized(str(normalized.get('sdk', ''))) == 'libobkit'
            and re.search(r'\blibobkit(?:\.so)?\b', current_question, re.I)
            and not re.search(r'\b[A-Za-z][A-Za-z0-9_-]*SDK\b|'
                              r'\b(?:OpenCV|EgoLowBle|EgoViewer)\b',
                              current_question, re.I)
            and device_sdk_scope_matches_question(current_question))
        if (libobkit_image_scope
                and normalized.get('task') in {
                    'inspect_package', 'firmware_image_requirement',
                    'firmware_image_requirements'}
                and (normalized['task'] != 'inspect_package'
                     or re.search(r'包内|README|\bpackage\b',
                                  current_question, re.I))):
            image_scope_rows = [row for row in rows
                                if row.get('sdk_layer') == 'device_c'
                                and 'sdk_scope' in row.get('usage_tasks', [])
                                and 'image_binding' in row.get('topics', [])
                                and any(str(ref.get('member', '')).endswith(
                                    '/README.md') for ref in row.get('source_refs', []))]
            if len(image_scope_rows) == 1:
                normalized['task'] = 'sdk_scope'
        if (((normalized.get('feature') == 'image_requirements'
                  and normalized.get('task') in (None, 'sdk_scope'))
                 or (normalized.get('task') == 'image_requirements'
                     and normalized.get('feature') is None))
                and isinstance(current_question, str)
                and re.search(r'\blibobkit(?:\.so)?\b', current_question, re.I)
                and re.search(r'镜像|firmware\s+image', current_question, re.I)
                and re.search(r'包内|README|\bpackage\b', current_question, re.I)
                and device_sdk_scope_matches_question(current_question)):
            # The model described an image requirement, while the reviewed
            # README publishes the SDK's image-binding scope. This mapping
            # requires one exact device-side source row, never a similarly
            # named host SDK example or a generic camera image question.
            image_scope_rows = [row for row in rows
                                if row.get('sdk_layer') == 'device_c'
                                and 'sdk_scope' in row.get('usage_tasks', [])
                                and 'image_binding' in row.get('topics', [])
                                and any(str(ref.get('member', '')).endswith(
                                    '/README.md') for ref in row.get('source_refs', []))]
            if len(image_scope_rows) == 1:
                normalized.pop('feature', None)
                normalized['task'] = 'sdk_scope'
        sample_type = normalized.get('sample_type')
        if (isinstance(sample_type, str)
                and re.search(r'示例|\b(?:example|sample|demo)\b', words, re.I)):
            # An LLM may use sample_type to paraphrase what an example does.
            # That is not an SDK catalog field. Keep the actual reviewed task
            # and discard the paraphrase only when one example row owns it.
            matching_examples = [row for row in rows
                                 if row.get('doc_type') == 'example'
                                 and sdk_usage_descriptor_matches(sample_type, row)]
            if len(matching_examples) == 1:
                normalized.pop('sample_type')
        task_descriptor = normalized.get('task')
        if (isinstance(task_descriptor, str)
                and task_descriptor.casefold() not in words.casefold()):
            # A model can append descriptive words to a reviewed task label.
            # Shorten it only when the user's exact SDK symbol identifies one
            # reviewed row and the whole description still matches that row.
            anchored_rows = [row for row in rows
                             if sdk_usage_descriptor_matches(task_descriptor, row)
                             and any(isinstance(symbol, str) and len(symbol) >= 5
                                     and re.search(r'(?<![A-Za-z0-9_])'
                                                   + re.escape(symbol)
                                                   + r'(?![A-Za-z0-9_])', words, re.I)
                                     for symbol in row.get('symbols', []))]
            if len(anchored_rows) == 1:
                canonical = [task for task in anchored_rows[0].get('usage_tasks', [])
                             if isinstance(task, str)
                             and task_descriptor.startswith(task + '_')]
                if len(canonical) == 1:
                    normalized['task'] = canonical[0]
                    task_descriptor = canonical[0]
                elif task_descriptor not in anchored_rows[0].get('usage_tasks', []):
                    # The row is source-bound but its multiple tasks do not
                    # identify a unique canonical task from this descriptor.
                    # Leave task selection to other exact, reviewed selectors.
                    normalized.pop('task')
                    task_descriptor = None
        if (isinstance(task_descriptor, str)
                and re.search(r'示例|\b(?:example|sample|demo)\b', words, re.I)):
            examples = [row for row in rows
                        if row.get('doc_type') == 'example'
                        and sdk_usage_descriptor_matches(task_descriptor, row)]
            described_parts = re.findall(r'[a-z0-9]+', task_descriptor.casefold())
            if len(examples) == 1 and described_parts:
                canonical = [task for task in examples[0].get('usage_tasks', [])
                             if isinstance(task, str)
                             and len((parts := re.findall(r'[a-z0-9]+', task.casefold())))
                             > len(described_parts)
                             and parts[:len(described_parts)] == described_parts]
                if len(canonical) == 1:
                    normalized['task'] = canonical[0]
        if (normalized.get('task') == 'link_and_place'
                and re.search(r'链接|\blink\b', words, re.I)
                and re.search(r'放置|\bplace(?:ment)?\b', words, re.I)
                and any('link_and_deploy' in row.get('usage_tasks', []) or
                        {'link_library', 'deploy_dll'} <= set(row.get('usage_tasks', []))
                        for row in rows)):
            # The model's wording describes the same source-reviewed install
            # task. Require an exact package/platform row before normalizing.
            normalized['task'] = 'link_and_deploy'
        task = normalized.get('task')
        if (isinstance(task, str) and re.search(r'compile|build', task, re.I)
                and re.search(r'编译|\bcompile\b|\bbuild\b', words, re.I)):
            anchored = [row for row in rows
                        if 'build' in row.get('usage_tasks', [])
                        and any(isinstance(symbol, str) and len(symbol) >= 6
                                and exact_symbol(symbol)
                                for symbol in row.get('symbols', []))]
            if len(anchored) == 1 and not sdk_usage_descriptor_matches(
                    task, anchored[0]):
                normalized['task'] = 'build'
        for descriptor_key in ('api', 'feature'):
            descriptor = normalized.get(descriptor_key)
            if not isinstance(descriptor, str) or descriptor.casefold() in words.casefold():
                continue
            if descriptor_key == 'api' and re.search(r'[,，;；/]', descriptor):
                parts = [part.strip() for part in re.split(r'[,，;；/]', descriptor)]
                if (2 <= len(parts) <= 8 and all(
                        re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', part)
                        and re.search(r'(?<![A-Za-z0-9_])' + re.escape(part)
                                      + r'(?![A-Za-z0-9_])', words, re.I)
                        for part in parts)):
                    # The parser below turns these user-named public symbols
                    # into an API list. They are not a model paraphrase.
                    continue
            candidates = [row for row in rows
                          if sdk_usage_descriptor_matches(descriptor, row)
                          and (descriptor_key != 'api'
                               or descriptor not in row.get('symbols', []))
                          and any(isinstance(symbol, str) and len(symbol) >= 6
                                  and symbol.casefold() in words.casefold()
                                  for symbol in row.get('symbols', []))]
            if len(candidates) != 1:
                continue
            tasks = [task for task in candidates[0].get('usage_tasks', [])
                     if isinstance(task, str) and task in descriptor]
            if len(tasks) == 1 and normalized.get('task') in (None, tasks[0]):
                normalized.pop(descriptor_key)
                normalized['task'] = tasks[0]
            elif normalized.get('task') is None:
                # The source row is unique, but this model paraphrase does
                # not select one of its reviewed task labels. Keep the row
                # reachable without pretending the paraphrase is an API or
                # feature published by the package.
                normalized.pop(descriptor_key)
        concept = normalized.get('api')
        if (isinstance(concept, str) and concept in words and
                not any(concept in row.get('symbols', []) for row in rows)):
            described = [row for row in rows
                         if concept.casefold() in str(row.get('answer', '')).casefold()
                         and len(row.get('usage_tasks', [])) == 1]
            if len(described) == 1:
                normalized.pop('api')
                normalized.setdefault('task', described[0]['usage_tasks'][0])
        if (isinstance(normalized.get('task'), str)
                and 'preset' in normalized['task']
                and 'profile' in normalized['task']
                and re.search(r'切换.{0,4}预设|switch.{0,12}preset', words, re.I)
                and re.search(r'\bStreamProfile\b', words, re.I)):
            described = [row for row in rows
                         if 'preset_profile' in row.get('usage_tasks', [])
                         and 'StreamProfile' in str(row.get('answer', ''))]
            if len(described) == 1:
                normalized['task'] = 'preset_profile'
        if (normalized.get('feature') == 'preset_switch'
                and normalized.get('task') == 'preset_profile'
                and re.search(r'切换.{0,4}预设|switch.{0,12}preset', words, re.I)
                and any('preset_profile' in row.get('usage_tasks', [])
                        for row in rows)):
            normalized.pop('feature')
        outputs = normalized.get('export_files')
        output_values = ([part.strip() for part in re.split(r'[,，;；]', outputs)]
                         if isinstance(outputs, str) else outputs)
        if (isinstance(outputs, str) and isinstance(output_values, list)
                and 2 <= len(output_values) <= 8
                and all(isinstance(name, str) and re.fullmatch(
                    r'[A-Za-z][A-Za-z0-9_.-]*\.[A-Za-z][A-Za-z0-9]{0,7}', name)
                    and name in words for name in output_values)):
            # A model may serialize user-named files as one comma-separated
            # string. Keep every exact filename; the SDK tool then checks all
            # of them against the same reviewed source row.
            normalized['export_files'] = output_values
        descriptive_pair = (isinstance(output_values, list)
                            and len(output_values) == 2
                            and all(isinstance(name, str) and name.strip()
                                    for name in output_values)
                            and sorted(
                                'align' if re.search(r'align|对齐', name, re.I)
                                and not re.search(r'imu', name, re.I)
                                and re.search(r'yaml', name, re.I)
                                else 'imu' if re.search(r'imu', name, re.I)
                                and not re.search(r'align|对齐', name, re.I)
                                and re.search(r'yaml', name, re.I)
                                else 'other'
                                for name in output_values) == ['align', 'imu'])
        if (descriptive_pair and
                all(isinstance(name, str) and name not in words
                    for name in output_values)
                and re.search(r'对齐|align', words, re.I)
                and re.search(r'\bIMU\b', words, re.I)
                and re.search(r'标定|calib', words, re.I)
                and re.search(r'导出|export', words, re.I)):
            described = [row for row in rows
                         if calibration_export_row_matches_question(row, words)
                         and (not normalized.get('task') or
                              sdk_usage_descriptor_matches(
                                  str(normalized['task']), row))]
            if len(described) == 1:
                normalized.pop('export_files')
                normalized['task'] = 'calibration_export'
        if (normalized.get('task') == 'export_raw_bytes'
                and isinstance(current_question, str)
                and re.search(r'原始字节|raw\s+bytes?', current_question, re.I)
                and re.search(r'导出|export', current_question, re.I)
                and isinstance(output_values, list)
                and len(output_values) == 2
                and all(isinstance(name, str) and name in current_question
                        for name in output_values)):
            # "Raw bytes" describes the exact two YAML outputs in this
            # reviewed example, not a new SDK API. Keep the user-named files
            # and require the source row's product, platform and CPU scope.
            described = [row for row in rows
                         if calibration_export_row_matches_question(
                             row, current_question)
                         and set(output_values) <= set(row.get('outputs', []))]
            if len(described) == 1:
                normalized['task'] = 'calibration_export'
        task = normalized.get('task')
        if task == 'prebuilt_demo_presence' and re.search(
                r'预编译|prebuilt', words, re.I):
            if any('inspect_package' in row.get('usage_tasks', [])
                   for row in rows):
                normalized['task'] = 'inspect_package'
        if (re.search(r'预编译\s*demo|prebuilt\s*demo', words, re.I)
                and any('inspect_package' in row.get('usage_tasks', [])
                        and 'linux' in str(row.get('platform', '')).casefold()
                        for row in rows)):
            for key in ('sample_type', 'feature'):
                if normalized.get(key) == 'prebuilt_demo':
                    normalized.pop(key)
                    normalized.setdefault('task', 'inspect_package')
        if task == 'link_and_deploy':
            if any({'link_library', 'deploy_dll'} <=
                   set(row.get('usage_tasks', [])) for row in rows):
                normalized['task'] = ['link_library', 'deploy_dll']
    if normalized.get('feature') == 'bluetooth' and re.search(
            r'蓝牙|\b(?:BLE|bluetooth)\b', words, re.I):
        normalized['feature'] = 'ble'
    if (normalized.get('feature') == 'invocation_and_completion_handling'
            and re.search(r'调用|invok', words, re.I)
            and re.search(r'完成|completion', words, re.I)):
        # How to call and finish are the requested answer shape, not a
        # separate SDK capability or compatibility selector.
        normalized.pop('feature')
    if normalized.get('steps') == []:
        normalized.pop('steps')
    files = normalized.get('export_files')
    files = [files] if isinstance(files, str) else files
    if (isinstance(files, list) and files and
            all(isinstance(name, str) and re.fullmatch(
                r'[A-Za-z][A-Za-z0-9_.-]*\.(?:cpp|c|h|hpp|java|swift)', name)
                and name in words for name in files)):
        normalized.pop('export_files')
        if 'source_files' in normalized and normalized['source_files'] != files:
            raise SchemaExtractionError('schema_sdk_source_file_conflict')
        normalized['source_files'] = files
    source_files = normalized.get('source_files')
    if source_files is not None:
        values = [source_files] if isinstance(source_files, str) else source_files
        if (not isinstance(values, list) or not values or any(
                not isinstance(name, str) or not re.fullmatch(
                    r'[A-Za-z][A-Za-z0-9_.-]*\.(?:cpp|c|h|hpp|java|swift)', name)
                or name not in words for name in values)):
            raise SchemaExtractionError('schema_sdk_source_file_not_grounded')
        normalized['source_files'] = values
    api = normalized.get('api')
    if isinstance(api, str) and re.search(r'[,，;；/]', api):
        parts = [part.strip() for part in re.split(r'[,，;；/]', api)]
        if (2 <= len(parts) <= 8 and
                all(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', part) and
                    re.search(r'(?<![A-Za-z0-9_])' + re.escape(part) +
                              r'(?![A-Za-z0-9_])', words, re.I)
                    for part in parts)):
            normalized['api'] = parts
    return normalized


def _normalize_spec_conditions(capability: str, conditions: dict[str, Any],
                               question: str) -> dict[str, Any]:
    """Keep a model's misplaced measured mode as an evidence selector."""
    if capability != 'lookup_spec':
        return conditions
    conditions = dict(conditions)
    if (conditions.get('standalone') is False
            and not re.search(r'非单机|不能单机|不独立|非独立|not.standalone|multi.device',
                              question, re.I)):
        conditions.pop('standalone')
    if 'steps' not in conditions:
        return conditions
    steps = conditions['steps']
    if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], str):
        raise SchemaExtractionError('schema_spec_steps_invalid')
    match = re.fullmatch(r'\s*resolution\s*[=:]\s*(\d{3,4}\s*[x×X]\s*\d{3,4})\s*',
                         steps[0], re.I)
    if not match:
        raise SchemaExtractionError('schema_spec_steps_invalid')
    resolution = _normalized(match.group(1))
    normalized_question = _normalized(question)
    if resolution not in normalized_question:
        raise SchemaExtractionError('schema_spec_resolution_not_grounded')
    explicit_modes = set(re.findall(r'(\d{3,4}x\d{3,4})@\d+(?:\.\d+)?',
                                    normalized_question))
    if len(explicit_modes) == 1 and resolution not in explicit_modes:
        raise SchemaExtractionError('schema_spec_resolution_target_conflict')
    if 'resolution' in conditions and _normalized(str(conditions['resolution'])) != resolution:
        raise SchemaExtractionError('schema_spec_resolution_target_conflict')
    return {**{key: value for key, value in conditions.items() if key != 'steps'},
            'resolution': resolution}


_RESOLUTION_PAIR = re.compile(r'(?<!\d)(\d{3,4}x\d{3,4})(?!\d)')
_RESOLUTION_VERSION = re.compile(r'(?<!\d)(\d{3,4}x\d{3,4})(?:版本|变体|版|version|variant)')


def _explicit_resolution_versions(text: str) -> set[str]:
    normalized = _normalized(text)
    versions = {match.group(1) for match in _RESOLUTION_VERSION.finditer(normalized)}
    versions.update(match.group(1) for match in re.finditer(
        r'(?<!\d)(\d{3,4}x\d{3,4})原生', normalized))
    # Existing reviewed family questions may identify a variant by its
    # resolution immediately before a static optical field, without "版本".
    versions.update(match.group(1) for match in re.finditer(
        r'(?<!\d)(\d{3,4}x\d{3,4})的(?:水平|垂直)?'
        r'(?:视场角|视角|fov)', normalized, re.I))
    # A trailing collective label scopes every member of a comparison:
    # "1600x1200 和 1920x1200 两个版本" names two variants, whereas
    # "支持 1600x1300@30，是否只支持 1600x1200" names output modes.
    group = re.compile(r'(?<!\d)(?:\d{3,4}x\d{3,4})'
                       r'(?:(?:和|与|及|、|或|/)\d{3,4}x\d{3,4})+'
                       r'(?:两|两个|多|不同)?(?:版|版本|变体)')
    for match in group.finditer(normalized):
        versions.update(_RESOLUTION_PAIR.findall(match.group()))
    return versions


def _reviewed_short_resolution_versions(text: str, knowledge, role: str) -> set[str]:
    """Expand a width-only variant label only through one visible SKU mapping."""
    if not text or knowledge is None or not hasattr(knowledge, 'records_for'):
        return set()
    families: dict[str, list[dict]] = {}
    for row in knowledge.records_for(role):
        if not isinstance(row, dict) or row.get('kind') != 'entity':
            continue
        scope = row.get('scope', {})
        variant = scope.get('resolution_variant')
        product = scope.get('product')
        if (not isinstance(product, str) or not isinstance(variant, str)
                or not re.fullmatch(r'\d{3,4}x\d{3,4}', _normalized(variant))):
            continue
        families.setdefault(product, []).append(row)
    from daq_fae.knowledge.identity_selectors import composite_versions_in_text
    selected: set[str] = composite_versions_in_text(text, knowledge.records_for(role))
    for clause in re.split(r'[。？！?；;\n]+', text):
        widths = set(re.findall(r'(?<!\d)(\d{3,4})\s*(?:版|版本|变体)',
                                clause))
        if not widths:
            continue
        matching: list[tuple[dict[str, str], set[str]]] = []
        for rows in families.values():
            variants = {_normalized(row['scope']['resolution_variant'])
                        for row in rows}
            by_width = {width: {variant for variant in variants
                                if variant.split('x', 1)[0] == width}
                        for width in widths}
            unique = {width: next(iter(values)) for width, values in by_width.items()
                      if len(values) == 1}
            if not unique:
                continue
            expanded = clause
            for width, variant in unique.items():
                expanded = re.sub(r'(?<!\d)' + re.escape(width)
                                  + r'(\s*(?:版|版本|变体))',
                                  lambda match, v=variant: v + match.group(1),
                                  expanded)
            labels = {label for row in rows
                      for label in [row.get('data', {}).get('name'),
                                    *row.get('data', {}).get('aliases', [])]
                      if isinstance(label, str) and label}
            if any(reviewed_name_in_query(label, expanded,
                                          selector_values=variants)
                   for label in labels):
                matching.append((unique, widths))
        # A short numeric label in a clause naming multiple product families
        # is ambiguous even if each family has a unique same-width SKU.
        if len(matching) == 1:
            unique, _ = matching[0]
            selected.update(unique.values())
    return selected


def _reviewed_width_variant_for_goal(value: Any, entities: list[str],
                                     selected_versions: set[str], knowledge,
                                     role: str) -> str | None:
    """Resolve a model's width-only `variant` through this goal's visible SKU."""
    if (type(value) not in (str, int) or len(entities) != 1
            or knowledge is None or not hasattr(knowledge, 'records_for')):
        return None
    short = re.fullmatch(r'(\d{3,4})(?:\s*(?:版|版本|变体))?', str(value).strip())
    if not short:
        return None
    candidates = {variant for variant in selected_versions
                  if variant.split('x', 1)[0] == short.group(1)}
    if len(candidates) != 1:
        return None
    selected = next(iter(candidates))
    for row in knowledge.records_for(role):
        if not isinstance(row, dict) or row.get('kind') != 'entity' \
                or _normalized(str(row.get('scope', {}).get('resolution_variant', ''))) \
                != selected:
            continue
        data = row.get('data', {})
        labels = [data.get('name'), *(data.get('aliases') or [])]
        if any(isinstance(label, str) and
               reviewed_name_in_query(label, entities[0],
                                      selector_values=(selected,))
               for label in labels):
            return selected
    return None


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
            elif ((active_hub or (prior_hub and current_counts)) and
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
        if (per_port_match and re.search(r'乘|multiply|\btimes\b', clause, re.I)
                and re.search(r'总供电|总输出|总功率|\btotal\b', clause, re.I)):
            # A stated per-port value in a multiplication proposal is a
            # premise, not another request for the per-port specification.
            continue
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


def _explicit_qualitative_hub_power(question: str, prior_user_context: str,
                                    other_entities: tuple[str, ...]) -> bool:
    active_hub = bool(re.search(r'\bHUB\b|集线', prior_user_context, re.I))
    for clause in re.split(r'[?？。!！；;，,\n]+', question):
        has_hub = bool(re.search(r'\bHUB\b|集线', clause, re.I))
        power = re.search(r'供电|\bpower\b', clause, re.I)
        feasibility = re.search(
            r'能否|能不能|是否|保证|够|可否|sufficient|enough', clause, re.I)
        if (power and feasibility and (has_hub or active_hub) and
                not _other_product_nearest(clause, power.start(), other_entities)):
            return True
        if has_hub:
            active_hub = True
        if power and _other_product_nearest(clause, power.start(), other_entities):
            active_hub = False
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
            inline_fps_modes = set()
            for mode in modes:
                mode_context = (r'(?:在|以|用|切到|切换到|切换为|改用|改为|改成).{0,12}'
                                + re.escape(mode))
                if (re.search(mode_context, clause) or
                        re.search(re.escape(mode) + r'(?:下|时|模式)', clause) or
                        ('输出模式' in clause and mode in clause)):
                    local_modes.add(mode)
                height = mode.split('x')[1]
                if re.search(r'(?:切到|切换到|切换为|改用|改为|改成).{0,12}'
                             + re.escape(height) + r'(?:后|下|时)', clause):
                    local_modes.add(mode)
                if re.search(re.escape(mode) + r'@\d+(?:\.\d+)?(?:fps)?', clause, re.I):
                    local_modes.add(mode)
                    inline_fps_modes.add(mode)
            pending_modes.update(local_modes)
            asks_fps = bool(re.search(r'(?:fps|帧率|帧)', clause, re.I)
                            and re.search(r'多少|几|最高|能到|达到|可达|能否|能跑|能填|吗|上限', clause))
            asks_fps = asks_fps or bool(inline_fps_modes and re.search(
                r'是否|支持|能否|能|可|吗|多少|几', clause))
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
                       selected_answer_language: str | None = None,
                       sdk_catalog: SdkCatalog | None = None,
                       knowledge=None, role: str = 'internal_fae') -> 'DaqRequestSchema':
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
        qualitative_power_feasibility = bool(proposed_hub_goal and
            _explicit_qualitative_hub_power(
                question, prior_power_context, other_power_entities))
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
        # An attachment carries observations, while official compatibility is
        # a separate product claim. A model can put the named device on the
        # attachment goal in a log follow-up. Move that grounded name to a
        # support obligation instead of rejecting the whole turn or letting
        # the attachment itself certify the product conclusion.
        if (attachment_present and re.search(r'日志|附件|\blog\b|attachment',
                                             question, re.I)
                and re.search(r'官方|正式|兼容|支持|official|compatible|supported',
                              question, re.I)):
            adjusted_goals = []
            attachment_entities = []
            for item in raw_goals:
                names = item.get('entities') if isinstance(item, dict) else None
                if (isinstance(item, dict) and item.get('kind') == 'attachment'
                        and item.get('capability') == 'search_attachments'
                        and isinstance(names, list) and names
                        and all(isinstance(name, str) and name.strip()
                                and _normalized(name) in accepted_scope
                                for name in names)):
                    attachment_entities.extend(names)
                    adjusted_goals.append({**item, 'entities': []})
                else:
                    adjusted_goals.append(item)
            if attachment_entities:
                for name in dict.fromkeys(attachment_entities):
                    if any(isinstance(item, dict) and item.get('kind') == 'product'
                           and item.get('capability') == 'check_software_support'
                           and name in (item.get('entities') or [])
                           for item in adjusted_goals):
                        continue
                    firmware = re.search(
                        r'(?:固件|firmware)(?:版本)?\s*'
                        r'(v?\d+(?:\.\d+){1,3})', question, re.I)
                    conditions = ({'firmware_version': firmware.group(1)}
                                  if firmware else {})
                    adjusted_goals.append({
                        'kind': 'product',
                        'capability': 'check_software_support',
                        'entities': [name], 'conditions': conditions,
                    })
                raw_goals = adjusted_goals
        short_current = _reviewed_short_resolution_versions(question, knowledge, role)
        short_prior = _reviewed_short_resolution_versions(prior_user_context,
                                                          knowledge, role)
        explicit_versions = ((_explicit_resolution_versions(question) | short_current)
                             or (_explicit_resolution_versions(prior_user_context)
                                 | short_prior))
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
            if kind == 'attachment' and capability == 'search_attachments':
                # Attachment selectors belong to the read call, not a product
                # evidence goal. The actual question and attachment owner
                # govern retrieval; model-added product conditions cannot
                # make the whole follow-up fail schema validation.
                raw = {**raw, 'conditions': {}}
            entities = raw.get('entities', [])
            if not isinstance(entities, list) or len(entities) > 6 or any(
                not isinstance(entity, str) or not entity.strip() or
                _normalized(entity) not in accepted_scope for entity in entities
            ):
                raise SchemaExtractionError('entity_not_grounded')
            if kind != 'product' and entities:
                raise SchemaExtractionError('schema_goal_scope_invalid')
            # Only a spec lookup consumes a claim field. A model can place
            # status names or API labels into `field` on an SDK goal; discard
            # that unused value before validating spec field syntax. The raw
            # signature below still detects distinct goals collapsed by this
            # normalization instead of silently merging obligations.
            field = raw.get('field') if (kind == 'product' and
                                         capability == 'lookup_spec') else None
            if field is not None and (not isinstance(field, str)
                                      or not _SAFE_CODE.fullmatch(field)):
                raise SchemaExtractionError('schema_field_invalid')
            conditions = raw.get('conditions', {})
            if not isinstance(conditions, dict) or len(conditions) > 16 or any(
                not isinstance(key, str) or not _SAFE_CODE.fullmatch(key) or
                _SENSITIVE_CONDITION_KEY.search(key) or
                not _safe_condition_value(value)
                for key, value in conditions.items()
            ) or len(json.dumps(conditions, ensure_ascii=False)) > 600:
                raise SchemaExtractionError('schema_conditions_invalid')
            if (capability == 'search_knowledge'
                    and isinstance(sdk_catalog, SdkCatalog)
                    and isinstance(conditions.get('sdk'), str)
                    and re.search(r'包(?:里|内|中|含|有)|package\s+(?:contents?|files?|'
                                  r'examples?)|demo|示例|构建资料', question, re.I)
                    and not re.search(r'变更|更新|发布说明|改动|改了|变化|新增|移除|修复|'
                                      r'changelog|release.note|what(?:\s+has)?\s+changed',
                                      question, re.I)
                    and sdk_catalog.search(question, 'internal_fae')):
                # Package contents are typed SDK evidence, even if the model
                # proposes a generic knowledge search for one subquestion.
                capability = 'sdk_evidence'
            try:
                raw_signature = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError) as exc:
                raise SchemaExtractionError('schema_goal_invalid') from exc
            if kind == 'product' and len(entities) == 1 and knowledge is not None:
                from daq_fae.knowledge.identity_selectors import composite_identity, composite_name_present
                identity = composite_identity(entities[0], knowledge.records_for(role), allow_full=True)
                if identity is not None:
                    named_now = composite_name_present(entities[0], question)
                    if candidate.get('topic_switch') is True and not named_now:
                        raise SchemaExtractionError('entity_not_grounded')
                    if not named_now and not composite_name_present(entities[0], prior_user_context):
                        raise SchemaExtractionError('entity_not_grounded')
                    identity_context = (question if named_now else
                                        prior_user_context + '\n' + question)
                    if composite_identity(entities[0], knowledge.records_for(role), allow_full=True,
                                          context_text=identity_context) is None:
                        raise SchemaExtractionError('resolution_variant_not_user_variant')
                    selector = identity['scope']['resolution_variant']
                    if (_normalized(selector) in _normalized(entities[0])
                            and selector not in explicit_versions):
                        raise SchemaExtractionError('resolution_variant_not_user_variant')
                    if ((conditions.get('resolution_variant') is not None and
                         _normalized(str(conditions['resolution_variant'])) != selector)
                            or _normalized(str(conditions.get('variant', selector))) not in {
                                selector, selector.split('x')[0]}):
                        raise SchemaExtractionError('schema_variant_alias_conflict')
                    entities = [identity['data']['name']]
                    conditions = {k: v for k, v in conditions.items() if k != 'variant'}
                    conditions['resolution_variant'] = selector
                    explicit_versions.add(selector)
            if kind == 'product' and 'variant' in conditions:
                reviewed_variant = _reviewed_width_variant_for_goal(
                    conditions['variant'], entities, short_current, knowledge, role)
                if reviewed_variant is not None:
                    if conditions.get('resolution_variant') not in (None, reviewed_variant):
                        raise SchemaExtractionError('schema_variant_alias_conflict')
                    conditions = {key: value for key, value in conditions.items()
                                  if key != 'variant'}
                    conditions['resolution_variant'] = reviewed_variant
            conditions = _normalize_operating_conditions(capability, conditions,
                                                         question)
            if (capability == 'lookup_procedure' and attachment_present
                    and re.search(r'证明|认定|\bprove\b|\bevidence\b', question, re.I)
                    and not re.search(r'如何|怎么|步骤|\bhow\s+to\b|\bprocedure\b',
                                      question, re.I)):
                steps = conditions.get('steps')
                if (isinstance(steps, list)
                        and any(step in _PROCEDURE_STEP_CODES for step in steps)):
                    # A model can confuse an observed state (for example file
                    # write completion) with an action in a procedure. A
                    # picture-proof question only needs the reviewed action
                    # as comparison evidence. Unknown operational actions
                    # remain unmapped and cannot gain authority here.
                    conditions['steps'] = [
                        step for step in steps
                        if (not isinstance(step, str) or
                            not re.search(r'_(?:complete|saved|success|finished)$',
                                          step, re.I) or
                            step in _PROCEDURE_STEP_CODES)]
            conditions = _normalize_encoded_sdk_selectors(
                capability, conditions, question + ' ' + prior_user_context)
            if (capability == 'lookup_procedure'
                    and conditions.get('host_connection') == 'none'
                    and not _operating_condition_grounded(
                        'host_connection', 'none', question + ' ' + prior_user_context)):
                # Independent capture alone does not assert that the user has
                # ruled out a computer or phone data host. Drop this model
                # inference instead of making it an unfillable evidence goal.
                conditions.pop('host_connection')
            if (capability == 'lookup_procedure'
                    and conditions.get('recording_mode') == 'local_tf_card'
                    and not _operating_condition_grounded(
                        'recording_mode', 'local_tf_card',
                        question + ' ' + prior_user_context)):
                # Mentioning data on a TF card does not assert that the user
                # performed the source's standalone local-recording mode.
                conditions.pop('recording_mode')
            if capability == 'resolve_entity' and type(conditions.get('standalone')) is bool:
                words = question + ' ' + prior_user_context
                pattern = (r'单机|独立|脱机|standalone|single.device'
                           if conditions['standalone'] else
                           r'非单机|不能单机|不独立|非独立|not.standalone|multi.device')
                if not re.search(pattern, words, re.I):
                    # Standalone mode is task context, not product identity;
                    # discard a model default only when the user never said it.
                    conditions.pop('standalone')
            if (capability == 'catalog' and conditions.get('standalone') is False
                    and not re.search(r'非单机|不能单机|不支持单机|without standalone',
                                      question + ' ' + prior_user_context, re.I)):
                # A model default is not a user requested catalog filter.
                conditions.pop('standalone')
            conditions = _normalize_spec_conditions(
                capability, conditions, question + ' ' + prior_user_context)
            conditions = _normalize_sdk_conditions(
                capability, conditions, question + ' ' + prior_user_context,
                sdk_catalog, current_question=question)
            if (capability == 'check_software_support'
                    and isinstance(conditions.get('sdk'), str)
                    and _normalized(conditions['sdk']) not in _normalized(
                        question + ' ' + prior_user_context)):
                # A model cannot invent a named SDK for a compatibility
                # request. A Java Wrapper question, for example, does not
                # authorize a separate OrbbecSDK selector.
                conditions.pop('sdk')
            if (capability == 'check_software_support'
                    and firmware_versions_compared_without_current(question)):
                conditions.pop('firmware_version', None)
            if (capability == 'sdk_evidence' and
                    any(_normalized(entity) == '网络ego' for entity in entities)):
                # "网络 EGO" names an EGO connection in an SDK usage question,
                # not a separate product. Preserve network as an exact usage
                # selector; the separate support goal keeps device identity.
                if any(conditions.get(key) not in (None, 'network')
                       for key in ('connection', 'connection_mode')):
                    raise SchemaExtractionError('schema_network_ego_connection_conflict')
                entities = ['EGO' if _normalized(entity) == '网络ego' else entity
                            for entity in entities]
                conditions.pop('connection_mode', None)
                conditions['connection'] = 'network'
            api = conditions.get('api')
            if (capability == 'sdk_evidence' and isinstance(api, str)
                    and not re.search(r'(?<![A-Za-z0-9_])' + re.escape(api) +
                                      r'(?![A-Za-z0-9_])', question + ' ' + prior_user_context, re.I)
                    and isinstance(sdk_catalog, SdkCatalog)
                    and sdk_catalog.grounds_selector(question, 'task', api)):
                # The model may name a user-described operation as if it were
                # an API symbol. An existing task can express the same
                # operation, so discard the redundant pseudo-API only when
                # both descriptors bind the same source-checked usage row.
                # Explicitly named APIs still require an exact source symbol.
                task = conditions.get('task')
                if task is None:
                    conditions['task'] = conditions.pop('api')
                elif isinstance(task, str) and any(
                        sdk_usage_descriptor_matches(api, row)
                        and sdk_usage_descriptor_matches(task, row)
                        for row in sdk_catalog.search(question, 'internal_fae')):
                    conditions.pop('api')
            if capability == 'search_knowledge':
                note_question = bool(re.search(r'日志|变更|发布说明|release.note|changelog',
                                               question, re.I))
                dated_note = note_question and any(key in conditions for key in (
                    'viewer_version', 'viewer_release_date', 'firmware_version',
                    'sdk_release_date', 'sdk_version'))
                # The full user wording is searched against a reviewed note.
                # A model paraphrase of its subject is not a dated selector.
                # Keep the version/build here; the separate support goal owns
                # the specific device combination and operating platform.
                note_feature = conditions.get('feature')
                if dated_note and (not isinstance(note_feature, str) or
                                   note_feature not in {
                                       'network_device_enumeration',
                                       'master_slave_sync', 'magnetometer'}):
                    conditions.pop('feature', None)
                if (dated_note and 'viewer_version' in conditions
                        and conditions.get('platform') == 'desktop'):
                    conditions.pop('platform')
                if (dated_note and 'viewer_version' in conditions
                        and any(isinstance(peer, dict) and
                                peer.get('capability') == 'check_software_support'
                                for peer in raw_goals)
                        and entities and all(not re.search(r'viewer', entity, re.I)
                                             for entity in entities)):
                    entities = []
                document_type = conditions.get('document_type')
                if (isinstance(document_type, str)
                        and _normalized(document_type) in {
                            'changelog', 'release_note', 'release_notes', '变更日志'}
                        and re.search(r'变更日志|更新说明|发布说明|changelog|release.note',
                                      question + ' ' + prior_user_context, re.I)):
                    # The selected knowledge goal already asks for a release
                    # note. This model-generated alias adds no source scope.
                    conditions.pop('document_type')
                if (conditions.get('aspect') == 'master_slave_sync' and
                        re.search(r'主从|同步|master.slave|sync',
                                  question + ' ' + prior_user_context, re.I)):
                    conditions.pop('aspect')
                    if 'feature' in conditions and conditions['feature'] != 'master_slave_sync':
                        raise SchemaExtractionError('schema_note_feature_conflict')
                    conditions['feature'] = 'master_slave_sync'
                if (any(re.search(r'viewer', entity, re.I) for entity in entities)
                        and _normalized(str(conditions.get('sdk', ''))) == 'egoviewer'):
                    conditions.pop('sdk')
                    for old, new in (('sdk_version', 'viewer_version'),
                                     ('sdk_release_date', 'viewer_release_date')):
                        if old in conditions:
                            value = conditions.pop(old)
                            if new in conditions and conditions[new] != value:
                                raise SchemaExtractionError('schema_viewer_version_conflict')
                            conditions[new] = value
            if (capability == 'sdk_evidence' and
                    _normalized(str(conditions.get('sdk', ''))) == 'libobkit' and
                    entities == ['EGO'] and re.search(
                        r'四目\s*EGO', question + ' ' + prior_user_context, re.I)):
                entities = ['四目 EGO']
            if capability == 'search_knowledge' and (
                    'build_date' in conditions or 'build' in conditions):
                date_key = ('viewer_release_date' if 'viewer_version' in conditions
                            or any(re.search(r'viewer', entity, re.I)
                                   for entity in entities)
                            else 'sdk_release_date' if 'sdk' in conditions else None)
                if date_key:
                    date = conditions.pop('build_date', None)
                    if date is None:
                        date = conditions.pop('build')
                    if date_key in conditions and conditions[date_key] != date:
                        raise SchemaExtractionError('schema_release_date_conflict')
                    conditions[date_key] = date
            if (capability == 'sdk_evidence' and conditions.get('source_files')
                    and entities and re.search(
                        r'包(?:里|中|内)|\bpackage\b|\barchive\b',
                        question + ' ' + prior_user_context, re.I)
                    and any(isinstance(peer, dict)
                            and peer.get('capability') == 'check_software_support'
                            and isinstance(peer.get('entities'), list)
                            and set(entities) <= set(peer['entities'])
                            for peer in raw_goals)):
                # A package member is SDK evidence. The separate support goal
                # retains the device selector; it must not leak into this goal.
                entities = []
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
                if ((aggregate_power_asked and not aggregate_port_counts)
                        or (field == 'total_downstream_port_power_output' and
                            qualitative_power_feasibility and not aggregate_power_asked)) \
                        and model_count is not None:
                    # A qualitative total question contains no simultaneous
                    # load count. The extractor's inferred count has no user
                    # authority and must not become a measurement condition.
                    conditions.pop('simultaneous_port_count', None)
                    conditions.pop('port_count', None)
                    model_count = None
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
                elif (field == 'total_downstream_port_power_output' and
                      not aggregate_power_asked and not qualitative_power_feasibility):
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
            if kind == 'product' and any(
                    isinstance(value, str) and
                    re.fullmatch(r'\d{3,4}x\d{3,4}', _normalized(value)) and
                    _normalized(value) not in explicit_versions
                    for value in variant_values):
                # The question may mention a resolution as an output mode.
                # Its mere presence cannot authorize a reviewed SKU selector.
                unmapped.add('resolution_variant')
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
            if not has_stereo:
                # Camera channel qualifiers govern optical specifications.
                # An invented stereo default cannot abort unrelated goals,
                # including RGB specifications that never requested stereo.
                camera_config = None
                stereo = None
            if camera_config is not None:
                if camera_config != 'stereo' or not has_stereo:
                    raise SchemaExtractionError('camera_config_not_grounded')
                stereo = True
            if (isinstance(stereo, str) and stereo.casefold() in {'front', 'forward'}
                    and has_stereo
                    and re.search(r'前向|前方|\bfront\b|\bforward\b',
                                  user_words, re.I)):
                # The model used a boolean stereo slot to carry orientation.
                # Preserve the user's front-pair condition as an unmet scope
                # selector; no published value may satisfy it by omission.
                conditions['direction'] = 'front'
                unmapped.add('direction')
                stereo = True
            if stereo is False:
                # A false default from extraction is not a user selector.
                # Keep explicit stereo wording and any other answerable goals
                # instead of failing the whole request.
                stereo = None
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
                if (key == 'task' and capability in {'inspect_topology', 'lookup_procedure'}
                        and isinstance(value, str) and _SAFE_CODE.fullmatch(value)):
                    # This is the model's query intent, not a physical user
                    # parameter. Actual published task_intents must still
                    # match it; a label alone never establishes a capability.
                    unmapped.discard(key)
                    continue
                if (key == 'resolution_variant'
                        and isinstance(value, str)
                        and _normalized(value) in explicit_versions):
                    # Explicit short EGO labels were expanded to the unique
                    # reviewed variant selector above; the full selector text
                    # need not appear verbatim in the user's question.
                    unmapped.discard(key)
                    continue
                operating_grounded = _operating_condition_grounded(
                    key, value, user_words)
                if operating_grounded is not None:
                    if operating_grounded:
                        unmapped.discard(key)
                    else:
                        unmapped.add(key)
                    continue
                if (capability == 'sdk_evidence' and key in {'feature', 'task'}
                        and isinstance(sdk_catalog, SdkCatalog)
                        and sdk_catalog.grounds_selector(
                            question if candidate.get('topic_switch') is True
                            else user_words, key, value, scope=conditions)):
                    unmapped.discard(key)
                    continue
                if (capability == 'search_knowledge' and key == 'feature'
                        and isinstance(value, str) and (
                            (value == 'network_device_enumeration' and
                             re.search(r'网络设备枚举|network.device.enumerat', user_words, re.I))
                            or (value == 'master_slave_sync' and
                                re.search(r'主从|同步|master.slave|sync', user_words, re.I))
                            or (value == 'magnetometer' and
                                re.search(r'磁力计|magnetometer', user_words, re.I)))):
                    continue
                if (capability == 'check_software_support' and key == 'feature'
                        and value == 'bluetooth' and re.search(
                            r'蓝牙|\b(?:BLE|bluetooth)\b', user_words, re.I)):
                    continue
                if (capability == 'sdk_evidence' and key == 'sdk'
                        and _sdk_name_grounded(value, user_words)):
                    continue
                if (capability == 'sdk_evidence' and key == 'connection'
                        and value == 'network'
                        and re.search(r'网络|\bnetwork\b', user_words, re.I)):
                    continue
                if (capability in {'sdk_evidence', 'check_software_support'}
                        and key == 'platform'
                        and isinstance(value, str) and (
                            (value == 'device_side' and re.search(r'设备端|device.side', user_words, re.I))
                            or (value == 'host_side' and re.search(r'主机侧|host.side', user_words, re.I))
                            or _sdk_platform_grounded(value, user_words))):
                    continue
                if (capability == 'sdk_evidence' and key == 'language'
                        and value == 'cpp'
                        and re.search(r'\bC\+\+\b|\bC\+\+(?!\w)', user_words, re.I)):
                    continue
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
            elif (kind == 'product' and capability == 'sdk_evidence'
                    and isinstance(conditions.get('source_files'), list)
                    and len(conditions['source_files']) > 1
                    and all(isinstance(name, str)
                            and re.search(r'(?<![A-Za-z0-9_.-])'
                                          + re.escape(name)
                                          + r'(?![A-Za-z0-9_.-])', question, re.I)
                            for name in conditions['source_files'])):
                scoped_conditions = [
                    _normalize_sdk_conditions(
                        capability, {**conditions, 'source_files': [name]},
                        user_words, sdk_catalog, current_question=question)
                    for name in dict.fromkeys(conditions['source_files'])
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
        # A request for a named build system is a separate obligation from
        # the package's reviewed build instructions. Preserve the unreviewed
        # CMake method while exposing a unique Makefile source as useful
        # partial evidence for the same device-side package.
        libobkit_cmake = re.search(
            r'\blibobkit\b[^。？！?；;\n]{0,70}\bCMake\b', question, re.I)
        if (libobkit_cmake and re.search(r'\bRK3576\b', question, re.I)
                and re.search(r'编译|安装|\b(?:build|install|compile)\b',
                              question, re.I)
                and isinstance(sdk_catalog, SdkCatalog)):
            requested_build = re.search(
                r'\blibobkit\b[^。？！?；;\n]{0,25}(20\d{6})', question, re.I)
            build_rows = [row for row in sdk_catalog.records
                          if row.get('sdk_layer') == 'device_c'
                          and row.get('doc_type') == 'build'
                          and 'build' in row.get('usage_tasks', [])
                          and 'rk3576' in str(row.get('platform', '')).casefold()
                          and row.get('source_refs')
                          and (not requested_build
                               or row.get('build') == requested_build.group(1))]
            if len(build_rows) == 1:
                shared_scope = {'sdk': 'libobkit', 'platform': 'rk3576'}
                if requested_build:
                    build = requested_build.group(1)
                    shared_scope['sdk_release_date'] = (
                        f'{build[:4]}-{build[4:6]}-{build[6:]}')
                for scoped, unmapped in (
                    ({**shared_scope, 'task': 'cmake_build_install'}, ('task',)),
                    ({**shared_scope, 'task': 'build'}, ())):
                    if any(goal.capability == 'sdk_evidence'
                           and {key: str(value).casefold() for key, value
                                in goal.condition_map().items()}
                           == {key: str(value).casefold() for key, value
                               in scoped.items()} for goal in goals):
                        continue
                    payload = ['product', 'sdk_evidence', [], None, scoped]
                    goal_id = 'goal_' + hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True
                    ).encode()).hexdigest()[:16]
                    if goal_id not in ids:
                        ids.add(goal_id)
                        goals.append(DaqGoal(
                            goal_id, 'product', 'sdk_evidence', (), None,
                            tuple(sorted(scoped.items())), unmapped))
                if len(goals) > 16:
                    raise SchemaExtractionError('schema_goals_expansion_limit')
        # A reviewed two-YAML export remains answerable when the user adds
        # an unreviewed extrinsic JSON output. Keep the JSON as a separate
        # unknown obligation instead of letting it hide the known example.
        calibration_scope = calibration_export_known_scope(question)
        if calibration_scope != question and isinstance(sdk_catalog, SdkCatalog):
            rows = [row for row in sdk_catalog.search(
                calibration_scope, 'internal_fae', include_all=True)
                if calibration_export_row_matches_question(row, calibration_scope)]
            if len(rows) == 1:
                known = {'language': 'cpp', 'task': 'calibration_export'}
                if not any(goal.capability == 'sdk_evidence'
                           and goal.condition_map().get('task') == 'calibration_export'
                           for goal in goals):
                    payload = ['product', 'sdk_evidence', ['EGO'], None, known]
                    goal_id = 'goal_' + hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                    if goal_id not in ids:
                        ids.add(goal_id)
                        goals.append(DaqGoal(goal_id, 'product', 'sdk_evidence',
                                             ('EGO',), None,
                                             tuple(sorted(known.items())), ()))
                if not any(goal.capability == 'sdk_evidence'
                           and 'json' in str(goal.condition_map().get(
                               'export_files', '')).casefold()
                           for goal in goals):
                    extra = {'export_files': ['extrinsic_calibration_json']}
                    payload = ['product', 'sdk_evidence', ['EGO'], None, extra]
                    goal_id = 'goal_' + hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                    if goal_id not in ids:
                        ids.add(goal_id)
                        goals.append(DaqGoal(
                            goal_id, 'product', 'sdk_evidence', ('EGO',), None,
                            tuple(sorted(extra.items())), ('export_files',)))
                if len(goals) > 16:
                    raise SchemaExtractionError('schema_goals_expansion_limit')
        # A current-turn correction supersedes a model goal copied from the
        # previous version. Preserve the newly requested full inventory as
        # unresolved even if the model omitted it entirely.
        current_package_version = _current_orbbec_package_version(question)
        if (current_package_version
                and re.search(r'不是我要的|不用了|不要|取消|\b(?:not|cancel)\b',
                              question, re.I)):
            stale = [goal for goal in goals
                     if goal.capability == 'sdk_evidence'
                     and goal.condition_map().get('sdk') == 'OrbbecSDK'
                     and isinstance(goal.condition_map().get('sdk_version'), str)
                     and goal.condition_map()['sdk_version'] != current_package_version
                     and (goal.condition_map().get('task') == 'inspect_package'
                          or set(goal.condition_map())
                          <= {'sdk', 'sdk_version', 'sdk_release_date'})]
            for goal in stale:
                goals.remove(goal)
                ids.discard(goal.id)
            if stale and not any(goal.capability == 'sdk_evidence'
                                 and goal.condition_map().get('sdk') == 'OrbbecSDK'
                                 and goal.condition_map().get('sdk_version')
                                 == current_package_version
                                 and goal.condition_map().get('task') == 'inspect_package'
                                 for goal in goals):
                scoped = {'sdk': 'OrbbecSDK',
                          'sdk_version': current_package_version,
                          'task': 'inspect_package'}
                payload = ['product', 'sdk_evidence', [], None, scoped]
                goal_id = 'goal_' + hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                if goal_id not in ids:
                    ids.add(goal_id)
                    goals.append(DaqGoal(goal_id, 'product', 'sdk_evidence',
                                         (), None, tuple(sorted(scoped.items())),
                                         ('task',)))
        # A broad package inventory is not in the source-checked SDK
        # catalog. Keep that request unknown while separately exposing the
        # verified example/build records for the named SDK version.
        if (isinstance(sdk_catalog, SdkCatalog)
                and re.search(r'包内容|包里有什么|package\s+(?:contents?|files?)',
                              question, re.I)
                and not re.search(r'(?<![A-Za-z0-9_])(?:linux|ubuntu|windows|'
                                  r'android|ios|macos|arm64|aarch64)'
                                  r'(?![A-Za-z0-9_])', question, re.I)):
            broad = [goal for goal in goals
                     if goal.capability == 'sdk_evidence'
                     and goal.condition_map().get('sdk') == 'OrbbecSDK'
                     and goal.condition_map().get('task') == 'inspect_package'
                     and 'task' in goal.unmapped_condition_keys
                     and 'sdk_version' not in goal.unmapped_condition_keys
                     and _current_orbbec_package_version(question)
                     == goal.condition_map().get('sdk_version')
                     and isinstance(goal.condition_map().get('sdk_version'), str)]
            for goal in broad:
                scoped = {'sdk': 'OrbbecSDK',
                          'sdk_version': goal.condition_map()['sdk_version']}
                if not any(row.get('sdk_layer') == 'host_cpp'
                           and row.get('version') == scoped['sdk_version']
                           and row.get('source_refs')
                           for row in sdk_catalog.records):
                    continue
                payload = ['product', 'sdk_evidence', [], None, scoped]
                goal_id = 'goal_' + hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                if goal_id not in ids:
                    ids.add(goal_id)
                    goals.append(DaqGoal(goal_id, 'product', 'sdk_evidence', (),
                                         None, tuple(sorted(scoped.items())), ()))
                    if len(goals) > 16:
                        raise SchemaExtractionError('schema_goals_expansion_limit')
        # In a "respectively" question, a second library is its own task.
        # A model must not turn its name into a feature of the first SDK
        # unless the user actually asked about integration between them.
        if (re.search(r'分别|\brespectively\b', question, re.I)
                and not re.search(r'对接|结合|交给|接入|输出到|联动|'
                                  r'\b(?:integrat|connect|bridge)\w*\b',
                                  question, re.I)):
            named_audio = {name.casefold() for name in
                           audio_playback_subjects(question)}
            for goal in tuple(goals):
                conditions = goal.condition_map()
                feature = str(conditions.get('feature', '')).casefold()
                sdk = str(conditions.get('sdk', '')).casefold()
                api = conditions.get('api')
                api_names = {str(value).casefold() for value in
                             (api if isinstance(api, list) else [api]) if value}
                if (goal.capability == 'sdk_evidence'
                        and ((feature in named_audio and feature != sdk)
                             or any(name in named_audio and name != sdk
                                    for name in api_names))
                        and (conditions.get('task') == 'audio_playback'
                             or conditions.get('feature') == 'audio_playback')):
                    goals.remove(goal)
                    ids.remove(goal.id)
        libobkit_any_firmware = bool(
            re.search(r'\blibobkit(?:\.so)?\b', question, re.I)
            and re.search(r'(?:任意|任何|其他|不同|未配套|非配套)'
                          r'[^。？！?；;\n]{0,20}固件|'
                          r'固件[^。？！?；;\n]{0,20}'
                          r'(?:上用|使用|兼容|支持)', question))
        # A capture example and its package README are separate deliverables.
        # The README can prove image binding for this exact package, while the
        # device/firmware compatibility question remains unresolved.
        if (isinstance(sdk_catalog, SdkCatalog)
                and libobkit_any_firmware
                and device_sdk_scope_matches_question(question)
                and not re.search(r'\b[A-Za-z][A-Za-z0-9_-]*SDK\b|'
                                  r'\b(?:OpenCV|EgoLowBle|EgoViewer)\b',
                                  question, re.I)):
            capture_rows = [row for row in sdk_catalog.records
                if row.get('sdk_layer') == 'device_c'
                and 'capture' in row.get('usage_tasks', [])
                and row.get('source_refs')]
            has_capture_goal = any(
                goal.capability == 'sdk_evidence'
                and goal.condition_map().get('sdk') == 'libobkit'
                and goal.condition_map().get('task') == 'capture'
                for goal in goals)
            has_support_goal = any(
                goal.capability == 'check_software_support'
                and goal.condition_map().get('sdk') == 'libobkit'
                for goal in goals)
            requested_builds = set(re.findall(r'(?<!\d)20\d{6}(?!\d)', question))
            scope_rows = [row for row in sdk_catalog.records
                          if ((has_capture_goal and len(capture_rows) == 1
                               and row.get('package') == capture_rows[0].get('package'))
                              or (not has_capture_goal and has_support_goal
                                  and (not requested_builds
                                       or str(row.get('build')) in requested_builds)))
                          and row.get('sdk_layer') == 'device_c'
                          and 'sdk_scope' in row.get('usage_tasks', [])
                          and 'image_binding' in row.get('topics', [])
                          and any(str(ref.get('member', '')).endswith('/README.md')
                                  for ref in row.get('source_refs', []))]
            has_scope_goal = any(
                goal.capability == 'sdk_evidence'
                and goal.condition_map().get('sdk') == 'libobkit'
                and goal.condition_map().get('task') == 'sdk_scope'
                for goal in goals)
            if ((has_capture_goal or has_support_goal)
                    and not has_scope_goal and len(scope_rows) == 1):
                scoped = {'sdk': 'libobkit', 'task': 'sdk_scope'}
                payload = ['product', 'sdk_evidence', [], None, scoped]
                goal_id = 'goal_' + hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                if goal_id not in ids:
                    ids.add(goal_id)
                    goals.append(DaqGoal(
                        goal_id, 'product', 'sdk_evidence', (), None,
                        tuple(sorted(scoped.items())), ()))
                    if len(goals) > 16:
                        raise SchemaExtractionError('schema_goals_expansion_limit')
        # Package image binding is not proof that the SDK works on every
        # firmware of a named device. Preserve the compatibility obligation
        # when a model supplied only the README/package goal.
        if (libobkit_any_firmware
                and any(goal.capability == 'sdk_evidence'
                        and goal.condition_map().get('sdk') == 'libobkit'
                        for goal in goals)
                and not any(goal.capability == 'check_software_support'
                            and goal.condition_map().get('sdk') == 'libobkit'
                            for goal in goals)):
            entities = (('EGO Pro',) if re.search(
                r'\bEGO\s*Pro\b', question, re.I) else ())
            scoped = {'sdk': 'libobkit', 'firmware_version': 'any'}
            payload = ['product', 'check_software_support',
                       sorted(entities), None, scoped]
            goal_id = 'goal_' + hashlib.sha256(json.dumps(
                payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
            if goal_id not in ids:
                ids.add(goal_id)
                goals.append(DaqGoal(
                    goal_id, 'product', 'check_software_support',
                    entities, None, tuple(sorted(scoped.items())),
                    ('firmware_version',)))
                if len(goals) > 16:
                    raise SchemaExtractionError('schema_goals_expansion_limit')
        # A model may collapse a comparison into one reviewed SDK goal. The
        # other named library remains a separate, unresolved evidence task.
        for audio_subject in audio_playback_subjects(question):
            subject_goals = [goal for goal in goals
                             if goal.capability == 'sdk_evidence'
                             and str(goal.condition_map().get('sdk', '')).casefold()
                             == audio_subject.casefold()]
            if any(goal.condition_map().get('feature') == 'audio_playback'
                   or goal.condition_map().get('task') == 'audio_playback'
                   for goal in subject_goals):
                continue
            generic = next((goal for goal in subject_goals
                            if set(goal.condition_map()) <= {
                                'sdk', 'sdk_version', 'sdk_release_date', 'platform'}), None)
            if generic is not None:
                scoped = _normalize_sdk_conditions(
                    'sdk_evidence',
                    {**generic.condition_map(), 'feature': 'audio_playback'},
                    question, sdk_catalog, current_question=question)
                payload = [generic.kind, generic.capability,
                           sorted(generic.entities), generic.field, scoped]
                goal_id = 'goal_' + hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                ids.remove(generic.id)
                ids.add(goal_id)
                goals[goals.index(generic)] = DaqGoal(
                    goal_id, generic.kind, generic.capability, generic.entities,
                    generic.field, tuple(sorted(scoped.items())),
                    tuple(sorted(set(generic.unmapped_condition_keys)
                                 | ({'feature'} if audio_subject != 'OrbbecSDK'
                                    else set()))))
                continue
            scoped = _normalize_sdk_conditions(
                'sdk_evidence',
                {'sdk': audio_subject, 'feature': 'audio_playback'},
                question, sdk_catalog, current_question=question)
            payload = ['product', 'sdk_evidence', [], None, scoped]
            goal_id = 'goal_' + hashlib.sha256(json.dumps(
                payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
            if goal_id not in ids:
                ids.add(goal_id)
                goals.append(DaqGoal(goal_id, 'product', 'sdk_evidence', (), None,
                                     tuple(sorted(scoped.items())),
                                     ('feature', 'sdk') if audio_subject != 'OrbbecSDK'
                                     else ()))
                if len(goals) > 16:
                    raise SchemaExtractionError('schema_goals_expansion_limit')
        # A comparison needs one obligation per version. The model sometimes
        # emits a single generic audio goal even when the user explicitly
        # names two OrbbecSDK releases; one source cannot prove equality.
        audio_goals = [goal for goal in goals
                       if goal.capability == 'sdk_evidence'
                       and goal.condition_map().get('sdk') == 'OrbbecSDK'
                       and (goal.condition_map().get('feature') == 'audio_playback'
                            or goal.condition_map().get('task') == 'audio_playback')]
        audio_versions = _compared_audio_sdk_versions(question)
        if audio_goals and audio_versions:
            unversioned = [goal for goal in audio_goals
                           if not goal.condition_map().get('sdk_version')]
            template = (unversioned or audio_goals)[0]
            for goal in unversioned:
                goals.remove(goal)
                ids.remove(goal.id)
            represented = {goal.condition_map().get('sdk_version')
                           for goal in goals if goal.capability == 'sdk_evidence'
                           and goal.condition_map().get('sdk') == 'OrbbecSDK'
                           and (goal.condition_map().get('feature') == 'audio_playback'
                                or goal.condition_map().get('task') == 'audio_playback')}
            for version in audio_versions:
                if version in represented:
                    continue
                scoped = {**template.condition_map(), 'sdk_version': version}
                payload = [template.kind, template.capability,
                           sorted(template.entities), template.field, scoped]
                goal_id = 'goal_' + hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                if goal_id not in ids:
                    ids.add(goal_id)
                    goals.append(DaqGoal(
                        goal_id, template.kind, template.capability,
                        template.entities, template.field, tuple(sorted(scoped.items())),
                        template.unmapped_condition_keys))
            if len(goals) > 16:
                raise SchemaExtractionError('schema_goals_expansion_limit')
        # Preserve every explicitly compared SDK package scope even when the
        # model returns one generic goal. A row may satisfy only its own cell.
        comparison_marker = (
            r'分别|都|对比|各|每个|每种|'
            r'\b(?:respectively|both|compare|each|every)\b')
        comparison_clauses = [clause for clause in re.split(
            r'[。？！?；;\n]+', question)
            if re.search(r'(?<![A-Za-z0-9_])OrbbecSDK(?![A-Za-z0-9_])',
                         clause, re.I)
            and re.search(comparison_marker, clause, re.I)]
        sdk_clauses = [clause for clause in re.split(
            r'[。？！?；;\n]+', question)
            if re.search(r'(?<![A-Za-z0-9_])OrbbecSDK(?![A-Za-z0-9_])',
                         clause, re.I)]
        comparison_question = (comparison_clauses[0]
                               if len(comparison_clauses) == 1 else '')
        comparison = bool(comparison_question)
        magnetometer_presence = bool(
            re.search(r'磁力计|magnetometer', comparison_question, re.I)
            and re.search(r'示例|\b(?:example|sample)\b',
                          comparison_question, re.I)
            and re.search(r'包|\bpackage\b', comparison_question, re.I)
            and not re.search(r'编译|运行|\b(?:compile|run)\b',
                              comparison_question, re.I))
        sdcard_download = bool(
            re.search(r'SD\s*卡|\bsd.?card\b', comparison_question, re.I)
            and re.search(r'下载|\bdownload\b', comparison_question, re.I))
        subject = ('magnetometer' if magnetometer_presence and not sdcard_download
                   else 'sd_card_file_download' if sdcard_download
                   and not magnetometer_presence else None)
        if (comparison and subject and isinstance(sdk_catalog, SdkCatalog)
                and not re.search(r'\blibobkit\b|\bEgoLowBLE\b',
                                  comparison_question, re.I)):
            platform_mentions = [(match.start(), name)
                                 for name, pattern in (
                ('linux_x86_64', r'\blinux\s*x86_64\b'),
                ('android', r'\bandroid\b'),
                ('windows_x64', r'\bwindows\s*x64\b'))
                for match in re.finditer(pattern, comparison_question, re.I)]
            platforms = list(dict.fromkeys(
                name for _, name in sorted(platform_mentions)))
            versions = list(_compared_orbbec_package_versions(
                comparison_question))
            if not versions:
                single_version = list(dict.fromkeys(
                    _ORBBEC_OWNED_VERSION.findall(comparison_question)))
                if len(single_version) == 1:
                    versions = single_version
            builds = list(dict.fromkeys(
                f'{date[:4]}-{date[4:6]}-{date[6:]}'
                for date in (re.sub(r'\D', '', item) for item in re.findall(
                    r'(?<!\d)20\d{2}[-/]?\d{2}[-/]?\d{2}(?!\d)',
                    comparison_question))))
            axes = [(key, values) for key, values in (
                ('platform', platforms), ('sdk_version', versions),
                ('sdk_release_date', builds)) if len(values) > 1]
            subject_goals = [goal for goal in goals
                             if goal.capability == 'sdk_evidence'
                             and not goal.condition_map().get('source_files')
                             and goal.condition_map().get('sdk') == 'OrbbecSDK'
                             and (goal.condition_map().get('feature') == subject
                                  or (subject == 'magnetometer'
                                      and goal.condition_map().get('task')
                                      == 'inspect_package')
                                  or (subject == 'sd_card_file_download'
                                      and goal.condition_map().get('task')
                                      in {'download', 'file_download',
                                          'sd_card_file_download'})
                                  or (len(sdk_clauses) == 1
                                      and set(goal.condition_map()) <= {
                                      'sdk', 'sdk_version', 'sdk_release_date',
                                      'platform'}))]
            if axes and subject_goals:
                template = subject_goals[0]
                for goal in subject_goals:
                    goals.remove(goal)
                    ids.remove(goal.id)
                scope_axes = {key: values for key, values in axes}
                # A single explicit selector applies to each compared cell.
                if len(platforms) == 1:
                    scope_axes['platform'] = platforms
                if len(versions) == 1:
                    scope_axes['sdk_version'] = versions
                if len(builds) == 1:
                    scope_axes['sdk_release_date'] = builds
                explicit_cross_product = bool(re.search(
                    r'每个版本|各版本|每个构建|各构建|每个平台|各平台|'
                    r'每种版本|\b(?:each|every)\s+(?:version|build|platform)\b',
                    comparison_question, re.I))
                if (len(axes) > 1 and not explicit_cross_product
                        and len({len(values) for _, values in axes}) == 1):
                    paired_axes = {key: values for key, values in scope_axes.items()
                                   if len(values) > 1}
                    fixed_axes = {key: values[0] for key, values in
                                  scope_axes.items() if len(values) == 1}
                    combinations = (
                        {**dict(zip(paired_axes, values)), **fixed_axes}
                        for values in zip(*paired_axes.values()))
                else:
                    combinations = (dict(zip(scope_axes, values))
                                    for values in product(*scope_axes.values()))
                for selected_scope in combinations:
                    cell_goals = [goal for goal in subject_goals
                                  if all(goal.condition_map().get(key)
                                         == selected_scope[key]
                                         for key, _ in axes)]
                    for source_goal in (cell_goals or [None]):
                        owner = source_goal or template
                        scoped = ({key: value for key, value in
                                   source_goal.condition_map().items()
                                   if key not in {'feature', 'task'}}
                                  if source_goal else {'sdk': 'OrbbecSDK'})
                        scoped.update(selected_scope)
                        scoped['feature'] = subject
                        if subject == 'magnetometer':
                            scoped['task'] = 'inspect_package'
                        platform_label = {
                            'linux_x86_64': 'Linux x86_64',
                            'windows_x64': 'Windows x64',
                        }.get(scoped.get('platform'), scoped.get('platform', ''))
                        scoped_question = ' '.join(filter(None, (
                            'OrbbecSDK', str(scoped.get('sdk_version', '')),
                            str(scoped.get('sdk_release_date', '')),
                            str(platform_label),
                            ('包里有磁力计示例吗？' if subject == 'magnetometer'
                             else 'SD 卡文件下载怎么做？'))))
                        scoped = _normalize_sdk_conditions(
                            'sdk_evidence', scoped, scoped_question, sdk_catalog,
                            current_question=scoped_question)
                        unmapped = (set(source_goal.unmapped_condition_keys)
                                    if source_goal else set()) - {
                            'feature', 'task', 'platform', 'sdk_version',
                            'sdk_release_date'}
                        if 'feature' in scoped and not sdk_catalog.grounds_selector(
                                scoped_question, 'feature', scoped['feature'],
                                scope=scoped):
                            unmapped.add('feature')
                        if 'task' in scoped and not sdk_catalog.grounds_selector(
                                scoped_question, 'task', scoped['task'],
                                scope=scoped):
                            unmapped.add('task')
                        entities = source_goal.entities if source_goal else ()
                        payload = [owner.kind, owner.capability,
                                   sorted(entities), owner.field, scoped]
                        goal_id = 'goal_' + hashlib.sha256(json.dumps(
                            payload, ensure_ascii=False,
                            sort_keys=True).encode()).hexdigest()[:16]
                        if goal_id not in ids:
                            ids.add(goal_id)
                            goals.append(DaqGoal(
                                goal_id, owner.kind, owner.capability,
                                entities, owner.field,
                                tuple(sorted(scoped.items())),
                                tuple(sorted(unmapped))))
                if len(goals) > 16:
                    raise SchemaExtractionError('schema_goals_expansion_limit')
        # A package example answers how to call an SDK API. A question about
        # whether a named device supports that API also needs device evidence,
        # even if the schema model returned only the package-usage goal.
        audio_goals = [goal for goal in goals
                       if goal.capability == 'sdk_evidence'
                       and goal.condition_map().get('sdk') == 'OrbbecSDK'
                       and (goal.condition_map().get('feature') == 'audio_playback'
                            or goal.condition_map().get('task') == 'audio_playback')]
        device_label = (r'[A-Za-z][A-Za-z0-9-]*'
                        r'(?:\s+[A-Za-z][A-Za-z0-9-]*){0,2}')
        device_list = (r'(' + device_label +
                       r'(?:\s*(?:和|与|及|、|\band\b)\s*' +
                       device_label + r'){0,5})')
        named_support = re.search(
            r'(?:^|[。？！?；;\n])\s*(?:请问\s*|我手上的\s*|我的\s*|'
            r'这台\s*|这款\s*)?' + device_list + r'\s*'
            r'(?:是否都支持|是否支持|能否支持|是否能用|能否使用|能否用|'
            r'都支持|均支持|能用|可以用|支持|'
            r'can\s+use|supports?)', question, re.I)
        applicable = re.search(r'(?:适用于|能否用于|能否使用|用于|支持)\s*' + device_list +
                               r'(?=\s*(?:吗|呢|[？?。；;\n]|$))', question, re.I)
        if audio_goals and (named_support or applicable):
            named_devices = [device.strip() for device in re.split(
                r'\s*(?:和|与|及|、|\band\b)\s*',
                (named_support or applicable).group(1), flags=re.I)]
            for device in dict.fromkeys(named_devices):
                if device.casefold() in {'orbbecsdk', 'sdk', 'linux', 'windows'}:
                    continue
                for usage_goal in audio_goals:
                    scoped = {'sdk': 'OrbbecSDK', 'feature': 'audio_playback'}
                    for key in ('sdk_version', 'platform'):
                        value = usage_goal.condition_map().get(key)
                        explicit_platform = (key == 'platform' and (re.search(
                            r'\b(?:linux|ubuntu|windows|win\s*(?:10|11)|'
                            r'mac(?:\s*os)?|darwin|android|ios)\b',
                            question, re.I) or sdk_cpu_architectures(question)))
                        if value and (key not in usage_goal.unmapped_condition_keys
                                      or explicit_platform):
                            scoped[key] = value
                    if any(goal.capability == 'check_software_support'
                           and goal.entities == (device,)
                           and all(goal.condition_map().get(key) == value
                                   for key, value in scoped.items())
                           for goal in goals):
                        continue
                    payload = ['product', 'check_software_support',
                               [device], None, scoped]
                    goal_id = 'goal_' + hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                    if goal_id not in ids:
                        ids.add(goal_id)
                        support_unmapped = tuple(sorted(
                            key for key in ('platform',)
                            if key in scoped and key in usage_goal.unmapped_condition_keys))
                        goals.append(DaqGoal(goal_id, 'product',
                                             'check_software_support', (device,), None,
                                             tuple(sorted(scoped.items())),
                                             support_unmapped))
                        if len(goals) > 16:
                            raise SchemaExtractionError('schema_goals_expansion_limit')
        # A single model goal cannot erase another file or build explicitly
        # requested in the current turn. Add independent source obligations;
        # each must pass the normal SDK evidence tool before coverage is full.
        sdk_file_goals = [goal for goal in goals
                          if goal.capability == 'sdk_evidence'
                          and goal.condition_map().get('source_files')]
        if sdk_file_goals:
            explicit_files = list(dict.fromkeys(re.findall(
                r'(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]*\.'
                r'(?:cpp|c|h|hpp|java|swift))(?![A-Za-z0-9_.-])',
                question, re.I)))
            file_clauses = [clause for clause in re.split(
                r'[?？。；;\n]+', question)
                if any(re.search(r'(?<![A-Za-z0-9_.-])'
                                 + re.escape(name)
                                 + r'(?![A-Za-z0-9_.-])', clause, re.I)
                       for name in explicit_files)]
            explicit_builds = list(dict.fromkeys(re.sub(r'\D', '', value)
                for clause in file_clauses
                for value in re.findall(
                    r'(?<!\d)20\d{2}[-/]?\d{2}[-/]?\d{2}(?!\d)',
                    clause)))
            explicit_versions = list(dict.fromkeys(
                value for clause in file_clauses
                for value in re.findall(
                    r'\bOrbbecSDK[_\s-]*v?(\d+\.\d+\.\d+)\b',
                    clause, re.I)))
            represented_files = {
                name for goal in sdk_file_goals
                for name in goal.condition_map().get('source_files', [])}
            represented_builds = {
                re.sub(r'\D', '', str(goal.condition_map().get(
                    'sdk_release_date', ''))) for goal in sdk_file_goals}
            represented_versions = {
                str(goal.condition_map().get('sdk_version', ''))
                for goal in sdk_file_goals}
            template = sdk_file_goals[0]
            for key, values in (
                    ('source_files', [name for name in explicit_files
                                      if name not in represented_files]),
                    ('sdk_release_date', [build for build in explicit_builds
                                          if build not in represented_builds]),
                    ('sdk_version', [version for version in explicit_versions
                                     if version not in represented_versions])):
                for value in values:
                    scoped = template.condition_map()
                    scoped[key] = ([value] if key == 'source_files' else
                                   f'{value[:4]}-{value[4:6]}-{value[6:]}'
                                   if len(value) == 8 else value)
                    payload = [template.kind, template.capability,
                               sorted(template.entities), template.field, scoped]
                    goal_id = 'goal_' + hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                    if goal_id in ids:
                        continue
                    ids.add(goal_id)
                    goals.append(DaqGoal(
                        goal_id, template.kind, template.capability,
                        template.entities, template.field,
                        tuple(sorted(scoped.items())),
                        template.unmapped_condition_keys))
                    if len(goals) > 16:
                        raise SchemaExtractionError('schema_goals_expansion_limit')
        # A question may name documented callbacks while asking whether they
        # perform an additional, undocumented action. Keep that action as an
        # unknown goal and add a separate goal for the named API definitions.
        # Only two exact user-named symbols from one reviewed row can anchor
        # this split; a fuzzy topic match cannot manufacture API coverage.
        if isinstance(sdk_catalog, SdkCatalog):
            for goal in tuple(goals):
                if (goal.capability != 'sdk_evidence'
                        or not goal.unmapped_condition_keys
                        or not set(goal.unmapped_condition_keys) <= {'task', 'feature'}):
                    continue
                anchored = []
                for row in sdk_catalog.search(question, 'internal_fae', include_all=True):
                    named = [symbol for symbol in row.get('callable_symbols', [])
                             if isinstance(symbol, str) and len(symbol) >= 6
                             and re.search(r'(?<![A-Za-z0-9_])'
                                           + re.escape(symbol)
                                           + r'(?![A-Za-z0-9_])', question, re.I)]
                    if len({symbol.casefold() for symbol in named}) >= 2:
                        anchored.append((row, named))
                if len(anchored) != 1:
                    continue
                _, named = anchored[0]
                known_conditions = {key: value for key, value in goal.conditions
                                    if key not in {'task', 'feature'}}
                apis = known_conditions.get('api')
                apis = [apis] if isinstance(apis, str) else apis
                if apis is not None and (not isinstance(apis, list) or
                        any(not isinstance(api, str) or api.casefold() not in {
                            symbol.casefold() for symbol in named}
                            for api in apis)):
                    continue
                known_conditions['api'] = list(dict.fromkeys(named))
                payload = [goal.kind, goal.capability, sorted(goal.entities),
                           goal.field, known_conditions]
                goal_id = 'goal_' + hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                if goal_id in ids:
                    continue
                ids.add(goal_id)
                goals.append(DaqGoal(goal_id, goal.kind, goal.capability,
                                     goal.entities, goal.field,
                                     tuple(sorted(known_conditions.items()))))
                if len(goals) > 16:
                    raise SchemaExtractionError('schema_goals_expansion_limit')
        # A named status callback establishes what it reports, not an extra
        # destructive operation. Keep an explicit delete question as its own
        # evidence obligation when the model proposed only the API definition.
        asked_callback_delete = any(
            re.search(r'\bsetEgoStateCallback\b', clause, re.I)
            and re.search(r'删除|清除|\b(?:delete|erase|clear)\b', clause, re.I)
            and re.search(r'录制状态|recording\s+state', clause, re.I)
            and not re.search(r'\b(?!OrbbecSDK\b)[A-Za-z][A-Za-z0-9_-]*SDK\b|'
                              r'\b(?:OpenCV|EgoLowBle|EgoViewer)\b',
                              clause, re.I)
            for clause in re.split(r'[?？。；;\n]+', question))
        callback_rows = ([row for row in sdk_catalog.search(
            question, 'internal_fae', include_all=True)
            if row.get('sdk_layer') == 'android_java'
            and 'setEgoStateCallback' in row.get('symbols', [])]
            if isinstance(sdk_catalog, SdkCatalog) else [])
        if (isinstance(sdk_catalog, SdkCatalog)
                and asked_callback_delete
                and any(goal.capability == 'sdk_evidence' for goal in goals)
                and callback_rows
                and not any(goal.capability == 'sdk_evidence'
                    and goal.condition_map().get('sdk') in (None, 'OrbbecSDK')
                    and goal.condition_map().get('api') in (
                        None, 'setEgoStateCallback')
                    and any(re.fullmatch(
                        r'(?:recording_state_(?:delete|clear|erase)|'
                        r'(?:delete|clear|erase)_recording_state)',
                        str(goal.condition_map().get(key, '')), re.I)
                        for key in ('task', 'feature'))
                    for goal in goals)):
            scoped = {'api': 'setEgoStateCallback',
                      'task': 'recording_state_delete'}
            if re.search(r'(?<![A-Za-z0-9_])OrbbecSDK(?![A-Za-z0-9_])',
                         question + ' ' + prior_user_context, re.I):
                scoped['sdk'] = 'OrbbecSDK'
            payload = ['product', 'sdk_evidence', [], None, scoped]
            goal_id = 'goal_' + hashlib.sha256(json.dumps(
                payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
            if goal_id not in ids:
                ids.add(goal_id)
                grounded = (len(callback_rows) == 1
                            and sdk_catalog.grounds_selector(
                                question, 'task', 'recording_state_delete'))
                goals.append(DaqGoal(
                    goal_id, 'product', 'sdk_evidence', (), None,
                    tuple(sorted(scoped.items())),
                    () if grounded else ('task',)))
                if len(goals) > 16:
                    raise SchemaExtractionError('schema_goals_expansion_limit')
        # Firmware image binding and camera imaging are different asks. A
        # generic package goal may retrieve the README while leaving the
        # camera requirement invisible in coverage; preserve that obligation
        # even when the model proposed only one SDK goal.
        followup_libobkit_readme = (
            candidate.get('topic_switch') is not True
            and re.match(r'^\s*(?:这个\s*)?README\s*对', question, re.I)
            and re.search(r'\blibobkit(?:\.so)?\b', prior_user_context, re.I)
            and not re.search(r'\b[A-Za-z][A-Za-z0-9_-]*(?:SDK|CV)\b|'
                              r'\b(?:EgoLowBle|EgoViewer)\b',
                              prior_user_context + ' ' + question, re.I)
            and any(goal.capability == 'sdk_evidence'
                    and goal.condition_map().get('sdk') == 'libobkit'
                    for goal in goals))
        def paired_image_asks(clause: str) -> bool:
            firmware = list(re.finditer(
                r'固件镜像|整机镜像|firmware\s+image', clause, re.I))
            camera = list(re.finditer(
                r'摄像头成像|相机图像|图像质量|画质|'
                r'camera\s+image|image\s+quality', clause, re.I))
            if re.search(r'\b[A-Za-z][A-Za-z0-9_-]*SDK\b|'
                         r'\b(?:OpenCV|EgoLowBle|EgoViewer)\b',
                         clause, re.I):
                return False
            for image in firmware:
                for imagery in camera:
                    between = (clause[image.end():imagery.start()]
                               if image.end() <= imagery.start() else
                               clause[imagery.end():image.start()]
                               if imagery.end() <= image.start() else '')
                    if (len(between) <= 48
                            and not re.search(r'其他|另一|别的', between)
                            and not re.search(r'[A-Za-z][A-Za-z0-9_-]*',
                                              re.sub(
                                                  r'\b(?:and|requirements?|what|'
                                                  r'are|the|for|about)\b', '',
                                                  between, flags=re.I))):
                        return True
            return False

        mixed_libobkit_image_clause = any(
            (re.search(r'\blibobkit(?:\.so)?\b', clause, re.I)
             or followup_libobkit_readme)
            and paired_image_asks(clause)
            for clause in re.split(r'[?？。；;\n]+', question))
        if (isinstance(sdk_catalog, SdkCatalog)
                and mixed_libobkit_image_clause
                and any(goal.capability == 'sdk_evidence' for goal in goals)
                and not any(goal.capability == 'sdk_evidence'
                            and goal.condition_map().get('sdk') == 'libobkit'
                            and any(goal.condition_map().get(key) ==
                                    'image_requirements'
                                    for key in ('feature', 'task'))
                            for goal in goals)):
            scoped = {'sdk': 'libobkit', 'feature': 'image_requirements'}
            payload = ['product', 'sdk_evidence', [], None, scoped]
            goal_id = 'goal_' + hashlib.sha256(json.dumps(
                payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
            if goal_id not in ids:
                ids.add(goal_id)
                grounded = sdk_catalog.grounds_selector(
                    question, 'feature', 'image_requirements')
                goals.append(DaqGoal(
                    goal_id, 'product', 'sdk_evidence', (), None,
                    tuple(sorted(scoped.items())),
                    () if grounded else ('feature',)))
                if len(goals) > 16:
                    raise SchemaExtractionError('schema_goals_expansion_limit')
        # A compound request can ask about a reviewed standalone procedure
        # and then ask whether it extends to a new combination. Those are two
        # evidence scopes even when the model proposes only one combined goal.
        for goal in tuple(goals):
            if goal.kind != 'product' or goal.capability != 'lookup_procedure' \
                    or len(goal.entities) < 2:
                continue
            for clause in re.split(r'[?？。；;\n]+', question):
                if (not re.search(r'单机|独立|standalone', clause, re.I)
                        or not re.search(r'步骤|流程|按键|怎么|如何|procedure|steps?',
                                         clause, re.I)
                        or re.search(r'组合|多机|三路|同步|再加|接电脑|经\s*HUB|'
                                     r'multi[-\s]?device|pairing', clause, re.I)):
                    continue
                named = [entity for entity in goal.entities
                         if re.search(re.escape(entity), clause, re.I)]
                named = [entity for entity in named if not any(
                    entity != longer and entity.casefold() in longer.casefold()
                    for longer in named)]
                if len(named) != 1:
                    continue
                scoped = {'standalone': True}
                if re.search(r'\bTF\s*卡\b|TF卡', clause, re.I):
                    scoped['recording_mode'] = 'local_tf_card'
                payload = ['product', 'lookup_procedure', named, None, scoped]
                digest = hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                goal_id = 'goal_' + digest
                if goal_id not in ids:
                    ids.add(goal_id)
                    goals.append(DaqGoal(goal_id, 'product', 'lookup_procedure',
                                         (named[0],), None, tuple(sorted(scoped.items()))))
                    if len(goals) > 16:
                        raise SchemaExtractionError('schema_goals_expansion_limit')
        # A short follow-up can name one version in the user's context while
        # the model scopes only some of several requested fields to it. A
        # single grounded version shared by peer goals applies to the same
        # entity's remaining fields; it is a product selector, never a claim
        # measurement condition. Keep comparisons and other entities separate.
        current_versions = _explicit_resolution_versions(question) | short_current
        prior_versions = (_explicit_resolution_versions(prior_user_context)
                          | short_prior)
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
        # A family field or specification overview can ask about several
        # reviewed variants without selecting one SKU. Give each visible
        # variant a separate source-bound goal, preserving a prior selected
        # variant on follow-up turns.
        if (knowledge is not None and hasattr(knowledge, 'verified_variant_scopes')
                and ((not current_versions and not prior_versions)
                     or len(current_versions) >= 2)):
            expanded_goals = []
            for goal in goals:
                if (goal.kind != 'product' or goal.capability != 'lookup_spec'
                        or goal.field not in {'all_reviewed_specs',
                                              'horizontal_fov', 'vertical_fov',
                                              'rgb_horizontal_fov', 'rgb_vertical_fov'}
                        or len(goal.entities) != 1 or goal.condition_map()
                        or goal.unmapped_condition_keys):
                    expanded_goals.append(goal)
                    continue
                choices = knowledge.verified_variant_scopes(
                    goal.entities[0], goal.field, role=role)
                visible_variants = choices.get('resolution_variant', [])
                variants = (sorted(current_versions) if current_versions
                            else visible_variants)
                if (set(choices) != {'resolution_variant'}
                        or not set(variants) <= set(visible_variants)
                        or not 2 <= len(variants) <= 4):
                    expanded_goals.append(goal)
                    continue
                for variant in variants:
                    scoped = {'resolution_variant': variant}
                    payload = [goal.kind, goal.capability, sorted(goal.entities),
                               goal.field, scoped]
                    digest = hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                    expanded_goals.append(DaqGoal(
                        'goal_' + digest, goal.kind, goal.capability,
                        goal.entities, goal.field, tuple(scoped.items()),
                        goal.unmapped_condition_keys, goal.critical))
            if len(expanded_goals) > 16:
                raise SchemaExtractionError('schema_goals_expansion_limit')
            if len({goal.id for goal in expanded_goals}) != len(expanded_goals):
                raise SchemaExtractionError('schema_goal_normalization_collision')
            goals = expanded_goals
            ids = {goal.id for goal in goals}
        for goal in goals:
            if goal.kind == 'product' and goal.capability == 'lookup_spec' \
                    and 'resolution_variant' not in goal.condition_map() \
                    and (current_versions or prior_versions) \
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
        # A dated source example cannot settle whether a named device feature
        # is *still* available. When reviewed change logs exist for that exact
        # source topic and device, require the historical statement as a
        # separate evidence goal instead of hoping the loop searches for it.
        if (isinstance(sdk_catalog, SdkCatalog)
                and sdk_catalog.release_notes is not None
                and not any(goal.capability == 'search_knowledge' for goal in goals)
                and re.search(r'仍(?:然)?可用|还能用|已恢复|是否可用|still available|restored',
                              question, re.I)):
            source_words = question
            if (re.search(r'这个示例|该示例|this example', question, re.I)
                    and candidate.get('topic_switch') is not True):
                # An explicit follow-up may refer to the source file and
                # build named in earlier user turns. The history is a cue,
                # never a product-support fact.
                source_words += '\n' + prior_user_context
            stems = {match.group(1).casefold() for match in re.finditer(
                r'\b([A-Za-z][A-Za-z0-9_-]*)\.(?:cpp|hpp|c|h)\b',
                source_words, re.I)}
            builds = {re.sub(r'\D', '', match.group()) for match in re.finditer(
                r'(?<!\d)20\d{2}[-/]?\d{2}[-/]?\d{2}(?!\d)', source_words)}
            if len(stems) == len(builds) == 1:
                stem, build = next(iter(stems)), next(iter(builds))
                for support in goals:
                    if (support.capability != 'check_software_support'
                            or len(support.entities) != 1
                            or str(support.condition_map().get('feature', '')).casefold()
                            != stem):
                        continue
                    entity = support.entities[0]
                    history = [row for row in sdk_catalog.release_notes.records
                               if row.get('family', '').casefold() == 'orbbecsdk'
                               and role in row.get('view_roles', [])
                               and row.get('build', '') < build
                               and stem in row.get('topics', [])
                               and _normalized(entity) in _normalized(
                                   row.get('title', '') + row.get('answer', ''))]
                    if history:
                        conditions = {'sdk': 'OrbbecSDK', 'feature': stem}
                        sdk_version = support.condition_map().get('sdk_version')
                        if isinstance(sdk_version, str):
                            conditions['sdk_version'] = sdk_version
                        payload = ['product', 'search_knowledge', [entity], None,
                                   conditions]
                        digest = hashlib.sha256(json.dumps(
                            payload, ensure_ascii=False, sort_keys=True).encode()
                            ).hexdigest()[:16]
                        goal_id = 'goal_' + digest
                        if goal_id not in ids:
                            ids.add(goal_id)
                            goals.append(DaqGoal(goal_id, 'product',
                                                 'search_knowledge', (entity,), None,
                                                 tuple(sorted(conditions.items()))))
                    break
            if len(goals) > 16:
                raise SchemaExtractionError('schema_goals_expansion_limit')
        # A change-log lookup can establish what was written in two builds,
        # but cannot certify the later build's actual feature state. Keep the
        # restoration claim as an independent software-support obligation.
        if re.search(r'已恢复|是否恢复|恢复了吗|\brestored\b', question, re.I):
            viewer_logs = [goal for goal in goals
                           if goal.capability == 'search_knowledge'
                           and goal.condition_map().get('feature') ==
                           'network_device_enumeration'
                           and goal.condition_map().get('viewer_release_date')]
            if len(viewer_logs) >= 2:
                later = max(viewer_logs, key=lambda item: str(
                    item.condition_map()['viewer_release_date']))
                conditions = {key: value for key, value in
                              later.condition_map().items()
                              if key in {'platform', 'viewer_version',
                                         'viewer_release_date', 'feature'}}
                payload = ['product', 'check_software_support',
                           sorted(later.entities), None, conditions]
                digest = hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()[:16]
                goal_id = 'goal_' + digest
                if goal_id not in ids:
                    ids.add(goal_id)
                    goals.append(DaqGoal(goal_id, 'product',
                                         'check_software_support', later.entities,
                                         None, tuple(sorted(conditions.items()))))
        # Camera count and an unconstrained maximum FPS are two known facts;
        # neither certifies each camera's simultaneous FPS. Represent that
        # joint claim explicitly so evidence coverage cannot report full.
        simultaneous_rgb = (re.search(r'\bRGB\b', question, re.I)
                            and re.search(r'同时|并发|simultaneous|concurrent',
                                          question, re.I)
                            and re.search(r'各|每|\bper\b', question, re.I)
                            and re.search(r'\d+(?:\.\d+)?\s*fps', question, re.I))
        count_match = re.search(
            r'(?<!\d)([1-9])\s*(?:路|颗|个)\s*RGB\b|\bRGB\s*([1-9])\s*路',
            question, re.I)
        if simultaneous_rgb and count_match:
            count = int(count_match.group(1) or count_match.group(2))
            peers = [goal for goal in goals
                     if goal.kind == 'product'
                     and goal.capability == 'lookup_spec'
                     and goal.field in {'rgb_max_frame_rate', 'rgb_max_fps'}
                     and len(goal.entities) == 1]
            for peer in peers:
                for field, conditions in (
                    ('rgb_camera_count', {
                        key: value for key, value in peer.condition_map().items()
                        if key in _IDENTITY_SELECTOR_CONDITIONS}),
                    ('rgb_simultaneous_max_frame_rate', {
                        **{key: value for key, value in peer.condition_map().items()
                           if key in _IDENTITY_SELECTOR_CONDITIONS},
                        'simultaneous_camera_count': count}),
                ):
                    payload = ['product', 'lookup_spec',
                               sorted(peer.entities), field, conditions]
                    digest = hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True).encode()
                        ).hexdigest()[:16]
                    goal_id = 'goal_' + digest
                    if goal_id not in ids:
                        ids.add(goal_id)
                        goals.append(DaqGoal(goal_id, 'product', 'lookup_spec',
                                             peer.entities, field,
                                             tuple(sorted(conditions.items()))))
        if len(goals) > 16:
            raise SchemaExtractionError('schema_goals_expansion_limit')
        kinds = {goal.kind for goal in goals if goal.kind != 'unknown'}
        for requested_mode, requested_variant, requested_channel in requested_modes:
            frame_rate_goals = [
                goal for goal in goals
                if goal.capability == 'lookup_spec'
                and _frame_rate_channel_matches(goal.field, requested_channel)
                and goal.condition_map().get('resolution') == requested_mode
                and (requested_variant is None or
                     goal.condition_map().get('resolution_variant') == requested_variant)
            ]
            if not frame_rate_goals:
                raise SchemaExtractionError('schema_missing_measurement_mode')
            # A frame rate conditioned on a named output mode is only useful
            # after that mode's availability has been checked independently.
            # Do not put the unverified mode into this companion selector.
            for frame_rate_goal in frame_rate_goals:
                if not (frame_rate_goal.field or '').startswith('rgb_'):
                    continue
                selectors = {key: value for key, value in
                             frame_rate_goal.condition_map().items()
                             if key in {'variant', 'resolution_variant',
                                        'hardware_revision', 'scope'}}
                payload = ['product', 'lookup_spec',
                           sorted(set(frame_rate_goal.entities)),
                           'rgb_resolution', selectors]
                digest = hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()[:16]
                goal_id = 'goal_' + digest
                if goal_id not in ids:
                    ids.add(goal_id)
                    goals.append(DaqGoal(goal_id, 'product', 'lookup_spec',
                                         frame_rate_goal.entities,
                                         'rgb_resolution',
                                         tuple(sorted(selectors.items()))))
                    if len(goals) > 16:
                        raise SchemaExtractionError('schema_goals_expansion_limit')
        request_kind = 'mixed' if len(kinds) > 1 else next(iter(kinds), 'unknown')
        topic_switch = candidate.get('topic_switch', False)
        if not isinstance(topic_switch, bool):
            raise SchemaExtractionError('topic_switch_invalid')
        native_context = question if topic_switch else question + '\n' + prior_user_context
        if re.search(r'原生(?:\s*RGB)?\s*分辨率|native\s+(?:rgb\s+)?resolution',
                     native_context, re.I):
            for goal in tuple(goals):
                if goal.kind != 'product' or goal.capability != 'lookup_spec' \
                        or goal.field not in {'rgb_max_frame_rate', 'rgb_max_fps',
                                              'rgb_resolution'}:
                    continue
                selectors = {key: value for key, value in goal.condition_map().items()
                             if key in {'variant', 'resolution_variant',
                                        'hardware_revision', 'scope'}}
                for field in ('rgb_resolution', 'rgb_native_resolution'):
                    payload = ['product', 'lookup_spec', sorted(set(goal.entities)),
                               field, selectors]
                    digest = hashlib.sha256(json.dumps(
                        payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                    goal_id = 'goal_' + digest
                    if goal_id not in ids:
                        ids.add(goal_id)
                        goals.append(DaqGoal(goal_id, 'product', 'lookup_spec',
                                             goal.entities, field,
                                             tuple(sorted(selectors.items()))))
                        if len(goals) > 16:
                            raise SchemaExtractionError('schema_goals_expansion_limit')
        # A stated soft preference still needs evidence when it influences a
        # product recommendation. The planner may omit it after focusing on
        # the hard condition, so retain its spec goal using only the already
        # user-grounded product selector from the peer RGB goal.
        if (re.search(r'\b\d+(?:\.\d+)?\s*fps\b', question, re.I)
                and re.search(r'偏好|可放宽|非硬条件|prefer|nice.to.have', question, re.I)
                and re.search(r'推荐|选型|选择|recommend|select', question, re.I)
                and re.search(r'\bRGB\b', question, re.I)):
            peers = [goal for goal in goals if goal.kind == 'product'
                     and goal.capability == 'lookup_spec'
                     and goal.field in {'rgb_resolution', 'rgb_native_resolution'}
                     and len(goal.entities) == 1]
            scopes = {(goal.entities, tuple(sorted(
                (key, value) for key, value in goal.condition_map().items()
                if key in {'variant', 'resolution_variant', 'hardware_revision'})))
                for goal in peers}
            if len(scopes) == 1 and not any(
                    goal.kind == 'product' and goal.capability == 'lookup_spec'
                    and goal.field in {'rgb_max_frame_rate', 'rgb_max_fps'}
                    and goal.entities == peers[0].entities for goal in goals):
                entities, selector_items = next(iter(scopes))
                selectors = dict(selector_items)
                payload = ['product', 'lookup_spec', sorted(entities),
                           'rgb_max_frame_rate', selectors]
                digest = hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                goal_id = 'goal_' + digest
                if goal_id not in ids:
                    ids.add(goal_id)
                    unmapped = tuple(key for key in peers[0].unmapped_condition_keys
                                     if key in selectors)
                    goals.append(DaqGoal(goal_id, 'product', 'lookup_spec',
                                         entities, 'rgb_max_frame_rate',
                                         tuple(sorted(selectors.items())), unmapped))
                    if len(goals) > 16:
                        raise SchemaExtractionError('schema_goals_expansion_limit')
        # A reviewed stop procedure describes expected checkpoints. It does
        # not diagnose a missing voice prompt or prove that a particular file
        # is intact. Keep that independent evidence obligation even when the
        # model emits only the procedure goal, otherwise coverage can be
        # reported as full while the answer correctly says the state is
        # unknown.
        checkpoint_conflict = bool(
            re.search(r'停止|\bstop\b', question, re.I)
            and re.search(r'没(?:有)?听(?:到|见)|未听(?:到|见)|'
                          r'没有语音|无语音|no\s+(?:voice|audio)\s+prompt',
                          question, re.I)
            and re.search(r'语音|提示|保存|\bprompt\b|\brecord', question, re.I))
        file_integrity_asked = bool(re.search(
            r'完整落盘|文件.{0,8}(?:完整|无误)|数据.{0,8}完整|'
            r'(?:fully|completely)\s+(?:saved|written)|file\s+integrity',
            question, re.I))
        if checkpoint_conflict or file_integrity_asked:
            for procedure in tuple(goals):
                procedure_steps = procedure.condition_map().get('steps')
                if procedure.kind != 'product' or procedure.capability != 'lookup_procedure' \
                        or not procedure.entities or not isinstance(procedure_steps, list) \
                        or 'stop_recording' not in procedure_steps:
                    continue
                if any(goal.kind == 'product' and goal.capability == 'risk'
                       and goal.entities == procedure.entities for goal in goals):
                    continue
                scoped = procedure.condition_map()
                payload = ['product', 'risk', sorted(procedure.entities), None, scoped]
                goal_id = 'goal_' + hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                if goal_id not in ids:
                    ids.add(goal_id)
                    goals.append(DaqGoal(
                        goal_id, 'product', 'risk', procedure.entities, None,
                        tuple(sorted(scoped.items())),
                        procedure.unmapped_condition_keys))
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
        elif refers_to_prior_attachment(question):
            # A visible session attachment remains user evidence on an
            # explicit follow-up. Re-read it in this turn instead of relying
            # on the prior assistant answer as an unsourced fact cache.
            dependency = 'relevant'
        # Presentation follows the user's words or explicit UI selection.
        # SDK symbols and enum names must not make a Chinese question English.
        if selected_answer_language is not None:
            language = selected_answer_language
        elif re.search(r'(?:请|请你)?\s*(?:用|使用|以)\s*英文(?:回答|作答|回复|说明|解释|写|输出)?'
                       r'|answer\s+in\s+English\b', question, re.IGNORECASE):
            language = 'en'
        elif re.search(r'(?:请|请你)?\s*(?:用|使用|以)\s*中文(?:回答|作答|回复|说明|解释|写|输出)?'
                       r'|answer\s+in\s+Chinese\b', question, re.IGNORECASE):
            language = 'zh'
        elif re.search(r'[\u3400-\u9fff]', question):
            language = 'zh'
        elif re.search(r'[A-Za-z]{2,}', question):
            language = 'en'
        else:
            language = candidate.get('answer_language') or 'zh'
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
                        'conditions': {'type': 'object', 'properties': {
                            'standalone': {'type': 'boolean'},
                            'steps': {'type': 'array', 'items': {'type': 'string'},
                                      'description': 'Keep user-stated steps in order; preserve uncovered step codes as evidence gaps.'},
                        }},
                    },
                    'required': ['kind', 'capability'],
                }},
            },
            'required': ['confidence', 'goals'],
        },
    },
}


def _requests_spec_overview(question: str) -> bool:
    """Recognize an explicit request for a product-wide parameter inventory."""
    broad = bool(re.search(
        r'(?:规格|参数).{0,18}(?:详细|全面|完整|全部|所有|尽可能多)|'
        r'(?:详细|全面|完整|全部|所有).{0,18}(?:规格|参数)|'
        r'\b(?:detailed|complete|full|all)\b.{0,24}\b(?:specs?|specifications?|parameters?)\b',
        question, re.I))
    scoped = bool(re.search(
        r'\bRGB\b|深度|\bDepth\b|\bIMU\b|\bUSB\b|帧率|分辨率|'
        r'视场角|同步|供电|蓝牙|麦克风|无线|\bSDK\b', question, re.I))
    return broad and not scoped


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
    def validated(candidate_calls: list[dict], *, retry_uncontrolled: bool) -> DaqRequestSchema:
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
            sdk_catalog=getattr(knowledge, 'sdk_catalog', None),
            knowledge=knowledge, role=role,
        )
        if (_requests_spec_overview(question)
                and any(goal.kind == 'product' for goal in schema.goals)
                and not any(goal.kind == 'product'
                            and goal.capability == 'lookup_spec'
                            and goal.field == 'all_reviewed_specs'
                            for goal in schema.goals)):
            raise SchemaExtractionError('schema_missing_spec_overview_goal')
        if retry_uncontrolled and any(
                key not in _CONDITIONS
                for goal in schema.goals for key in goal.unmapped_condition_keys):
            raise SchemaExtractionError('schema_uncontrolled_condition_keys')
        if retry_uncontrolled and any(
                goal.capability == 'lookup_procedure' and
                'steps' in goal.unmapped_condition_keys for goal in schema.goals):
            raise SchemaExtractionError('schema_step_value_unverified')
        if not any(goal.capability == 'catalog' for goal in schema.goals):
            # Product inventory is a governed catalog question. A service
            # coverage contract may count records but cannot enumerate or
            # deduplicate product identities.
            from daq_fae.task_context import prepare_turn
            baseline = prepare_turn(question)
            if 'catalog' in baseline.planned_capabilities:
                raise SchemaExtractionError('schema_missing_catalog_goal')
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
        return validated(calls, retry_uncontrolled=True)
    except SchemaExtractionError as exc:
        # Retry once for either a missing call or an invalid schema. Never
        # pass the untrusted candidate back as facts or hide provider errors.
        correction = ('上一次回复没有调用要求的工具。现在必须只调用 '
                      '`submit_daq_request_schema` 一次；不要输出正文。'
                      if not calls else
                      '用户要求产品整体规格参数的详细介绍。上次只列了一个字段。'
                      '请为已点名产品列一个 `lookup_spec` goal，'
                      '`field` 必须是 `all_reviewed_specs`；保留用户明确提出的其他目标。'
                      if str(exc) == 'schema_missing_spec_overview_goal' else
                      '上一次结构化结果漏掉明确规格目标（schema_missing_explicit_spec_goal）。'
                      '请按产品与变体逐项补充 `lookup_spec` goal，保留原有其他目标；'
                      '不要把分辨率版本当成测量条件或第二个产品。'
                      if str(exc) == 'schema_missing_explicit_spec_goal' else
                      '上一次把产品数量或清单问题归入服务覆盖，漏掉了产品目录取证目标。'
                      '本轮必须列 `product/catalog` goal，并按目录实际返回的实体和产品名计数；'
                      '服务合同的实体记录数不能代替产品款数。现在只调用结构化工具一次。'
                      if str(exc) == 'schema_missing_catalog_goal' else
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
                      '上一次使用了未受控的条件键。受控键：' +
                      ', '.join(sorted(_CONDITIONS)) + '。请仅从用户原话抽取条件，'
                      '单机且无电脑或手机主机连接写 `standalone=true` 与 '
                      '`host_connection="none"`；供电、连接和存储询问写 '
                      '`topology_aspects` 数组；本机 TF 卡录制及明确步骤分别写 '
                      '`recording_mode`、`steps`、`indicators`。未知的变体、固件、'
                      'Viewer 版本不能补猜；无法用受控键表达的条件仍需保留原键作为缺口。'
                      '现在只调用结构化工具一次。'
                      if str(exc) == 'schema_uncontrolled_condition_keys' else
                      '上一次步骤值未通过核验。`steps` 只列用户明确问到的步骤，'
                      '保持原先后次序。' +
                      ('已核对的 EGO Pro 单机录制步骤代码：' +
                       ', '.join(_PROCEDURE_STEP_CODES) +
                       '；断电插卡对应 insert_card_power_off。'
                       if re.search(r'\bEGO\s*Pro\b', question, re.I) and
                       re.search(r'单机|独立|本机|standalone', question, re.I) else '') +
                      '其他设备或未覆盖流程的步骤仍保留用户原意的短码，后续标证据缺口，'
                      '不能为套入已核对流程而省略或改写。现在只调用结构化工具一次。'
                      if str(exc) == 'schema_step_value_unverified' else
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
                      '上一次把产品实体或产品条件放进非产品目标。附件目标只用 '
                      '`attachment/search_attachments`，不填实体和条件；'
                      '日志中的设备、固件及 SDK 只是用户线索，官方兼容结论须另列 '
                      '`product/check_software_support` 目标。'
                      '现在只重新调用结构化工具一次。'
                      if str(exc) == 'schema_goal_scope_invalid' else
                      '上一次结构化结果未通过校验（' + str(exc) + '）。请仅重新调用 '
                      '`submit_daq_request_schema` 一次。产品实体只能逐字来自本轮问题或'
                      '提供的用户历史；无法接地的实体请省略。不要补产品事实。')
        # A single correction must recheck the full structural contract. Fixing
        # only the first reported key can otherwise introduce a second invalid
        # scope on the one permitted retry. These are planning instructions,
        # never an automatic conversion of an invalid goal into a product fact.
        retry_contract = {
            'revalidate_all_goals': True,
            'single_entity_capabilities': ['lookup_spec', 'resolve_entity'],
            'input_facts': 'Separate each necessary input by its actual entity; do not guess values.',
            'combined_result_is_not_per_device_fact': True,
            'combined_conditions': 'Keep external/system conditions on the combined goal, not every input.',
            'unmapped_conditions': 'Preserve as evidence gaps; do not invent aliases, selectors or defaults.',
            'instruction': 'Recheck all goals and submit exactly once; no product answer.',
        }
        retry = [{**messages[0], 'content': messages[0]['content'] + '\n' + correction
                  + '\nSCHEMA_RETRY_CONTRACT\n' + json.dumps(retry_contract, ensure_ascii=False)},
                 messages[1]]
        retry_calls = [event for event in adapter.chat(
            retry, tools=[_SCHEMA_TOOL], required_tool='submit_daq_request_schema')
            if event.get('type') == 'tool_call']
        return validated(retry_calls, retry_uncontrolled=False)
