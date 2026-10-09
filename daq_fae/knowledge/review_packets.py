"""Candidate-only, source-located review packets for frequently changing DAQ material."""

from __future__ import annotations

from fnmatch import fnmatchcase
import hashlib
import json
import re


_ROLES = ("internal_fae", "tmall_support", "channel")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_CLASSIFICATION = (
    re.compile(r"Document\s+Classification\s*[:：]\s*(Confidential|Public)", re.I),
    re.compile(r"文档密级\s*[:：]\s*(外部公开|内部[^\s]*)"),
)


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def _validate_recipe(recipe: dict) -> None:
    if not isinstance(recipe, dict) or not isinstance(recipe.get("cases"), list):
        raise ValueError("review recipe must contain cases")
    _require_string(recipe.get("version"), "recipe version")
    ids = set()
    for case in recipe["cases"]:
        if not isinstance(case, dict):
            raise ValueError("review case must be an object")
        case_id = _require_string(case.get("id"), "case id")
        if case_id in ids:
            raise ValueError(f"duplicate review case: {case_id}")
        ids.add(case_id)
        for key in ("group", "question", "owner"):
            _require_string(case.get(key), f"{case_id} {key}")
        selectors = case.get("selectors")
        if not isinstance(selectors, list) or not selectors:
            raise ValueError(f"{case_id} needs selectors")
        for selector in selectors:
            if not isinstance(selector, dict):
                raise ValueError("selector must be an object")
            if selector.get("match_on", "text") not in {"text", "asset_path"}:
                raise ValueError("selector match_on invalid")
            glob = _require_string(selector.get("source_glob"), "source_glob")
            if glob.startswith("/") or ".." in glob.split("/"):
                raise ValueError("source_glob must be relative")
            pattern = _require_string(selector.get("pattern"), "pattern")
            if len(pattern) > 300:
                raise ValueError("pattern too long")
            try:
                re.compile(pattern, re.I)
            except re.error as exc:
                raise ValueError(f"invalid selector pattern: {case_id}") from exc


def _validate_snapshot(snapshot: dict) -> dict[str, dict]:
    if not isinstance(snapshot, dict) or not _HEX64.fullmatch(
        str(snapshot.get("archive_manifest_sha256", ""))
    ):
        raise ValueError("snapshot archive identity missing")
    if not isinstance(snapshot.get("sources"), list) or not isinstance(snapshot.get("chunks"), list):
        raise ValueError("snapshot sources/chunks missing")
    sources = {}
    for row in snapshot["sources"]:
        if not isinstance(row, dict):
            raise ValueError("source must be an object")
        path = _require_string(row.get("path"), "source path")
        if path in sources:
            raise ValueError(f"duplicate source: {path}")
        if not _HEX64.fullmatch(str(row.get("sha256", ""))):
            raise ValueError("source hash invalid")
        sources[path] = row
    for chunk in snapshot["chunks"]:
        if not isinstance(chunk, dict):
            raise ValueError("chunk must be an object")
        path = chunk.get("source_path")
        if path not in sources or chunk.get("source_sha256") != sources[path]["sha256"]:
            raise ValueError("chunk source hash missing or stale")
        if not isinstance(chunk.get("locator"), dict) or not isinstance(chunk.get("text"), str):
            raise ValueError("chunk locator/text invalid")
        if not _HEX64.fullmatch(str(chunk.get("text_sha256", ""))):
            raise ValueError("chunk text hash invalid")
    return sources


def _marking(chunks: list[dict]) -> str:
    first = [row for row in chunks if row["locator"] == {"kind": "page", "page": 1}
             or row["locator"].get("kind") == "lines" and row["locator"].get("start") == 1]
    labels = set()
    for chunk in first:
        for pattern in _CLASSIFICATION:
            match = pattern.search(chunk["text"][:4000])
            if match:
                label = match.group(1).casefold()
                labels.add("public" if label in {"public", "外部公开"} else "confidential")
    if len(labels) > 1:
        return "mixed"
    return next(iter(labels), "unknown")


def _excerpt(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - 100)
    end = min(len(text), match.end() + 100)
    excerpt = " ".join(text[start:end].split())
    excerpt = _URL.sub("[URL redacted]", excerpt)
    return excerpt[:260]


def build_review_packet(snapshot: dict, recipe: dict) -> dict:
    """Find review candidates. No output field represents factual or access approval."""
    _validate_recipe(recipe)
    sources = _validate_snapshot(snapshot)
    by_path: dict[str, list[dict]] = {path: [] for path in sources}
    for chunk in snapshot["chunks"]:
        by_path[chunk["source_path"]].append(chunk)
    cases = []
    mapped = set()
    for case in recipe["cases"]:
        hits = []
        missing = []
        for index, selector in enumerate(case["selectors"]):
            pattern = re.compile(selector["pattern"], re.I)
            match_on = selector.get("match_on", "text")
            found = 0
            for path in sorted(sources):
                if not fnmatchcase(path, selector["source_glob"]):
                    continue
                if match_on == "asset_path":
                    source = sources[path]
                    if source.get("kind") != "asset" or not pattern.search(path.rsplit("/", 1)[-1]):
                        continue
                    size = source.get("size")
                    if type(size) is not int or size < 0:
                        raise ValueError("asset size missing or invalid")
                    found += 1
                    mapped.add(path)
                    hits.append({
                        "selector_index": index,
                        "source_ref": {"path": path, "sha256": source["sha256"],
                                       "locator": {"kind": "file"}},
                        "evidence_basis": "asset_metadata_only", "size": size,
                    })
                    continue
                for chunk in by_path[path]:
                    match = pattern.search(chunk["text"])
                    if not match:
                        continue
                    found += 1
                    mapped.add(path)
                    hits.append({
                        "selector_index": index,
                        "source_ref": {"path": path, "sha256": sources[path]["sha256"],
                                       "locator": chunk["locator"]},
                        "text_sha256": chunk["text_sha256"],
                        "excerpt": _excerpt(chunk["text"], match),
                    })
            if not found:
                missing.append(index)
        cases.append({
            "id": case["id"], "group": case["group"], "question": case["question"],
            "owner": case["owner"], "case_recipe_sha256": _digest(case),
            "status": "pending", "evidence": hits,
            "missing_selectors": missing,
        })
    source_markings = {path: {"claimed_classification": _marking(by_path[path]),
                              "authorization": "pending_owner_review"}
                       for path in sorted(sources)}
    access_review = {path: {"view_roles": {role: "pending" for role in _ROLES},
                            "forward_roles": {role: "pending" for role in _ROLES}}
                     for path in sorted(sources)}
    return {
        "format_version": 1, "status": "candidate_review_only",
        "archive_manifest_sha256": snapshot["archive_manifest_sha256"],
        "extractor_version": snapshot.get("extractor_version"),
        "recipe_sha256": _digest(recipe), "recipe_version": recipe["version"],
        "source_inventory": {path: sources[path]["sha256"] for path in sorted(sources)},
        "source_markings": source_markings, "access_review": access_review,
        "cases": cases, "unmapped_sources": sorted(set(sources) - mapped),
    }


def compare_review_packets(previous: dict, current: dict) -> dict:
    """Flag source and review-item changes without inheriting prior decisions."""
    before = previous["source_inventory"]
    after = current["source_inventory"]
    source_change = {
        "added": sorted(after.keys() - before.keys()),
        "changed": sorted(path for path in before.keys() & after.keys()
                          if before[path] != after[path]),
        "removed": sorted(before.keys() - after.keys()),
    }
    old_cases = {case["id"]: case for case in previous["cases"]}
    new_cases = {case["id"]: case for case in current["cases"]}
    case_change = {
        "added": sorted(new_cases.keys() - old_cases.keys()),
        "changed": sorted(case_id for case_id in old_cases.keys() & new_cases.keys()
                          if old_cases[case_id] != new_cases[case_id]),
        "removed": sorted(old_cases.keys() - new_cases.keys()),
    }
    current_unmapped = set(current.get("unmapped_sources", []))
    previous_unmapped = set(previous.get("unmapped_sources", []))
    unmapped_changes = (set(source_change["added"] + source_change["changed"])
                        & current_unmapped)
    unmapped_changes |= set(source_change["removed"]) & previous_unmapped
    return {
        "sources": source_change, "cases": case_change,
        "recipe_changed": previous["recipe_sha256"] != current["recipe_sha256"],
        "unmapped_source_changes": sorted(unmapped_changes),
        "missing_selectors": {case["id"]: case["missing_selectors"]
                              for case in current["cases"] if case["missing_selectors"]},
    }
