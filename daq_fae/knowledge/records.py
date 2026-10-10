"""Offline schema and provenance gates for manually governed DAQ records."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import re
from urllib.parse import urlsplit


KINDS = {"entity", "claim", "topology", "procedure", "software", "link"}
STATUSES = {"candidate", "verified", "conflict", "unknown", "unsupported"}
ROLES = {"internal_fae", "tmall_support", "channel"}
_ID = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
_UNRESOLVED_MARKERS = {
    "pending_external_selector", "module_revision_unconfirmed",
    "needs_interface_reconciliation", "variant_unconfirmed",
    "software_version_unconfirmed",
}
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


def record_fingerprint(row: dict) -> str:
    """Bind every approval to exact fact, applicability, and source identities."""
    core = {key: row[key] for key in ("id", "kind", "status", "scope", "source_refs", "data")}
    payload = json.dumps(core, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def access_fingerprint(row: dict) -> str:
    access = row.get("access_review") or {}
    payload = json.dumps({
        "record_sha256": record_fingerprint(row),
        "view_roles": sorted(access.get("view_roles", [])),
        "forward_roles": sorted(access.get("forward_roles", [])),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _data_valid(kind: str, status: str, data: dict) -> bool:
    if kind == "entity":
        return _nonempty(data.get("name")) and data.get("entity_type") in {
            "family", "device", "variant", "component", "kit", "hub",
        }
    if kind == "claim":
        value = data.get("value")
        return (isinstance(data.get("entity_id"), str) and
                bool(_ID.fullmatch(data["entity_id"])) and
                isinstance(data.get("field"), str) and bool(_ID.fullmatch(data["field"])) and
                _nonempty(data.get("unit")) and isinstance(data.get("conditions"), dict) and
                (status == "conflict" or
                 (value is not None and value != "" and value != [] and value != {})))
    if kind == "topology":
        return (isinstance(data.get("members"), list) and bool(data["members"]) and
                all(_nonempty(item) for item in data["members"]) and
                isinstance(data.get("roles"), dict) and
                isinstance(data.get("connections"), list) and
                isinstance(data.get("power"), dict) and
                _nonempty(data.get("platform")) and _nonempty(data.get("sync_target")) and
                _nonempty(data.get("storage")))
    if kind == "procedure":
        return (all(_nonempty(data.get(key)) for key in ("task", "topology_id")) and
                all(isinstance(data.get(key), list) for key in
                    ("prerequisites", "steps", "checks", "failure_branches")) and
                bool(data.get("steps")) and bool(data.get("checks")))
    if kind == "software":
        return all(_nonempty(data.get(key)) for key in
                   ("entity_id", "hardware_revision", "platform", "connection_mode",
                    "software", "version", "capability", "evidence_level"))
    if kind == "link":
        return all(_nonempty(data.get(key)) for key in ("url", "title", "link_type"))
    return False


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


def link_review_valid(row: dict) -> bool:
    """Validate a separately reviewed page bound to exact delivery applicability."""
    review = row.get("link_review")
    data = row.get("data", {})
    if not isinstance(data, dict):
        return False
    page = data.get("page_evidence")
    if not _review_valid(review) or not isinstance(page, dict):
        return False
    url = data.get("url")
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or \
                parsed.password or parsed.port not in (None, 443):
            return False
        captured = date.fromisoformat(page["captured_at"])
        expires = date.fromisoformat(page["valid_until"])
        reviewed = date.fromisoformat(review["reviewed_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return (review.get("final_url") == url and
            captured <= reviewed <= date.today() <= expires and
            all(_nonempty(page.get(key)) for key in ("title", "version")) and
            isinstance(page.get("snapshot_sha256"), str) and
            re.fullmatch(r"[0-9a-f]{64}", page["snapshot_sha256"]) is not None and
            bool(row.get("scope")) and page.get("sku_scope") == row["scope"])


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
        if not isinstance(row.get("scope"), dict) or not row["scope"]:
            findings.append(_finding(record_id, "scope_invalid"))
        elif "required_selectors" in row["scope"]:
            selectors = row["scope"]["required_selectors"]
            if not isinstance(selectors, list) or not selectors or not all(
                isinstance(key, str) and key != "required_selectors" and
                bool(_ID.fullmatch(key)) and key in row["scope"] and
                row["scope"][key] not in (None, "", [], {})
                for key in selectors
            ) or len(set(selectors)) != len(selectors):
                findings.append(_finding(record_id, "scope_selector_invalid"))
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
        if isinstance(kind, str) and kind in KINDS and isinstance(status, str) \
                and status in STATUSES and not _data_valid(kind, status, data):
            findings.append(_finding(record_id, f"{kind}_data_invalid"))
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
            if any(data.get(marker) is True for marker in _UNRESOLVED_MARKERS):
                findings.append(_finding(record_id, "unresolved_review_marker"))
            fingerprint = record_fingerprint(row) if all(
                key in row for key in ("id", "kind", "status", "scope", "source_refs", "data")
            ) else None
            fact_review = row.get("fact_review")
            if not _review_valid(fact_review):
                findings.append(_finding(record_id, "fact_review_missing"))
            elif fact_review.get("record_sha256") != fingerprint:
                findings.append(_finding(record_id, "fact_review_stale"))
            access = row.get("access_review")
            if not _review_valid(access) or not isinstance(access.get("view_roles"), list) \
                    or not access["view_roles"] or not all(isinstance(role, str) and role
                    and role in ROLES for role in access["view_roles"]) \
                    or not isinstance(access.get("forward_roles"), list) \
                    or not all(isinstance(role, str) and role in ROLES
                               for role in access["forward_roles"]) \
                    or len(set(access["view_roles"])) != len(access["view_roles"]) \
                    or len(set(access["forward_roles"])) != len(access["forward_roles"]) \
                    or not set(access["forward_roles"]) <= set(access["view_roles"]):
                findings.append(_finding(record_id, "access_review_missing"))
            elif access.get("record_sha256") != access_fingerprint(row):
                findings.append(_finding(record_id, "access_review_stale"))
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
                if not link_review_valid(row):
                    findings.append(_finding(record_id, "link_review_missing"))
                elif review.get("record_sha256") != fingerprint:
                    findings.append(_finding(record_id, "link_review_stale"))
        row["answerable"] = answerable
        normalized.append(row)
    entity_rows = {row["id"]: row for row in normalized
                   if row.get("kind") == "entity" and isinstance(row.get("id"), str)}
    entities_by_name: dict[str, list[dict]] = {}
    for entity in entity_rows.values():
        data = entity.get("data")
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            entities_by_name.setdefault(data["name"].strip().casefold(), []).append(entity)
    for group in entities_by_name.values():
        if len(group) < 2:
            continue
        seen_selectors: dict[str, str] = {}
        for entity in group:
            scope = entity.get("scope")
            if not isinstance(scope, dict) or not scope.get("required_selectors"):
                findings.append(_finding(entity["id"],
                                         "duplicate_variant_name_without_selector"))
                continue
            selectors = scope["required_selectors"]
            if not isinstance(selectors, list) or not all(
                isinstance(key, str) and key in scope for key in selectors
            ):
                continue
            signature = json.dumps([[key, scope[key]] for key in sorted(selectors)],
                                   ensure_ascii=False, sort_keys=True, default=str)
            previous = seen_selectors.get(signature)
            if previous:
                findings.append(_finding(previous, "duplicate_variant_selector_value"))
                findings.append(_finding(entity["id"], "duplicate_variant_selector_value"))
            else:
                seen_selectors[signature] = entity["id"]
    for row in normalized:
        if row.get("kind") not in {"claim", "software"} or not isinstance(row.get("data"), dict):
            continue
        entity_id = row["data"].get("entity_id")
        entity = entity_rows.get(entity_id) if isinstance(entity_id, str) else None
        entity_scope = entity.get("scope") if entity else None
        row_scope = row.get("scope")
        if not isinstance(entity_scope, dict) or not isinstance(row_scope, dict):
            continue
        selectors = entity_scope.get("required_selectors")
        if isinstance(selectors, list) and selectors and all(isinstance(key, str) for key in selectors):
            inherited = row_scope.get("required_selectors")
            if not isinstance(inherited, list) or any(
                key not in inherited or row_scope.get(key) != entity_scope.get(key)
                for key in selectors
            ):
                findings.append(_finding(row.get("id"), "entity_selector_not_propagated"))
    claims_by_key: dict[str, list[dict]] = {}
    for row in normalized:
        if row.get("kind") != "claim" or row.get("status") not in {
            "verified", "unsupported", "conflict",
        }:
            continue
        data = row.get("data")
        if not isinstance(data, dict) or not isinstance(row.get("scope"), dict):
            continue
        key = json.dumps([data.get("entity_id"), data.get("field"), data.get("conditions"),
                          row["scope"]], ensure_ascii=False, sort_keys=True, default=str)
        claims_by_key.setdefault(key, []).append(row)
    for group in claims_by_key.values():
        answerable_rows = [row for row in group if row["status"] in {"verified", "unsupported"}]
        if any(row["status"] == "conflict" for row in group) and answerable_rows:
            for row in group:
                row["answerable"] = False
                findings.append(_finding(row["id"], "unadjudicated_conflict"))
            continue
        values = {json.dumps([row["status"], row["data"].get("value"),
                              row["data"].get("unit")], ensure_ascii=False,
                             sort_keys=True, default=str) for row in answerable_rows}
        if len(values) > 1:
            for row in group:
                row["answerable"] = False
                findings.append(_finding(row["id"], "unadjudicated_conflict"))
    invalid_ids = {finding["record_id"] for finding in findings}
    for row in normalized:
        if row.get("id") in invalid_ids:
            row["answerable"] = False
    entities = {row["id"] for row in normalized
                if row.get("kind") == "entity" and row["answerable"]}
    for row in normalized:
        if not row["answerable"]:
            continue
        data = row["data"]
        if row["kind"] == "topology" and any(
            member not in entities for member in data["members"]
        ):
            row["answerable"] = False
            findings.append(_finding(row["id"], "entity_reference_missing"))
        elif row["kind"] in {"claim", "software"} and data["entity_id"] not in entities:
            row["answerable"] = False
            findings.append(_finding(row["id"], "entity_reference_missing"))
    topologies = {row["id"] for row in normalized
                  if row.get("kind") == "topology" and row["answerable"]}
    for row in normalized:
        if row["answerable"] and row["kind"] == "procedure" and \
                row["data"]["topology_id"] not in topologies:
            row["answerable"] = False
            findings.append(_finding(row["id"], "topology_reference_missing"))
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
        data = row.get("data")
        if isinstance(data, dict) and isinstance(data.get("candidates"), list):
            paths.update(candidate.get("source_ref", {}).get("path")
                         for candidate in data["candidates"]
                         if isinstance(candidate, dict) and
                         isinstance(candidate.get("source_ref"), dict))
        if paths & changed:
            impacted.add(record_id)
        if change.get("added") and row.get("status") in {"unknown", "unsupported"}:
            negative.add(record_id)
    return {
        "impacted_record_ids": sorted(impacted),
        "recheck_negative_ids": sorted(negative),
    }
