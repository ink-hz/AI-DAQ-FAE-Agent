"""Source-bound SDK, Viewer and firmware release-note statements.

These records say what a particular log states. They never certify runtime
behavior, device support, a firmware pairing or a download authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scripts.archive_candidate_snapshot import verify_archive

from .section_search import terms

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"(?<![a-z0-9.])v?(\d+(?:\.\d+){2,3})(?![a-z0-9.])",
                      re.IGNORECASE)
_MAX_LOG_BYTES = 2_000_000


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _serialized(records: list[dict]) -> bytes:
    return b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")) + "\n").encode()
                    for row in records)


def _is_log_path(path: str) -> bool:
    return path.casefold().endswith(("changelog.txt", "changelist.txt"))


def _versions(text: str) -> set[str]:
    return set(_VERSION.findall(text))


def _mentioned_families(text: str) -> list[str]:
    lowered = text.casefold()
    families = []
    if "viewer" in lowered:
        families.append("egoviewer")
    if "orbbecsdk" in lowered or re.search(r"\bsdk\b", lowered):
        families.append("orbbecsdk")
    if "固件" in text or "firmware" in lowered:
        families.append("firmware")
    return families


def _build_tokens(text: str) -> set[str]:
    normalized = re.sub(
        r"(?<!\d)(20\d{2})[-/](\d{2})[-/](\d{2})(?!\d)",
        lambda match: "".join(match.groups()), text.casefold())
    return set(re.findall(r"(?<!\d)20\d{6,}(?!\d)", normalized))


def _selector_scopes(query: str) -> dict[str, dict[str, set[str]]]:
    """Bind versions, builds and platforms to their named query family."""
    clauses = re.split(r"(?:与|和|以及|及|、|[;,；，]|\band\b)", query,
                       flags=re.IGNORECASE)
    mentioned = [_mentioned_families(clause) for clause in clauses]
    scopes: dict[str, dict[str, set[str]]] = {}
    last_families: list[str] = []
    for index, clause in enumerate(clauses):
        families = mentioned[index]
        if families:
            last_families = families
        targets = families or last_families or next(
            (item for item in mentioned[index + 1:] if item), [])
        lowered = clause.casefold()
        platforms = set()
        if "android" in lowered:
            platforms.add("android")
        if ("desktop" in lowered or "桌面" in clause or
                "linux" in lowered or "windows" in lowered):
            platforms.add("desktop")
        if "ios" in lowered or "iphoneos" in lowered:
            platforms.add("ios")
        for family in targets:
            scope = scopes.setdefault(family, {"platforms": set(),
                                               "versions": set(), "builds": set()})
            scope["platforms"].update(platforms)
            scope["versions"].update(_versions(clause))
            scope["builds"].update(_build_tokens(clause))
    return scopes


def _platform_matches(row: dict, platform: str) -> bool:
    if platform == "desktop":
        return row["family"] == "EgoViewer desktop"
    return row["platform"].casefold() == platform


def build_release_notes(curated: Path, archive: Path) -> tuple[list[dict], dict]:
    """Verify one curated statement per unique log hash in the 1015 archive."""
    verified = verify_archive(archive)
    original = json.loads((archive / "manifest.json").read_text())
    log_files = [item for item in original["files"] if _is_log_path(item["path"])]
    manifest_by_path = {item["path"]: item for item in log_files}
    paths_by_hash: dict[str, list[str]] = {}
    for item in log_files:
        paths_by_hash.setdefault(item["sha256"], []).append(item["path"])
    specifications = json.loads(curated.read_text())
    if not isinstance(specifications, list) or not specifications:
        raise ValueError("SDK release-note curated inventory missing")
    records = []
    ids: set[str] = set()
    used_hashes: set[str] = set()
    for item in specifications:
        if not isinstance(item, dict):
            raise ValueError("SDK release-note row invalid")  # noqa: TRY004 - invalid catalog
        path, digest, kb_id = item.get("archive_path"), item.get("file_sha256"), item.get("kb_id")
        if (not isinstance(path, str) or path not in manifest_by_path
                or not isinstance(digest, str) or not _SHA.fullmatch(digest)
                or manifest_by_path[path]["sha256"] != digest):
            raise ValueError("SDK release-note path or hash mismatch")
        if (not isinstance(kb_id, str) or not kb_id.startswith("daq-note-")
                or kb_id in ids or digest in used_hashes):
            raise ValueError("SDK release-note duplicate or invalid ID/hash")
        ids.add(kb_id)
        used_hashes.add(digest)
        if (item.get("doc_type") != "release_note"
                or item.get("evidence_scope") != "release_note_statement_only"
                or item.get("device_support") != "unknown"
                or item.get("view_roles") != ["internal_fae"]
                or not isinstance(item.get("answer"), str)
                or "变更日志" not in item["answer"]
                or not isinstance(item.get("question_patterns"), list)
                or not item["question_patterns"]
                or any(not isinstance(value, str) or not value.strip()
                       for value in item["question_patterns"])
                or any(not isinstance(item.get(key), str) or not item[key].strip()
                       for key in ("family", "version", "build", "platform", "title"))):
            raise ValueError("SDK release-note scope invalid")
        source = archive / "files" / path
        if source.is_symlink() or not source.is_file() or source.stat().st_size > _MAX_LOG_BYTES:
            raise ValueError("SDK release-note source unsafe or too large")
        content = source.read_bytes()
        if _digest(content) != digest:
            raise ValueError("SDK release-note source hash mismatch")
        lines = content.decode("utf-8").splitlines()
        raw_refs = item.get("source_refs")
        if not isinstance(raw_refs, list) or not raw_refs:
            raise ValueError("SDK release-note line references missing")
        refs = []
        for raw_ref in raw_refs:
            if not isinstance(raw_ref, dict):
                raise ValueError("SDK release-note line reference invalid")  # noqa: TRY004 - invalid catalog
            start, end = raw_ref.get("start_line"), raw_ref.get("end_line")
            if (type(start) is not int or type(end) is not int
                    or not 1 <= start <= end <= len(lines)):
                raise ValueError("SDK release-note line span invalid")
            span = "\n".join(lines[start - 1:end]).encode()
            refs.append({"archive_path": path, "package_sha256": digest,
                         "member": Path(path).name, "member_sha256": digest,
                         "span_sha256": _digest(span),
                         "start_line": start, "end_line": end})
        record = {key: value for key, value in item.items()
                  if key not in {"source_refs", "archive_path"}}
        record["source_refs"] = refs
        record["duplicate_archive_paths"] = sorted(
            other for other in paths_by_hash[digest] if other != path)
        records.append(record)
    if used_hashes != set(paths_by_hash):
        raise ValueError("SDK release-note unique log hash coverage incomplete")
    records.sort(key=lambda row: row["kb_id"])
    payload = _serialized(records)
    manifest = {"format_version": "daq-release-notes-source-checked/v1",
                "archive_manifest_sha256": verified["manifest_sha256"],
                "records_sha256": _digest(payload), "note_count": len(records),
                "original_log_count": len(log_files),
                "duplicate_log_copies": len(log_files) - len(paths_by_hash)}
    return records, manifest


class ReleaseNoteCatalog:
    def __init__(self, records: list[dict], manifest: dict):
        self.records = tuple(records)
        self.manifest = manifest

    @classmethod
    def load(cls, directory: Path, archive: Path) -> ReleaseNoteCatalog:
        records, expected = build_release_notes(directory / "release_notes_curated.json", archive)
        if ((directory / "Release_Notes.jsonl").read_bytes() != _serialized(records)
                or json.loads((directory / "Release_Notes_manifest.json").read_text()) != expected):
            raise ValueError("SDK release-note catalog differs from source checked build")
        return cls(records, expected)

    def search(self, query: str, role: str) -> list[dict]:
        if role != "internal_fae" or not isinstance(query, str) or not query.strip() or len(query) > 2000:
            return []
        families = _mentioned_families(query)
        if not families:
            return []
        scopes = _selector_scopes(query)
        candidates = []
        for row in self.records:
            family = next((item for item in families
                           if item in row["family"].casefold()), None)
            if family is None:
                continue
            scope = scopes[family]
            if (scope["platforms"] and not any(
                    _platform_matches(row, platform)
                    for platform in scope["platforms"])):
                continue
            if (scope["versions"] and not scope["versions"] & _versions(
                    row["answer"] if family == "firmware" else row["version"])):
                continue
            if (scope["builds"] and not any(row["build"].startswith(build)
                                            for build in scope["builds"])):
                continue
            candidates.append(row)
        query_terms = terms(query)
        ranked = []
        for row in candidates:
            fields = [(6, row["title"]), (5, " ".join(row["question_patterns"])),
                      (3, " ".join(row["topics"])), (1, row["answer"])]
            score = sum(weight * sum(len(term) for term in query_terms & terms(value))
                        for weight, value in fields)
            if any(scopes[family]["builds"] for family in families
                   if family in row["family"].casefold()):
                score += 100
            if score:
                ranked.append((score, row))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["kb_id"]))
        if len(families) == 1:
            return [row for _, row in ranked[:5]]
        best_by_family = {}
        for _, row in ranked:
            family = next((item for item in families
                           if item in row["family"].casefold()), None)
            if family is not None:
                best_by_family.setdefault(family, row)
        selected = list(best_by_family.values())
        selected_ids = {row["kb_id"] for row in selected}
        selected.extend(row for _, row in ranked if row["kb_id"] not in selected_ids)
        return selected[:5]
