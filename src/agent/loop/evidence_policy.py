"""Optional domain evidence gate; transport and existing safety gates stay in Loop.

A policy creates a fresh session for each request. Only ``observe`` receives full
in-process evidence. Snapshots must contain audit IDs/statuses, never source text,
credentials or attachment content. Policies cannot rewrite submissions or outcomes.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol

from src.agent.loop.answer_contract import AnswerSubmission
from src.agent.loop.tools import ToolResult

_REASON_CODE = re.compile(r"[a-z][a-z0-9_:-]{0,127}\Z")
_REQUIREMENT_STATUSES = frozenset({"satisfied", "missing", "conflict", "unknown"})
_COVERAGE_STATUSES = frozenset({"full", "partial", "empty", "unknown"})


@dataclass(frozen=True)
class EvidenceDecision:
    action: Literal["allow", "request_evidence", "reject"]
    reason_code: str = ""
    notice: str = ""

    def __post_init__(self) -> None:
        if self.action not in {"allow", "request_evidence", "reject"}:
            raise ValueError("evidence_policy_action_invalid")
        if not isinstance(self.notice, str):
            raise ValueError("evidence_policy_notice_invalid")
        if (
            not isinstance(self.reason_code, str)
            or (self.reason_code and _REASON_CODE.fullmatch(self.reason_code) is None)
            or (self.action != "allow" and not self.reason_code)
            or (self.action == "request_evidence" and not self.notice.strip())
        ):
            raise ValueError("evidence_policy_reason_invalid")


@dataclass(frozen=True)
class EvidenceSnapshot:
    planned_capabilities: tuple[str, ...] = ()
    actual_capabilities: tuple[str, ...] = ()
    capability_coverage: Mapping[str, str] = field(default_factory=dict)
    requirement_status: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("planned_capabilities", "actual_capabilities"):
            values = tuple(getattr(self, name))
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError("evidence_policy_capabilities_invalid")
            object.__setattr__(self, name, tuple(dict.fromkeys(values)))
        for name, statuses in (
            ("capability_coverage", _COVERAGE_STATUSES),
            ("requirement_status", _REQUIREMENT_STATUSES),
        ):
            values = dict(getattr(self, name))
            if any(
                not isinstance(key, str) or not key.strip() or value not in statuses
                for key, value in values.items()
            ):
                raise ValueError("evidence_policy_status_invalid")
            object.__setattr__(self, name, MappingProxyType(values))

    @property
    def has_outstanding_requirements(self) -> bool:
        # An empty ledger has not established complete coverage.
        return not self.requirement_status or any(
            value != "satisfied" for value in self.requirement_status.values()
        )


class EvidenceSession(Protocol):
    def observe(self, tool_name: str, result: ToolResult) -> None: ...

    def evaluate(self, submission: AnswerSubmission) -> EvidenceDecision: ...

    def snapshot(self) -> EvidenceSnapshot: ...


class EvidencePolicy(Protocol):
    def begin(self, requirements: Mapping[str, object]) -> EvidenceSession: ...
