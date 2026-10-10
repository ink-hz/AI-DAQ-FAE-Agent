"""Offline reconciliation of source-bound B2 candidate normalization reviews.

The result is a private review input, never a verified or active release.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .records import record_fingerprint


DISPOSITIONS = {
    "typed_proposal", "duplicate_existing", "compound_needs_review",
    "context_only", "conflict", "source_mismatch", "unresolved",
}
_SOURCE_CHECK_KEYS = ("checks", "references", "hash_checks")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _check_sources(entry: dict, original: dict) -> None:
    check = entry.get("source_check")
    if not isinstance(check, dict) or not str(check.get("status", "")).strip():
        raise ValueError(f"source check absent: {original['id']}")
    inspected = next((check[key] for key in _SOURCE_CHECK_KEYS if isinstance(check.get(key), list)), None)
    if inspected is None or not inspected:
        raise ValueError(f"source checks absent: {original['id']}")
    expected = {_canonical(ref) for ref in original["source_refs"]}
    actual = set()
    for row in inspected:
        ref = row.get("source_ref")
        if not isinstance(ref, dict):
            raise ValueError(f"source check invalid: {original['id']}")
        actual.add(_canonical(ref))
        if (row.get("actual_sha256") != ref.get("sha256") or
                not any(row.get(key) is True for key in ("hash_matches", "sha256_matches", "sha256_match")) or
                not any(row.get(key) is True for key in ("locator_exists", "page_exists"))):
            raise ValueError(f"source hash or locator unchecked: {original['id']}")
    if actual != expected:
        raise ValueError(f"source checks incomplete: {original['id']}")


def _claim_from_draft(original: dict, draft: dict) -> dict:
    if "data" in draft:
        candidate = deepcopy(draft)
        if candidate.get("kind") == "claim":
            bound = candidate.get("data", {}).get("original_record_id")
            if bound not in (None, original["id"]):
                raise ValueError(f"candidate rebound to another original: {original['id']}")
            candidate["data"]["original_record_id"] = original["id"]
    else:
        data = {key: deepcopy(value) for key, value in draft.items()
                if key not in {"status", "scope", "source_refs"}}
        data["original_record_id"] = original["id"]
        identity = {"original_id": original["id"], "scope": draft.get("scope"),
                    "source_refs": draft.get("source_refs"), "field": draft.get("field"),
                    "value": draft.get("value"), "unit": draft.get("unit"),
                    "conditions": draft.get("conditions")}
        candidate = {
            "id": "claim:b2n:" + hashlib.sha256(_canonical(identity).encode()).hexdigest()[:24],
            "kind": "claim", "status": draft.get("status"),
            "scope": deepcopy(draft.get("scope")),
            "source_refs": deepcopy(draft.get("source_refs")),
            "data": data,
        }
    if candidate.get("status") != "candidate" or candidate.get("kind") not in {"claim", "procedure"}:
        raise ValueError(f"positive claim is not candidate: {original['id']}")
    if candidate.get("scope") != original["scope"]:
        raise ValueError(f"candidate scope changed: {original['id']}")
    refs = candidate.get("source_refs")
    if not isinstance(refs, list) or not refs or any(ref not in original["source_refs"] for ref in refs):
        raise ValueError(f"candidate source widened: {original['id']}")
    if any(key in candidate for key in ("review", "access_review", "link_review", "permission_review")):
        raise ValueError(f"candidate contains approval: {original['id']}")
    data = candidate.get("data")
    if not isinstance(data, dict) or any(key in data for key in ("review", "access_review", "permission_review")):
        raise ValueError(f"candidate data invalid: {original['id']}")
    if candidate["kind"] == "claim" and not all(data.get(key) not in (None, "", [], {})
                                                for key in ("entity_id", "field", "value", "unit", "conditions")):
        raise ValueError(f"candidate fact incomplete: {original['id']}")
    if candidate["kind"] == "claim" and data["entity_id"] != original["data"].get("entity_id"):
        raise ValueError(f"candidate entity changed: {original['id']}")
    if candidate["kind"] == "procedure" and candidate != original:
        raise ValueError(f"procedure changed without step review: {original['id']}")
    return candidate


def merge_normalization_reviews(originals: list[dict], batches: list[list[dict]]) -> tuple[list[dict], dict]:
    """Check one-to-one dispositions and return candidate proposals plus a safe summary.

    A proposed claim may narrow a cited source list, but cannot widen its product,
    variant, topology, or status. Exact original procedures remain in the input.
    """
    by_id = {row["id"]: row for row in originals}
    if len(by_id) != len(originals):
        raise ValueError("duplicate original record IDs")
    entries = [entry for batch in batches for entry in batch]
    ids = [entry.get("original_id") for entry in entries]
    if len(ids) != len(set(ids)) or set(ids) != set(by_id):
        raise ValueError("normalization coverage must be exact")
    proposals: list[dict] = []
    retained_procedures = 0
    for entry in entries:
        original = by_id[entry["original_id"]]
        if entry.get("original_sha256") != record_fingerprint(original):
            raise ValueError(f"original fingerprint changed: {original['id']}")
        if entry.get("source_refs") != original["source_refs"]:
            raise ValueError(f"original source refs changed: {original['id']}")
        if entry.get("disposition") not in DISPOSITIONS:
            raise ValueError(f"unknown disposition: {original['id']}")
        _check_sources(entry, original)
        drafts = entry.get("proposed_claims")
        if not isinstance(drafts, list):
            raise ValueError(f"proposals missing: {original['id']}")
        if entry["disposition"] in {"conflict", "duplicate_existing", "context_only",
                                    "source_mismatch", "unresolved"} and drafts:
            raise ValueError(f"positive claim from unresolved record: {original['id']}")
        for draft in drafts:
            candidate = _claim_from_draft(original, draft)
            if candidate["id"] == original["id"] and candidate["kind"] == "procedure":
                retained_procedures += 1
            else:
                proposals.append(candidate)
    candidate_ids = [row["id"] for row in proposals]
    if len(set(candidate_ids)) != len(candidate_ids) or set(candidate_ids) & set(by_id):
        raise ValueError("duplicate or reused proposal ID")
    counts = Counter(entry["disposition"] for entry in entries)
    report = {
        "coverage": {"originals": len(originals), "dispositions": len(entries), "missing": 0},
        "dispositions": dict(sorted(counts.items())),
        "new_candidate_claims": len(proposals),
        "retained_candidate_procedures": retained_procedures,
        "publication_status": "candidate_only_no_active_release",
    }
    return proposals, report


def expand_snapshot_for_review(snapshot: dict, refs: list[dict], archive_root: Path) -> dict:
    """Add exact Markdown subline locators for offline candidate validation.

    The source must match its archived hash, and the subline must sit within an
    already indexed chunk. This creates a review-only snapshot copy; callers
    cannot use it as proof of approval or publication readiness.
    """
    root = Path(archive_root).resolve()
    expanded = deepcopy(snapshot)
    sources = {row["path"]: row for row in expanded.get("sources", [])}
    indexed = {(row["source_path"], row["source_sha256"], _canonical(row["locator"]))
               for row in expanded.get("chunks", [])}
    for ref in refs:
        path = ref.get("path")
        sha = ref.get("sha256")
        locator = ref.get("locator")
        if not isinstance(path, str) or not isinstance(locator, dict):
            raise ValueError("invalid locator reference")
        key = (path, sha, _canonical(locator))
        if key in indexed:
            continue
        source = sources.get(path)
        if source is None or source.get("sha256") != sha or source.get("kind") != "markdown":
            raise ValueError(f"source unavailable for subline locator: {path}")
        if (locator.get("kind") != "lines" or not isinstance(locator.get("start"), int) or
                not isinstance(locator.get("end"), int)):
            raise ValueError(f"non-Markdown locator absent from snapshot: {path}")
        start, end = locator["start"], locator["end"]
        source_path = (root / path).resolve()
        if not source_path.is_relative_to(root):
            raise ValueError(f"source path outside archive: {path}")
        raw = source_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError(f"source bytes changed: {path}")
        lines = raw.decode(source.get("encoding") or "utf-8-sig").splitlines()
        if not 1 <= start <= end <= len(lines):
            raise ValueError(f"subline locator outside source: {path}")
        parents = [chunk for chunk in expanded["chunks"]
                   if chunk["source_path"] == path and chunk["source_sha256"] == sha and
                   chunk["locator"].get("kind") == "lines" and
                   chunk["locator"]["start"] <= start <= end <= chunk["locator"]["end"]]
        text = "\n".join(lines[start - 1:end])
        if not parents or not any(text in chunk.get("text", "") for chunk in parents):
            raise ValueError(f"subline locator not in indexed text: {path}")
        text_sha = hashlib.sha256(text.encode()).hexdigest()
        expanded["chunks"].append({
            "chunk_id": hashlib.sha256(_canonical({"path": path, "sha256": sha,
                                                    "locator": locator, "text_sha256": text_sha}).encode()).hexdigest(),
            "source_path": path,
            "source_sha256": sha,
            "locator": deepcopy(locator),
            "text": text,
            "text_sha256": text_sha,
        })
        indexed.add(key)
    return expanded


def audit_candidate_vocabulary(proposals: list[dict], baseline_fields: list[dict]) -> dict:
    """Expose field-definition work before any normalized claim is approved."""
    baseline = {row["field_id"]: row for row in baseline_fields}
    if len(baseline) != len(baseline_fields):
        raise ValueError("duplicate baseline field IDs")
    grouped: dict[str, list[dict]] = {}
    for row in proposals:
        if row["kind"] != "claim" or row["status"] != "candidate":
            raise ValueError("vocabulary input must be candidate claims")
        grouped.setdefault(row["data"]["field"], []).append(row)
    fields = []
    new_fields = []
    multi_unit = []
    baseline_mismatch = []
    unreviewed_baseline = []
    for field_id, rows in sorted(grouped.items()):
        units = sorted({row["data"]["unit"] for row in rows})
        comparators = sorted({str(row["data"].get("comparator", "")) for row in rows})
        baseline_row = baseline.get(field_id)
        is_new = baseline_row is None
        has_multi_unit = len(units) > 1
        has_baseline_mismatch = baseline_row is not None and (
            not set(units) <= set(baseline_row.get("units", [])) or
            not set(comparators) <= set(baseline_row.get("comparators", [])))
        baseline_unreviewed = baseline_row is not None and baseline_row.get("vocabulary_status") != "verified"
        if is_new:
            new_fields.append(field_id)
        if has_multi_unit:
            multi_unit.append(field_id)
        if has_baseline_mismatch:
            baseline_mismatch.append(field_id)
        if baseline_unreviewed:
            unreviewed_baseline.append(field_id)
        fields.append({
            "field_id": field_id,
            "vocabulary_status": "candidate_review_pending",
            "record_ids": sorted(row["id"] for row in rows),
            "products": sorted({row["scope"]["product"] for row in rows}),
            "units": units,
            "comparators": comparators,
            "baseline_field_exists": not is_new,
            "baseline_units": baseline_row.get("units", []) if baseline_row else [],
            "baseline_comparators": baseline_row.get("comparators", []) if baseline_row else [],
            "review_reasons": [reason for active, reason in (
                (is_new, "new_field_definition"),
                (has_multi_unit, "multiple_candidate_units"),
                (has_baseline_mismatch, "baseline_unit_or_comparator_mismatch"),
                (baseline_unreviewed, "baseline_definition_unreviewed"),
                (True, "trusted_field_definition_review_required"),
            ) if active],
        })
    # A status string in a private dictionary cannot authenticate approval.
    # This offline builder has no trusted field-definition signature importer.
    blocked = sorted(grouped)
    return {
        "field_count": len(fields),
        "new_field_ids": new_fields,
        "multi_unit_field_ids": multi_unit,
        "baseline_mismatch_field_ids": baseline_mismatch,
        "unreviewed_baseline_field_ids": unreviewed_baseline,
        "blocked_field_ids": blocked,
        "fields": fields,
        "publication_status": "candidate_vocabulary_review_pending",
    }
