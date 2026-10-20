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


def sdk_cpu_architectures(text: str) -> tuple[str, ...]:
    """Canonicalize explicit CPU names; preserve unfamiliar architectures."""
    tokens = re.findall(
        r'(?<![A-Za-z0-9_])(?:x86[_-]?64|x64|amd64|aarch64|arm64|'
        r'armv\d+(?:-a)?|riscv\d+(?:gc)?|'
        r'[A-Za-z][A-Za-z0-9_]*(?:32|64))(?![A-Za-z0-9_])',
        text, re.I)
    canonical = []
    for token in tokens:
        name = token.casefold()
        if name in {'x86_64', 'x86-64', 'x64', 'amd64'}:
            name = 'x86_64'
        elif name in {'arm64', 'aarch64'} or re.fullmatch(r'armv(?:8|9)(?:-a)?', name):
            name = 'arm64'
        canonical.append(name)
    return tuple(dict.fromkeys(canonical))


def audio_playback_subjects(question: str) -> tuple[str, ...]:
    """Bind an audio task to SDK/library names in the same user clause."""
    subjects = []
    previous_audio = False
    # Names must have SDK/library morphology or occur in the reviewed SDK
    # family registry. A generic CamelCase device label (for example
    # WristCam) is an applicability target, not another SDK task.
    library_name = re.compile(
        r'(?<![A-Za-z0-9_])(OrbbecSDK|EgoLowBle|lib[a-z][A-Za-z0-9]*|'
        r'[A-Za-z]+SDK|[A-Z]{2,}[A-Za-z]*\d+|'
        r'[A-Z][a-z]+(?:AL|Audio))(?![A-Za-z0-9_])')
    for clause in re.split(r'[?？。；;\n]+', question):
        audio = bool(re.search(r'音频|\baudio\b', clause, re.I)
                     and re.search(r'播放|\bplay(?:back)?\b', clause, re.I))
        elliptical = bool(previous_audio and re.search(r'呢|\b(?:what about|and)\b',
                                                    clause, re.I))
        if not (audio or elliptical):
            previous_audio = False
            continue
        matches = list(library_name.finditer(clause))
        for index, match in enumerate(matches):
            name = match.group(1)
            if name.casefold() in {'arm64', 'aarch64', 'amd64'}:
                continue
            preceding = (clause[matches[index - 1].end():match.start()]
                         if index else clause[:match.start()])
            if index and re.search(
                    r'适用于|适合|能否用于|(?<!是否)(?<!能否)支持|'
                    r'\b(?:applicable to|for)\b', preceding, re.I):
                continue
            following = clause[match.end():matches[index + 1].start()
                               if index + 1 < len(matches) else len(clause)]
            if index + 1 < len(matches) and re.search(
                    r'是否支持|能否支持|能否使用|能否用(?!于)|'
                    r'\b(?:support|supports|can use)\b', following, re.I):
                continue
            # The first library's package/build request does not inherit the
            # next library's audio task merely because both share a clause.
            if audio and index + 1 < len(matches) and re.search(
                    r'包内容|包里|包含|预编译|构建|\b(?:package|prebuilt|build)\b',
                    following, re.I) and not re.search(r'音频|\baudio\b', following, re.I):
                continue
            if elliptical and not re.search(
                    r'^\s*(?:v?\d+(?:\.\d+){0,2})?\s*(?:呢|\bwhat about\b)',
                                            following, re.I):
                continue
            subjects.append('OrbbecSDK' if name.casefold() == 'orbbecsdk' else name)
        previous_audio = audio or elliptical
    return tuple(dict.fromkeys(subjects))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity_text(value: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).casefold().replace('×', 'x'))


def calibration_export_row_matches_question(
        row: dict, question: str, *, known_product_names=()) -> bool:
    """Bind explicit output, example subject, OS and CPU to the source row."""
    if 'calibration_export' not in row.get('usage_tasks', []):
        return False
    if not (re.search(r'对齐|align', question, re.I)
            or re.search(r'\bIMU\b', question, re.I)):
        return False
    outputs = [str(value).casefold() for value in row.get('outputs', [])]
    named_files = {name.casefold() for name in re.findall(
        r'(?<![A-Za-z0-9_.-])(?:[A-Za-z][A-Za-z0-9_-]*\.)+'
        r'(?:yaml|yml|json|xml|csv|txt|bin|pdf|dat|ini|zip)'
        r'(?![A-Za-z0-9_.-])',
        question, re.I)}
    if not named_files <= set(outputs):
        return False
    requested_formats = {name.casefold() for name in re.findall(
        r'(?<![A-Za-z0-9])(?:yaml|yml|json|xml|csv|txt|bin|pdf|dat|ini|zip)'
        r'(?![A-Za-z0-9])', question, re.I)}
    requested_formats |= {name.casefold() for name in re.findall(
        r'\.([A-Za-z][A-Za-z0-9]{0,7})(?![A-Za-z0-9])', question, re.I)}
    requested_formats |= {match.group(1).casefold() for match in re.finditer(
        r'(?<![A-Za-z0-9])([A-Za-z0-9]{2,8})\s*'
        r'(?:文件|格式|file|format)(?![A-Za-z0-9])',
        question, re.I)}
    requested_formats.discard('c')
    requested_formats.discard('cpp')
    if any(not any(output.endswith('.' + name) for output in outputs)
           for name in requested_formats):
        return False
    platform = str(row.get('platform', '')).casefold()
    os_patterns = {
        'linux': r'(?<![A-Za-z0-9_])(?:linux|ubuntu)(?![A-Za-z0-9_])',
        'windows': r'(?<![A-Za-z0-9_])(?:windows|win\s*(?:10|11))'
                   r'(?![A-Za-z0-9_])',
        'macos': r'(?<![A-Za-z0-9_])(?:mac(?:\s*os)?|darwin)'
                 r'(?![A-Za-z0-9_])',
        'android': r'(?<![A-Za-z0-9_])android(?![A-Za-z0-9_])',
        'ios': r'(?<![A-Za-z0-9_])ios(?![A-Za-z0-9_])',
        'freebsd': r'(?<![A-Za-z0-9_])freebsd(?![A-Za-z0-9_])',
    }
    row_os = {name for name, pattern in os_patterns.items()
              if re.search(pattern, platform, re.I)}
    asked_os = {name for name, pattern in os_patterns.items()
                if re.search(pattern, question, re.I)}
    if asked_os and not asked_os <= row_os:
        return False
    row_cpu = set(sdk_cpu_architectures(platform))
    asked_cpu = set(sdk_cpu_architectures(question))
    if asked_cpu and not asked_cpu <= row_cpu:
        return False
    subjects = {_identity_text(subject) for subject in row.get(
        'example_subjects', []) if isinstance(subject, str)}
    if re.search(r'(?<![A-Za-z0-9])EGO(?![A-Za-z0-9])', question, re.I):
        if re.search(r'(?:(?<![A-Za-z0-9])EGO\s*[- ]?\s*Pro'
                     r'(?![A-Za-z0-9])|(?<![A-Za-z0-9])EGO\s*四目|'
                     r'四目\s*EGO(?![A-Za-z0-9]))',
                     question, re.I):
            asked_subject = 'egopro'
        elif re.search(r'(?<![A-Za-z0-9])EGO\s*[- ]?\s*Depth'
                       r'(?![A-Za-z0-9])', question, re.I):
            asked_subject = 'egodepth'
        else:
            asked_subject = 'ego'
        if asked_subject not in subjects:
            return False
    elif 'ego' in subjects:
        # The source is an EGO example. Without that user scope the model
        # cannot borrow it for another named device or a generic question.
        return False
    other_products = {
        _identity_text(name) for name in known_product_names
        if isinstance(name, str) and re.search(
            r'(?<![A-Za-z0-9])' + re.escape(name).replace(r'\ ', r'\s*')
            + r'(?![A-Za-z0-9])', question, re.I)
    } - subjects
    if other_products:
        return False
    return True


def calibration_export_known_scope(question: str) -> str:
    """Isolate the reviewed YAML ask before an added, unreviewed JSON ask."""
    match = re.search(r'(?:还(?:能|可以)|另外|并且|同时)\s*'
                      r'(?:导出|export)\s*(?:外参|extrinsic)\s*'
                      r'(?:标定|calibration)?\s*(?:JSON|\.json)', question, re.I)
    if not match:
        return question
    prefix, suffix = question[:match.start()], question[match.end():]
    # Only a terminal, standalone extra-output question may be split. Any
    # following device, platform or output qualifier must remain in scope.
    if not re.fullmatch(r'[\s吗呢啊吧？?。！!]*', suffix):
        return question
    if (not all(re.search(pattern, prefix, re.I) for pattern in (
            r'对齐|align', r'\bIMU\b', r'标定|calib', r'YAML', r'导出|export'))):
        return question
    return prefix


def device_sdk_scope_matches_question(question: str) -> bool:
    """Distinguish a firmware image/package-scope ask from camera imagery."""
    if re.search(r'图像|照片|画质|画面|清晰度|分辨率|像素|拍摄|'
                 r'成像|摄像头|镜头|影像|图片|视觉|分别|'
                 r'camera\s+image|image\s+quality|photo|picture|'
                 r'imaging|lens|visual|resolution|pixel', question, re.I):
        return False
    return bool(re.search(r'固件镜像|整机镜像|镜像|firmware\s+image|'
                          r'任意[^。？！?；;\n]{0,20}固件|'
                          r'固件[^。？！?；;\n]{0,20}上用|'
                          r'(?:固件|板子|板卡|设备端|RK3576|firmware|board)'
                          r'[^。？！?；;\n]{0,24}'
                          r'(?:兼容|支持|适用|通用|使用|运行|绑定)|'
                          r'(?:兼容|支持|适用|通用|使用|运行|绑定)'
                          r'[^。？！?；;\n]{0,24}'
                          r'(?:固件|板子|板卡|设备端|RK3576|firmware|board)',
                          question, re.I))


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
        "run": "runtime", "running": "runtime",
        "linking": "link", "libraries": "library", "dlls": "dll",
        "placement": "deploy", "placing": "deploy", "place": "deploy",
        "integration": "deploy", "removed": "removal", "status": "state",
    }
    ignored = {"and", "the", "of", "in", "for", "order", "sequence", "how", "to",
               "example", "sample", "semantics", "meaning", "check", "verify",
               "confirm", "vs"}

    def tokens(text: str) -> set[str]:
        return {aliases.get(token, token) for token in re.findall(r"[a-z0-9]+", normalized(text))
                if token not in ignored}

    wanted = tokens(value)
    known = tokens(" ".join(labels))
    # A partly overlapping description may add a new action (for example,
    # ``recording_state_delete``). Every substantive selector token must be
    # present in the reviewed row before it can certify an SDK goal.
    return len(wanted) >= 2 and wanted <= known


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
                subjects = record.get('example_subjects', [])
                if (not isinstance(subjects, list) or
                        any(not isinstance(subject, str) or not subject.strip()
                            for subject in subjects) or
                        (subjects and record['doc_type'] != 'example')):
                    raise ValueError('SDK example subjects invalid')
                source_names = [re.sub(r'[^a-z0-9]', '', ref['member'].casefold())
                                for ref in refs]
                if any(not any(re.sub(r'[^a-z0-9]', '', subject.casefold()) in name
                               for name in source_names)
                       for subject in subjects):
                    raise ValueError('SDK example subject lacks source member')
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
                callable_symbols = record.get('callable_symbols', [])
                if (not isinstance(callable_symbols, list)
                        or any(not isinstance(symbol, str)
                               for symbol in callable_symbols)
                        or len(callable_symbols) != len(set(callable_symbols))
                        or any(symbol not in record.get('symbols', [])
                               or not any(re.search(
                                   r'(?<![A-Za-z0-9_])' + re.escape(symbol)
                                   + r'\s*\(', span)
                                   for span in source_spans)
                               for symbol in callable_symbols)):
                    raise ValueError('SDK callable symbol lacks source call span')
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
                         role: str = "internal_fae", *,
                         scope: dict | None = None) -> bool:
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
                       for row in self.search(question, role, include_all=True))
        if (len(values) == 1 and values[0] == 'inspect_package'
                and re.search(r'包|package', question, re.I)
                and re.search(r'demo|示例', question, re.I)
                and re.search(r'构建|预编译|build|prebuilt', question, re.I)):
            return any(row.get('sdk_layer') == 'ble_cpp'
                       and 'linux' in str(row.get('platform', '')).casefold()
                       and sdk_usage_descriptor_matches(values[0], row)
                       for row in self.search(question, role, include_all=True))
        if (len(values) == 1 and values[0] in {'download', 'file_download'}
                and re.search(r'SD\s*卡|\bsd.?card\b', question, re.I)
                and re.search(r'下载|\bdownload\b', question, re.I)):
            aliases = {'linux_x86_64': 'Linux x86_64',
                       'linux_x64': 'Linux x86_64',
                       'windows_x64': 'Windows x64'}
            scoped = scope if isinstance(scope, dict) else {}
            platform = str(scoped.get('platform', ''))
            matching = [row for row in self.search(
                question, role, include_all=True)
                if 'sdcard_file_service' in row.get('usage_tasks', [])
                and values[0] in row.get('usage_tasks', [])
                and (not platform or _identity_text(aliases.get(
                    platform, platform)) in _identity_text(row.get('platform', '')))
                and (not scoped.get('sdk_release_date')
                     or re.sub(r'\D', '', str(scoped['sdk_release_date']))
                     == row.get('build'))
                and (not scoped.get('sdk_version')
                     or scoped['sdk_version'] == row.get('version'))]
            if len(matching) == 1:
                return True
        if (len(values) == 1 and values[0] == 'preset_profile'
                and re.search(r'预设|\bpreset\b', question, re.I)
                and re.search(r'\bStreamProfile\b|流\s*profile', question, re.I)):
            return any('preset_profile' in row.get('usage_tasks', [])
                       and 'StreamProfile' in str(row.get('answer', ''))
                       for row in self.search(question, role, include_all=True))
        if len(values) == 1 and values[0] == 'magnetometer':
            platform_aliases = {
                'linux_x64': 'Linux x86_64',
                'linux_x86_64': 'Linux x86_64',
                'windows_x64': 'Windows x64',
            }
            for clause in re.split(r'[?？。；;\n]+', question):
                if not (re.search(r'(?<![A-Za-z0-9_])OrbbecSDK'
                                  r'(?![A-Za-z0-9_])', clause, re.I)
                        and re.search(r'磁力计|magnetometer', clause, re.I)
                        and re.search(r'示例|\b(?:example|sample)\b', clause, re.I)):
                    continue
                matching = [row for row in self.search(
                    clause, role, include_all=True)
                    if row.get('sdk_layer') == 'host_cpp'
                    and row.get('doc_type') == 'example'
                    and 'magnetometer' in row.get('topics', [])
                    and (not isinstance(scope, dict)
                         or not scope.get('platform')
                         or _identity_text(platform_aliases.get(
                             str(scope['platform']), str(scope['platform']))) in
                         _identity_text(str(row.get('platform', ''))))
                    and (not isinstance(scope, dict)
                         or not scope.get('sdk_release_date')
                         or re.sub(r'\D', '', str(scope['sdk_release_date']))
                         == row.get('build'))]
                if len(matching) == 1:
                    return True
        if (len(values) == 1 and values[0] == 'audio_playback'
                and 'OrbbecSDK' in audio_playback_subjects(question)
                and re.search(r'音频|\baudio\b', question, re.I)
                and re.search(r'播放|\bplayback\b|\bplay\b', question, re.I)):
            # A short natural-language topic need not reproduce a catalog
            # question pattern verbatim. Ground it only when one reviewed
            # example owns that task in the requested SDK/platform scope.
            asked_os = {name for name, pattern in (
                ('linux', r'\blinux\b|\bubuntu\b'),
                ('windows', r'\b(?:windows|win\s*(?:10|11))\b'),
                ('macos', r'\bmac(?:\s*os)?\b|\bdarwin\b'),
                ('android', r'\bandroid\b'), ('ios', r'\bios\b'))
                if re.search(pattern, question, re.I)}
            asked_arch = set(sdk_cpu_architectures(question))
            matching = [row for row in self.search(question, role, include_all=True)
                        if row.get('doc_type') == 'example'
                        and 'audio_playback' in row.get('usage_tasks', [])
                        and (not asked_os or all(
                            name in str(row.get('platform', '')).casefold()
                            for name in asked_os))
                        and (not asked_arch or asked_arch <= set(
                            sdk_cpu_architectures(str(row.get('platform', '')))))]
            return len(matching) == 1
        if (key == 'task' and values == ['calibration_export']
                and re.search(r'对齐|align', question, re.I)
                and re.search(r'IMU', question, re.I)
                and re.search(r'标定|calib', question, re.I)
                and re.search(r'导出|export', question, re.I)):
            return any(calibration_export_row_matches_question(row, question)
                       and {'align_calib.yaml', 'imu_calib.yaml'} <=
                       set(row.get('outputs', []))
                       for row in self.search(question, role, include_all=True))
        if (key == 'task' and values == ['capture']
                and re.search(r'\blibobkit\b', question, re.I)
                and re.search(r'相机|camera', question, re.I)
                and re.search(r'\bIMU\b', question, re.I)
                and re.search(r'音频|audio', question, re.I)
                and re.search(r'初始化|配置|启停|退出|lifecycle|'
                              r'init|config|start|stop', question, re.I)):
            scoped = scope if isinstance(scope, dict) else {}
            matching = [row for row in self.search(
                question, role, include_all=True)
                if row.get('sdk_layer') == 'device_c'
                and row.get('doc_type') == 'example'
                and 'capture' in row.get('usage_tasks', [])
                and {'camera', 'imu', 'audio', 'lifecycle'} <=
                set(row.get('topics', []))
                and (not scoped.get('sdk_version') or
                     scoped['sdk_version'] == row.get('version'))
                and (not scoped.get('sdk_release_date') or
                     re.sub(r'\D', '', str(scoped['sdk_release_date'])) ==
                     row.get('build'))]
            return len(matching) == 1
        if (key == 'task' and values == ['link_library', 'deploy_dll']
                and re.search(r'Windows\s*x64', question, re.I)
                and re.search(r'链接|\blink\b', question, re.I)
                and re.search(r'放置|部署|\bDLL\b|deploy|place', question, re.I)):
            return any({'link_library', 'deploy_dll'} <=
                       set(row.get('usage_tasks', []))
                       and 'windows' in str(row.get('platform', '')).casefold()
                       for row in self.search(question, role, include_all=True))
        if (key == 'task' and values == ['link_and_deploy']
                and re.search(r'链接|\blink\b', question, re.I)
                and re.search(r'放置|部署|\bplace(?:ment)?\b|\bdeploy\b',
                              question, re.I)):
            return any('link_and_deploy' in row.get('usage_tasks', [])
                       and row.get('sdk_layer') == 'ble_cpp'
                       for row in self.search(question, role, include_all=True))
        if (len(values) == 1 and values[0] == 'multi_device_sync_mode_check_and_set'
                and re.search(r'主从|master.slave', question, re.I)
                and re.search(r'同步|sync', question, re.I)
                and re.search(r'判断|检查|设置|模式支持|读写权限|位图|'
                              r'check|set|permission|bitmap', question, re.I)):
            scoped = scope if isinstance(scope, dict) else {}
            if (scoped.get('sdk') not in (None, 'OrbbecSDK')
                    or scoped.get('language') not in (None, 'cpp', 'c++')):
                return False
            platform = str(scoped.get('platform') or '')
            platform_aliases = {'linux_x64': 'Linux x86_64',
                                'linux_x86_64': 'Linux x86_64',
                                'windows_x64': 'Windows x64'}
            matching = [row for row in self.search(question, role, include_all=True)
                        if row.get('sdk_layer') == 'host_cpp'
                        and row.get('doc_type') == 'example'
                        and sdk_usage_descriptor_matches(values[0], row)
                        and (not scoped.get('sdk_version')
                             or scoped['sdk_version'] == row.get('version'))
                        and (not scoped.get('sdk_release_date')
                             or re.sub(r'\D', '', str(scoped['sdk_release_date']))
                             == row.get('build'))
                        and (not platform or _identity_text(
                            platform_aliases.get(platform, platform)) in
                            _identity_text(str(row.get('platform', ''))))]
            return len(matching) == 1
        if (len(values) == 1 and values[0] == 'library_linking_and_dll_placement'
                and re.search(r'链接|\blink\b', question, re.I)
                and re.search(r'DLL', question, re.I)):
            return any(row.get('sdk_layer') == 'ble_cpp'
                       and 'windows' in str(row.get('platform', '')).casefold()
                       and sdk_usage_descriptor_matches(values[0], row)
                       for row in self.search(question, role, include_all=True))
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
                       for row in self.search(question, role, include_all=True))
        if key == 'task' and all(isinstance(item, str) for item in values):
            for row in self.search(question, role, include_all=True):
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

    def search(self, query: str, role: str, *, include_all: bool = False) -> list[dict]:
        if role != "internal_fae" or not isinstance(query, str) or len(query) > 2000:
            return []
        normalized = query.casefold()
        if "egoviewer" in normalized and "orbbecsdk" not in normalized:
            return []
        compact_dates = re.sub(r"(?<=\d)[-/](?=\d)", "", normalized)
        asked_sdk_versions = set(re.findall(
            r'(?<![a-z0-9_])orbbecsdk[_\s-]*(?:的[_\s-]*)?'
            r'(?:版本[_\s-]*(?:是[_\s-]*)?)?v?'
            r'(\d+\.\d+\.\d+)'
            r'(?![a-z0-9_])', normalized))
        if 'orbbecsdk' in normalized:
            # In a version comparison the library name often precedes only
            # the first version. Keep the reviewed side even when the other
            # version has no catalog rows.
            version_families = {'.'.join(str(row.get('version', '')).split('.')[:2])
                                for row in self.records
                                if row.get('sdk_layer') in {'host_cpp', 'android_java',
                                                            'ios_framework'}}
            asked_sdk_versions.update(
                version for version in re.findall(
                    r'(?<!\d)\d+\.\d+\.\d+(?!\d)', normalized)
                if '.'.join(version.split('.')[:2]) in version_families)
        asked_builds = set(re.findall(r"(?<!\d)20\d{6}(?!\d)", compact_dates))
        firmware_118 = bool(re.search(
            r"(?:固件|firmware)(?:版本|version)?[\s:：是为=]*1\.1\.8", normalized))
        reviewed_versions = {row.get('version') for row in self.records}
        if (re.search(r"(?<!\d)2\.9\.(?!0(?!\d))\d+(?!\d)", normalized)
                and not (asked_sdk_versions & reviewed_versions)) or (
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
            if ("orbbecsdk" in normalized
                    and (not layers or "libobkit" in normalized or
                         ('android_java' in layers and re.search(
                             r'(?<![A-Za-z0-9_])(?:linux|ubuntu|host)'
                             r'(?![A-Za-z0-9_])|主机侧', query, re.I)))):
                layers.add("host_cpp")
        selected = [row for row in self.records if row["sdk_layer"] in layers] if layers else self.records
        if asked_sdk_versions:
            selected = [row for row in selected
                        if row['sdk_layer'] == 'device_c' and len(layers) > 1
                        or row.get('version') in asked_sdk_versions]
        if asked_builds:
            scoped_builds = {}
            unscoped_builds = set()
            if len(layers) > 1:
                for match in re.finditer(r'(?<!\d)20\d{6}(?!\d)', compact_dates):
                    clause_start = max(compact_dates.rfind(mark, 0, match.start())
                                       for mark in '?？。；;\n') + 1
                    prefix = compact_dates[clause_start:match.start()]
                    owners = list(re.finditer(
                        r'(?<![a-z0-9_])(?:orbbecsdk|libobkit|egolowble)'
                        r'(?![a-z0-9_])', prefix))
                    owner = owners[-1].group().casefold() if owners else None
                    layer = {'orbbecsdk': 'host_cpp', 'libobkit': 'device_c',
                             'egolowble': 'ble_cpp'}.get(owner)
                    if layer and len(prefix) - owners[-1].end() <= 50:
                        scoped_builds.setdefault(layer, set()).add(match.group())
                    else:
                        unscoped_builds.add(match.group())
            else:
                unscoped_builds = asked_builds
            selected = [row for row in selected
                        if (not unscoped_builds or row['build'] in unscoped_builds)
                        and (row['sdk_layer'] not in scoped_builds
                             or row['build'] in scoped_builds[row['sdk_layer']])]
        if re.search(r'包内容|包里有什么|package\s+(?:contents?|files?)',
                     query, re.I):
            # A broad package question can return only the source-checked
            # members in this catalog. It is not a complete archive listing.
            members = sorted(selected, key=lambda row: (
                row.get('build', ''), row['kb_id']), reverse=True)
            return members if include_all else members[:3]
        query_terms = terms(query)
        topical_terms = {
            term for term in query_terms
            if term not in {'orbbecsdk', 'libobkit', 'egolowble'}
            and not re.fullmatch(r'v?\d+(?:\.\d+)+', term)
        }
        if topical_terms:
            # The SDK family and version select a source scope; they must not
            # outrank the actual question topic just because every package row
            # repeats that identity in its title or summary.
            query_terms = topical_terms
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
        if include_all:
            # Schema normalization must see every source-reviewed descriptor
            # relevant to the user's words. A top-three answer retrieval cap
            # must not hide a second goal from the planner.
            return [row for _, row in ranked]
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
