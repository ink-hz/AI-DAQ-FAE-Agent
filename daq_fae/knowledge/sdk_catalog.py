"""Source-bound, Dev-only SDK usage facts; never certifies device compatibility."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tarfile

from .section_search import terms
from scripts.archive_candidate_snapshot import verify_archive


PACKAGES = {
    "host_cpp_20260921": {
        "glob": "SDK/SDK&OrbbecViewer_v2.9.0_20260921/linux/OrbbecSDK*_linux_x86_64.tar.gz",
        "version": "2.9.0", "build": "20260921", "platform": "Linux x86_64",
    },
    "host_cpp_20260923": {
        "glob": "SDK/SDK&OrbbecViewer_v2.9.0_20260923/linux/OrbbecSDK*_linux_x86_64.tar.gz",
        "version": "2.9.0", "build": "20260923", "platform": "Linux x86_64",
    },
    "host_cpp_20260929": {
        "glob": "SDK/SDK&OrbbecViewer_v2.9.0_20260929/linux/OrbbecSDK*_linux_x86_64.tar.gz",
        "version": "2.9.0", "build": "20260929", "platform": "Linux x86_64",
    },
    "device_c_20260909": {
        "glob": "SDK/设备端SDK-四目EGO使用/四目设备SDK/libobkit.20260909_*.tar.gz",
        "version": "20260909", "build": "20260909", "platform": "RK3576 AArch64 Linux",
    },
}
DOC_TYPES = {"api", "example", "build", "install", "troubleshooting"}
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _serialized(records: list[dict]) -> bytes:
    return b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")) + "\n").encode()
                    for row in records)


def _package_paths(archive: Path) -> dict[str, Path]:
    files = archive / "files"
    paths = {}
    for package_id, spec in PACKAGES.items():
        found = list(files.glob(spec["glob"]))
        if len(found) != 1 or found[0].is_symlink() or not found[0].is_file():
            raise ValueError("SDK package missing or ambiguous: " + package_id)
        paths[package_id] = found[0]
    return paths


def _source_member(bundle: tarfile.TarFile, suffix: str) -> tuple[str, bytes]:
    matches = [member for member in bundle.getmembers() if member.isfile()
               and member.name.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError("SDK source member missing or ambiguous: " + suffix)
    member = matches[0]
    if member.name.startswith("/") or ".." in Path(member.name).parts:
        raise ValueError("SDK source member has unsafe path")
    stream = bundle.extractfile(member)
    if stream is None:
        raise ValueError("SDK source member cannot be read")
    return member.name, stream.read()


def build_catalog(curated: Path, archive: Path) -> tuple[list[dict], dict]:
    verified = verify_archive(archive)
    archive_manifest = json.loads((archive / "manifest.json").read_text())
    source_hashes = {row["path"]: row["sha256"] for row in archive_manifest["files"]}
    paths = _package_paths(archive)
    specifications = json.loads(curated.read_text())
    if not isinstance(specifications, list) or not specifications:
        raise ValueError("SDK curated inventory missing")
    ids = set()
    records = []
    for package_id, path in paths.items():
        relative = path.relative_to(archive / "files").as_posix()
        with tarfile.open(path) as bundle:
            for item in specifications:
                if item.get("package") != package_id:
                    continue
                record = {key: value for key, value in item.items() if key != "source_refs"}
                if (not isinstance(record.get("kb_id"), str) or record["kb_id"] in ids
                        or record.get("doc_type") not in DOC_TYPES
                        or not isinstance(record.get("answer"), str)
                        or not record["answer"].strip()
                        or not isinstance(record.get("question_patterns"), list)
                        or not record["question_patterns"]):
                    raise ValueError("SDK curated record invalid")
                ids.add(record["kb_id"])
                record.update(version=PACKAGES[package_id]["version"],
                              build=PACKAGES[package_id]["build"],
                              platform=PACKAGES[package_id]["platform"],
                              confidence="code_verified" if record["doc_type"] == "example"
                              else "doc_verified", last_verified="2026-10-15",
                              view_roles=["internal_fae"])
                refs = []
                source_spans = []
                for ref in item["source_refs"]:
                    member, content = _source_member(bundle, ref["member_suffix"])
                    start, end = ref["start_line"], ref["end_line"]
                    lines = content.decode("utf-8").splitlines()
                    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                        raise ValueError("SDK source line span invalid")
                    source_spans.append("\n".join(lines[start - 1:end]))
                    refs.append({"archive_path": relative,
                                 "package_sha256": source_hashes[relative],
                                 "member": member, "member_sha256": _sha(content),
                                 "start_line": start, "end_line": end})
                for field in ("symbols", "outputs"):
                    values = record.get(field, [])
                    if (not isinstance(values, list) or any(
                            not isinstance(value, str) or not value.strip()
                            for value in values)):
                        raise ValueError("SDK " + field + " invalid")
                    for value in values:
                        if not any(value in span for span in source_spans) and not any(
                                value in ref["member"] for ref in refs):
                            raise ValueError("SDK " + (
                                "symbol" if field == "symbols" else "output") +
                                " lacks source span: " + value)
                record["code_tokens"] = sorted(set(re.findall(
                    r"\b[A-Za-z][A-Za-z0-9_]*_[A-Za-z0-9_]+\b",
                    "\n".join(source_spans))))
                record["source_refs"] = refs
                records.append(record)
    if len(records) != len(specifications):
        raise ValueError("SDK curated package unknown")
    records.sort(key=lambda row: row["kb_id"])
    payload = _serialized(records)
    manifest = {"format_version": "daq-sdk-source-checked/v1",
                "archive_manifest_sha256": verified["manifest_sha256"],
                "records_sha256": _sha(payload), "record_count": len(records),
                "source_ref_count": sum(len(row["source_refs"]) for row in records),
                "packages": {key: {"archive_path": paths[key].relative_to(archive / "files").as_posix(),
                                   "sha256": source_hashes[paths[key].relative_to(archive / "files").as_posix()],
                                   "version": spec["version"], "build": spec["build"]}
                             for key, spec in PACKAGES.items()}}
    return records, manifest


class SdkCatalog:
    def __init__(self, records: list[dict], manifest: dict):
        self.records = tuple(records)
        self.manifest = manifest

    @classmethod
    def load(cls, directory: Path, archive: Path) -> "SdkCatalog":
        records, expected = build_catalog(directory / "curated.json", archive)
        stored_records = (directory / "SDK_Knowledge.jsonl").read_bytes()
        stored_manifest = json.loads((directory / "SDK_Knowledge_manifest.json").read_text())
        if stored_records != _serialized(records) or stored_manifest != expected:
            raise ValueError("SDK catalog differs from source checked build")
        return cls(records, expected)

    def search(self, query: str, role: str) -> list[dict]:
        if role != "internal_fae" or not isinstance(query, str) or len(query) > 2000:
            return []
        normalized = query.casefold()
        compact_dates = re.sub(r"(?<=\d)[-/](?=\d)", "", normalized)
        asked_builds = set(re.findall(r"(?<!\d)20\d{6}(?!\d)", compact_dates))
        firmware_118 = bool(re.search(
            r"(?:固件|firmware)(?:版本|version)?[\s:：是为=]*1\.1\.8", normalized))
        if (asked_builds and not asked_builds <= {
                row["build"] for row in self.records}) or re.search(
                    r"(?<!\d)2\.9\.(?!0\b)\d+(?!\d)", normalized) or (
                        "1.1.8" in normalized and not firmware_118):
            return []
        selected = self.records
        if "libobkit" in normalized or "设备端" in query or "rk3576" in normalized:
            selected = [row for row in selected if row["sdk_layer"] == "device_c"]
        elif "orbbecsdk" in normalized or "主机侧" in query or "c++" in normalized:
            selected = [row for row in selected if row["sdk_layer"] == "host_cpp"]
        if asked_builds:
            selected = [row for row in selected if row["build"] in asked_builds]
        query_terms = terms(query)
        ranked = []
        for row in selected:
            fields = [(8, row["title"]), (6, " ".join(row["question_patterns"])),
                      (5, " ".join(row["symbols"] + row["topics"])),
                      (1, row["answer"])]
            score = sum(weight * sum(len(term) for term in query_terms & terms(value))
                        for weight, value in fields)
            if score:
                ranked.append((score, row))
        ranked.sort(key=lambda item: (-item[0], item[1]["kb_id"]))
        if not ranked:
            return []
        floor = ranked[0][0] * 0.65
        return [row for score, row in ranked if score >= floor][:3]
