"""In-memory session 存储(MVP 单进程)。"""
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agent.schema import RequestSchema
from src.attachments.models import AttachmentDescriptor

_MAX_STATE_ITEMS = 8

# Authenticated conversations belong to a Platform subject. The subject type is
# derived from the authentication mode, never from a browser-supplied field.
AuthenticationMode = Literal[
    "public_customer", "platform_enterprise", "platform_partner"
]
OWNER_SUBJECT_TYPES: dict[str, str] = {
    "platform_enterprise": "enterprise_member",
    "platform_partner": "partner_operator",
}
# Session identity is permanently bound: a conversation never changes hands.
_IMMUTABLE_SESSION_FIELDS = frozenset({"session_id", "owner_subject_id"})
# Persisted checkpoint envelope version. Bump only with an explicit reader.
# v2 adds `clarification_round_count`, the orchestrator's anti-loop counter:
# without it a restored conversation could be re-clarified indefinitely.
SESSION_CHECKPOINT_VERSION = 2


def validate_owner_binding(
    *,
    authentication_mode: str,
    internal_user_id: str | None,
    owner_subject_id: str | None,
) -> None:
    """Shared owner-binding rule for session creation and checkpoint restore.

    The trusted `PlatformSubject` projection is the only owner source. For an
    enterprise member the subject id *is* the internal user id, so the two must
    agree exactly; a partner operator carries no internal user id; a public
    conversation carries no owner at all.
    """
    if authentication_mode == "public_customer":
        valid = internal_user_id is None and owner_subject_id is None
    elif authentication_mode == "platform_enterprise":
        valid = bool(internal_user_id) and owner_subject_id == internal_user_id
    elif authentication_mode == "platform_partner":
        valid = bool(owner_subject_id) and internal_user_id is None
    else:
        valid = False
    if not valid:
        raise ValueError("session_identity_binding_invalid")


def _append_unique(existing: list[str], incoming: list[str], *, limit: int = _MAX_STATE_ITEMS) -> list[str]:
    result = list(existing)
    seen = {item.strip().lower() for item in result}
    for raw in incoming:
        item = str(raw).strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[-limit:]


def _append_unique_scenario(existing: list[str], incoming: list[str]) -> list[str]:
    filtered: list[str] = []
    for item in incoming:
        text = str(item).strip()
        if not text:
            continue
        if any(text in old or old in text for old in existing):
            continue
        filtered.append(text)
    return _append_unique(existing, filtered)


def _extract_excluded_models(text: str) -> list[str]:
    """Best-effort parse of explicit negative memory, e.g. "不要 Femto Mega"."""
    if not text:
        return []
    pattern = re.compile(
        r"(?:不要|不考虑|排除|不用|别用|不要用|not\s+consider|exclude)\s+"
        r"([A-Za-z0-9][A-Za-z0-9 _+-]{1,40})",
        re.IGNORECASE,
    )
    results: list[str] = []
    for match in pattern.finditer(text):
        value = match.group(1).strip(" ,，。.;；:：")
        if value:
            results.append(value)
    return results


def _infer_scenario_terms(text: str) -> list[str]:
    terms: list[str] = []
    keyword_map = [
        ("机械臂", "机械臂"),
        ("抓取", "抓取"),
        ("夹取", "抓取"),
        ("pick", "抓取"),
        ("agv", "AGV"),
        ("amr", "AMR"),
        ("服务机器人", "服务机器人"),
        ("导航", "导航"),
        ("避障", "避障"),
        ("测量", "测量"),
        ("扫描", "扫描"),
        ("建图", "建图"),
    ]
    lower = text.lower()
    for keyword, value in keyword_map:
        if keyword.lower() in lower:
            terms.append(value)
    return terms


def _infer_constraint_terms(text: str) -> list[str]:
    number = r"[0-9]+(?:\.[0-9]+)?"
    unit = r"mm|毫米|cm|厘米|m|米"
    order: list[tuple[int, str]] = []
    consumed: list[tuple[int, int]] = []

    def overlaps(span: tuple[int, int]) -> bool:
        return any(span[0] < end and start < span[1] for start, end in consumed)

    def normalized_unit(raw: str, *, precision: bool) -> str:
        lowered = raw.lower()
        if lowered in {"mm", "毫米"}:
            return "mm"
        if lowered in {"cm", "厘米"}:
            return "cm"
        return "m" if lowered == "m" else "米"

    def collect_role(prefix_pattern: str, label: str, *, precision: bool) -> None:
        range_pattern = re.compile(
            rf"(?:{prefix_pattern})\s*({number})\s*({unit})?\s*"
            rf"(?:-|~|～|至|到)\s*({number})\s*({unit})",
            re.IGNORECASE,
        )
        for match in range_pattern.finditer(text):
            span = match.span()
            if overlaps(span):
                continue
            low, first_unit, high, final_unit = match.groups()
            left_unit = normalized_unit(first_unit or final_unit, precision=precision)
            right_unit = normalized_unit(final_unit, precision=precision)
            value = (
                f"{low}-{high}{right_unit}"
                if left_unit == right_unit
                else f"{low}{left_unit}-{high}{right_unit}"
            )
            order.append((span[0], f"{label} {value}"))
            consumed.append(span)

        single_pattern = re.compile(
            rf"(?:{prefix_pattern})\s*"
            rf"(小于|低于|少于|<|≤|不超过|within)?\s*"
            rf"({number})\s*({unit})",
            re.IGNORECASE,
        )
        for match in single_pattern.finditer(text):
            span = match.span()
            if overlaps(span):
                continue
            operator, value, raw_unit = match.groups()
            op = "≤" if operator else ""
            value_unit = normalized_unit(raw_unit, precision=precision)
            order.append((span[0], f"{label} {op}{value}{value_unit}"))
            consumed.append(span)

    collect_role(
        r"工作距离|拍摄距离|测量距离|距离|range",
        "工作距离",
        precision=False,
    )
    collect_role(
        r"圆心误差|中心误差|轮廓误差|几何误差",
        "圆心误差",
        precision=True,
    )
    collect_role(
        r"精度误差|精度|定位误差|测量误差|平面误差|深度误差|accuracy|error",
        "精度",
        precision=True,
    )

    for pattern, value in (
        (r"毫米级|mm\s*级|millimeter", "精度 毫米级"),
        (r"厘米级|cm\s*级|centimeter", "精度 厘米级"),
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match is not None and not overlaps(match.span()):
            order.append((match.start(), value))
            consumed.append(match.span())

    ordered = [value for _, value in sorted(order)]
    return list(dict.fromkeys(ordered))


@dataclass
class SessionContext:
    """上线质量层的轻量咨询状态。

    这是 Session 的权威长期记忆。RequestSchema 代表本轮抽取结果;
    current_schema 只作为合并后的结构化视图保存在这里,避免 Session 同时
    持有多份会漂移的上下文状态。
    """
    current_schema: RequestSchema | None = None
    scenario: list[str] = field(default_factory=list)
    accumulated_constraints: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    technical_components: list[str] = field(default_factory=list)
    candidate_models: list[str] = field(default_factory=list)
    excluded_models: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)
    last_user_message: str = ""
    last_assistant_summary: str = ""

    @property
    def constraints(self) -> list[str]:
        return self.accumulated_constraints

    @constraints.setter
    def constraints(self, value: list[str]) -> None:
        self.accumulated_constraints = value

    @property
    def pending_clarifications(self) -> list[str]:
        return self.pending_questions

    @pending_clarifications.setter
    def pending_clarifications(self, value: list[str]) -> None:
        self.pending_questions = value

    def apply_schema(self, new_schema: RequestSchema) -> RequestSchema:
        if new_schema.is_topic_switch:
            return self.replace_topic(new_schema)
        if self.current_schema is None:
            self.current_schema = new_schema
        else:
            self.current_schema = self.current_schema.merge(new_schema)
        self.update_from_schema(self.current_schema)
        return self.current_schema

    def replace_topic(self, new_schema: RequestSchema) -> RequestSchema:
        """Replace active consultation memory without deleting the session log."""

        self.current_schema = new_schema
        self.scenario = []
        self.accumulated_constraints = []
        self.products = []
        self.platforms = []
        self.technical_components = []
        self.candidate_models = []
        self.excluded_models = []
        self.pending_questions = []
        self.last_user_message = ""
        self.last_assistant_summary = ""
        self.update_from_schema(new_schema)
        return self.current_schema

    def compact(self) -> None:
        """Repair dirty in-memory state accumulated before stricter dedupe existed."""
        self.scenario = _append_unique([], self.scenario)
        self.accumulated_constraints = _append_unique([], self.accumulated_constraints)
        self.products = _append_unique([], self.products)
        self.platforms = _append_unique([], self.platforms)
        self.technical_components = _append_unique([], self.technical_components)
        self.candidate_models = _append_unique([], self.candidate_models)
        self.excluded_models = _append_unique([], self.excluded_models)
        self.pending_questions = _append_unique([], self.pending_questions)

    def update_from_schema(self, schema: RequestSchema) -> None:
        self.compact()
        self.scenario = _append_unique(self.scenario, schema.scenario)
        self.accumulated_constraints = _append_unique(
            self.accumulated_constraints,
            schema.constraints,
        )
        self.products = _append_unique(self.products, schema.products)
        self.platforms = _append_unique(self.platforms, schema.platforms)
        self.technical_components = _append_unique(
            self.technical_components,
            schema.technical_components,
        )
        if schema.products:
            self.candidate_models = _append_unique(self.candidate_models, schema.products)
        self.pending_questions = list(schema.missing_for_bucket)[-_MAX_STATE_ITEMS:]
        self.compact()

    def record_message(self, role: str, content: str) -> None:
        text = str(content).strip()
        if not text:
            return
        self.compact()
        if role == "user":
            self.last_user_message = text[:500]
            self.scenario = _append_unique_scenario(
                self.scenario,
                _infer_scenario_terms(text),
            )
            self.accumulated_constraints = _append_unique(
                self.accumulated_constraints,
                _infer_constraint_terms(text),
            )
            self.excluded_models = _append_unique(
                self.excluded_models,
                _extract_excluded_models(text),
            )
        elif role == "assistant":
            compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
            self.last_assistant_summary = compact[:500]
        self.compact()

    def to_prompt_block(self) -> str:
        self.compact()
        lines = ["当前咨询上下文:"]
        if self.scenario:
            lines.append(f"- 场景: {'、'.join(self.scenario[-4:])}")
        if self.accumulated_constraints:
            lines.append(f"- 已确认约束: {'、'.join(self.accumulated_constraints[-6:])}")
        if self.products:
            lines.append(f"- 已提到型号: {'、'.join(self.products[-4:])}")
        if self.candidate_models:
            lines.append(f"- 候选型号: {'、'.join(self.candidate_models[-4:])}")
        if self.excluded_models:
            lines.append(f"- 明确排除: {'、'.join(self.excluded_models[-4:])}")
        if self.platforms:
            lines.append(f"- 平台 / 开发环境: {'、'.join(self.platforms[-4:])}")
        if self.technical_components:
            lines.append(f"- 技术点: {'、'.join(self.technical_components[-4:])}")
        if self.pending_questions:
            lines.append(f"- 待确认问题: {'、'.join(self.pending_questions[-4:])}")
        if self.last_user_message:
            lines.append(f"- 用户上一轮补充: {self.last_user_message}")
        if self.last_assistant_summary:
            lines.append(f"- 上一轮答复摘要: {self.last_assistant_summary}")

        if len(lines) == 1:
            return ""

        lines.extend([
            "",
            "回答质量要求:",
            "- 必须承接当前咨询上下文,不要把本轮短句当孤立问题。",
            "- 能给初步方向时先给判断和假设,不要只追问。",
            "- 按 FAE 咨询方式回答:结论、依据、注意/风险、验证/下一步。",
            "- 追问最多 2 个,且必须服务于下一步选型、验证或排查。",
        ])
        return "\n".join(lines)


@dataclass
class Session:
    session_id: str
    channel: Literal["fae", "ecom"]
    created_at: float
    last_active: float
    authentication_mode: AuthenticationMode = "public_customer"
    internal_user_id: str | None = None
    # Trusted Platform subject that owns an authenticated conversation.
    # None means public/anonymous, which stays out of every authenticated read.
    owner_subject_id: str | None = None
    messages: list[dict] = field(default_factory=list)        # [{role, content}]
    last_recommendations: list[str] = field(default_factory=list)
    session_context: SessionContext = field(default_factory=SessionContext)
    # §2.5.4 — orchestrator 连续走 clarification 时递增,达 2 强制走桶不再追问
    clarification_round_count: int = 0
    # loop 显式会话状态(src/agent/loop/state.py;确定性写入,orchestrator 维护)
    loop_state: object | None = None
    attachments: dict[str, AttachmentDescriptor] = field(default_factory=dict)
    active_attachment_ids: list[str] = field(default_factory=list)

    def __setattr__(self, name: str, value: object) -> None:
        """Reject rebinding a conversation to another id or another subject.

        Immutability is enforced in memory as well as in PostgreSQL so a bug in
        an upper layer cannot silently transfer a conversation.
        """
        if name in _IMMUTABLE_SESSION_FIELDS and name in self.__dict__:
            raise AttributeError(f"{name}_immutable")
        super().__setattr__(name, value)

    @property
    def owner_subject_type(self) -> str | None:
        """Subject type derived from the authentication mode, never from input."""
        if self.owner_subject_id is None:
            return None
        return OWNER_SUBJECT_TYPES.get(self.authentication_mode)

    @property
    def current_schema(self) -> RequestSchema | None:
        return self.session_context.current_schema

    @current_schema.setter
    def current_schema(self, value: RequestSchema | None) -> None:
        self.session_context.current_schema = value

    @property
    def consultation_state(self) -> SessionContext:
        return self.session_context

    def update_schema(self, new_schema: RequestSchema) -> None:
        self.session_context.apply_schema(new_schema)
        self.last_active = time.time()

    def replace_topic(self, new_schema: RequestSchema) -> None:
        self.session_context.replace_topic(new_schema)
        self.last_active = time.time()

    def append_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self.consultation_state.record_message(role, content)
        self.last_active = time.time()

    def bind_attachments(
        self,
        descriptors: list[AttachmentDescriptor],
        *,
        explicit_ids: list[str] | None,
    ) -> None:
        for descriptor in descriptors:
            self.attachments[descriptor.attachment_id] = descriptor
        if explicit_ids is not None:
            self.active_attachment_ids = [
                attachment_id
                for attachment_id in explicit_ids
                if attachment_id in self.attachments
            ]
        self.last_active = time.time()

    def visible_attachments(self) -> list[AttachmentDescriptor]:
        return [
            self.attachments[attachment_id]
            for attachment_id in self.active_attachment_ids
            if attachment_id in self.attachments
        ]

    def to_checkpoint(self) -> dict:
        """Deterministic JSON envelope of restorable conversation state.

        Transient orchestration state (`loop_state`, attachment descriptors) and
        the enterprise `internal_user_id` are deliberately excluded: the first is
        rebuilt per turn, the second is re-derived from the trusted subject.
        """
        return {
            "version": SESSION_CHECKPOINT_VERSION,
            "session_id": self.session_id,
            "channel": self.channel,
            "authentication_mode": self.authentication_mode,
            "owner_subject_id": self.owner_subject_id,
            "messages": list(self.messages),
            "session_context": serialize_session_context(self.session_context),
            "active_attachment_ids": list(self.active_attachment_ids),
            # Anti-loop state (§2.5.4): persisted so a device switch cannot
            # reset the clarification budget.
            "clarification_round_count": self.clarification_round_count,
        }

    @classmethod
    def from_checkpoint(
        cls, payload: object, *, created_at: float, last_active: float
    ) -> "Session":
        if not isinstance(payload, dict):
            raise ValueError("session_checkpoint_invalid")
        version = payload.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("session_checkpoint_version_unsupported")
        reader = _CHECKPOINT_READERS.get(version)
        if reader is None:
            # Only the versions with an explicit closed reader are accepted; a
            # future or unknown envelope is never parsed leniently.
            raise ValueError("session_checkpoint_version_unsupported")
        try:
            parsed = reader.model_validate(payload)
        except ValidationError as exc:
            raise ValueError("session_checkpoint_invalid") from exc
        # An enterprise subject id is the internal user id by definition, so the
        # binding is re-derived rather than persisted twice and trusted on read.
        internal_user_id = (
            parsed.owner_subject_id
            if parsed.authentication_mode == "platform_enterprise"
            else None
        )
        try:
            validate_owner_binding(
                authentication_mode=parsed.authentication_mode,
                internal_user_id=internal_user_id,
                owner_subject_id=parsed.owner_subject_id,
            )
        except ValueError as exc:
            raise ValueError("session_checkpoint_invalid") from exc
        return cls(
            session_id=parsed.session_id,
            channel=parsed.channel,
            created_at=created_at,
            last_active=last_active,
            authentication_mode=parsed.authentication_mode,
            internal_user_id=internal_user_id,
            owner_subject_id=parsed.owner_subject_id,
            messages=[
                {"role": message.role, "content": message.content}
                for message in parsed.messages
            ],
            session_context=parsed.session_context.to_context(),
            active_attachment_ids=list(parsed.active_attachment_ids),
            clarification_round_count=parsed.clarification_round_count,
        )


ConsultationState = SessionContext


def serialize_session_context(context: SessionContext) -> dict:
    """Serialize the authoritative consultation memory as deterministic JSON.

    Every field is enumerated explicitly instead of reflected: adding a
    `SessionContext` field must be a reviewed decision about persisted
    conversation memory, and the covering test fails until it is made here.
    """
    schema = context.current_schema
    return {
        "current_schema": (
            None if schema is None else schema.model_dump(mode="json")
        ),
        "scenario": list(context.scenario),
        "accumulated_constraints": list(context.accumulated_constraints),
        "products": list(context.products),
        "platforms": list(context.platforms),
        "technical_components": list(context.technical_components),
        "candidate_models": list(context.candidate_models),
        "excluded_models": list(context.excluded_models),
        "pending_questions": list(context.pending_questions),
        "last_user_message": context.last_user_message,
        "last_assistant_summary": context.last_assistant_summary,
    }


class _SessionContextCheckpoint(BaseModel):
    """Strict inverse of `serialize_session_context`."""

    model_config = ConfigDict(extra="forbid")

    current_schema: RequestSchema | None = None
    scenario: list[str] = Field(default_factory=list)
    accumulated_constraints: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    technical_components: list[str] = Field(default_factory=list)
    candidate_models: list[str] = Field(default_factory=list)
    excluded_models: list[str] = Field(default_factory=list)
    pending_questions: list[str] = Field(default_factory=list)
    last_user_message: str = ""
    last_assistant_summary: str = ""

    def to_context(self) -> SessionContext:
        return SessionContext(
            current_schema=self.current_schema,
            scenario=list(self.scenario),
            accumulated_constraints=list(self.accumulated_constraints),
            products=list(self.products),
            platforms=list(self.platforms),
            technical_components=list(self.technical_components),
            candidate_models=list(self.candidate_models),
            excluded_models=list(self.excluded_models),
            pending_questions=list(self.pending_questions),
            last_user_message=self.last_user_message,
            last_assistant_summary=self.last_assistant_summary,
        )


class _CheckpointMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1)
    content: str


class _SessionCheckpointBase(BaseModel):
    """Fields shared by every checkpoint envelope version.

    Parsing is pure data validation: no pickle, no dynamic import, no JSON
    object hook. Unknown fields and unknown modes are rejected, not ignored.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    channel: Literal["fae", "ecom"]
    authentication_mode: AuthenticationMode
    owner_subject_id: str | None = None
    messages: list[_CheckpointMessage] = Field(default_factory=list)
    session_context: _SessionContextCheckpoint = Field(
        default_factory=_SessionContextCheckpoint
    )
    active_attachment_ids: list[str] = Field(default_factory=list)


class _SessionCheckpointV1(_SessionCheckpointBase):
    """Closed reader for the exact v1 envelope, kept for rollout compatibility.

    v1 has no persisted anti-loop counter, so a v1 conversation restores with
    the initial value. `extra="forbid"` keeps this a *closed* reader: a v2
    payload relabelled as v1 is rejected instead of partially accepted.
    """

    version: Literal[1]

    @property
    def clarification_round_count(self) -> int:
        return 0


class _SessionCheckpoint(_SessionCheckpointBase):
    """Strict inverse of `Session.to_checkpoint` (current version)."""

    version: Literal[2]
    # Strict: a bool, float or numeric string is malformed state, not a count.
    clarification_round_count: Annotated[int, Field(ge=0, strict=True)]


# Envelope versions this process can read, each with its own closed reader.
_CHECKPOINT_READERS: dict[int, type[_SessionCheckpointBase]] = {
    1: _SessionCheckpointV1,
    2: _SessionCheckpoint,
}


# 长会话提示阈值:SessionContext 每列表上限 8 条、loop conclusions 上限 5 条、
# loop 裸历史 2 条,约 10 个 assistant 轮后记忆进入饱和/丢弃区。
# 补偿逻辑,登记于 evals/compensation_baseline.json:session_length_new_session_hint。
SESSION_LENGTH_HINT_ASSISTANT_TURNS = 10


def session_length_hint(session: "Session") -> dict | None:
    """通道层显式提示:会话过长时建议开启新会话。

    只产生建议性元数据(done.session_hint),不改变任何回答行为,
    不清空会话,不做静默降级。
    """
    turns = sum(
        1 for m in session.messages if m.get("role") == "assistant"
    )
    if turns < SESSION_LENGTH_HINT_ASSISTANT_TURNS:
        return None
    return {
        "suggest_new_session": True,
        "reason": "session_length",
        "assistant_turns": turns,
    }


class SessionStore:
    def __init__(self, *, ttl_seconds: int):
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl_seconds
        # Guards the lookup-then-seat window in `adopt` only. `get`, `create`
        # and `delete` stay lock-free: `create` mints a fresh id nobody else
        # holds, and only `adopt` decides between two candidates for one id.
        self._adopt_lock = threading.Lock()

    def create(
        self,
        *,
        channel: Literal["fae", "ecom"],
        authentication_mode: AuthenticationMode = "public_customer",
        internal_user_id: str | None = None,
        owner_subject_id: str | None = None,
    ) -> Session:
        if (
            authentication_mode == "platform_enterprise"
            and owner_subject_id is None
        ):
            # The enterprise subject id is the internal user id; derive the owner
            # projection instead of accepting it from the caller.
            owner_subject_id = internal_user_id
        validate_owner_binding(
            authentication_mode=authentication_mode,
            internal_user_id=internal_user_id,
            owner_subject_id=owner_subject_id,
        )
        now = time.time()
        sid = str(uuid.uuid4())
        s = Session(
            session_id=sid,
            channel=channel,
            created_at=now,
            last_active=now,
            authentication_mode=authentication_mode,
            internal_user_id=internal_user_id,
            owner_subject_id=owner_subject_id,
        )
        self._sessions[sid] = s
        return s

    def get(self, sid: str) -> Session | None:
        s = self._sessions.get(sid)
        if s is None:
            return None
        if time.time() - s.last_active > self._ttl:
            del self._sessions[sid]
            return None
        return s

    def adopt(self, session: Session) -> Session:
        """Seat a session restored from durable storage, or keep the incumbent.

        Used on an authenticated cache miss: the session already carries its own
        identity and owner binding from the checkpoint, so it is seated as-is
        rather than rebuilt via `create`. The binding is still re-validated here,
        because the cache entry is what every later ownership check reads.

        A concurrent restore of the same conversation must not displace a live
        entry: the incumbent holds the messages appended so far and the turn
        index the next checkpoint writes, so the caller is handed the incumbent
        and the restored copy is discarded. The returned session is the one the
        caller must use.

        Two workers restoring the same conversation at once is the ordinary
        shape of an authenticated cache miss, so the lookup and the seat are one
        atomic step. Without that, both can observe an empty slot and seat their
        own copy: the loser keeps appending to a session the store no longer
        holds and reuses the turn index the winner is about to write.
        """
        validate_owner_binding(
            authentication_mode=session.authentication_mode,
            internal_user_id=session.internal_user_id,
            owner_subject_id=session.owner_subject_id,
        )
        with self._adopt_lock:
            incumbent = self.get(session.session_id)
            if incumbent is not None:
                return incumbent
            self._sessions[session.session_id] = session
            return session

    def delete(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def gc(self) -> int:
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if now - s.last_active > self._ttl]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)
