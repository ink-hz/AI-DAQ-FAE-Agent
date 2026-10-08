"""Offline schema and provenance gates for manually governed DAQ records."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import re
from urllib.parse import urlsplit


KINDS = {"entity", "claim", "topology", "procedure", "software", "link"}
STATUSES = {"candidate", "verified", "conflict", "unknown", "unsupported"}
_ID = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
_REQUIRED_DATA = {
    "entity": {"name", "entity_type"},
    "claim": {"entity_id", "field", "unit", "conditions"},
    "topology": {"members", "roles", "connections", "power", "platform", "sync_target", "storage"},
    "procedure": {"task", "topology_id", "prerequisites", "steps", "checks", "failure_branches"},
    "software": {"entity_id", "hardware_revision", "platform", "connection_mode",
                 "software", "version", "capability", "evidence_level"},
    "link": {"url", "title", "link_type"},
}


def _finding(record_id: object, code: str) -> dict[str, str]:
    return {"record_id": str(record_id), "code": code}


def _review_valid(review: object) -> bool:
    if not isinstance(review, dict):
        return False
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        return False
    try:
        date.fromisoformat(str(review["reviewed_at"]))
    except (KeyError, ValueError, TypeError):
        return False
    return True


def _valid_source_ref(ref: object, sources: dict, chunks: set) -> str | None:
    if not isinstance(ref, dict):
        return "source_ref_invalid"
    path = ref.get("path")
    source = sources.get(path) if isinstance(path, str) else None
    if source is None:
        return "source_missing"
    if source.get("sha256") != ref.get("sha256"):
        return "source_hash_mismatch"
    locator = ref.get("locator")
    if locator == {"kind": "file"} and source.get("kind") == "asset":
        return None
    if not isinstance(locator, dict):
        return "source_locator_invalid"
    if locator.get("kind") == "page":
        valid = (set(locator) == {"kind", "page"} and
                 type(locator.get("page")) is int and locator["page"] >= 1)
    elif locator.get("kind") == "lines":
        valid = (set(locator) == {"kind", "start", "end"} and
                 type(locator.get("start")) is int and type(locator.get("end")) is int and
                 1 <= locator["start"] <= locator["end"])
    else:
        valid = False
    if not valid:
        return "source_locator_missing"
    key = (path, ref["sha256"], json.dumps(locator, sort_keys=True))
    if key not in chunks:
        return "source_locator_missing"
    return None


def validate_records(records: list[dict], snapshot: dict) -> tuple[list[dict], list[dict]]:
    """Return normalized records plus findings; callers must reject any findings."""
    sources = {row["path"]: row for row in snapshot.get("sources", [])}
    chunks = {
        (row["source_path"], row["source_sha256"], json.dumps(row["locator"], sort_keys=True))
        for row in snapshot.get("chunks", [])
    }
    normalized: list[dict] = []
    findings: list[dict] = []
    seen: set[str] = set()
    for original in records:
        if not isinstance(original, dict):
            findings.append(_finding("", "record_invalid"))
            continue
        row = deepcopy(original)
        record_id = row.get("id")
        kind = row.get("kind")
        status = row.get("status")
        if not isinstance(record_id, str) or not _ID.fullmatch(record_id):
            findings.append(_finding(record_id, "id_invalid"))
        elif record_id in seen:
            findings.append(_finding(record_id, "duplicate_id"))
        if isinstance(record_id, str):
            seen.add(record_id)
        if not isinstance(kind, str) or kind not in KINDS:
            findings.append(_finding(record_id, "kind_invalid"))
        if not isinstance(status, str) or status not in STATUSES:
            findings.append(_finding(record_id, "status_invalid"))
        if not isinstance(row.get("scope"), dict):
            findings.append(_finding(record_id, "scope_invalid"))
        refs = row.get("source_refs")
        if not isinstance(refs, list) or not refs:
            findings.append(_finding(record_id, "source_refs_missing"))
            refs = []
        for ref in refs:
            code = _valid_source_ref(ref, sources, chunks)
            if code:
                findings.append(_finding(record_id, code))
        data = row.get("data")
        if not isinstance(data, dict):
            findings.append(_finding(record_id, "data_invalid"))
            data = {}
        if kind in _REQUIRED_DATA and not _REQUIRED_DATA[kind] <= data.keys():
            findings.append(_finding(record_id, "data_fields_missing"))
        if kind == "claim" and status != "conflict" and "value" not in data:
            findings.append(_finding(record_id, "claim_value_missing"))
        if status == "conflict":
            candidates = data.get("candidates")
            if not isinstance(candidates, list) or len(candidates) < 2:
                findings.append(_finding(record_id, "conflict_candidates_missing"))
            else:
                values = [repr(candidate.get("value")) for candidate in candidates
                          if isinstance(candidate, dict)]
                if len(set(values)) < 2:
                    findings.append(_finding(record_id, "conflict_values_not_distinct"))
                for candidate in candidates:
                    ref = candidate.get("source_ref") if isinstance(candidate, dict) else None
                    code = _valid_source_ref(ref, sources, chunks)
                    if code:
                        findings.append(_finding(record_id, code))
        answerable = status in {"verified", "unsupported"}
        if answerable:
            if not _review_valid(row.get("fact_review")):
                findings.append(_finding(record_id, "fact_review_missing"))
            access = row.get("access_review")
            if not _review_valid(access) or not isinstance(access.get("view_roles"), list) \
                    or not access["view_roles"] or not all(isinstance(role, str) and role
                    for role in access["view_roles"]) or not isinstance(access.get("forward_roles"), list) \
                    or not all(isinstance(role, str) and role for role in access["forward_roles"]) \
                    or not set(access["forward_roles"]) <= set(access["view_roles"]):
                findings.append(_finding(record_id, "access_review_missing"))
            if any(isinstance(ref, dict) and ref.get("locator") == {"kind": "file"}
                   for ref in refs) and kind not in {"software", "link"}:
                findings.append(_finding(record_id, "asset_not_fact_evidence"))
            if kind == "software" and data.get("evidence_level") not in {
                "device_tested", "end_to_end_verified",
            }:
                findings.append(_finding(record_id, "software_support_unverified"))
            if kind == "software" and refs and all(
                isinstance(ref, dict) and ref.get("locator") == {"kind": "file"}
                for ref in refs
            ):
                findings.append(_finding(record_id, "asset_cannot_verify_support"))
            if kind == "link":
                review = row.get("link_review")
                url = data.get("url")
                parsed = urlsplit(url) if isinstance(url, str) else None
                if not _review_valid(review) or review.get("final_url") != url \
                        or parsed is None or parsed.scheme != "https" or not parsed.hostname \
                        or parsed.username or parsed.password:
                    findings.append(_finding(record_id, "link_review_missing"))
        row["answerable"] = answerable
        normalized.append(row)
    return normalized, findings


def impact_report(records: list[dict], change: dict) -> dict[str, list[str]]:
    changed = set(change.get("changed", [])) | set(change.get("removed", []))
    impacted = set()
    negative = set()
    for row in records:
        record_id = row.get("id")
        if not isinstance(record_id, str):
            continue
        paths = {ref.get("path") for ref in row.get("source_refs", [])
                 if isinstance(ref, dict)}
        if paths & changed:
            impacted.add(record_id)
        if change.get("added") and row.get("status") in {"unknown", "unsupported"}:
            negative.add(record_id)
    return {
        "impacted_record_ids": sorted(impacted),
        "recheck_negative_ids": sorted(negative),
    }
