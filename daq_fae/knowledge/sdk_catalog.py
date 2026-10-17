"""Source-bound, Dev-only SDK usage facts; never certifies device compatibility."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tarfile
import unicodedata
import zipfile

from .section_search import terms
from .release_notes import ReleaseNoteCatalog
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
    "android_java_20260929": {
        "glob": "SDK/SDK&OrbbecViewer_v2.9.0_20260929/Android/OrbbecSDK-Android-Wrapper-beta2.2.5-202609292240.zip",
        "version": "beta2.2.5", "build": "20260929", "platform": "Android", "format": "zip",
    },
    "ble_windows_20260929": {
        "glob": "SDK/SDK&OrbbecViewer_v2.9.0_20260929/windows/EgoLowBle-windows-x64-1.1.8.zip",
        "version": "1.1.8", "build": "20260929", "platform": "Windows x64", "format": "zip",
    },
    "ble_linux_20260929": {
        "glob": "SDK/SDK&OrbbecViewer_v2.9.0_20260929/linux/EgoLowBle-linux-x64-1.1.8.zip",
        "version": "1.1.8", "build": "20260929", "platform": "Linux x86_64", "format": "zip",
    },
    "ios_framework_20260929": {
        "glob": "SDK/SDK&OrbbecViewer_v2.9.0_20260929/ios/OrbbecSDK_v2.9.0_202609292146_7947205ab_iOS_OS64.tar.gz",
        "version": "2.9.0", "build": "20260929", "platform": "iOS ARM64",
    },
}
DOC_TYPES = {"api", "example", "build", "install", "troubleshooting"}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_SOURCE_MEMBER_BYTES = 2_000_000


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity_text(value: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).casefold().replace('×', 'x'))


def sdk_usage_descriptor_matches(value: str, row: dict) -> bool:
    """Match an SDK selector only against the reviewed record's own labels."""
    labels = [str(item) for key in ("usage_tasks", "topics", "symbols")
              for item in row.get(key, []) if isinstance(item, str)]
    if row.get("sdk_layer") == "ios_framework":
        ios_aliases = {"embed_framework": "integrate_framework",
                       "simulator_artifacts": "simulator_scope"}
        value = ios_aliases.get(value, value)

    def normalized(text: str) -> str:
        return re.sub(r"\bsd[_\s-]?card(?![a-z])", "sdcard", text.casefold())

    if _identity_text(normalized(value)) in {
            _identity_text(normalized(label)) for label in labels}:
        return True
    aliases = {
        "initialization": "init", "initialize": "init", "initialise": "init",
        "initializing": "init", "startup": "start", "starting": "start",
        "stopping": "stop", "configuration": "config", "configure": "config",
        "compilation": "build", "compile": "build",
        "linking": "link", "libraries": "library", "dlls": "dll",
        "placement": "deploy", "placing": "deploy", "integration": "deploy",
    }
    ignored = {"and", "the", "of", "order", "sequence", "how", "to", "example", "sample"}

    def tokens(text: str) -> set[str]:
        return {aliases.get(token, token) for token in re.findall(r"[a-z0-9]+", normalized(text))
                if token not in ignored}

    wanted = tokens(value)
    known = tokens(" ".join(labels))
    overlap = wanted & known
    return len(wanted) >= 2 and len(overlap) >= 2 and len(overlap) * 5 >= len(wanted) * 3


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


def _source_member(bundle: tarfile.TarFile | zipfile.ZipFile, suffix: str) -> tuple[str, bytes]:
    if isinstance(bundle, zipfile.ZipFile):
        matches = [member for member in bundle.infolist() if not member.is_dir()
                   and member.filename.endswith(suffix)]
    else:
        matches = [member for member in bundle.getmembers() if member.isfile()
                   and member.name.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError("SDK source member missing or ambiguous: " + suffix)
    member = matches[0]
    name = member.filename if isinstance(bundle, zipfile.ZipFile) else member.name
    normalized = name.replace("\\", "/")
    if (normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized)
            or ".." in Path(normalized).parts or "\x00" in normalized):
        raise ValueError("SDK source member has unsafe path")
    member_size = member.file_size if isinstance(bundle, zipfile.ZipFile) else member.size
    if member_size > _MAX_SOURCE_MEMBER_BYTES:
        raise ValueError("SDK source member too large")
    if isinstance(bundle, zipfile.ZipFile):
        return name, bundle.read(member)
    stream = bundle.extractfile(member)
    if stream is None:
        raise ValueError("SDK source member cannot be read")
    return name, stream.read()


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
        opener = zipfile.ZipFile if PACKAGES[package_id].get("format") == "zip" else tarfile.open
        with opener(path) as bundle:
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
                last_verified = item.get("last_verified", "2026-10-15")
                if not isinstance(last_verified, str) or not re.fullmatch(
                        r"20\d{2}-\d{2}-\d{2}", last_verified):
                    raise ValueError("SDK last_verified invalid")
                record.update(version=PACKAGES[package_id]["version"],
                              build=PACKAGES[package_id]["build"],
                              platform=PACKAGES[package_id]["platform"],
                              confidence="code_verified" if record["doc_type"] == "example"
                              else "doc_verified", last_verified=last_verified,
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
    def __init__(self, records: list[dict], manifest: dict,
                 release_notes: ReleaseNoteCatalog | None = None):
        self.records = tuple(records)
        self.manifest = manifest
        self.release_notes = release_notes

    def grounds_selector(self, question: str, key: str, value: object,
                         role: str = "internal_fae") -> bool:
        """Use one reviewed record's descriptor and user-quoted intent phrase."""
        if role != "internal_fae" or key not in {"feature", "task"} or not isinstance(question, str):
            return False
        values = [value] if isinstance(value, str) else value
        if (not isinstance(values, list) or not values or
                any(not isinstance(item, str) or not item.strip() for item in values)):
            return False
        asked = _identity_text(question)
        ios_phrases = {
            "embed_framework": r"(?:嵌入.{0,16}framework|embed.{0,16}framework)",
            "simulator_artifacts": r"simulator.{0,12}(?:产物|包|支持|artifact)",
        }
        if len(values) == 1 and values[0] in ios_phrases and re.search(
                ios_phrases[values[0]], question, re.I):
            return any(row.get("sdk_layer") == "ios_framework"
                       and sdk_usage_descriptor_matches(values[0], row)
                       for row in self.search(question, role))
        if (len(values) == 1 and values[0] == 'inspect_package'
                and re.search(r'包|package', question, re.I)
                and re.search(r'demo|示例', question, re.I)
                and re.search(r'构建|预编译|build|prebuilt', question, re.I)):
            return any(row.get('sdk_layer') == 'ble_cpp'
                       and 'linux' in str(row.get('platform', '')).casefold()
                       and sdk_usage_descriptor_matches(values[0], row)
                       for row in self.search(question, role))
        if (len(values) == 1 and values[0] == 'multi_device_sync_mode_check_and_set'
                and re.search(r'主从|master.slave', question, re.I)
                and re.search(r'同步|sync', question, re.I)
                and re.search(r'检查|设置|check|set', question, re.I)):
            return any(row.get('sdk_layer') == 'host_cpp'
                       and sdk_usage_descriptor_matches(values[0], row)
                       for row in self.search(question, role))
        if (len(values) == 1 and values[0] == 'library_linking_and_dll_placement'
                and re.search(r'链接|\blink\b', question, re.I)
                and re.search(r'DLL', question, re.I)):
            return any(row.get('sdk_layer') == 'ble_cpp'
                       and 'windows' in str(row.get('platform', '')).casefold()
                       and sdk_usage_descriptor_matches(values[0], row)
                       for row in self.search(question, role))
        intent_phrases = {
            'link_library_and_place_dll': (r'链接|\blink\b', r'\bDLL\b'),
            'link_libraries_and_deploy_dlls': (r'链接|\blink\b', r'\bDLL\b'),
            'link_library_and_deploy_dll': (r'链接|\blink\b', r'\bDLL\b'),
            'dll_integration': (r'\bDLL\b', r'集成|integrat'),
            'build_and_deploy': (r'编译|\bbuild\b', r'部署|deploy'),
            'compile_and_deploy': (r'编译|\bbuild\b', r'部署|deploy'),
        }
        if (key == 'task' and len(values) == 1 and values[0] in intent_phrases
                and all(re.search(pattern, question, re.I)
                        for pattern in intent_phrases[values[0]])):
            return any(sdk_usage_descriptor_matches(values[0], row)
                       for row in self.search(question, role))
        if key == 'task' and all(isinstance(item, str) for item in values):
            for row in self.search(question, role):
                if (all(sdk_usage_descriptor_matches(item, row) for item in values)
                        and any(_identity_text(symbol) in asked
                                for symbol in row.get('symbols', [])
                                if isinstance(symbol, str) and len(symbol) >= 6)):
                    return True
        return any(
            role in row.get("view_roles", [])
            and all(sdk_usage_descriptor_matches(item, row) for item in values)
            and any(_identity_text(pattern) in asked
                    for pattern in row.get("question_patterns", [])
                    if isinstance(pattern, str) and pattern.strip())
            for row in self.records
        )

    @classmethod
    def load(cls, directory: Path, archive: Path) -> "SdkCatalog":
        records, expected = build_catalog(directory / "curated.json", archive)
        stored_records = (directory / "SDK_Knowledge.jsonl").read_bytes()
        stored_manifest = json.loads((directory / "SDK_Knowledge_manifest.json").read_text())
        if stored_records != _serialized(records) or stored_manifest != expected:
            raise ValueError("SDK catalog differs from source checked build")
        release_notes = (ReleaseNoteCatalog.load(directory, archive)
                         if (directory / "release_notes_curated.json").exists() else None)
        return cls(records, expected, release_notes)

    def search(self, query: str, role: str) -> list[dict]:
        if role != "internal_fae" or not isinstance(query, str) or len(query) > 2000:
            return []
        normalized = query.casefold()
        if "egoviewer" in normalized and "orbbecsdk" not in normalized:
            return []
        compact_dates = re.sub(r"(?<=\d)[-/](?=\d)", "", normalized)
        asked_builds = set(re.findall(r"(?<!\d)20\d{6}(?!\d)", compact_dates))
        firmware_118 = bool(re.search(
            r"(?:固件|firmware)(?:版本|version)?[\s:：是为=]*1\.1\.8", normalized))
        if (asked_builds and not asked_builds <= {
                row["build"] for row in self.records}) or re.search(
                    r"(?<!\d)2\.9\.(?!0\b)\d+(?!\d)", normalized) or (
                        "1.1.8" in normalized and not firmware_118
                        and "egolowble" not in normalized):
            return []
        layers = set()
        if "egolowble" in normalized:
            layers.add("ble_cpp")
        else:
            if "android" in normalized or "java" in normalized:
                layers.add("android_java")
            if "ios" in normalized or "iphone" in normalized or "swift" in normalized:
                layers.add("ios_framework")
            if "libobkit" in normalized or "设备端" in query or "rk3576" in normalized:
                layers.add("device_c")
            if ("主机侧" in query or "host" in normalized or "c++" in normalized
                    or "cpp" in normalized):
                layers.add("host_cpp")
            if "orbbecsdk" in normalized and not layers:
                layers.add("host_cpp")
        selected = [row for row in self.records if row["sdk_layer"] in layers] if layers else self.records
        if asked_builds:
            selected = [row for row in selected if row["build"] in asked_builds]
        query_terms = terms(query)
        ranked = []
        for row in selected:
            fields = [(8, row["title"]), (6, " ".join(row["question_patterns"])),
                      (5, " ".join(row["symbols"] + row["topics"])),
                      (10, " ".join(str(ref.get("member", "")).replace("\\", "/").rsplit("/", 1)[-1]
                                      for ref in row.get("source_refs", [])
                                      if re.fullmatch(r"[0-9a-f]{64}", str(ref.get("member_sha256", ""))))),
                      (1, row["answer"])]
            score = sum(weight * sum(len(term) for term in query_terms & terms(value))
                        for weight, value in fields)
            if score:
                ranked.append((score, row))
        ranked.sort(key=lambda item: (-item[0], item[1]["kb_id"]))
        if not ranked:
            return []
        if len(layers) > 1:
            best_by_layer = {}
            for _, row in ranked:
                best_by_layer.setdefault(row["sdk_layer"], row)
            chosen = list(best_by_layer.values())
            chosen_ids = {row["kb_id"] for row in chosen}
            chosen.extend(row for _, row in ranked if row["kb_id"] not in chosen_ids)
            return chosen[:max(3, len(best_by_layer))]
        floor = ranked[0][0] * 0.65
        return [row for score, row in ranked if score >= floor][:3]
