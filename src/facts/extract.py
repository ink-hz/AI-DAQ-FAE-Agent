"""从型号 Markdown 的规格表抽取事实草稿。

支持三种形态:
- 两列表(参数|值):逐行映射字段字典;
- 三列注记表(项|数值|来源/备注/说明/边界):第三列进行级 note;
- 按节聚合(FieldDef.section_capture):整节内容作为一行证据(SDK/平台支持类)。
更宽的矩阵表(平台矩阵、模式表)跳过。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

from src.data.product_loader import KNOWLEDGE_MD_NAMES, load_product_catalog
from src.facts.schema import FieldDef, normalize_label, parse_range, synonym_index

PRESERVED_STATUSES = {"fae_verified", "conflict"}
_NOTE_HEADER_RE = re.compile(r"来源|备注|说明|边界|note|remark|source", re.IGNORECASE)
_SECTION_CAPTURE_MAX = 1500
_HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class TableContext:
    source_file: str
    section_path: tuple[str, ...]
    headers: tuple[str, ...]
    line: int
    table_type: str


@dataclass(frozen=True)
class LabeledFactCandidate:
    label: str
    raw_value: str
    note: str
    context: TableContext


@dataclass(frozen=True)
class RejectedFactCandidate:
    candidate: LabeledFactCandidate
    reason: str
    field_id: str | None = None


@dataclass(frozen=True)
class DocumentedFieldCandidate:
    model_id: str
    field_id: str
    source_file: str
    section_path: tuple[str, ...]
    line: int
    label: str
    raw_value: str
    row_cells: tuple[str, ...] = ()
    headers: tuple[str, ...] = ()

    @property
    def section(self) -> str:
        return " > ".join(self.section_path)

    @property
    def source_row(self) -> str:
        cells = self.row_cells or (self.label, self.raw_value)
        return " | ".join(cells)


@dataclass
class FactsDiff:
    """重抽取与现有矩阵的逐行对比(K2 diff 门禁)。

    verified_divergence:人工核验行(fae_verified/conflict)与新抽取不一致——
    说明源文档或抽取逻辑变了,必须报警人看,不可静默;merge_rows 保证不覆盖,
    diff 保证被看见。
    """
    added: list[dict] = dc_field(default_factory=list)
    changed: list[dict] = dc_field(default_factory=list)      # doc_extracted 行值变化
    missing: list[dict] = dc_field(default_factory=list)      # doc_extracted 行重抽取缺席
    unmanaged_missing: list[dict] = dc_field(default_factory=list)
    verified_divergence: list[dict] = dc_field(default_factory=list)
    normalized_only: list[dict] = dc_field(default_factory=list)
    table_reselected: list[dict] = dc_field(default_factory=list)
    verified_value_enrichment: list[dict] = dc_field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added
            or self.changed
            or self.missing
            or self.verified_divergence
            or self.table_reselected
        )

    @property
    def has_verified_divergence(self) -> bool:
        return bool(self.verified_divergence)


def _normalized_raw_value(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("≤", "<=").replace("≥", ">=").replace("±", "+/-")
    return re.sub(r"\s+", "", text).casefold()


def _source_identity(row: dict) -> tuple[str, str, str]:
    source = row.get("source", {}) or {}
    return (
        str(source.get("file", "")),
        str(source.get("section", "")),
        str(source.get("table_type", "")),
    )


def _source_changed(old: dict, new: dict) -> bool:
    old_source = _source_identity(old)
    new_source = _source_identity(new)
    if old_source[0] != new_source[0]:
        return True
    old_section, new_section = old_source[1], new_source[1]
    same_section_path = (
        old_section == new_section
        or new_section.startswith(f"{old_section} > ")
        or old_section.startswith(f"{new_section} > ")
    )
    if not same_section_path:
        return True
    # 旧事实没有 table_type 时是元数据升级，不代表重选了另一张表。
    return bool(old_source[2] and new_source[2] and old_source[2] != new_source[2])


def diff_rows(existing: list[dict], drafts: list[dict]) -> FactsDiff:
    diff = FactsDiff()
    existing_by_field = {r["field"]: r for r in existing}
    # 同字段多行草稿时 merge_rows 取第一条,diff 必须同语义
    drafts_by_field: dict[str, dict] = {}
    for r in drafts:
        drafts_by_field.setdefault(r["field"], r)
    for fid, draft in drafts_by_field.items():
        old = existing_by_field.get(fid)
        if old is None:
            diff.added.append(draft)
            continue
        old_raw = str(old.get("raw_value", ""))
        new_raw = str(draft.get("raw_value", ""))
        source_changed = _source_changed(old, draft)
        if old_raw == new_raw:
            if source_changed:
                diff.table_reselected.append({
                    "field": fid,
                    "old_source": old.get("source", {}),
                    "new_source": draft.get("source", {}),
                    "old_raw_value": old_raw,
                    "new_raw_value": new_raw,
                })
            if (
                old.get("status") in PRESERVED_STATUSES
                and old.get("value") != draft.get("value")
            ):
                diff.verified_value_enrichment.append({
                    "field": fid,
                    "verified_value": old.get("value"),
                    "draft_value": draft.get("value"),
                })
            continue
        if _normalized_raw_value(old_raw) == _normalized_raw_value(new_raw):
            diff.normalized_only.append({
                "field": fid,
                "old_raw_value": old_raw,
                "new_raw_value": new_raw,
            })
            if source_changed:
                diff.table_reselected.append({
                    "field": fid,
                    "old_source": old.get("source", {}),
                    "new_source": draft.get("source", {}),
                    "old_raw_value": old_raw,
                    "new_raw_value": new_raw,
                })
            continue
        if source_changed:
            diff.table_reselected.append({
                "field": fid,
                "old_source": old.get("source", {}),
                "new_source": draft.get("source", {}),
                "old_raw_value": old_raw,
                "new_raw_value": new_raw,
            })
        if old.get("status") in PRESERVED_STATUSES:
            diff.verified_divergence.append({
                "field": fid,
                "verified_raw_value": old_raw,
                "draft_raw_value": new_raw,
                "verified_status": old.get("status"),
            })
        else:
            diff.changed.append({
                "field": fid,
                "old_raw_value": old_raw,
                "new_raw_value": new_raw,
            })
    for fid, old in existing_by_field.items():
        # 人工核验行不受抽取覆盖范围影响,缺席不计 missing
        if fid not in drafts_by_field and old.get("status") not in PRESERVED_STATUSES:
            source = old.get("source") or {}
            if source.get("table_type"):
                diff.missing.append(old)
            else:
                diff.unmanaged_missing.append(old)
    return diff


def _table_blocks(section_text: str) -> list[list[list[str]]]:
    """把节内容切成表块;每块是行列表,行是 cell 列表(分隔行已去掉)。"""
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in section_text.splitlines():
        line = line.strip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue
            current.append(cells)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _contextual_table_blocks(
    section_text: str,
    section_path: tuple[str, ...],
) -> list[tuple[tuple[str, ...], list[tuple[int, list[str]]]]]:
    """保留标题路径与真实行号的 Markdown 表块。"""
    blocks: list[tuple[tuple[str, ...], list[tuple[int, list[str]]]]] = []
    current: list[tuple[int, list[str]]] = []
    current_path = list(section_path)
    # 调用方传入的 section_path 是当前文本之外的父路径；正文 H2/H3 在其下。
    heading_levels = [1 + index for index in range(len(current_path))]

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append((tuple(current_path), current))
            current = []

    for line_number, raw_line in enumerate(section_text.splitlines(), start=1):
        stripped = raw_line.strip()
        heading = _HEADING_RE.match(stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if current_path and title == current_path[-1]:
                continue
            while heading_levels and heading_levels[-1] >= level:
                heading_levels.pop()
                current_path.pop()
            current_path.append(title)
            heading_levels.append(level)
            continue
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            current.append((line_number, cells))
        else:
            flush()
    flush()
    return blocks


def classify_table_context(
    section_path: tuple[str, ...],
    headers: tuple[str, ...],
) -> str:
    """按章节与表头分类；不读取型号或问题文本。"""
    context = " ".join((*section_path, *headers)).casefold()
    if re.search(r"固件|firmware|升级|更新", context):
        return "firmware_operation"
    if re.search(r"机械|结构和外观|安装|尺寸|外观|mount|mechanical", context):
        return "mechanical"
    if re.search(r"认证|合规证书|certif|compliance report", context):
        return "certification"
    if re.search(r"sdk|ros\s?2|平台支持|platform support|wrapper", context):
        return "sdk_platform_support"
    if re.search(r"数据流|输出流|stream|分辨率|frame rate|fps", context):
        return "stream_capability"
    if re.search(
        r"规格|参数|性能|硬件|基础|环境|防护|接口|同步|供电|功耗|电气|"
        r"光学|传感器|specification|specs",
        context,
    ):
        return "product_spec"
    if len(headers) >= 2 and normalize_label(headers[1]) in {
        "值",
        "数值",
        "规格值",
        "value",
    }:
        return "product_spec"
    return "unknown"


def parse_contextual_tables(
    section_text: str,
    *,
    source_file: str,
    section_path: tuple[str, ...] = (),
) -> list[LabeledFactCandidate]:
    candidates: list[LabeledFactCandidate] = []
    for path, block in _contextual_table_blocks(section_text, section_path):
        if not block:
            continue
        headers = tuple(block[0][1])
        width = len(headers)
        note_table = width == 3 and bool(_NOTE_HEADER_RE.search(headers[2]))
        if width != 2 and not note_table:
            continue
        table_type = classify_table_context(path, headers)
        for line_number, cells in block[1:]:
            if len(cells) != width:
                continue
            candidates.append(
                LabeledFactCandidate(
                    label=cells[0],
                    raw_value=cells[1],
                    note=cells[2] if note_table else "",
                    context=TableContext(
                        source_file=source_file,
                        section_path=path,
                        headers=headers,
                        line=line_number,
                        table_type=table_type,
                    ),
                )
            )
    return candidates


def scan_documented_field_models(
    knowledge_dir: Path,
    fields: dict[str, FieldDef],
) -> dict[str, set[str]]:
    """Find models whose Markdown table data rows explicitly name each field.

    Only an exact normalized label in the first cell of a non-header table row
    counts. Values are deliberately not parsed here: conditional and matrix
    tables still require source review before structured-fact promotion.
    """
    found: dict[str, set[str]] = {field_id: set() for field_id in fields}
    for candidate in scan_documented_field_candidates(knowledge_dir, fields):
        found[candidate.field_id].add(candidate.model_id)
    return found


def scan_documented_field_candidates(
    knowledge_dir: Path,
    fields: dict[str, FieldDef],
) -> tuple[DocumentedFieldCandidate, ...]:
    """返回带稳定来源行的精确标签候选，供覆盖裁决与审计复放。"""
    labels = synonym_index(fields)
    candidates: list[DocumentedFieldCandidate] = []
    for model_id, info in load_product_catalog(Path(knowledge_dir)).items():
        model_dir = Path(info["dir"])
        for md_name in info["md_files"]:
            if md_name not in KNOWLEDGE_MD_NAMES:
                continue
            text = (model_dir / md_name).read_text(encoding="utf-8")
            for section_path, block in _contextual_table_blocks(text, ()):
                headers = tuple(block[0][1]) if block else ()
                for line, row in block[1:]:
                    if not row:
                        continue
                    field_id = labels.get(normalize_label(row[0]))
                    if field_id:
                        candidates.append(
                            DocumentedFieldCandidate(
                                model_id=model_id,
                                field_id=field_id,
                                source_file=md_name,
                                section_path=section_path,
                                line=line,
                                label=row[0],
                                raw_value=row[1] if len(row) > 1 else "",
                                row_cells=tuple(row),
                                headers=headers,
                            )
                        )
    return tuple(candidates)


def parse_labeled_tables(section_text: str) -> list[tuple[str, str, str]]:
    """两列表 → (label, value, "");三列注记表 → (label, value, note);其余跳过。"""
    rows: list[tuple[str, str, str]] = []
    for block in _table_blocks(section_text):
        width = len(block[0])
        if width == 2:
            # block[0] 是表头(markdown 表必有表头行):"接口|作用"这类表头
            # 标签会命中字段同义词,当数据行会产生垃圾草稿。
            rows.extend((c[0], c[1], "") for c in block[1:] if len(c) == 2)
        elif width == 3 and _NOTE_HEADER_RE.search(block[0][2]):
            rows.extend((c[0], c[1], c[2]) for c in block[1:] if len(c) == 3)
    return rows


def parse_two_col_tables(section_text: str) -> list[tuple[str, str]]:
    """向后兼容:只返回两列表的 (label, value)。"""
    return [
        (label, value)
        for label, value, note in parse_labeled_tables(section_text)
        if not note
    ]


def build_draft_rows(
    items: list[tuple],
    fields: dict[str, FieldDef],
    source_file: str,
    section: str,
) -> tuple[list[dict], list[str]]:
    index = synonym_index(fields)
    rows: list[dict] = []
    unmapped: list[str] = []
    for item in items:
        label, raw_value = item[0], item[1]
        note = item[2] if len(item) > 2 else ""
        fid = index.get(normalize_label(label))
        if fid is None:
            unmapped.append(label)
            continue
        row = {
            "field": fid,
            "raw_value": raw_value,
            "qualifier": {},
            "source": {"file": source_file, "section": section},
            "note": note,
        }
        if fields[fid].value_type == "range":
            rng = parse_range(raw_value)
            if rng is None:
                row["value"] = None
                row["status"] = "needs_normalization"
            else:
                row["value"] = {
                    "min": rng.min, "max": rng.max,
                    "unit": rng.unit, "open_ended": rng.open_ended,
                }
                row["status"] = "doc_extracted"
        else:
            row["value"] = raw_value
            row["status"] = "doc_extracted"
        rows.append(row)
    return rows, unmapped


def build_contextual_draft_rows(
    candidates: list[LabeledFactCandidate],
    fields: dict[str, FieldDef],
) -> tuple[list[dict], list[str], list[RejectedFactCandidate]]:
    """只把字段允许的表格语境变成草稿；其余候选显式报告。"""
    index = synonym_index(fields)
    accepted: list[tuple[dict, LabeledFactCandidate, str]] = []
    unmapped: list[str] = []
    rejected: list[RejectedFactCandidate] = []
    for candidate in candidates:
        field_id = index.get(normalize_label(candidate.label))
        if field_id is None:
            unmapped.append(candidate.label)
            continue
        table_type = candidate.context.table_type
        if table_type == "unknown":
            rejected.append(
                RejectedFactCandidate(candidate, "unknown_table_type", field_id)
            )
            continue
        if table_type not in fields[field_id].allowed_table_types:
            rejected.append(
                RejectedFactCandidate(
                    candidate,
                    "field_disallows_table_type",
                    field_id,
                )
            )
            continue
        section = " > ".join(candidate.context.section_path)
        draft_rows, _ = build_draft_rows(
            [(candidate.label, candidate.raw_value, candidate.note)],
            fields,
            candidate.context.source_file,
            section,
        )
        row = draft_rows[0]
        row["source"].update({
            "line": candidate.context.line,
            "table_type": table_type,
        })
        accepted.append((row, candidate, field_id))

    accepted_by_field: dict[str, list[tuple[dict, LabeledFactCandidate, str]]] = {}
    for accepted_item in accepted:
        accepted_by_field.setdefault(accepted_item[2], []).append(accepted_item)
    rows: list[dict] = []
    for field_id, items in accepted_by_field.items():
        fingerprints = {
            (
                _normalized_raw_value(row.get("raw_value", "")),
                repr(sorted((row.get("qualifier") or {}).items())),
            )
            for row, _, _ in items
        }
        if len(fingerprints) > 1:
            rejected.extend(
                RejectedFactCandidate(
                    candidate,
                    "multiple_field_candidates_require_adjudication",
                    field_id,
                )
                for _, candidate, _ in items
            )
            continue
        rows.append(items[0][0])
    return rows, unmapped, rejected


def build_section_capture_rows(
    title: str,
    content: str,
    fields: dict[str, FieldDef],
    source_file: str,
) -> list[dict]:
    """H2 标题命中 FieldDef.section_capture 时,整节内容作为一行聚合证据。"""
    key = normalize_label(title)
    rows: list[dict] = []
    for f in fields.values():
        if not f.section_capture:
            continue
        if key not in {normalize_label(t) for t in f.section_capture}:
            continue
        body = "\n".join(
            line for line in content.splitlines() if not line.startswith("## ")
        ).strip()[:_SECTION_CAPTURE_MAX]
        rows.append({
            "field": f.id,
            "raw_value": body,
            "value": body,
            "qualifier": {},
            "source": {"file": source_file, "section": title},
            "note": "",
            "status": "doc_extracted",
        })
    return rows


def merge_rows(existing: list[dict], drafts: list[dict]) -> list[dict]:
    preserved = {
        r["field"]: r for r in existing if r.get("status") in PRESERVED_STATUSES
    }
    merged = list(preserved.values())
    seen = set(preserved)
    for row in drafts:
        if row["field"] not in seen:
            merged.append(row)
            seen.add(row["field"])
    return merged
