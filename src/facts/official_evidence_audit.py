"""Deterministic closure audit for active official-evidence claims.

The audit deliberately inspects a small, explicit set of production knowledge
surfaces.  It does not crawl archived assets, evaluations, or review reports.
Every discovered line-level claim must have a reviewed adjudication; additions,
deletions, and wording changes therefore fail closed until they are reviewed.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Mapping

import yaml

AUDIT_VERDICTS = {
    "aligned",
    "corrected",
    "legacy_conflict",
    "historical_only",
    "no_data",
    "out_of_scope_debt",
}

_ACTIVE_MODEL_FILES = (
    "facts.yaml",
    "hardware.md",
    "index.md",
    "product.md",
    "software.md",
)
_UNITY = re.compile(r"(?<![A-Za-z])unity(?![A-Za-z])", re.IGNORECASE)
_CAPACITY = re.compile(
    r"multi[- ]camera|多机|\bsecondary\b|daisy(?:-chain)?|"
    r"star topology|星型|链型|sync hub|usbfs_memory|USB root controller",
    re.IGNORECASE,
)
_SYNC_INTERFACE = re.compile(
    r"\bVSYNC(?:_IN|_OUT)?\b|\bPTP(?:\s*V?2)?\b|\bRS485[AB]?\b|"
    r"hardware timestamps?|hardware timestamp|硬件时间戳|时间戳|"
    r"\btrigger_support\b|\btrigger(?:s|ed|ing)?\b|触发",
    re.IGNORECASE,
)
_CATEGORY_PATTERNS = (
    ("software_surface", _UNITY),
    ("multi_camera_capacity", _CAPACITY),
    ("synchronization_interface", _SYNC_INTERFACE),
)
_CATEGORY_FACT_FIELDS = {
    "multi_camera_capacity": frozenset({
        "multi_camera_sync_capacity",
        "sync_support",
    }),
    "synchronization_interface": frozenset({
        "serial_interface_support",
        "sync_support",
        "time_sync_support",
        "trigger_support",
    }),
}


class OfficialEvidenceAuditError(ValueError):
    """Raised when the governed audit data is invalid or incomplete."""


@dataclass(frozen=True, order=True)
class ActiveEvidenceClaim:
    claim_id: str
    category: str
    relative_path: str
    line: int
    text: str


@dataclass(frozen=True)
class EvidenceAdjudication:
    claim_id: str
    verdict: str
    reason: str
    reviewer: str
    source: Mapping[str, object]


def _active_files(knowledge_dir: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for directory in sorted(knowledge_dir.iterdir(), key=lambda path: path.name):
        if (
            not directory.is_dir()
            or directory.name.startswith("_")
            or directory.name == "evals"
        ):
            continue
        files.extend(
            path
            for name in _ACTIVE_MODEL_FILES
            if (path := directory / name).is_file()
        )
    topics = knowledge_dir / "_topics"
    if topics.is_dir():
        files.extend(sorted(topics.glob("*.md"), key=lambda path: path.name))
    return tuple(sorted(files, key=lambda path: path.relative_to(knowledge_dir).as_posix()))


def _claim_id(relative_path: str, category: str, text: str) -> str:
    normalized = " ".join(text.split()).casefold()
    payload = f"{relative_path}\0{category}\0{normalized}".encode("utf-8")
    return f"oea_{sha256(payload).hexdigest()[:20]}"


def _fact_fields(path: Path) -> frozenset[str]:
    try:
        rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise OfficialEvidenceAuditError(
            f"{path}: invalid facts YAML"
        ) from exc
    if not isinstance(rows, list):
        raise OfficialEvidenceAuditError(f"{path}: facts must be a list")
    fields: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise OfficialEvidenceAuditError(
                f"{path}: facts[{index}] must be a mapping"
            )
        field = row.get("field")
        if not isinstance(field, str) or not field.strip():
            raise OfficialEvidenceAuditError(
                f"{path}: facts[{index}].field must be a non-empty string"
            )
        fields.add(field.strip())
    return frozenset(fields)


def _category_applies(
    category: str,
    relative_path: str,
    *,
    model_fact_fields: Mapping[str, frozenset[str]],
) -> bool:
    top_level = relative_path.split("/", 1)[0]
    if top_level == "_topics":
        return True
    if category == "software_surface":
        return True
    governed_fields = _CATEGORY_FACT_FIELDS.get(category, frozenset())
    return bool(model_fact_fields.get(top_level, frozenset()) & governed_fields)


def discover_active_claims(knowledge_dir: Path) -> tuple[ActiveEvidenceClaim, ...]:
    """Return sorted line-level claims from the exact governed active scope."""
    # Exact duplicate wording in the same file is one semantic claim.  Coalescing
    # it keeps IDs independent of line numbers while still failing closed if the
    # wording changes.
    claims_by_key: dict[tuple[str, str, str], ActiveEvidenceClaim] = {}
    active_files = _active_files(knowledge_dir)
    model_fact_fields = {
        path.parent.name: _fact_fields(path)
        for path in active_files
        if path.name == "facts.yaml"
    }
    for path in active_files:
        relative_path = path.relative_to(knowledge_dir).as_posix()
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            text = raw_line.strip()
            if not text:
                continue
            for category, pattern in _CATEGORY_PATTERNS:
                if _category_applies(
                    category,
                    relative_path,
                    model_fact_fields=model_fact_fields,
                ) and pattern.search(text):
                    normalized = " ".join(text.split()).casefold()
                    claims_by_key.setdefault(
                        (relative_path, category, normalized),
                        ActiveEvidenceClaim(
                            claim_id=_claim_id(relative_path, category, text),
                            category=category,
                            relative_path=relative_path,
                            line=line_number,
                            text=text,
                        ),
                    )
    claims = sorted(claims_by_key.values())
    ids = [claim.claim_id for claim in claims]
    duplicate_ids = sorted(claim_id for claim_id, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        raise OfficialEvidenceAuditError(
            "duplicate active claim IDs; split or disambiguate identical claims: "
            + ", ".join(duplicate_ids)
        )
    return tuple(claims)


def _validate_source(source: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(source, dict) or not source:
        raise OfficialEvidenceAuditError(f"{label}: structured source is required")
    if not any(str(source.get(key) or "").strip() for key in ("url", "ref", "path", "commit", "pdf")):
        raise OfficialEvidenceAuditError(
            f"{label}: source requires url, ref, path, commit, or pdf"
        )
    if source.get("pdf") and not source.get("page"):
        raise OfficialEvidenceAuditError(f"{label}: PDF source requires page")
    return dict(source)


def load_adjudications(
    path: Path,
    *,
    claims: Iterable[ActiveEvidenceClaim],
) -> dict[str, EvidenceAdjudication]:
    """Load a closed adjudication manifest and reject missing/stale decisions."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise OfficialEvidenceAuditError(f"official evidence adjudications are missing: {path}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise OfficialEvidenceAuditError(f"{path}: unsupported version")
    if not str(data.get("reviewed_at") or "").strip():
        raise OfficialEvidenceAuditError(f"{path}: reviewed_at is required")
    raw_rows = data.get("adjudications")
    if not isinstance(raw_rows, list):
        raise OfficialEvidenceAuditError(f"{path}: adjudications must be a list")

    known_ids = {claim.claim_id for claim in claims}
    decisions: dict[str, EvidenceAdjudication] = {}
    for index, raw in enumerate(raw_rows):
        label = f"adjudications[{index}]"
        if not isinstance(raw, dict):
            raise OfficialEvidenceAuditError(f"{label}: must be a mapping")
        claim_id = str(raw.get("claim_id") or "").strip()
        if not claim_id:
            raise OfficialEvidenceAuditError(f"{label}: claim_id is required")
        if claim_id in decisions:
            raise OfficialEvidenceAuditError(f"duplicate adjudication: {claim_id}")
        verdict = str(raw.get("verdict") or "").strip()
        if verdict not in AUDIT_VERDICTS:
            raise OfficialEvidenceAuditError(f"{claim_id}: unknown verdict {verdict!r}")
        reason = str(raw.get("reason") or "").strip()
        reviewer = str(raw.get("reviewer") or "").strip()
        if not reason or not reviewer:
            raise OfficialEvidenceAuditError(f"{claim_id}: reason and reviewer are required")
        decisions[claim_id] = EvidenceAdjudication(
            claim_id=claim_id,
            verdict=verdict,
            reason=reason,
            reviewer=reviewer,
            source=_validate_source(raw.get("source"), label=claim_id),
        )

    stale = sorted(set(decisions) - known_ids)
    if stale:
        raise OfficialEvidenceAuditError("stale adjudications: " + ", ".join(stale))
    missing = sorted(known_ids - set(decisions))
    if missing:
        raise OfficialEvidenceAuditError("unadjudicated active claims: " + ", ".join(missing))
    return decisions


def build_official_evidence_audit(
    knowledge_dir: Path,
    adjudications_path: Path,
) -> dict[str, object]:
    claims = discover_active_claims(knowledge_dir)
    adjudications = load_adjudications(adjudications_path, claims=claims)
    raw_manifest = yaml.safe_load(adjudications_path.read_text(encoding="utf-8")) or {}
    rows: list[dict[str, object]] = []
    for claim in claims:
        decision = adjudications[claim.claim_id]
        rows.append({**asdict(claim), **asdict(decision)})
    category_counts = Counter(claim.category for claim in claims)
    verdict_counts = Counter(adjudications[claim.claim_id].verdict for claim in claims)
    return {
        "version": 1,
        "reviewed_at": str(raw_manifest["reviewed_at"]),
        "scope": {
            "included": [
                "Knowledge/*/{index,product,hardware,software}.md",
                "Knowledge/*/facts.yaml",
                "Knowledge/_topics/*.md",
            ],
            "excluded": ["evals/**", "docs/reviews/**"],
        },
        "summary": {
            "active_claims": len(claims),
            "unclosed_claims": 0,
            "by_category": dict(sorted(category_counts.items())),
            "by_verdict": dict(sorted(verdict_counts.items())),
        },
        "claims": rows,
    }
