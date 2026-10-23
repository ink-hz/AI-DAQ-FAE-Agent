"""Request-local DAQ evidence ledger for the shared Loop policy hook."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from copy import deepcopy
import json
import re

from src.agent.loop.answer_contract import AnswerSubmission
from src.agent.loop.evidence_policy import EvidenceDecision, EvidenceSnapshot
from src.agent.loop.tools import ToolResult

from daq_fae.service_contract import ServiceContract
from daq_fae.supporting_evidence import observed_support


def question_requirements(_question: str) -> dict[str, object]:
    """Represent the universal governed-evidence need before a domain planner exists.

    The empty release has no entity/schema facts to plan narrower claims. The
    requirement stays explicit so an empty plan cannot be mistaken for coverage.
    """
    return {"requirements": [{
        "id": "question_evidence", "capability": "search_knowledge", "critical": True,
    }]}


@dataclass(frozen=True)
class _Requirement:
    id: str
    capability: str
    critical: bool
    evidence_class: str
    source_ids: tuple[str, ...]
    field: str | None
    entities: tuple[str, ...]
    identity_conditions: tuple[tuple[str, str], ...]
    measurement_conditions: tuple[tuple[str, str], ...]
    delivery_required: bool


def _spec_field_key(field: str) -> str:
    return field.replace('_frame_rate', '_fps').casefold()


def _entity_label(value: str) -> str:
    return re.sub(r'\s+', '', value.casefold().replace('×', 'x'))


def _capture_failure_claims(answer: str) -> list[str | None]:
    """Return IDs for affirmative capture-start failures in visible prose."""
    claims: list[str | None] = []
    for clause in re.split(r'[。；;，,\n]+|但|不过|然而|并且|所以|因此|而', answer):
        for match in re.finditer(
                r'(?:采集)?启动(?:请求)?[^。；;，,\n]{0,12}?失败|'
                r'capture[_ ]start[^.;,\n]{0,32}?failed', clause, re.I):
            prefix = clause[max(0, match.start() - 12):match.start()]
            phrase = match.group()
            suffix = clause[match.end():match.end() + 3]
            if (re.search(r'不能|无法|没有证据|不代表|不等于|未证实|尚未|'
                          r'是否|能否|\b(?:cannot|can.t|not|unknown|whether)\b',
                          prefix, re.I)
                    or re.search(r'并未|尚未|没有|不曾|未曾|未必|是否|能否', phrase)
                    or re.match(r'\s*(?:了|过)?(?:吗|么|\?|？)', suffix)):
                continue
            request_ids = re.findall(r'\bR-[A-Za-z0-9_.-]+\b', clause[:match.end()], re.I)
            claims.extend(value.casefold() for value in request_ids[-1:])
            if not request_ids:
                claims.append(None)
    return claims


def _capture_event_status(text: str) -> tuple[set[str | None], set[str | None]]:
    """Parse warning IDs and explicit *request* failures from one attachment."""
    warned: set[str | None] = set()
    failed: set[str | None] = set()
    for line in text.splitlines():
        if not re.search(r'capture_start|采集启动', line, re.I):
            continue
        request = re.search(r'\brequest_id\s*[:=]\s*([A-Za-z0-9_.-]+)', line, re.I)
        request_id = request.group(1).casefold() if request else None
        if re.search(r'\bwarn\b|timeout|超时|告警|警告', line, re.I):
            warned.add(request_id)
        terminal = bool(re.search(
            r'^\s*(?:\d{4}-\d{2}-\d{2}\S*\s+)?'
            r'(?:(?:INFO|DEBUG|ERROR|TRACE)\s+)?'
            r'(?:result|final|terminal)\s+capture_start\b'
            r'[^\n]*\bstatus\s*[:=]\s*(?:failed|failure|error)\b|'
            r'\bcapture_start\b[^\n]*\b(?:request_status|capture_status)\s*[:=]\s*'
            r'(?:failed|failure|error)\b|'
            r'^\s*capture_start\s+failed\s*(?:$|[;,.])|'
            r'采集启动请求(?:的)?(?:最终)?(?:状态|结果)\s*[:：=]\s*失败',
            line, re.I))
        if terminal:
            failed.add(request_id)
    return warned, failed


def _capture_failure_supported(
        claim_id: str | None, attachment_text: Mapping[str, list[str]],
        question: str) -> bool:
    events = [_capture_event_status('\n'.join(chunks))
              for chunks in attachment_text.values()]
    if claim_id is None:
        question_ids = {
            value.casefold() for value in re.findall(
                r'\bR-[A-Za-z0-9_.-]+\b', question, re.I)
        }
        if question_ids:
            return all(_capture_failure_supported(value, attachment_text, '')
                       for value in question_ids)
    if claim_id is not None:
        if any(claim_id in warned and claim_id not in failed
               for warned, failed in events):
            return False
        return any(claim_id in failed for _warned, failed in events)
    warnings = [(warned, failed) for warned, failed in events if warned]
    if warnings:
        return all(warned <= failed for warned, failed in warnings)
    return any(failed for _warned, failed in events)


class DaqEvidencePolicy:
    # The shared Loop keeps its camera submit_answer schema unchanged. DAQ
    # opts into source-bound, visible answer units for requested product goals.
    answer_delivery_units_enabled = True

    def __init__(self, *, allow_dev_source_check: bool = False,
                 service_contract: ServiceContract | None = None,
                 question: str = '',
                 answer_language: str = 'zh',
                 allowed_chinese_terms: tuple[str, ...] = (),
                 allowed_tools: frozenset[str] | None = None,
                 knowledge=None, role: str | None = None):
        if answer_language not in {'zh', 'en'}:
            raise ValueError('daq_answer_language_invalid')
        self.allow_dev_source_check = allow_dev_source_check
        self.service_contract = service_contract
        self.question = question
        self.answer_language = answer_language
        self.allowed_chinese_terms = allowed_chinese_terms
        self.allowed_tools = allowed_tools
        if knowledge is not None and not role:
            raise ValueError('daq_policy_knowledge_role_required')
        self.evidence_entities = ({row['id']: row for row in knowledge.records_for(role)
                                   if row['kind'] == 'entity'} if knowledge is not None else {})
        self._session: DaqEvidenceSession | None = None

    @property
    def last_submission(self) -> AnswerSubmission | None:
        return self._session.last_submission if self._session is not None else None

    def begin(self, requirements: Mapping[str, object]) -> "DaqEvidenceSession":
        raw = requirements.get("requirements", [])
        if not isinstance(raw, list):
            raise ValueError("daq_requirements_invalid")
        parsed: list[_Requirement] = []
        ids: set[str] = set()
        for entry in raw:
            if not isinstance(entry, dict):
                raise ValueError("daq_requirement_invalid")
            requirement_id = entry.get("id")
            capability = entry.get("capability")
            if (not isinstance(requirement_id, str) or not requirement_id.strip()
                    or not isinstance(capability, str) or not capability.strip()
                    or requirement_id in ids):
                raise ValueError("daq_requirement_invalid")
            evidence_class = entry.get("evidence_class", "governed_required")
            source_ids = entry.get("source_ids", [])
            if (evidence_class not in {"governed_required", "user_evidence_required",
                                       "service_contract", "clarification_required"}
                    or not isinstance(source_ids, list)
                    or any(not isinstance(value, str) or not value for value in source_ids)
                    or (evidence_class == "user_evidence_required" and not source_ids)):
                raise ValueError("daq_requirement_evidence_class_invalid")
            ids.add(requirement_id)
            entities = entry.get('entities') or []
            conditions = entry.get('conditions') or {}
            if not isinstance(entities, list) or not isinstance(conditions, dict):
                raise ValueError('daq_requirement_invalid')
            parsed.append(_Requirement(
                requirement_id, capability, entry.get("critical", True) is True,
                evidence_class, tuple(dict.fromkeys(source_ids)),
                entry.get('field') if isinstance(entry.get('field'), str) else None,
                tuple(value.casefold() for value in entities if isinstance(value, str)),
                tuple((key, str(value).casefold()) for key, value in conditions.items()
                      if key in {'variant', 'resolution_variant', 'hardware_revision'}),
                tuple((key, _entity_label(str(value))) for key, value in conditions.items()
                      if key == 'resolution'),
                entry.get('delivery_required') is True,
            ))
        if not parsed:
            raise ValueError("daq_requirements_empty")
        self._session = DaqEvidenceSession(
            tuple(parsed), allow_dev_source_check=self.allow_dev_source_check,
            service_contract=self.service_contract, question=self.question,
            answer_language=self.answer_language,
            allowed_chinese_terms=self.allowed_chinese_terms,
            allowed_tools=self.allowed_tools,
            raw_requirements=deepcopy(raw), evidence_entities=self.evidence_entities)
        return self._session


class DaqEvidenceSession:
    def __init__(self, requirements: tuple[_Requirement, ...], *,
                 allow_dev_source_check: bool = False,
                 service_contract: ServiceContract | None = None,
                 question: str = '',
                 answer_language: str = 'zh',
                 allowed_chinese_terms: tuple[str, ...] = (),
                 allowed_tools: frozenset[str] | None = None,
                 raw_requirements: list[dict] | None = None,
                 evidence_entities: dict[str, dict] | None = None):
        self.requirements = requirements
        self.last_submission: AnswerSubmission | None = None
        self.status = {
            item.id: ('satisfied' if item.evidence_class == 'service_contract'
                      and service_contract is not None
                      and service_contract.supports(item.capability) else 'unknown')
            for item in requirements
        }
        self.actual: list[str] = []
        self.tool_failed = False
        self.attachment_failed = False
        self.variant_selector_missing = False
        self._observed_user_sources = {item.id: set() for item in requirements}
        self._observed_attachment_text: dict[str, list[str]] = {}
        self._internal_provenance: set[str] = set()
        self._public_sdk_symbols: set[str] = set()
        self._reviewed_interface_text: list[str] = []
        self._observed_numeric_specs: set[tuple[str, int | float, str, str, str]] = set()
        self._alternative_numeric_measurements: set[tuple[int | float, str, str, str]] = set()
        self._resolved_entities_by_label: dict[str, set[str]] = {}
        self._deliverable_sources: dict[str, set[str]] = {
            item.id: set() for item in requirements}
        self._required_delivery_sources: dict[str, set[str]] = {}
        self._supporting_sources: dict[str, set[str]] = {}
        self._raw_requirements = deepcopy(raw_requirements or [])
        self._evidence_entities = deepcopy(evidence_entities or {})
        self.question = question
        self.allow_dev_source_check = allow_dev_source_check
        self.answer_language = answer_language
        self.allowed_chinese_terms = allowed_chinese_terms
        self.allowed_tools = allowed_tools

    def observe(self, tool_name: str, result: ToolResult) -> None:
        if tool_name not in self.actual:
            self.actual.append(tool_name)
        if result.status == "tool_error":
            code = result.content.get("error") if isinstance(result.content, dict) else None
            if (code == "tool_outside_request_goals" and
                    self.allowed_tools is not None and
                    tool_name not in self.allowed_tools):
                # The DAQ pre-loop withheld this tool. No evidence was read or
                # failed, so the attempt cannot certify a goal or poison a
                # truthful abstention about the planned evidence gaps.
                return
            if tool_name == "analyze_image" and code in {
                "vision_unavailable", "vision_timeout", "vision_http_error",
                "vision_transport_error", "vision_invalid_response",
                "vision_output_truncated", "vision_runtime_error",
            }:
                self.attachment_failed = True
            else:
                self.tool_failed = True
            return
        content = result.content if isinstance(result.content, dict) else {}
        if result.status == 'ok' and tool_name in {'search_attachments', 'read_attachment'}:
            user_sources = {
                source.get('source_id') for source in result.sources
                if isinstance(source, dict) and source.get('type') == 'user_attachment'
            }
            required_sources = {
                source_id for item in self.requirements
                if item.evidence_class == 'user_evidence_required'
                for source_id in item.source_ids
            }
            user_sources.intersection_update(required_sources)
            if tool_name == 'search_attachments':
                for hit in content.get('hits', []):
                    if (isinstance(hit, dict) and hit.get('source_id') in user_sources
                            and isinstance(hit.get('text'), str)):
                        self._observed_attachment_text.setdefault(
                            hit['source_id'], []).append(hit['text'])
            elif (content.get('source_id') in user_sources
                  and isinstance(content.get('text'), str)):
                self._observed_attachment_text.setdefault(
                    content['source_id'], []).append(content['text'])
        if tool_name == 'lookup_spec' and result.status == 'not_found' and \
                content.get('reason') == 'variant_selector_required':
            self.variant_selector_missing = True
        if result.status == "ok":
            if (tool_name == 'resolve_entity' and _has_governed_source(
                    result, allow_dev_source_check=self.allow_dev_source_check)):
                for match in content.get('matches', []):
                    if not isinstance(match, dict) or match.get('kind') != 'entity':
                        continue
                    entity_id = match.get('id')
                    data = match.get('data') or {}
                    if not isinstance(entity_id, str) or not isinstance(data, dict):
                        continue
                    aliases = data.get('aliases') or []
                    for label in [data.get('name'), *aliases]:
                        if isinstance(label, str) and label.strip():
                            self._resolved_entities_by_label.setdefault(
                                _entity_label(label), set()).add(entity_id.casefold())
            if (tool_name == 'lookup_spec' and _has_governed_source(
                    result, allow_dev_source_check=self.allow_dev_source_check)):
                spec_requirements = [item for item in self.requirements
                                     if item.capability == 'lookup_spec' and item.field]
                wanted_fields = {_spec_field_key(item.field) for item in spec_requirements}
                if (re.search(r'\bHUB\b|集线', self.question, re.I)
                        and re.search(r'(?:\d+|[一二两三四五六七八九十])\s*(?:个)?\s*'
                                      r'(?:下游)?\s*口', self.question)):
                    wanted_fields.add('downstream_port_count')
                for match in content.get('matches', []):
                    if not isinstance(match, dict) or match.get('kind') != 'claim' \
                            or match.get('status') != 'verified':
                        continue
                    data = match.get('data') or {}
                    scope = match.get('scope') or {}
                    if not isinstance(data, dict) or not isinstance(scope, dict):
                        continue
                    field = data.get('field')
                    value = data.get('value')
                    if (not isinstance(field, str) or
                            _spec_field_key(field) not in wanted_fields or
                            type(value) not in (int, float)):
                        continue
                    if (_spec_field_key(field) == 'downstream_port_count'
                            and not spec_requirements
                            and (str(scope.get('product') or '').casefold() != 'hub'
                                 or str(data.get('entity_id') or 'entity:hub').casefold()
                                 != 'entity:hub')):
                        continue
                    matching_requirements = [item for item in spec_requirements if (
                            _spec_field_key(item.field) == _spec_field_key(field)
                            and (not item.entities or any(
                                str(data.get('entity_id') or '').casefold() == entity
                                or str(data.get('entity_id') or '').casefold() in
                                self._resolved_entities_by_label.get(
                                    _entity_label(entity), set())
                                or (isinstance(content.get('query_entity'), str)
                                    and _entity_label(content['query_entity']) ==
                                    _entity_label(entity))
                                for entity in item.entities))
                            and all(str(scope.get(key) or '').casefold() == expected
                                    for key, expected in item.identity_conditions)
                            )]
                    if spec_requirements and not matching_requirements:
                        continue
                    conditions = data.get('conditions') or {}
                    if not isinstance(conditions, dict):
                        conditions = {}
                    source_resolution = _entity_label(str(conditions.get('resolution') or ''))
                    for item in matching_requirements:
                        if any(key == 'resolution' and expected != source_resolution
                               for key, expected in item.measurement_conditions):
                            if source_resolution:
                                self._alternative_numeric_measurements.add(
                                    (value, str(data.get('unit') or ''), source_resolution,
                                     str(scope.get('resolution_variant') or '')))
                    self._observed_numeric_specs.add((
                        _spec_field_key(field), value,
                        str(data.get('unit') or ''),
                        str(scope.get('resolution_variant') or ''),
                        str(conditions.get('resolution') or ''),
                    ))
            for match in content.get("matches", []):
                if isinstance(match, dict):
                    self._reviewed_interface_text.extend(
                        value for key in ('answer', 'summary', 'statement', 'title', 'text')
                        if isinstance((value := match.get(key)), str)
                    )
                    if tool_name == "sdk_evidence":
                        sdk_tokens = []
                        for field in ("symbols", "code_tokens"):
                            values = match.get(field, [])
                            if isinstance(values, list):
                                sdk_tokens.extend(values)
                        self._public_sdk_symbols.update(
                            symbol.casefold() for symbol in sdk_tokens
                            if isinstance(symbol, str) and
                            re.fullmatch(r"[A-Za-z][A-Za-z0-9_:]*", symbol)
                        )
                    for value in (match.get("id"), (match.get("data") or {}).get("field")):
                        if isinstance(value, str) and value:
                            self._internal_provenance.add(value.casefold())
            for source in result.sources:
                if not isinstance(source, dict):
                    continue
                source_id = source.get("source_id")
                if isinstance(source_id, str) and source_id:
                    self._internal_provenance.add(source_id.casefold())
                release_id = source.get("release_id")
                if isinstance(release_id, str) and release_id:
                    self._internal_provenance.add(release_id.casefold())
                for ref in source.get("source_refs", []):
                    if isinstance(ref, dict):
                        if isinstance(ref.get("path"), str):
                            self._internal_provenance.add(ref["path"].casefold())
                        if isinstance(ref.get("member"), str):
                            self._internal_provenance.add(ref["member"].casefold())
                            self._internal_provenance.add(ref["member"].rsplit("/", 1)[-1].casefold())
        matched = content.get("matched_requirement_ids", [])
        matched_ids = set(matched) if isinstance(matched, list) and all(
            isinstance(value, str) for value in matched
        ) else set()
        if result.status == 'ok' and _has_governed_source(
                result, allow_dev_source_check=self.allow_dev_source_check):
            visible_source_ids = _governed_source_ids(
                result, allow_dev_source_check=self.allow_dev_source_check)
            if tool_name == 'lookup_spec':
                self._supporting_sources.update(observed_support(
                    content, visible_source_ids, self._raw_requirements, self._evidence_entities,
                    allow_dev_source_check=self.allow_dev_source_check))
            exact = content.get('requirement_source_ids')
            if isinstance(exact, dict):
                for requirement_id, source_ids in exact.items():
                    if (requirement_id in self._deliverable_sources
                            and requirement_id in matched_ids and isinstance(source_ids, list)
                            and all(isinstance(value, str) for value in source_ids)):
                        self._deliverable_sources[requirement_id].update(
                            set(source_ids) & visible_source_ids)
                        required = content.get('required_delivery_source_ids', {})
                        members = (required.get(requirement_id)
                                   if isinstance(required, dict) else None)
                        if isinstance(members, list) and all(
                                isinstance(member, str) for member in members):
                            # An exhaustive collection is a stronger delivery
                            # contract than merely citing an arbitrary subset.
                            # Only actual, verified sources of this goal count.
                            self._required_delivery_sources.setdefault(
                                requirement_id, set()).update(
                                    set(members) & set(source_ids) & visible_source_ids)
            elif (len(matched_ids) == len(visible_source_ids) == 1
                  and next(iter(matched_ids)) in self._deliverable_sources):
                self._deliverable_sources[next(iter(matched_ids))].update(
                    visible_source_ids)
        empty_release = content.get("reason") == "empty_knowledge_release"
        for item in self.requirements:
            if item.evidence_class in {'service_contract', 'clarification_required'}:
                continue
            if empty_release and result.status == "not_found" and item.evidence_class == "governed_required":
                if self.status[item.id] != "satisfied":
                    self.status[item.id] = "missing"
                continue
            if item.capability != tool_name:
                continue
            if item.evidence_class == "user_evidence_required":
                if result.status == "ok":
                    observed = {
                        source.get("source_id") for source in result.sources
                        if isinstance(source, dict) and source.get("type") == "user_attachment"
                    }
                    self._observed_user_sources[item.id].update(
                        observed.intersection(item.source_ids)
                    )
                    if set(item.source_ids) <= self._observed_user_sources[item.id]:
                        self.status[item.id] = "satisfied"
                elif result.status == "not_found":
                    self.status[item.id] = "missing"
                continue
            if item.id not in matched_ids and not (empty_release and result.status == "not_found"):
                continue
            previous = self.status[item.id]
            if result.status == "conflict" or (
                tool_name == 'lookup_spec' and result.status == 'not_found'
                and content.get('reason') == 'reviewed_conflict_notice'
                and content.get('claim_status') == 'conflict'
            ):
                self.status[item.id] = "conflict"
            elif previous == "conflict":
                continue
            elif result.status == "ok" and _has_governed_source(
                    result, allow_dev_source_check=self.allow_dev_source_check):
                self.status[item.id] = "satisfied"
            elif result.status == "not_found" and previous != "satisfied":
                self.status[item.id] = "missing"

    def evaluate(self, submission: AnswerSubmission) -> EvidenceDecision:
        self.last_submission = submission
        fields = (submission.conclusion, submission.basis, submission.cautions,
                  submission.next_steps, submission.missing,
                  submission.pending_confirmation, submission.handoff_reason,
                  submission.unresolved_constraints)
        visible_text = '\n'.join(value for value in (
            *fields, *(unit.text for unit in submission.delivery_units)) if value)
        failure_claims = (_capture_failure_claims(visible_text)
                          if self._observed_attachment_text else [])
        if any(not _capture_failure_supported(claim_id, self._observed_attachment_text,
                                              self.question)
               for claim_id in failure_claims):
            return EvidenceDecision(
                'request_evidence', 'daq_attachment_terminal_outcome_unverified',
                ('The user log reports a capture-start event, but does not establish '
                 'that the request failed. Describe the observed warning and leave '
                 'the request outcome unknown unless the log explicitly records it.'
                 if self.answer_language == 'en' else
                 '用户日志只记录采集启动事件，未证明请求最终失败。请只描述已看到的告警；'
                 '若日志没有明确的失败结果，就把请求结果保留为未知。'),
            )
        if r'\n' in visible_text:
            return EvidenceDecision(
                'request_evidence', 'daq_literal_escape_in_answer',
                ('Replace literal backslash-n sequences with actual paragraph or '
                 'list breaks. Keep the same facts, conditions and outcome.'
                 if self.answer_language == 'en' else
                 '请把正文中的字面量反斜杠 n 改成真正的换行或列表分行；'
                 '保留原有事实、适用条件和终态。'),
            )
        if re.search(r'(?i)\b[A-Za-z][A-Za-z0-9_.-]*\.(?:cpp|c|h|hpp|java|swift)\b',
                     visible_text):
            return EvidenceDecision(
                'request_evidence', 'daq_source_filename_in_answer',
                ('Describe the verified API or example without source file names. '
                 'Keep public API symbols, output names, conditions and outcome.'
                 if self.answer_language == 'en' else
                 '请改写正文中的源码文件名，只说明已核对的接口或示例；'
                 '保留公开 API、输出文件名、适用条件和终态。'),
            )
        if self.answer_language == 'en':
            for term in self.allowed_chinese_terms:
                if isinstance(term, str) and term:
                    visible_text = visible_text.replace(term, '')
            if re.search(r'[\u3400-\u9fff]', visible_text):
                return EvidenceDecision(
                    'request_evidence', 'daq_answer_language_mismatch',
                    'Rewrite every answer field in English, including the conclusion, '
                    'basis, cautions, missing evidence and next steps. Preserve the '
                    'same facts, evidence limits, outcome and product names.',
                )
        elif (len(re.findall(r'\b[A-Za-z]{2,}\b', visible_text)) >= 20
              and len(re.findall(r'[\u3400-\u9fff]', visible_text)) < 8):
            return EvidenceDecision(
                'request_evidence', 'daq_answer_language_mismatch',
                '请把结论、依据、注意、缺口和下一步都改用中文陈述；保留相同事实、'
                '证据边界和终态，产品名、单位及 SDK 名称可保留原文。',
            )
        prose = "\n".join(value for value in (
            submission.conclusion, submission.basis, submission.cautions,
            submission.next_steps, submission.missing, submission.pending_confirmation,
            submission.handoff_reason, submission.unresolved_constraints,
            *(unit.text for unit in submission.delivery_units),
        ) if value).casefold()
        code_scan = prose
        # A symbol the user typed is public conversation content, even when
        # this turn has no SDK hit. It must not be mistaken for an internal
        # schema key solely because it contains underscores.
        for symbol in sorted(set(re.findall(
                r'\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b', self.question)),
                key=len, reverse=True):
            code_scan = re.sub(r'(?<![a-z0-9_])' + re.escape(symbol.casefold()) +
                               r'(?![a-z0-9_])', '', code_scan)
        for symbol in sorted(self._public_sdk_symbols, key=len, reverse=True):
            code_scan = re.sub(r'(?<![a-z0-9_])' + re.escape(symbol) +
                               r'(?![a-z0-9_])', '', code_scan)
        if (re.search(r'\b[a-z]+(?:_[a-z]+)+\b(?!\.[a-z0-9]+)', code_scan)
                or re.search(r'\b(?:claim|entity|topology|procedure|software|link):[a-z0-9_.:-]+', prose)
                or re.search(r'第\s*\d+\s*(?:行|页)|\b(?:line|page)\s+\d+\b', prose)
                or any(token in prose for token in self._internal_provenance)):
            return EvidenceDecision(
                "request_evidence", "daq_internal_provenance_in_answer",
                ("Rewrite the answer in natural English. Remove internal record IDs, "
                 "field codes, tool status codes, source file paths and page/line locators from every answer "
                 "field. Keep the same facts, conditions, evidence gaps and outcome. "
                 "Sources are already attached as structured metadata."
                 if self.answer_language == 'en' else
                 "请重新提交终稿：正文只用自然中文说明结论、适用条件和必要缺口；"
                 "删除内部记录 ID、英文内部字段代码、原始文件路径和页码行号。"
                 "来源已保留在结构化 sources，不必重复取证。"),
            )
        if self.question and 'sdk_evidence' in self.actual:
            grounded_text = (self.question + '\n' +
                             '\n'.join(self._reviewed_interface_text)).casefold()
            for pattern in (r'\busb\b', r'\bwi[ -]?fi\b', r'蓝牙|\bble\b',
                            r'\bhub\b', r'网络|\bnetwork\b', r'以太网|\bethernet\b'):
                if re.search(pattern, prose, re.I) and not re.search(
                        pattern, grounded_text, re.I):
                    return EvidenceDecision(
                        'request_evidence', 'daq_unrequested_interface_in_answer',
                        ('Remove connection types that were only guessed in tool calls. '
                         'State the verified package facts and ask for the actual connection.'
                         if self.answer_language == 'en' else
                         '删除仅在工具调用中猜测、用户与已核对来源均未给出的连接方式；'
                         '保留包内事实，并只询问实际连接方式。'),
                    )
        if self.tool_failed:
            return EvidenceDecision("reject", "daq_evidence_tool_failure")
        if self.attachment_failed and submission.outcome == "resolved":
            return EvidenceDecision("reject", "daq_attachment_evidence_failed")
        delivered_ids: set[str] = set()
        delivered_sources: dict[str, set[str]] = {}
        by_id = {item.id: item for item in self.requirements}
        for unit in submission.delivery_units:
            item = by_id.get(unit.requirement_id)
            supporting = self._supporting_sources.get(unit.requirement_id)
            if ((item is None and supporting is None)
                    or (item is not None and self.status[item.id] != 'satisfied')):
                return EvidenceDecision('request_evidence',
                                        'daq_delivery_requirement_unverified',
                                        '删除没有本轮已审目标支持的交付单元。')
            allowed_sources = (self._deliverable_sources[item.id]
                               if item is not None else supporting)
            if not set(unit.source_ids) <= allowed_sources:
                return EvidenceDecision('request_evidence',
                                        'daq_delivery_source_mismatch',
                                        '交付单元必须引用该目标在本轮实际匹配的来源。')
            if item is not None:
                delivered_ids.add(item.id)
            delivered_sources.setdefault(unit.requirement_id, set()).update(unit.source_ids)
        missing_members = {
            requirement_id: sorted(required - delivered_sources.get(requirement_id, set()))
            for requirement_id, required in self._required_delivery_sources.items()
            if required - delivered_sources.get(requirement_id, set())
        }
        if missing_members:
            return EvidenceDecision(
                'request_evidence', 'daq_collection_delivery_incomplete',
                ('The requested collection is incomplete. Deliver every returned member '
                 'with its own scope; variants may be grouped under one product, '
                 'but retain all their sources. Missing source IDs: '
                 if self.answer_language == 'en' else
                 '所请求集合尚未完整交付。请覆盖工具返回的每个成员；'
                 '同一产品的变体可合并说明，但必须保留各变体来源。缺少来源 ID：')
                + json.dumps(missing_members, ensure_ascii=False, sort_keys=True),
            )
        missing_deliveries = [item.id for item in self.requirements
                              if item.delivery_required
                              and self.status[item.id] == 'satisfied'
                              and item.id not in delivered_ids]
        if missing_deliveries:
            source_map = {requirement_id: sorted(
                self._deliverable_sources[requirement_id])
                for requirement_id in missing_deliveries}
            return EvidenceDecision(
                'request_evidence', 'daq_known_goal_delivery_missing',
                '本轮已核实的目标尚未逐项交付。请将每项目标的适用条件和已知事实写入 '
                'delivery_units；当前缺失目标及可用来源是 '
                + json.dumps(source_map, ensure_ascii=False, sort_keys=True)
                + '。每个目标单独一项，source_ids 只能取对应列表；'
                  '空列表表示尚无可绑定来源，须继续取证。'
                  '未知目标继续保留在 missing。')
        if submission.outcome != "resolved":
            missing_fps_resolutions = {
                expected for item in self.requirements
                if item.capability == 'lookup_spec'
                and item.field and _spec_field_key(item.field).endswith('_fps')
                and self.status[item.id] != 'satisfied'
                for key, expected in item.measurement_conditions
                if key == 'resolution'
            }
            if missing_fps_resolutions and self._alternative_numeric_measurements:
                for clause in re.split(r'[。；;\n]+|但|不过|然而|却',
                                       '\n'.join((submission.conclusion,
                                                  submission.basis,
                                                  submission.cautions,
                                                  submission.next_steps,
                                                  submission.missing,
                                                  submission.pending_confirmation,
                                                  submission.handoff_reason,
                                                  submission.unresolved_constraints,
                                                  *(unit.text for unit in
                                                    submission.delivery_units)))):
                    uncertainty = bool(re.search(
                            r'未知|待核实|未(?:核实|证实|确认|查到|找到|给出|标明|获证)|'
                            r'无法(?:确认|判断|证明)|不能(?:确认|证明|断言|填写|填|据此)|'
                            r'不(?:能|可)(?:确认|证明|断言|填写|填)|'
                            r'\b(?:unknown|unverified|not\s+(?:verified|confirmed|known)|'
                            r'cannot\s+(?:confirm|verify|conclude))\b',
                            clause, re.I))
                    for value_match in re.finditer(
                            r'(?<![\d.])(?P<value>\d+(?:\.\d+)?)\s*'
                            r'(?P<unit>fps|帧\s*[/／]\s*秒|帧每秒)'
                            r'(?![A-Za-z0-9_])', clause, re.I):
                        value = float(value_match.group('value'))
                        prefix = clause[:value_match.start()]
                        scoped_prefix = prefix.rsplit('、', 1)[-1]
                        affirmative = bool(re.search(
                            r'(?:为|是|等于|可达|达到|支持|最大|填为|'
                            r'填入|可以填|可填|暂填|回填|填)\s*$',
                            prefix, re.I)) and not bool(re.search(
                                r'能否|是否|可否|能不能|可不可以|吗|\?|？', clause))
                        scoped_resolutions = list(re.finditer(
                            r'(?<!\d)\d{3,4}\s*[×xX]\s*\d{3,4}(?!\d)',
                            scoped_prefix))
                        local_resolution = (_entity_label(scoped_resolutions[-1].group())
                                            if scoped_resolutions else '')
                        postfix_resolution_match = re.match(
                            r'[\s,，(（]*(?:@|对应|在|测量分辨率\s*(?:是|为|[:：])?)\s*'
                            r'(\d{3,4}\s*[×xX]\s*\d{3,4})',
                            clause[value_match.end():])
                        postfix_resolution = (
                            _entity_label(postfix_resolution_match.group(1))
                            if postfix_resolution_match else '')
                        current_segment = re.split(r'[，,、]', prefix)[-1]
                        if postfix_resolution and (
                                not local_resolution or
                                local_resolution == postfix_resolution or
                                not re.search(
                                    r'(?<!\d)\d{3,4}\s*[×xX]\s*\d{3,4}(?!\d)',
                                    current_segment)):
                            local_resolution = postfix_resolution
                        stated_versions = list(re.finditer(
                            r'(?<!\d)(\d{4}(?:\s*[×xX]\s*\d{4})?)'
                            r'\s*版(?:本)?', scoped_prefix))
                        stated_variant = (_entity_label(stated_versions[-1].group(1))
                                          if stated_versions else '')

                        def variant_matches(source_variant: str) -> bool:
                            if not stated_variant:
                                return True
                            normalized = _entity_label(source_variant)
                            return (normalized == stated_variant
                                    or normalized.startswith(stated_variant + 'x'))
                        source_bound = any(
                            value == source_value
                            and source_resolution == local_resolution
                            and variant_matches(source_variant)
                            and (unit.casefold() == 'fps' or
                                 _entity_label(unit) ==
                                 _entity_label(value_match.group('unit')))
                            for source_value, unit, source_resolution, source_variant
                            in self._alternative_numeric_measurements
                        )
                        if source_bound:
                            continue
                        target_bound = any(
                            field.endswith('_fps') and value == source_value
                            and _entity_label(source_resolution) == local_resolution
                            and variant_matches(variant)
                            and (not variant or _entity_label(variant) in
                                 _entity_label(scoped_prefix) or
                                 _entity_label(variant) == postfix_resolution)
                            and (unit.casefold() == 'fps' or
                                 _entity_label(unit) ==
                                 _entity_label(value_match.group('unit')))
                            for field, source_value, unit, variant, source_resolution
                            in self._observed_numeric_specs
                        )
                        if target_bound:
                            continue
                        if re.search(r'能否|是否|可否|能不能|可不可以|吗|\?|？',
                                     clause):
                            continue
                        local_prefix = re.split(r'[，,]', prefix)[-1]
                        negative_or_future = bool(re.search(
                            r'没有|未(?:能|核实|查到|获证|找到|确认)|不能|无法|'
                            r'不(?:是|满足|代表|够)|达不到|低于|建议|需要|拿到|请提供|'
                            r'寻找|求证|查询|记录缺失', local_prefix))
                        asked_fps_values = {float(match.group(1)) for match in
                            re.finditer(r'(?<![\d.])(\d+(?:\.\d+)?)\s*'
                                        r'(?:fps|帧\s*[/／]\s*秒|帧每秒)',
                                        self.question, re.I)}
                        if negative_or_future:
                            continue
                        if (value in asked_fps_values and not affirmative
                                and re.search(r'必须|要求|硬条件|目标|同时满足',
                                              clause)):
                            continue
                        if (value in asked_fps_values and not affirmative
                                and re.search(r'其他候选|继续找|请提供|拿到|正式规格|'
                                              r'测试记录|的要求', clause)):
                            continue
                        if (not local_resolution and value in asked_fps_values
                                and not affirmative):
                            continue
                        if (uncertainty and not affirmative and
                                not local_resolution):
                            continue
                        return EvidenceDecision(
                            'request_evidence',
                            'daq_partial_numeric_measurement_scope_mismatch',
                            ('The numeric fact comes from a different measured '
                             'condition. State its exact source resolution in the '
                             'same clause, and keep the requested condition unknown.'
                             if self.answer_language == 'en' else
                             '该数值来自另一测量条件。请在同一分句写明来源分辨率，'
                             '所问分辨率仍保持未知。'),
                        )
            if submission.basis and not any(
                    value == "satisfied" for value in self.status.values()) and \
                    not self._observed_numeric_specs and not any(
                        requirement_id in self._supporting_sources
                        for requirement_id in delivered_sources):
                return EvidenceDecision(
                    "request_evidence", "daq_partial_basis_without_current_source",
                    ("No planned evidence goal was verified in this turn. Re-query the "
                     "exact prior fact before presenting it as verified, or remove it "
                     "from the basis and state only the current gap."
                     if self.answer_language == 'en' else
                     "本轮没有已满足的证据目标。若要复述上一轮的产品或 SDK 事实，"
                     "请重新查询该事实并取得本轮来源；否则删除依据中的已确认事实，"
                     "只说明当前缺口。"),
                )
            if submission.conclusion and not any(
                value == "satisfied" for value in self.status.values()
            ) and not self._observed_numeric_specs:
                return EvidenceDecision("reject", "daq_unverified_partial_conclusion")
            return EvidenceDecision("allow")
        outstanding = [
            item for item in self.requirements
            if item.critical and self.status[item.id] != "satisfied"
        ]
        if not outstanding:
            return EvidenceDecision("allow")
        if any(self.status[item.id] in {"missing", "conflict"} for item in outstanding):
            if self.variant_selector_missing and all(
                    item.capability == 'lookup_spec' and self.status[item.id] == 'missing'
                    for item in outstanding):
                return EvidenceDecision(
                    'request_evidence', 'daq_variant_selector_needed',
                    '用户尚未指定产品变体；即使逐个查到已核对的条件数值，也不能提交 '
                    'resolved。请按变体分别说明数值和适用范围，要求用户选择，并改用 '
                    'safe_abstained。',
                )
            if (not any(self.status[item.id] == 'conflict' for item in outstanding)
                    and any(item.evidence_class == 'governed_required'
                            and self.status[item.id] == 'satisfied'
                            for item in self.requirements)):
                return EvidenceDecision(
                    'request_evidence', 'daq_partial_evidence_only',
                    '已有部分来源核实的事实，但仍有关键目标缺少已发布证据。'
                    '请改用 safe_abstained，逐项说明已确认的数值及适用条件，'
                    '明确未证实的组合或选型边界；不要把缺口写成整体资料不存在。',
                )
            return EvidenceDecision("reject", "daq_evidence_not_satisfied")
        return EvidenceDecision(
            "request_evidence", "daq_evidence_needed",
            "请调用相应数采取证工具核对关键需求；缺少已发布且按来源核对的资料时改用 safe_abstained。",
        )

    def snapshot(self) -> EvidenceSnapshot:
        planned = tuple(dict.fromkeys(item.capability for item in self.requirements))
        coverage: dict[str, str] = {}
        for capability in planned:
            statuses = [
                self.status[item.id] for item in self.requirements
                if item.capability == capability
            ]
            coverage[capability] = (
                "full" if all(status == "satisfied" for status in statuses)
                else "partial" if "satisfied" in statuses
                else "empty" if "missing" in statuses
                else "unknown"
            )
        return EvidenceSnapshot(
            planned_capabilities=planned,
            actual_capabilities=tuple(self.actual),
            capability_coverage=coverage,
            requirement_status={**self.status, **{
                key: 'satisfied' for key in self._supporting_sources}},
        )


def _has_governed_source(result: ToolResult, *, allow_dev_source_check: bool = False) -> bool:
    return bool(_governed_source_ids(
        result, allow_dev_source_check=allow_dev_source_check))


def _governed_source_ids(result: ToolResult, *, allow_dev_source_check: bool = False
                         ) -> set[str]:
    return {
        source['source_id']
        for source in result.sources
        if isinstance(source, dict)
        and ((source.get("type") in {"daq_governed_claim", "daq_governed_sdk"}
              and source.get("verification_status") == "verified")
             or (isinstance(result.content, dict)
                 and result.content.get('evidence_scope') ==
                 'release_note_statement_only'
                 and source.get('type') == 'daq_governed_release_note'
                 and source.get('verification_status') == 'verified')
             or (allow_dev_source_check and source.get("type") in {
                 "daq_dev_source_checked_claim", "daq_sdk_source_checked"}
                 and source.get("verification_status") == "source_checked")
             or (allow_dev_source_check and isinstance(result.content, dict)
                 and result.content.get('evidence_scope') ==
                 'release_note_statement_only'
                 and source.get('type') == 'daq_release_note_source_checked'
                 and source.get('verification_status') == 'source_checked'))
        and isinstance(source.get("release_id"), str)
        and bool(source["release_id"].strip())
        and isinstance(source.get("source_id"), str)
        and bool(source["source_id"].strip())
    }
