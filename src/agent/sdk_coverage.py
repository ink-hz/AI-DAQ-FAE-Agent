"""Typed SDK capability coverage, separate from positive evidence records.

Positive SDK facts remain in ``records.jsonl`` and must have source references.
An audited ``no_data`` state belongs here so absence is visible without being
misrepresented as unsupported.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


_STATES = frozenset({"supported", "unsupported", "no_data"})


@dataclass(frozen=True)
class SdkCapabilityCoverage:
    capability: str
    aliases: tuple[str, ...]
    sdk_layer: str
    state: str
    evidence_ids: tuple[str, ...]
    audited_scope: str
    audit_note: str = ""
    reviewed_at: str = ""

    def to_payload(self) -> dict:
        return {
            "capability": self.capability,
            "sdk_layer": self.sdk_layer,
            "state": self.state,
            "evidence_ids": list(self.evidence_ids),
            "audited_scope": self.audited_scope,
            "audit_note": self.audit_note,
            "reviewed_at": self.reviewed_at,
        }


def load_sdk_capability_coverage(path: Path) -> tuple[SdkCapabilityCoverage, ...]:
    if not path.is_file():
        return ()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = raw.get("coverage") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("SDK capability coverage must be a list")

    result: list[SdkCapabilityCoverage] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"coverage row {index} must be an object")
        capability = str(row.get("capability") or "").strip()
        sdk_layer = str(row.get("sdk_layer") or "").strip()
        state = str(row.get("state") or "").strip()
        evidence_ids = tuple(str(v) for v in (row.get("evidence_ids") or []) if v)
        aliases = tuple(str(v).lower() for v in (row.get("aliases") or []) if v)
        if not capability or not sdk_layer or state not in _STATES:
            raise ValueError(f"invalid SDK coverage row {index}")
        key = (capability, sdk_layer)
        if key in seen:
            raise ValueError(f"duplicate SDK coverage row: {capability}/{sdk_layer}")
        if state in {"supported", "unsupported"} and not evidence_ids:
            raise ValueError(f"{state} requires evidence_ids: {capability}/{sdk_layer}")
        if state == "no_data" and evidence_ids:
            raise ValueError(f"no_data must not carry evidence_ids: {capability}/{sdk_layer}")
        seen.add(key)
        result.append(SdkCapabilityCoverage(
            capability=capability,
            aliases=aliases,
            sdk_layer=sdk_layer,
            state=state,
            evidence_ids=evidence_ids,
            audited_scope=str(row.get("audited_scope") or ""),
            audit_note=str(row.get("audit_note") or ""),
            reviewed_at=str(row.get("reviewed_at") or ""),
        ))
    return tuple(result)


def matching_sdk_coverage(
    rows: tuple[SdkCapabilityCoverage, ...],
    query: str,
    sdk_layers: tuple[str, ...],
) -> list[SdkCapabilityCoverage]:
    """Return audited rows only for capabilities and layers named by the request."""
    text = query.lower()
    capabilities = {
        row.capability
        for row in rows
        if row.capability.lower() in text or any(alias in text for alias in row.aliases)
    }
    if not capabilities:
        return []
    layer_filter = set(sdk_layers)
    return [
        row for row in rows
        if row.capability in capabilities
        and (not layer_filter or row.sdk_layer in layer_filter)
    ]

