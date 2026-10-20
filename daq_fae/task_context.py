"""DAQ user setup memory and conservative evidence requirements.

Syntactic intake captures explicit user declarations; it does not resolve products
or certify compatibility. Structured updates can come from a reviewed extractor.
All values retain user authority regardless of who performed the extraction.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace

from daq_fae.request_schema import (
    DaqRequestSchema, firmware_versions_compared_without_current,
    stated_current_firmware_version,
)

_FIELDS = frozenset({
    'equipment', 'variant', 'resolution_variant', 'topology', 'platform',
    'viewer_version', 'sdk_version',
    'firmware_version', 'connection', 'power', 'recording_format', 'storage', 'task',
})
_SWITCH = re.compile(r'^(?:换个场景|换个问题|另一个问题|重新开始|new topic|switch topic)', re.I)
_FOLLOWUP = re.compile(r'继续|然后|还是|下一步|那|这个|它|\b(?:it|still|next|continue)\b', re.I)
_INTENTS = {
    'catalog': r'(?:有哪些|有多少|多少款|几款).{0,12}(?:产品|设备|型号|套件|系统)|(?:产品|设备|型号|套件|系统).{0,12}(?:有哪些|有多少|多少款|几款)|型号列表|产品目录|产品线|\b(?:catalog|product list|product range)\b',
    'selection': r'推荐|选型|选择|适合|选哪个|选什么|怎么选|如何选|怎样选|选用|方案建议|\b(?:recommend|selection|choose|suitable)\b',
    'resolve_entity': r'设备|型号|变体|配置|套件|\b(?:device|model|variant|kit)\b',
    'lookup_spec': r'规格|参数|精度|分辨率|帧率|带宽|功耗|供电输出|电源输出|基线|视场角|蓝牙|Wi[- ]?Fi|无线通信|USB.{0,8}(?:接口|物理)|物理接口|(?:几路|几颗|几个).{0,8}(?:相机|摄像头)|(?:相机|摄像头).{0,8}数量|(?:下游端口.{0,5}(?:有几个|数量|几个|数)|(?:有几个|几个).{0,5}下游端口)|\b(?:spec|specification|accuracy|resolution|fps|bandwidth|fov|camera count|power output)\b',
    'inspect_topology': r'接线|连接|主从|同步|组合|供电(?!输出)|\b(?:topology|connection|sync|wiring|power)\b',
    'lookup_procedure': r'步骤|安装|配置|采集|录制|落盘|保存|启动|\b(?:procedure|install|setup|record|capture|save)\b',
    'check_software_support': r'兼容|viewer|sdk|软件|固件|firmware|\b(?:compatible|software support)\b',
    'sdk_evidence': r'\bsdk\b|\bapi\b|\bros2?\b|\bpython\b|代码|开发接口',
    'official_links': r'链接|下载|官网|\b(?:link|download|official website)\b',
    'experience': r'经验|案例|最佳实践|常见问题|\b(?:experience|case study|best practice)\b',
    'risk': r'风险|注意事项|安全|合规|数据丢失|\b(?:risk|safety|compliance|data loss)\b',
}
_DIAGNOSTIC = re.compile(
    r'失败|报错|日志|没有画面|断开|排查|排障|故障|异常|不稳定|丢帧|掉帧|卡顿|'
    r'\b(?:error|timeout|failed|failure|log|disconnect|troubleshoot|unstable|stutter)\b',
    re.I,
)
_TASKS = {
    'installation': r'安装|\binstall(?:ation)?\b',
    'configuration': r'配置|\bconfigur(?:e|ation)\b|\bsetup\b',
    'recording': r'录制|\brecord(?:ing)?\b',
    'capture': r'采集|\bcapture\b', 'storage': r'落盘|保存|\bsave\b',
    'startup': r'启动|\bstart(?:up)?\b',
}
_SOFTWARE = {'sdk': r'\bsdk\b', 'viewer': r'\bviewer\b', 'firmware': r'固件|\bfirmware\b'}
_SPEC_FIELDS = {
    'camera_count': r'(?:几路|几颗|几个).{0,8}(?:相机|摄像头)|(?:相机|摄像头).{0,8}数量|camera count',
    'downstream_port_count': r'(?:下游端口.{0,5}(?:有几个|数量|几个|数)|(?:有几个|几个).{0,5}下游端口)',
    'physical_usb_port': r'USB.{0,8}(?:接口|物理)|物理接口',
    'wireless_version': r'蓝牙|BLE|Wi[- ]?Fi|无线通信',
    'accuracy': r'精度|accuracy',
    'resolution': r'分辨率(?!版本)|resolution(?!\s*variant)',
    'frame_rate': r'帧率|\bfps\b', 'bandwidth': r'带宽|bandwidth',
    'power_consumption': r'功耗|power consumption', 'baseline': r'基线|baseline',
    'power_output': r'供电输出|电源输出|\bpower output\b',
    'horizontal_fov': r'水平视场角|横向视场角|\bhfov\b',
    'vertical_fov': r'垂直视场角|纵向视场角|\bvfov\b',
    'lens_type': r'鱼眼|fisheye|镜头类型|lens type',
}

# A user's task and desired result are part of the question, but they are not
# selectors for a published specification claim. Keep them visible separately
# so the evidence ledger can match the actual product/measurement scope.
_SPEC_EVIDENCE_CONDITIONS = frozenset({
    'variant', 'resolution_variant', 'hardware_revision', 'resolution',
    'platform', 'connection', 'connection_mode', 'viewer_version',
    'sdk_version', 'firmware_version', 'per_port', 'scope', 'camera_type',
    'lens_type', 'port', 'simultaneous_port_count',
})
_IDENTITY_SELECTOR_KEYS = frozenset({'variant', 'resolution_variant', 'hardware_revision'})
_GOAL_SCOPE_HISTORY_LIMIT = 96


@dataclass(frozen=True)
class UserAssertion:
    value: object
    origin_turn: int
    certainty: str = 'asserted'
    authority: str = 'user_supplied'


@dataclass(frozen=True)
class DaqTaskContext:
    topic_id: str = 'topic_1'
    turn: int = 0
    values: dict[str, UserAssertion] = field(default_factory=dict)
    attempted_steps: tuple[str, ...] = ()
    active_capabilities: tuple[str, ...] = ()
    grounded_scope: dict = field(default_factory=dict)
    setup_statement: UserAssertion | None = None
    # User-grounded scope cues, kept separately from device setup assertions.
    # A release-note version or compared firmware is never installed firmware.
    goal_scope_history: tuple[dict, ...] = ()

    def get(self, name, default=None):
        item = self.values.get(name)
        return copy.deepcopy(item.value) if item else default

    def to_checkpoint(self):
        return {'version': 3, 'topic_id': self.topic_id, 'turn': self.turn,
                'values': {key: asdict(value) for key, value in self.values.items()},
                'attempted_steps': list(self.attempted_steps),
                'active_capabilities': list(self.active_capabilities),
                'grounded_scope': copy.deepcopy(self.grounded_scope),
                'setup_statement': (asdict(self.setup_statement)
                                    if self.setup_statement else None),
                'goal_scope_history': copy.deepcopy(list(self.goal_scope_history))}

    @classmethod
    def from_checkpoint(cls, checkpoint):
        if checkpoint.get('version') not in {1, 2, 3}:
            raise ValueError('daq_context_version_invalid')
        values = {}
        for key, raw in checkpoint.get('values', {}).items():
            if key not in _FIELDS or raw.get('authority') != 'user_supplied':
                raise ValueError('daq_context_authority_invalid')
            values[key] = UserAssertion(copy.deepcopy(raw['value']), int(raw['origin_turn']), _certainty(raw.get('certainty', 'asserted')))
        grounded = copy.deepcopy(checkpoint.get('grounded_scope', {}))
        if not isinstance(grounded, dict) or set(grounded) - {'entities', 'conditions'} or \
                not isinstance(grounded.get('entities', []), list) or \
                not all(isinstance(value, str) and value.startswith('entity:')
                        for value in grounded.get('entities', [])) or \
                not isinstance(grounded.get('conditions', {}), dict):
            raise ValueError('daq_grounded_scope_invalid')
        raw_setup = checkpoint.get('setup_statement')
        if raw_setup is not None and (not isinstance(raw_setup, dict)
                or raw_setup.get('authority') != 'user_supplied'
                or not isinstance(raw_setup.get('value'), str)
                or not 0 < len(raw_setup['value']) <= 1000
                or not isinstance(raw_setup.get('origin_turn'), int)
                or not 0 < raw_setup['origin_turn'] <= int(checkpoint['turn'])):
            raise ValueError('daq_setup_statement_invalid')
        setup = (UserAssertion(raw_setup['value'], raw_setup['origin_turn'],
                               _certainty(raw_setup.get('certainty', 'asserted')))
                 if raw_setup is not None else None)
        raw_history = checkpoint.get('goal_scope_history', [])
        if (not isinstance(raw_history, list)
                or len(raw_history) > _GOAL_SCOPE_HISTORY_LIMIT):
            raise ValueError('daq_goal_scope_history_invalid')
        scope_history = []
        for item in raw_history:
            if (not isinstance(item, dict)
                    or set(item) != {'field', 'value', 'origin_turn',
                                     'authority', 'certainty'}
                    or not isinstance(item['field'], str)
                    or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', item['field'])
                    or not _scope_value_safe(item['value'])
                    or not isinstance(item['origin_turn'], int)
                    or not 0 < item['origin_turn'] <= int(checkpoint['turn'])
                    or item['authority'] != 'user_supplied'
                    or item['certainty'] not in {'asserted', 'hypothesis'}):
                raise ValueError('daq_goal_scope_history_invalid')
            scope_history.append(copy.deepcopy(item))
        return cls(str(checkpoint['topic_id']), int(checkpoint['turn']), values,
                   tuple(checkpoint.get('attempted_steps', [])),
                   tuple(checkpoint.get('active_capabilities', [])), grounded, setup,
                   tuple(scope_history))

    def tool_context(self):
        return {'authority': 'user_supplied_only', **self.to_checkpoint()}


@dataclass(frozen=True)
class TurnPlan:
    context: DaqTaskContext
    requirements: list[dict]
    planned_capabilities: tuple[str, ...]
    planning_status: str
    context_note: str
    request_schema: DaqRequestSchema | None = None
    invalidated_fields: tuple[dict[str, str], ...] = ()
    goal_scope_lineage: dict[str, dict] = field(default_factory=dict)

    def evidence_requirements(self):
        return {'requirements': copy.deepcopy(self.requirements)}

    def field_lineage(self) -> dict[str, dict]:
        return {key: {
            'value': copy.deepcopy(item.value),
            'origin_turn': item.origin_turn,
            'authority': item.authority,
            'certainty': item.certainty,
            'use': ('current_turn' if item.origin_turn == self.context.turn
                    else 'carried_from_prior_turn'),
        } for key, item in self.context.values.items()}

    def goal_field_lineage(self) -> dict[str, dict]:
        return copy.deepcopy(self.goal_scope_lineage)


def explicit_topic_switch(message: str) -> bool:
    return bool(_SWITCH.search(message.strip()))


def _certainty(value):
    if value not in {'asserted', 'hypothesis'}:
        raise ValueError('daq_context_certainty_invalid')
    return value


def _scope_value_safe(value):
    return ((isinstance(value, str) and 0 < len(value) <= 128)
            or (type(value) is int and -10_000_000_000 <= value <= 10_000_000_000)
            or type(value) is bool
            or (type(value) is list and 0 < len(value) <= 8
                and all(isinstance(item, str) and 0 < len(item) <= 128
                        for item in value)))


def _scope_key(name, value):
    if isinstance(value, str):
        normalized = re.sub(r'\s+', '', value.casefold().replace('×', 'x'))
        if name.endswith('_version') and re.match(r'^v\d', normalized):
            normalized = normalized[1:]
        value = normalized
    return json.dumps([name, value], ensure_ascii=False, sort_keys=True)


def _scope_cue_in_message(name, value, message):
    """Mark provenance only when the current user's words support the cue."""
    if not _scope_value_safe(value):
        return False
    # These are provenance aliases for the schema's bounded selector values.
    # They do not authorize a product fact or a software support conclusion.
    if name == 'language' and value == 'cpp':
        return bool(re.search(r'(?<![A-Za-z0-9_])C\+\+(?![A-Za-z0-9_])|\bC plus plus\b',
                              message, re.I))
    if name == 'feature' and isinstance(value, str):
        feature_cues = {
            'multi_device_sync_mode_check_and_set':
                r'多设备.{0,8}主从同步|主从同步.{0,8}(?:模式|示例)|'
                r'multi.device.{0,12}sync',
            'master_slave_sync': r'主从同步|master.slave.sync',
            'network_device_enumeration': r'网络设备枚举|network.device.enumeration',
            'magnetometer': r'磁力计|magnetometer',
            'image_requirements': r'摄像头成像|成像(?:质量|要求)|image.requirements?',
        }
        if value in feature_cues:
            return bool(re.search(feature_cues[value], message, re.I))
    if name == 'task' and isinstance(value, str):
        task_cues = {
            'synchronized_recording': r'同步(?:录制|保存|落盘|采集)|'
                                      r'synchroni[sz]ed.recording',
            'sync_recording': r'同步(?:录制|保存|落盘|采集)|'
                              r'synchroni[sz]ed.recording',
            'recording_state': r'录制状态(?:回调)?|recording.state',
            'recording_state_delete': r'删(?:除|掉).{0,6}录制状态|'
                                      r'(?:delete|clear).{0,12}recording.state',
            'firmware_upgrade': r'固件.{0,8}升级|升级(?:步骤|流程|固件)|'
                                r'firmware.{0,8}(?:upgrade|update)',
        }
        if value in task_cues:
            return bool(re.search(task_cues[value], message, re.I))
    if name == 'steps' and value == 'stop_recording':
        return bool(re.search(r'停止(?:录制|后)|停录|stop.recording', message, re.I))
    if name == 'indicators' and isinstance(value, str):
        indicator_cues = {
            'led_state': r'灯态|指示灯|[红绿蓝黄]灯',
            'green_led': r'绿灯|green.led',
            'voice_prompt': r'语音|播报|(?:听到|听见).{0,12}[“"「]?(?:数据已保存|录制已停止)',
        }
        if value in indicator_cues:
            return bool(re.search(indicator_cues[value], message, re.I))
    if name == 'per_port' and type(value) is bool:
        return bool(value and re.search(r'每(?:个|路)?口|单(?:个|路)?口|单口|单路', message))
    if name == 'standalone' and type(value) is bool:
        return bool(value and re.search(r'单机|独立|脱机|不(?:连接|接)(?:电脑|主机)', message))
    if name == 'host_connection' and value == 'none':
        return bool(re.search(r'不(?:连接|接)(?:电脑|手机|主机|PC)|无需(?:电脑|手机|主机|PC)|脱机',
                              message, re.I))
    if name == 'host_connection' and value in {'computer', 'pc'}:
        return bool(re.search(r'(?<!不)(?:接|连接)(?:到|上)?(?:电脑|主机|PC)|'
                              r'(?:通过|经由?)\s*(?:电脑|主机|PC)|'
                              r'\b(?:on|via|through)\s+(?:a\s+)?PC\b',
                              message, re.I))
    if name == 'recording_mode' and value == 'local_tf_card':
        return bool(re.search(r'(?:本机|单机|本地|独立).{0,12}(?:TF|SD|存储)\s*卡|'
                              r'(?:TF|SD|存储)\s*卡.{0,12}(?:本机|单机|本地|独立)',
                              message, re.I))
    if type(value) is list:
        return bool(value) and all(_scope_cue_in_message(name, item, message)
                                   for item in value)
    if name == 'wristcam_count' and type(value) is int:
        chinese = {1: '一', 2: '[两二]', 3: '三', 4: '四'}.get(value, '')
        numeral = rf'(?:{value}|{chinese})' if chinese else str(value)
        return bool(re.search(
            rf'{numeral}\s*(?:台|个)\s*WristCam|'
            rf'WristCam\s*{numeral}\s*(?:台|个)', message, re.I))
    if name in {'simultaneous_port_count', 'simultaneous_camera_count'} \
            and type(value) is int:
        chinese = {1: '一', 2: '[两二]', 3: '三', 4: '四',
                   5: '五', 6: '六', 7: '七', 8: '八'}.get(value, '')
        numeral = rf'(?:{value}|{chinese})' if chinese else str(value)
        if name == 'simultaneous_port_count':
            return bool(re.search(
                rf'(?<!\d){numeral}\s*(?:个|路)?(?:口|端口)', message))
        return bool(re.search(
            rf'(?<!\d){numeral}\s*(?:颗|个|路|台)\s*'
            rf'(?:RGB\s*)?(?:相机|摄像头|RGB)', message, re.I))
    if type(value) is bool:
        return False
    if type(value) is int:
        return bool(re.search(rf'(?<!\d){value}(?!\d)', message))
    asked = re.sub(r'\s+', '', message.casefold().replace('×', 'x'))
    token = re.sub(r'\s+', '', value.casefold().replace('×', 'x'))
    return bool(token and token in asked)


def _goal_scope_provenance(message, schema, old, turn, switched):
    # A switch invalidates the active setup, but an explicit comparison or
    # return can still refer to the earlier user's exact words. Keep those
    # audit cues without treating them as verified product evidence.
    return_to_prior = bool(re.search(
        r'回到|返回(?:刚才|之前|前面)|'
        r'综合.{0,12}(?:两条|线索|前面|上述)|'
        r'结合.{0,12}(?:两条|线索|前面|上述)|两者|二者', message))
    old_history = list(copy.deepcopy(old.goal_scope_history))
    # Preserve the bounded audit archive across topic switches. An ordinary
    # follow-up may reuse only cues from its active topic; explicit return or
    # synthesis may refer to older user words without reviving old grounding.
    history = old_history
    active_start = int(old.topic_id.removeprefix('topic_')) if re.fullmatch(
        r'topic_\d+', old.topic_id) else 1
    eligible = (old_history if return_to_prior else
                [item for item in old_history
                 if not switched and item['origin_turn'] >= active_start])
    prior = {_scope_key(item['field'], item['value']): item for item in eligible}
    last_by_field = {}
    for item in old_history:
        last_by_field[item['field']] = item
    lineage: dict[str, dict] = {}
    changed: dict[str, object] = {}

    # A schema model can omit a selector on the first turn and add it on a
    # follow-up. Retain only bounded cues stated in the user's own words, so
    # later goal provenance does not depend on that first schema shape.
    for name, value in (
        ('feature', 'multi_device_sync_mode_check_and_set'),
        ('feature', 'master_slave_sync'),
        ('task', 'recording_state'),
    ):
        if _scope_cue_in_message(name, value, message):
            item = {'field': name, 'value': value, 'origin_turn': turn,
                    'authority': 'user_supplied', 'certainty': 'asserted'}
            changed[_scope_key(name, value)] = item
            history.append(item)

    def locate(name, value):
        if _scope_cue_in_message(name, value, message):
            item = {'field': name, 'value': copy.deepcopy(value),
                    'origin_turn': turn, 'authority': 'user_supplied',
                    'certainty': 'asserted'}
            if _scope_value_safe(value):
                key = _scope_key(name, value)
                if key not in changed:
                    changed[key] = item
                    history.append(item)
            return {key: copy.deepcopy(item[key]) for key in (
                'value', 'origin_turn', 'authority', 'certainty')} | {'use': 'current_turn'}
        item = prior.get(_scope_key(name, value)) if _scope_value_safe(value) else None
        if item is not None:
            return {key: copy.deepcopy(item[key]) for key in (
                'value', 'origin_turn', 'authority', 'certainty')} | {
                    'use': 'carried_from_prior_turn'}
        return {'value': copy.deepcopy(value), 'origin_turn': None,
                'authority': 'schema_unattributed', 'certainty': 'unknown',
                'use': 'unattributed'}

    if schema is not None:
        for goal in schema.goals:
            if goal.kind != 'product':
                continue
            fields = {'entities': [locate('entity', name) for name in goal.entities]}
            for name, value in goal.condition_map().items():
                fields[name] = locate(name, value)
            lineage[goal.id] = fields
    invalidated = []
    if switched:
        invalidated = [{'field': name, 'reason': 'topic_switch'}
                       for name in sorted(last_by_field)]
    else:
        current_by_field = {}
        for item in changed.values():
            current_by_field.setdefault(item['field'], set()).add(
                _scope_key(item['field'], item['value']))
        for name, keys in current_by_field.items():
            if (len(keys) == 1 and name in last_by_field
                    and next(iter(keys)) != _scope_key(
                        name, last_by_field[name]['value'])):
                invalidated.append({'field': name, 'reason': 'user_correction'})
    return lineage, tuple(history[-_GOAL_SCOPE_HISTORY_LIMIT:]), invalidated


def _extract(message):
    updates = {}
    for key, pattern in (
        ('equipment', r'(?:设备|型号|equipment|device)\s*(?:是|为|[:：=]|is)\s*([^；;，,。\n]+)'),
        ('variant', r'(?:变体|硬件修订|variant|revision)\s*(?:是|为|[:：=]|is)\s*([^；;，,。\n]+)'),
        ('resolution_variant', r'(?:分辨率版本|resolution\s*variant)\s*(?:是|为|[:：=]|is)\s*([0-9]{3,4}\s*[x×X]\s*[0-9]{3,4})'),
        ('platform', r'(?:平台|操作系统|platform|os)\s*(?:是|为|[:：=]|is)\s*([^；;，,。\n]+)'),
        ('connection', r'(?:连接方式|连接|connection(?: mode)?)\s*(?:是|为|[:：=]|is)\s*([^；;，,。\n]+)'),
    ):
        match = re.search(pattern, message, re.I)
        if match:
            value = match.group(1).strip()
            if key == 'resolution_variant':
                value = re.sub(r'\s*[x×X]\s*', 'x', value)
            updates[key] = [part.strip() for part in re.split(r'\s*\+\s*|、|\s+(?:和|与|及)\s+', value)] if key == 'equipment' else value
    equipment = updates.get('equipment')
    if isinstance(equipment, list) and len(equipment) == 1:
        ego = re.fullmatch(r'EGO\s+([0-9]{3,4})\s*[x×X]\s*([0-9]{3,4})',
                           equipment[0], re.I)
        if ego:
            selected = f'{ego.group(1)}x{ego.group(2)}'
            updates['equipment'] = ['EGO']
            updates['resolution_variant'] = (
                selected if updates.get('resolution_variant', selected) == selected else None
            )
    for key, label in (('viewer_version', 'viewer'), ('sdk_version', 'sdk'), ('firmware_version', r'固件|firmware')):
        matches = list(re.finditer(rf'(?:{label})\s*(?:版本)?\s*(?:为|是|[:：=]|is)?\s*v?([0-9]+(?:\.[0-9A-Za-z_-]+)+)', message, re.I))
        if key == 'firmware_version':
            current = stated_current_firmware_version(message)
            if current:
                updates[key] = current
            elif matches and not firmware_versions_compared_without_current(message):
                updates[key] = matches[-1].group(1)
        elif matches:
            updates[key] = matches[-1].group(1)
    steps = re.findall(r'(?:已经|已尝试|试过|\btried\b)\s*([^；;，,。\n]+)', message, re.I)
    if steps:
        updates['attempted_steps'] = [step.strip() for step in steps]
    tasks = [name for name, pattern in _TASKS.items() if re.search(pattern, message, re.I)]
    if tasks:
        updates['task'] = tasks
    return updates


def extract_context_hints(excerpts) -> dict:
    """Extract tentative scope hints from Platform context, not user memory."""
    allowed = {'equipment', 'variant', 'platform', 'viewer_version',
               'sdk_version', 'firmware_version'}
    hints = {}
    conflicts = set()
    safe_label = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._+/-]{0,63}$')
    safe_version = re.compile(r'^[0-9][0-9A-Za-z._-]{0,31}$')
    for excerpt in excerpts:
        if not isinstance(excerpt, str):
            continue
        for key, value in _extract(excerpt).items():
            if key not in allowed or key in conflicts:
                continue
            parts = value if isinstance(value, list) else [value]
            pattern = safe_version if key.endswith('_version') else safe_label
            if not parts or len(parts) > 4 or not all(
                isinstance(part, str) and pattern.fullmatch(part) for part in parts
            ):
                continue
            if key in hints and hints[key] != value:
                hints.pop(key)
                conflicts.add(key)
            else:
                hints[key] = value
    return hints


def prepare_turn(message: str, *, previous=None, updates=None, topic_switch=None,
                 attachment_source_ids=(), image_source_ids=(), context_hints=None,
                 grounded_targets=None, request_schema: DaqRequestSchema | None = None,
                 clear_prior_grounding: bool = False) -> TurnPlan:
    """Prepare a turn without mutating prior state or granting facts/permissions.

    Pass the returned context into the next turn/checkpoint, tool_context() to the
    toolbox, context_note to Loop, and evidence_requirements() to EvidencePolicy.
    Exact requirement IDs must be echoed by governed tools as
    matched_requirement_ids before the policy can satisfy them.
    """
    if not isinstance(message, str) or not message.strip():
        raise ValueError('daq_message_invalid')
    if request_schema is not None and (not isinstance(request_schema, DaqRequestSchema)
                                       or request_schema.original_question != message):
        raise ValueError('daq_request_schema_invalid')
    old = DaqTaskContext.from_checkpoint(previous) if isinstance(previous, dict) else previous or DaqTaskContext()
    if not isinstance(old, DaqTaskContext):
        raise ValueError('daq_context_invalid')
    switched = ((request_schema.topic_switch if request_schema is not None else False)
                or explicit_topic_switch(message)) if topic_switch is None else topic_switch
    turn = old.turn + 1
    values = {} if switched else copy.deepcopy(old.values)
    steps = [] if switched else list(old.attempted_steps)
    explicit_updates = dict(updates or {})
    incoming = {**_extract(message), **explicit_updates}
    if (request_schema is not None and 'firmware_version' in incoming
            and 'firmware_version' not in explicit_updates
            and stated_current_firmware_version(message) is None):
        # A version selected for a structured evidence query describes the
        # document or candidate combination. It is not the user's installed
        # firmware unless the user explicitly says so.
        selected = str(incoming['firmware_version']).casefold().removeprefix('v')
        if any(str(goal.condition_map().get('firmware_version', '')).casefold()
               .removeprefix('v') == selected
               for goal in request_schema.goals if goal.kind == 'product'):
            incoming.pop('firmware_version')
    for key, raw in incoming.items():
        if key == 'attempted_steps':
            if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
                raise ValueError('daq_attempted_steps_invalid')
            steps = list(dict.fromkeys([*steps, *raw]))
            continue
        if key not in _FIELDS:
            raise ValueError('daq_context_field_unknown')
        if raw is None:
            values.pop(key, None)
            continue
        certainty = 'hypothesis' if re.search(r'可能|猜测|\b(?:maybe|possibly)\b', message, re.I) else 'asserted'
        if isinstance(raw, dict) and 'value' in raw:
            certainty = _certainty(raw.get('certainty', certainty))
            raw = raw['value']
        if key == 'equipment' and (not isinstance(raw, list) or not all(isinstance(item, str) for item in raw)):
            raise ValueError('daq_equipment_invalid')
        values[key] = UserAssertion(copy.deepcopy(raw), turn, certainty)
    if request_schema is not None:
        for key in _IDENTITY_SELECTOR_KEYS:
            old_selector = values.get(key)
            if old_selector is None or old_selector.origin_turn == turn:
                continue
            requested = {
                str(value).casefold().replace('×', 'x').replace(' ', '')
                for goal in request_schema.goals if goal.kind == 'product'
                for name, value in goal.condition_map().items() if name == key
            }
            grounded_conditions_preview = (
                grounded_targets.get('conditions', {})
                if isinstance(grounded_targets, dict) else {})
            if isinstance(grounded_conditions_preview, dict) and key in grounded_conditions_preview:
                requested.add(str(grounded_conditions_preview[key]).casefold()
                              .replace('×', 'x').replace(' ', ''))
            prior = str(old_selector.value).casefold().replace('×', 'x').replace(' ', '')
            if clear_prior_grounding or (requested and requested != {prior}):
                # A later explicit variant selection supersedes the old
                # user assertion; otherwise a third follow-up can revert it.
                values.pop(key, None)
    if request_schema is not None:
        capabilities = [goal.capability for goal in request_schema.goals]
    else:
        capabilities = [name for name, pattern in _INTENTS.items() if re.search(pattern, message, re.I)]
        if _DIAGNOSTIC.search(message):
            capabilities += ['search_knowledge', 'lookup_procedure', 'check_software_support']
        if not switched and len(message) <= 80 and _FOLLOWUP.search(message):
            capabilities = [*old.active_capabilities, *capabilities]
    if values.get('equipment') and 'resolve_entity' not in capabilities and request_schema is None:
        capabilities.insert(0, 'resolve_entity')
    status = 'partial' if capabilities else 'unknown'
    if not capabilities and request_schema is None:
        capabilities = ['search_knowledge']
    capabilities = list(dict.fromkeys(capabilities))
    topic_id = f'topic_{turn}' if switched else old.topic_id
    setup = None if switched else old.setup_statement
    if not switched and request_schema is not None \
            and any(goal.kind == 'unknown' for goal in request_schema.goals) \
            and any(goal.kind == 'product' for goal in request_schema.goals):
        setup = UserAssertion(message[:1000], turn,
                              'hypothesis' if re.search(r'可能|猜测|\b(?:maybe|possibly)\b',
                                                        message, re.I) else 'asserted')
    prior_grounding = {} if switched or clear_prior_grounding else copy.deepcopy(old.grounded_scope)
    if request_schema is not None and prior_grounding:
        prior_selectors = prior_grounding.get('conditions', {})
        if any(
            key in prior_selectors
            and str(prior_selectors[key]).casefold().replace('×', 'x').replace(' ', '')
            != str(value).casefold().replace('×', 'x').replace(' ', '')
            for goal in request_schema.goals if goal.kind == 'product'
            for key, value in goal.condition_map().items()
        ):
            # A user-selected new variant cannot inherit the old canonical
            # entity, even when the new variant is not in the reviewed view.
            prior_grounding = {}
    context = DaqTaskContext(topic_id, turn, values, tuple(steps), tuple(capabilities),
                             prior_grounding, setup)
    hints = copy.deepcopy(dict(context_hints or {}))
    if set(hints) - {'equipment', 'variant', 'platform', 'viewer_version',
                     'sdk_version', 'firmware_version'}:
        raise ValueError('daq_context_hints_invalid')
    hinted_conditions = {key: value for key, value in hints.items()
                         if key != 'equipment' and key not in values}
    grounded = copy.deepcopy(grounded_targets or {})
    if (not isinstance(grounded, dict) or set(grounded) - {'entities', 'conditions'} or
            not isinstance(grounded.get('entities', []), list) or
            not all(isinstance(value, str) and value for value in grounded.get('entities', [])) or
            not isinstance(grounded.get('conditions', {}), dict)):
        raise ValueError('daq_grounded_targets_invalid')
    if grounded.get('entities'):
        context = replace(context, grounded_scope=grounded)
    elif context.grounded_scope:
        grounded = copy.deepcopy(context.grounded_scope)
    grounded_conditions = {key: value for key, value in grounded.get('conditions', {}).items()
                           if key not in values and key not in hinted_conditions}
    used_hints = bool(hinted_conditions or ('equipment' in hints and 'equipment' not in values))
    scope = {'topic_id': topic_id,
             'entities': context.get('equipment', hints.get('equipment', grounded.get('entities', []))),
             'conditions': {**grounded_conditions, **hinted_conditions,
                            **{key: value.value for key, value in values.items() if key != 'equipment'}}}
    if request_schema is not None:
        # Measurement and per-port conditions belong to an individual schema
        # goal. A turn-wide regex or grounded phrase can refer to only one
        # clause of a comparison and must not filter its other goals.
        scope['conditions'].pop('resolution', None)
        scope['conditions'].pop('per_port', None)
        # Likewise, an extracted action word is not a condition on every
        # structured goal. The schema attaches a task to the relevant goal.
        scope['conditions'].pop('task', None)
    sdk_families = ({str(goal.condition_map().get('sdk')).casefold()
                     for goal in request_schema.goals
                     if goal.kind == 'product'
                     and goal.capability in {'sdk_evidence',
                                             'check_software_support'}
                     and goal.condition_map().get('sdk')}
                    if request_schema is not None else set())
    measurement_resolution = re.search(
        r'(?:在|以)\s*([0-9]{3,4})\s*[x×X]\s*([0-9]{3,4})\s*分辨率(?:下|时)',
        message, re.IGNORECASE)
    if request_schema is None and measurement_resolution:
        scope['conditions']['resolution'] = (
            f'{measurement_resolution.group(1)}x{measurement_resolution.group(2)}')
    if request_schema is None and re.search(r'供电输出|电源输出', message):
        aggregate_ports = re.search(
            r'(?:[二三四五六七八九十两多]|[2-9]\d*|所有|全部)\s*(?:个|路)?\s*(?:下游)?(?:端口|口)',
            message) and re.search(r'同时|总(?:共|计|输出)?|合计|整体|一起', message)
        if aggregate_ports or re.search(r'(?:总|合计)(?:的)?(?:供电|电源)输出', message):
            scope['conditions']['per_port'] = False
        elif re.search(r'(?:单个|每个|每路|单路).{0,12}(?:下游)?端口', message):
            scope['conditions']['per_port'] = True
    requirements = []
    fields = [name for name, pattern in _SPEC_FIELDS.items() if re.search(pattern, message, re.I)]
    if 'resolution' in fields and len(fields) > 1 and len(re.findall(
            r'分辨率|\bresolution\b', message, re.IGNORECASE)) == 1 and re.search(
                r'(?:在|以)\s*[0-9]{3,4}\s*[x×X]\s*[0-9]{3,4}\s*分辨率(?:下|时)',
                message, re.IGNORECASE):
        fields.remove('resolution')
    if 'resolution' in fields:
        rgb = bool(re.search(r'\bRGB\b|彩色|鱼眼', message, re.IGNORECASE))
        mono = bool(re.search(r'\bMono\b|单色|黑白', message, re.IGNORECASE))
        if rgb or mono:
            index = fields.index('resolution')
            fields[index:index + 1] = (["rgb_resolution"] if rgb else []) + (
                ["mono_resolution"] if mono else [])
    if 'frame_rate' in fields:
        rgb = bool(re.search(r'\bRGB\b|彩色|鱼眼', message, re.IGNORECASE))
        mono = bool(re.search(r'\bMono\b|单色|黑白', message, re.IGNORECASE))
        if rgb or mono:
            index = fields.index('frame_rate')
            fields[index:index + 1] = (["rgb_frame_rate"] if rgb else []) + (
                ["mono_frame_rate"] if mono else [])
    software = [name for name, pattern in _SOFTWARE.items() if re.search(pattern, message, re.I)]
    for capability in ([] if request_schema is not None else capabilities):
        dimensions = fields if capability == 'lookup_spec' and fields else (
            software if capability == 'check_software_support' and software else [None])
        for claim_field in dimensions:
            requirement = {'capability': capability, 'critical': True, 'status': 'unknown',
                           'evidence_class': 'governed_required', **copy.deepcopy(scope),
                           'conditions_authority': ('platform_context_unverified' if used_hints else 'user_supplied'),
                           'entity_status': 'unresolved'}
            if claim_field:
                requirement['software' if capability == 'check_software_support' else 'field'] = claim_field
            if status == 'unknown':
                requirement['reason'] = 'intent_or_entity_not_grounded'
            digest = hashlib.sha256(json.dumps(requirement, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            requirement['id'] = f'req_{capability}_{digest}'
            requirements.append(requirement)
    if request_schema is not None:
        product_goals = [goal for goal in request_schema.goals if goal.kind == 'product']
        distinct_goal_entities = {tuple(goal.entities) for goal in product_goals
                                  if goal.entities}
        selector_signatures = {
            tuple(sorted((key, str(value).casefold().replace('×', 'x').replace(' ', ''))
                         for key, value in goal.condition_map().items()
                         if key in _IDENTITY_SELECTOR_KEYS))
            for goal in product_goals
        }
        shared_grounding_safe = (all(len(goal.entities) == 1 for goal in product_goals)
                                 and len(distinct_goal_entities) <= 1
                                 and len(selector_signatures) <= 1)
        if not shared_grounding_safe:
            # A global canonical binding or its selector cannot stand in for
            # multiple members or for goals with different variant scopes.
            context = replace(context, grounded_scope={})
            for key in _IDENTITY_SELECTOR_KEYS:
                scope['conditions'].pop(key, None)
        for goal in request_schema.goals:
            if goal.kind == 'service':
                requirements.append({
                    'id': 'req_' + goal.id, 'goal_id': goal.id,
                    'capability': goal.capability, 'critical': True,
                    'status': 'unknown', 'evidence_class': 'service_contract',
                })
            elif goal.kind == 'unknown':
                requirements.append({
                    'id': 'req_' + goal.id, 'goal_id': goal.id,
                    'capability': 'clarify', 'critical': True,
                    'status': 'unknown', 'evidence_class': 'clarification_required',
                })
            elif goal.kind == 'attachment':
                provided = (image_source_ids if goal.capability == 'analyze_image'
                            else attachment_source_ids)
                ids = sorted(set(provided))
                if ids and not all(isinstance(item, str) and item for item in ids):
                    raise ValueError('daq_attachment_sources_invalid')
                requirements.append({
                    'id': 'req_' + goal.id, 'goal_id': goal.id,
                    'capability': goal.capability, 'critical': True,
                    'status': 'unknown',
                    'evidence_class': ('user_evidence_required' if ids
                                       else 'clarification_required'),
                    **({'source_ids': ids} if ids else {}),
                })
            elif goal.kind == 'product':
                goal_scope = copy.deepcopy(scope)
                goal_conditions = goal.condition_map()
                if (len(sdk_families) > 1 and goal.capability in {
                        'sdk_evidence', 'check_software_support'}):
                    # A turn-wide version or platform may belong to another
                    # named SDK in the same sentence. Each family must carry
                    # its own typed selectors to be used as evidence scope.
                    for key in ('sdk_version', 'sdk_release_date', 'platform'):
                        if key not in goal_conditions:
                            goal_scope['conditions'].pop(key, None)
                if goal.capability in {'lookup_spec', 'resolve_entity'}:
                    # In a structured request, claim selectors belong to the
                    # individual goal. Setup task, software and measurement
                    # context cannot filter an entity identity or an unrelated
                    # specification.
                    goal_scope['conditions'] = ({
                        key: (values[key].value if key in values
                              else grounded_conditions[key])
                        for key in _IDENTITY_SELECTOR_KEYS
                        if shared_grounding_safe
                        and (key in values or key in grounded_conditions)
                    })
                    if goal.capability == 'resolve_entity':
                        goal_conditions = {key: value for key, value in
                                           goal_conditions.items()
                                           if key in _IDENTITY_SELECTOR_KEYS}
                if (goal.capability == 'search_knowledge' and
                        any(key in goal_conditions for key in (
                            'sdk_release_date', 'viewer_release_date',
                            'firmware_version', 'viewer_version'))):
                    # A dated release-note goal has its own version scope.
                    # Other versions in the turn may belong to support goals.
                    for key in ('sdk_version', 'sdk_release_date',
                                'viewer_version', 'viewer_release_date',
                                'firmware_version'):
                        if key not in goal_conditions:
                            goal_scope['conditions'].pop(key, None)
                if shared_grounding_safe and grounded_targets and grounded_targets.get('entities'):
                    # This ID was resolved from the role-visible published
                    # entity view; the model's surface name remains a user
                    # assertion but does not become the evidence key.
                    goal_scope['entities'] = list(grounded_targets['entities'])
                elif not shared_grounding_safe:
                    goal_scope['entities'] = list(goal.entities)
                elif goal.entities and not goal_scope['entities']:
                    goal_scope['entities'] = list(goal.entities)
                goal_scope['conditions'].update(goal_conditions)
                question_constraints = {}
                if goal.capability == 'lookup_spec':
                    evidence_conditions = _SPEC_EVIDENCE_CONDITIONS
                    if (goal.field or '').casefold().endswith(
                            ('resolution', 'resolution_options')):
                        evidence_conditions = evidence_conditions - {'resolution'}
                    question_constraints = {
                        key: value for key, value in goal_scope['conditions'].items()
                        if key not in evidence_conditions
                    }
                    goal_scope['conditions'] = {
                        key: value for key, value in goal_scope['conditions'].items()
                        if key in evidence_conditions
                    }
                requirement = {
                    'id': 'req_' + goal.id, 'goal_id': goal.id,
                    'capability': goal.capability, 'critical': True,
                    'status': 'unknown', 'evidence_class': 'governed_required',
                    'delivery_required': goal.capability != 'resolve_entity',
                    **goal_scope,
                    'conditions_authority': ('platform_context_unverified' if used_hints
                                             and (goal.capability not in {
                                                 'lookup_spec', 'resolve_entity'}
                                                  or not goal.entities)
                                             else 'user_supplied'),
                    'entity_status': 'unresolved',
                }
                if question_constraints:
                    requirement['question_constraints'] = question_constraints
                if goal.unmapped_condition_keys and (
                        goal.capability != 'lookup_spec' or
                        bool(set(goal.unmapped_condition_keys) & _IDENTITY_SELECTOR_KEYS)):
                    requirement['reason'] = 'unmapped_condition_unverified'
                    requirement['unmapped_condition_keys'] = list(goal.unmapped_condition_keys)
                if goal.field:
                    requirement['field'] = goal.field
                requirements.append(requirement)
    for capability, provided in (
        ('search_attachments', attachment_source_ids),
        ('analyze_image', image_source_ids),
    ):
        if request_schema is not None and any(
                goal.kind == 'attachment' and goal.capability == capability
                for goal in request_schema.goals):
            continue
        if provided and (request_schema is None or request_schema.attachment_dependency in {
                'relevant', 'required_for_answer'}):
            ids = sorted(set(provided))
            if not all(isinstance(item, str) and item for item in ids):
                raise ValueError('daq_attachment_sources_invalid')
            payload = json.dumps([topic_id, capability, ids], sort_keys=True)
            requirements.append({
                'id': f'req_{capability}_' + hashlib.sha256(payload.encode()).hexdigest()[:16],
                'capability': capability, 'critical': True, 'status': 'unknown',
                'source_ids': ids, 'evidence_class': 'user_evidence_required',
            })
    planned = tuple(dict.fromkeys(item['capability'] for item in requirements))
    goal_lineage, scope_history, scope_invalidated = _goal_scope_provenance(
        message, request_schema, old, turn, switched)
    context = replace(context, goal_scope_history=scope_history)
    note = 'DAQ session context: user declarations only; not verified product facts.\n' + json.dumps(context.tool_context(), ensure_ascii=False, sort_keys=True)
    if request_schema is not None:
        note += '\nValidated request goals: ' + json.dumps({
            'original_question': request_schema.original_question,
            'effective_question': request_schema.effective_question,
            'request_kind': request_schema.request_kind,
            'goals': [asdict(goal) for goal in request_schema.goals],
        }, ensure_ascii=False, sort_keys=True)
    if attachment_source_ids or image_source_ids:
        note += ('\nAuthorized current-turn user attachment source_ids, not product facts. '
                 'Use only an exact image_source_id for analyze_image; never guess from a filename.\n'
                 + json.dumps({
                     'image_source_ids': sorted(set(image_source_ids or [])),
                     'text_source_ids': sorted(set(attachment_source_ids or [])),
                 }, ensure_ascii=False, sort_keys=True))
    if image_source_ids:
        note += ('\nDescribe only what analyze_image actually observed. Visual observation is not '
                 'a pixel measurement: do not estimate exact RGB/hex colors, dimensions, '
                 'fine texture, noise or compression artifacts. For a visibility-only '
                 'question, lead with visible content; mention missing product attribution '
                 'only if it matters to the question.')
    invalidated = []
    for key, prior in old.values.items():
        current = context.values.get(key)
        if current is None:
            reason = ('topic_switch' if switched else
                      'user_cleared' if key in incoming and incoming[key] is None else
                      'selector_invalidated' if key in _IDENTITY_SELECTOR_KEYS else
                      'context_invalidated')
        elif current.origin_turn == turn and current.value != prior.value:
            reason = 'user_correction'
        else:
            continue
        invalidated.append({'field': key, 'reason': reason})
    for item in scope_invalidated:
        if item not in invalidated:
            invalidated.append(item)
    if invalidated:
        note += '\nInvalidated prior user fields: ' + json.dumps(
            invalidated, ensure_ascii=False, sort_keys=True)
    return TurnPlan(context, requirements, planned, status, note, request_schema,
                    tuple(invalidated), goal_lineage)
