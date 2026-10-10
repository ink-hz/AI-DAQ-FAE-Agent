"""Bind unsigned B2 normalized claims into an offline B3 dependency graph.

This module does not review facts, grant roles, freeze questions or publish
knowledge. It preserves the prior graph and adds scoped candidate dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json

from .update_impact import bind_coverage_questions


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def extend_review_graph(baseline: dict, candidates: list[dict],
                        bindings: list[dict], vocabulary: dict) -> dict:
    """Return a review-only graph; reject stale or unbound candidate inputs."""
    result = deepcopy(baseline)
    old_records = {row["id"]: row for row in result["records"]}
    sections = {row["section_id"]: row for row in result["sections"]}
    sources = {row["path"]: row for row in result["snapshot"]["sources"]}
    if (len(old_records) != len(result["records"]) or
            len(sections) != len(result["sections"]) or
            len(sources) != len(result["snapshot"]["sources"])):
        raise ValueError("duplicate baseline identity")
    candidate_by_id = {row["id"]: row for row in candidates}
    if len(candidate_by_id) != len(candidates) or set(candidate_by_id) & set(old_records):
        raise ValueError("duplicate candidate identity")
    fields = {row["field_id"]: row for row in vocabulary["fields"]}
    if len(fields) != len(vocabulary["fields"]):
        raise ValueError("duplicate candidate field definition")
    used_fields = {row["data"]["field"] for row in candidates}
    if (set(fields) != used_fields or set(vocabulary["blocked_field_ids"]) != used_fields or
            any(row.get("vocabulary_status") != "candidate_review_pending" for row in fields.values())):
        raise ValueError("candidate field vocabulary is not fully review blocked")

    by_candidate = defaultdict(list)
    seen_bindings = set()
    for binding in bindings:
        cid, oid, sid = (binding["candidate_record_id"],
                         binding["original_record_id"], binding["section_id"])
        if cid not in candidate_by_id or oid not in old_records or sid not in sections:
            raise ValueError("normalization binding has unknown identity")
        candidate, original, section = candidate_by_id[cid], old_records[oid], sections[sid]
        ref = binding["source_ref"]
        key = (cid, oid, sid, _canonical(ref))
        if key in seen_bindings:
            raise ValueError("duplicate normalization binding")
        seen_bindings.add(key)
        if (candidate["data"].get("original_record_id") != oid or
                ref not in candidate.get("source_refs", []) or
                original not in section.get("record_assertions", []) or
                oid not in section.get("dependency_claim_ids", []) or
                candidate.get("scope") != original.get("scope") or
                ref not in original.get("source_refs", []) or
                ref not in section.get("source_refs", [])):
            raise ValueError("normalization binding differs from original section or source")
        by_candidate[cid].append(binding)

    grouped = defaultdict(list)
    for cid, row in candidate_by_id.items():
        if (row.get("kind") != "claim" or row.get("status") != "candidate" or
                not isinstance(row.get("scope"), dict) or
                not isinstance(row["data"].get("conditions"), dict) or
                not row.get("source_refs") or
                any(key in row for key in ("fact_review", "access_review", "view_roles", "forward_roles"))):
            raise ValueError("normalization graph accepts unsigned candidate claims only")
        refs = row["source_refs"]
        if (set(map(_canonical, refs)) !=
                { _canonical(binding["source_ref"]) for binding in by_candidate[cid] }):
            raise ValueError("candidate has missing or extraneous source bindings")
        if any(ref["path"] not in sources or
               sources[ref["path"]]["sha256"] != ref["sha256"] for ref in refs):
            raise ValueError("candidate source is missing from baseline snapshot")
        new_row = deepcopy(row)
        # The normalized claim is a proposal derived from this exact original
        # assertion. Section context can affect the original's applicability,
        # so changes to it must conservatively reach the normalized claim.
        new_row["dependency_record_ids"] = [row["data"]["original_record_id"]]
        result["records"].append(new_row)
        # This remains a context dependency, not a reviewed assertion in the
        # chapter body. Candidate changes still trigger chapter re-review.
        for sid in {b["section_id"] for b in by_candidate[cid]}:
            result_section = sections[sid]
            if cid not in result_section["dependency_claim_ids"]:
                result_section["dependency_claim_ids"].append(cid)
        coverage_scope = {"product_scope": row["scope"],
                          "conditions": row["data"]["conditions"]}
        coverage_identity = (row["data"]["entity_id"], row["data"]["field"],
                             _canonical(coverage_scope))
        grouped[coverage_identity].append(new_row)

    new_cells = []
    for (entity_id, field_id, scope_json), rows in sorted(grouped.items()):
        refs = {_canonical(ref): ref for row in rows for ref in row["source_refs"]}
        new_cells.append({
            "coverage_kind": "entity_field", "entity_id": entity_id,
            "field_id": field_id, "scope": json.loads(scope_json),
            "status": "candidate", "audit_scope": "normalized_candidate_records_only",
            "record_ids": sorted(row["id"] for row in rows),
            "source_refs": [refs[key] for key in sorted(refs)],
            "status_counts": {"candidate": len(rows)},
            "vocabulary_status": "candidate_review_pending",
        })
    added_coverage, added_questions = bind_coverage_questions(new_cells)
    old_coverage = {row["coverage_id"] for row in result["coverage"]}
    old_questions = {row["question_id"] for row in result["questions"]}
    if (old_coverage & {row["coverage_id"] for row in added_coverage} or
            old_questions & {row["question_id"] for row in added_questions}):
        raise ValueError("candidate coverage collides with baseline")
    result["coverage"].extend(added_coverage)
    result["questions"].extend(added_questions)
    for field_id, definition in fields.items():
        result["field_definitions"][field_id] = {
            "status": "candidate_review_pending",
            "baseline": deepcopy(baseline.get("field_definitions", {}).get(field_id)),
            "candidate": deepcopy(definition),
        }
    return result
