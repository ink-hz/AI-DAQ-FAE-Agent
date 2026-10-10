"""Join unsigned field, conflict and chapter evidence for private D2 review."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy

from .records import record_fingerprint

_DECISIONS = {"compound_needs_review", "conflict", "source_mismatch", "unresolved"}


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _unique_refs(refs: list[dict]) -> list[dict]:
    unique = {_canonical(ref): ref for ref in refs}
    return [deepcopy(unique[key]) for key in sorted(unique)]


def assemble_queue(originals: list[dict], dispositions: list[dict],
                   candidates: list[dict], proposal_bindings: list[dict],
                   vocabulary: dict, section_index: dict, audit: dict,
                   old_bindings: list[dict], review_items: list[dict]) -> dict:
    """Create one unsigned review queue with exact original-to-section links."""
    old = {row["id"]: row for row in originals}
    new = {row["id"]: row for row in candidates}
    sections = {row["section_id"]: row for row in section_index["sections"]}
    decisions = {row["original_id"]: row for row in dispositions}
    fields = {row["field_id"]: row for row in vocabulary["fields"]}
    items = {row["item_id"]: row for row in review_items}
    if (len(old) != len(originals) or len(new) != len(candidates) or
            len(sections) != len(section_index["sections"]) or
            len(decisions) != len(dispositions) or len(fields) != len(vocabulary["fields"]) or
            len(items) != len(review_items)):
        raise ValueError("duplicate review queue identity")
    if (set(old) != set(decisions) or set(sections) != set(section_index["bodies"]) or
            set(items) != set(new)):
        raise ValueError("review queue inventory is incomplete")
    for cid, item in items.items():
        if (item["record"] != new[cid] or item["source_refs"] != new[cid]["source_refs"] or
                {_canonical(part["source_ref"]) for part in item["source_bodies"]} !=
                {_canonical(ref) for ref in new[cid]["source_refs"]} or
                any(not isinstance(part.get("text"), str) or
                    (part.get("sha256") and
                     hashlib.sha256(part["text"].encode()).hexdigest() != part["sha256"])
                    for part in item["source_bodies"])):
            raise ValueError("candidate review item differs from record or source")
    for oid, disposition in decisions.items():
        if (disposition["original_sha256"] != record_fingerprint(old[oid]) or
                disposition["source_refs"] != old[oid]["source_refs"]):
            raise ValueError("disposition differs from original record")
    if (set(fields) != {row["data"]["field"] for row in candidates} or
            set(vocabulary["blocked_field_ids"]) != set(fields) or
            any(row.get("vocabulary_status") != "candidate_review_pending"
                for row in fields.values())):
        raise ValueError("field queue must contain only blocked candidate definitions")

    section_by_original = defaultdict(set)
    seen_old = set()
    for binding in old_bindings:
        oid, sid = binding["record_id"], binding["section_id"]
        if (oid not in old or sid not in sections or (oid, sid) in seen_old or
                binding["source_refs"] != old[oid]["source_refs"] or
                old[oid] not in sections[sid].get("record_assertions", []) or
                oid not in sections[sid].get("dependency_claim_ids", [])):
            raise ValueError("original-to-section binding differs from assertion")
        seen_old.add((oid, sid))
        section_by_original[oid].add(sid)
    if set(section_by_original) != set(old):
        raise ValueError("original record has no section binding")

    section_by_candidate = defaultdict(set)
    seen_proposals = set()
    for binding in proposal_bindings:
        cid, oid, sid = (binding["candidate_record_id"],
                         binding["original_record_id"], binding["section_id"])
        if (cid not in new or oid not in old or sid not in section_by_original[oid] or
                (cid, oid, sid, _canonical(binding["source_ref"])) in seen_proposals):
            raise ValueError("candidate-to-section binding has invalid identity")
        seen_proposals.add((cid, oid, sid, _canonical(binding["source_ref"])))
        if (new[cid]["data"].get("original_record_id") != oid or
                new[cid]["scope"] != old[oid]["scope"] or
                binding["source_ref"] not in new[cid]["source_refs"] or
                binding["source_ref"] not in old[oid]["source_refs"]):
            raise ValueError("candidate differs from original scope or source")
        section_by_candidate[cid].add(sid)
    if set(section_by_candidate) != set(new):
        raise ValueError("candidate has no section binding")
    for cid, row in new.items():
        if row.get("status") != "candidate" or row.get("kind") != "claim":
            raise ValueError("review queue accepts candidate claims only")
        if {_canonical(ref) for ref in row["source_refs"]} != {
                _canonical(b["source_ref"]) for b in proposal_bindings
                if b["candidate_record_id"] == cid}:
            raise ValueError("candidate source binding incomplete")
    for field_id, definition in fields.items():
        if sorted(definition["record_ids"]) != sorted(
                row["id"] for row in candidates if row["data"]["field"] == field_id):
            raise ValueError("field definition record membership differs")

    by_section_findings = defaultdict(list)
    for finding in audit["findings"]:
        sid = finding["section_id"]
        if sid not in sections:
            raise ValueError("audit finding references unknown section")
        by_section_findings[sid].append(finding)

    field_queue = []
    for field_id, definition in sorted(fields.items()):
        records = [row for row in candidates if row["data"]["field"] == field_id]
        labels = sorted({old[row["data"]["original_record_id"]]["data"]["source_label"]
                         for row in records})
        payload = {
            "field_id": field_id, "status": "candidate_review_pending",
            "proposed_name_zh": None, "observed_source_labels": labels,
            "observed_units": sorted({row["data"]["unit"] for row in records}),
            "observed_comparators": sorted({row["data"]["comparator"] for row in records}),
            "product_scopes": sorted({row["scope"]["product"] for row in records}),
            "candidate_record_ids": sorted(row["id"] for row in records),
            "original_record_ids": sorted({row["data"]["original_record_id"] for row in records}),
            "section_ids": sorted({sid for row in records for sid in section_by_candidate[row["id"]]}),
            "source_refs": _unique_refs([ref for row in records for ref in row["source_refs"]]),
            "review_reasons": deepcopy(definition["review_reasons"]),
            "field_vocabulary_candidate": deepcopy(definition),
            "candidate_review_items": deepcopy([items[row["id"]] for row in records]),
        }
        payload["review_sha256"] = _digest(payload)
        field_queue.append(payload)

    decision_queue = []
    for oid, disposition in sorted(decisions.items()):
        if disposition["disposition"] not in _DECISIONS:
            continue
        payload = {
            "original_record_id": oid, "disposition": disposition["disposition"],
            "status": "pending_human_adjudication", "reason": disposition["reason"],
            "scope": deepcopy(old[oid]["scope"]),
            "source_refs": deepcopy(old[oid]["source_refs"]),
            "source_check": deepcopy(disposition["source_check"]),
            "original_record": deepcopy(old[oid]),
            "section_ids": sorted(section_by_original[oid]),
            "safe_partial_candidate_ids": sorted(
                cid for cid, row in new.items() if row["data"]["original_record_id"] == oid),
        }
        payload["review_sha256"] = _digest(payload)
        decision_queue.append(payload)

    section_queue = []
    for sid, section in sorted(sections.items()):
        candidate_ids = sorted(cid for cid, ids in section_by_candidate.items() if sid in ids)
        decision_ids = sorted(item["original_record_id"] for item in decision_queue
                              if sid in item["section_ids"])
        body = section_index["bodies"][sid]
        if (not isinstance(body, str) or
                section.get("body_sha256") and
                hashlib.sha256(body.encode()).hexdigest() != section["body_sha256"]):
            raise ValueError("section body differs from index hash")
        payload = {
            "section_id": sid, "status": "candidate_requires_human_review",
            "section": deepcopy(section), "body": body,
            "source_refs": deepcopy(section["source_refs"]),
            "candidate_record_ids": candidate_ids,
            "field_ids": sorted({new[cid]["data"]["field"] for cid in candidate_ids}),
            "content_decision_ids": decision_ids,
            "findings": deepcopy(by_section_findings[sid]),
        }
        payload["review_sha256"] = _digest(payload)
        section_queue.append(payload)
    return {"field_definitions": field_queue, "content_decisions": decision_queue,
            "sections": section_queue,
            "summary": {"fields": len(field_queue), "content_decisions": len(decision_queue),
                        "sections": len(section_queue), "section_findings": len(audit["findings"]),
                        "fact_reviews": 0, "access_reviews": 0, "online_eligible": False}}
