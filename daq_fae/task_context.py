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
from dataclasses import asdict, dataclass, field

_FIELDS = frozenset({
    'equipment', 'variant', 'topology', 'platform', 'viewer_version', 'sdk_version',
    'firmware_version', 'connection', 'power', 'recording_format', 'storage', 'task',
})
_SWITCH = re.compile(r'^(?:换个场景|换个问题|另一个问题|重新开始|new topic|switch topic)', re.I)
_FOLLOWUP = re.compile(r'继续|然后|还是|下一步|那|这个|它|\b(?:it|still|next|continue)\b', re.I)
_INTENTS = {
    'resolve_entity': r'设备|型号|变体|配置|套件|\b(?:device|model|variant|kit)\b',
    'lookup_spec': r'规格|参数|精度|分辨率|帧率|带宽|功耗|基线|\b(?:spec|specification|accuracy|resolution|fps|bandwidth)\b',
    'inspect_topology': r'接线|连接|主从|同步|组合|端口|供电|\b(?:topology|connection|sync|wiring|hub|power)\b',
    'lookup_procedure': r'步骤|安装|配置|采集|录制|落盘|保存|启动|\b(?:procedure|install|setup|record|capture|save)\b',
    'check_software_support': r'版本|兼容|支持|固件|viewer|sdk|firmware|\b(?:version|compatible|support)\b',
    'sdk_evidence': r'\bsdk\b|\bapi\b|\bros2?\b|\bpython\b|代码|接口',
    'official_links': r'链接|下载|官网|\b(?:link|download|official website)\b',
}
_DIAGNOSTIC = re.compile(r'失败|报错|日志|没有画面|断开|\b(?:error|timeout|failed|failure|log|disconnect)\b', re.I)
_TASKS = {
    'installation': r'安装|\binstall(?:ation)?\b',
    'configuration': r'配置|\bconfigur(?:e|ation)\b|\bsetup\b',
    'recording': r'录制|\brecord(?:ing)?\b',
    'capture': r'采集|\bcapture\b', 'storage': r'落盘|保存|\bsave\b',
    'startup': r'启动|\bstart(?:up)?\b',
}
_SOFTWARE = {'sdk': r'\bsdk\b', 'viewer': r'\bviewer\b', 'firmware': r'固件|\bfirmware\b'}
_SPEC_FIELDS = {
    'accuracy': r'精度|accuracy', 'resolution': r'分辨率|resolution',
    'frame_rate': r'帧率|\bfps\b', 'bandwidth': r'带宽|bandwidth',
    'power_consumption': r'功耗|power consumption', 'baseline': r'基线|baseline',
}


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

    def get(self, name, default=None):
        item = self.values.get(name)
        return copy.deepcopy(item.value) if item else default

    def to_checkpoint(self):
        return {'version': 1, 'topic_id': self.topic_id, 'turn': self.turn,
                'values': {key: asdict(value) for key, value in self.values.items()},
                'attempted_steps': list(self.attempted_steps),
                'active_capabilities': list(self.active_capabilities)}

    @classmethod
    def from_checkpoint(cls, checkpoint):
        if checkpoint.get('version') != 1:
            raise ValueError('daq_context_version_invalid')
        values = {}
        for key, raw in checkpoint.get('values', {}).items():
            if key not in _FIELDS or raw.get('authority') != 'user_supplied':
                raise ValueError('daq_context_authority_invalid')
            values[key] = UserAssertion(copy.deepcopy(raw['value']), int(raw['origin_turn']), _certainty(raw.get('certainty', 'asserted')))
        return cls(str(checkpoint['topic_id']), int(checkpoint['turn']), values,
                   tuple(checkpoint.get('attempted_steps', [])), tuple(checkpoint.get('active_capabilities', [])))

    def tool_context(self):
        return {'authority': 'user_supplied_only', **self.to_checkpoint()}


@dataclass(frozen=True)
class TurnPlan:
    context: DaqTaskContext
    requirements: list[dict]
    planned_capabilities: tuple[str, ...]
    planning_status: str
    context_note: str

    def evidence_requirements(self):
        return {'requirements': copy.deepcopy(self.requirements)}


def _certainty(value):
    if value not in {'asserted', 'hypothesis'}:
        raise ValueError('daq_context_certainty_invalid')
    return value


def _extract(message):
    updates = {}
    for key, pattern in (
        ('equipment', r'(?:设备|型号|equipment|device)\s*(?:是|为|[:：=]|is)\s*([^；;，,。\n]+)'),
        ('variant', r'(?:变体|硬件修订|variant|revision)\s*(?:是|为|[:：=]|is)\s*([^；;，,。\n]+)'),
        ('platform', r'(?:平台|操作系统|platform|os)\s*(?:是|为|[:：=]|is)\s*([^；;，,。\n]+)'),
    ):
        match = re.search(pattern, message, re.I)
        if match:
            value = match.group(1).strip()
            updates[key] = [part.strip() for part in re.split(r'\s*\+\s*|、', value)] if key == 'equipment' else value
    for key, label in (('viewer_version', 'viewer'), ('sdk_version', 'sdk'), ('firmware_version', r'固件|firmware')):
        matches = list(re.finditer(rf'(?:{label})\s*(?:版本)?\s*(?:为|是|[:：=]|is)?\s*v?([0-9]+(?:\.[0-9A-Za-z_-]+)+)', message, re.I))
        if matches:
            updates[key] = matches[-1].group(1)
    steps = re.findall(r'(?:已经|已尝试|试过|\btried\b)\s*([^；;，,。\n]+)', message, re.I)
    if steps:
        updates['attempted_steps'] = [step.strip() for step in steps]
    tasks = [name for name, pattern in _TASKS.items() if re.search(pattern, message, re.I)]
    if tasks:
        updates['task'] = tasks
    return updates


def prepare_turn(message: str, *, previous=None, updates=None, topic_switch=None,
                 attachment_source_ids=(), image_source_ids=()) -> TurnPlan:
    """Prepare a turn without mutating prior state or granting facts/permissions.

    Pass the returned context into the next turn/checkpoint, tool_context() to the
    toolbox, context_note to Loop, and evidence_requirements() to EvidencePolicy.
    Exact requirement IDs must be echoed by governed tools as
    matched_requirement_ids before the policy can satisfy them.
    """
    if not isinstance(message, str) or not message.strip():
        raise ValueError('daq_message_invalid')
    old = DaqTaskContext.from_checkpoint(previous) if isinstance(previous, dict) else previous or DaqTaskContext()
    if not isinstance(old, DaqTaskContext):
        raise ValueError('daq_context_invalid')
    switched = bool(_SWITCH.search(message.strip())) if topic_switch is None else topic_switch
    turn = old.turn + 1
    values = {} if switched else copy.deepcopy(old.values)
    steps = [] if switched else list(old.attempted_steps)
    incoming = {**_extract(message), **dict(updates or {})}
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
    capabilities = [name for name, pattern in _INTENTS.items() if re.search(pattern, message, re.I)]
    if _DIAGNOSTIC.search(message):
        capabilities += ['search_knowledge', 'lookup_procedure', 'check_software_support']
    if not switched and len(message) <= 80 and _FOLLOWUP.search(message):
        capabilities = [*old.active_capabilities, *capabilities]
    if values.get('equipment') and 'resolve_entity' not in capabilities:
        capabilities.insert(0, 'resolve_entity')
    status = 'partial' if capabilities else 'unknown'
    if not capabilities:
        capabilities = ['search_knowledge']
    capabilities = list(dict.fromkeys(capabilities))
    topic_id = f'topic_{turn}' if switched else old.topic_id
    context = DaqTaskContext(topic_id, turn, values, tuple(steps), tuple(capabilities))
    scope = {'topic_id': topic_id, 'entities': context.get('equipment', []),
             'conditions': {key: value.value for key, value in values.items() if key != 'equipment'}}
    requirements = []
    fields = [name for name, pattern in _SPEC_FIELDS.items() if re.search(pattern, message, re.I)]
    software = [name for name, pattern in _SOFTWARE.items() if re.search(pattern, message, re.I)]
    for capability in capabilities:
        dimensions = fields if capability == 'lookup_spec' and fields else (
            software if capability == 'check_software_support' and software else [None])
        for claim_field in dimensions:
            requirement = {'capability': capability, 'critical': True, 'status': 'unknown',
                           'evidence_class': 'governed_required', **copy.deepcopy(scope),
                           'conditions_authority': 'user_supplied', 'entity_status': 'unresolved'}
            if claim_field:
                requirement['software' if capability == 'check_software_support' else 'field'] = claim_field
            if status == 'unknown':
                requirement['reason'] = 'intent_or_entity_not_grounded'
            digest = hashlib.sha256(json.dumps(requirement, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            requirement['id'] = f'req_{capability}_{digest}'
            requirements.append(requirement)
    for capability, provided in (
        ('search_attachments', attachment_source_ids),
        ('analyze_image', image_source_ids),
    ):
        if provided:
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
    note = 'DAQ session context: user declarations only; not verified product facts.\n' + json.dumps(context.tool_context(), ensure_ascii=False, sort_keys=True)
    return TurnPlan(context, requirements, planned, status, note)
