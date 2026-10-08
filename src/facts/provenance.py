"""结构化事实到本地章节与 canonical source 的来源完整性审计。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.official_url_policy import is_promotable_publisher_url

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_SECTION_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_SOURCE_TYPES = {"local", "global_trace", "official_web"}
_WAVE2_MARKER = "20260708 第二波回填"


@dataclass(frozen=True)
class ProvenanceFinding:
    model_id: str
    field_id: str
    code: str
    source_file: str
    source_section: str
    detail: str = ""


def _fidelity_text(value: object) -> str:
    """Normalize representation only; never add synonyms or interpretations."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if character.isalnum())


def _declared_section_text(path: Path, section: str) -> str:
    requested = tuple(part.strip() for part in section.split(" > ") if part.strip())
    if not requested:
        return ""
    # Use the declared leaf heading and stop at the next sibling/ancestor. This
    # supports both model-title H1 sources and ordinary H2 fact sections.
    leaf = requested[-1]
    text = path.read_text(encoding="utf-8")
    headings = list(_SECTION_HEADING_RE.finditer(text))
    for index, match in enumerate(headings):
        if match.group(2).strip() != leaf:
            continue
        level = len(match.group(1))
        end = len(text)
        for following in headings[index + 1:]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        return text[match.end():end]
    return ""


def audit_wave2_fact_source_fidelity(
    knowledge_dir: Path,
) -> tuple[ProvenanceFinding, ...]:
    """Verify every wave-2 tech-route raw value is present in its declared section.

    The existing batch marker scopes the first deterministic audit to exactly the
    31 rows introduced together. This is an evidence-fidelity check, not a claim
    that every row in the batch is wrong.
    """
    findings: list[ProvenanceFinding] = []
    for facts_path in sorted(Path(knowledge_dir).glob("*/facts.yaml")):
        rows = yaml.safe_load(facts_path.read_text(encoding="utf-8")) or []
        for row in rows:
            if (
                row.get("field") != "tech_route"
                or _WAVE2_MARKER not in str(row.get("note") or "")
            ):
                continue
            source = row.get("source") or {}
            if not isinstance(source, dict):
                continue
            source_path = facts_path.parent / str(source.get("file") or "")
            section = str(source.get("section") or "")
            if not source_path.is_file():
                continue
            section_text = _declared_section_text(source_path, section)
            raw_value = _fidelity_text(row.get("raw_value"))
            if raw_value and raw_value not in _fidelity_text(section_text):
                findings.append(ProvenanceFinding(
                    model_id=facts_path.parent.name,
                    field_id="tech_route",
                    code="raw_value_not_in_declared_section",
                    source_file=str(source.get("file") or ""),
                    source_section=section,
                    detail=str(row.get("raw_value") or ""),
                ))
    return tuple(findings)


def _headings(path: Path) -> set[str]:
    return {
        match.group(1).strip()
        for match in _HEADING_RE.finditer(path.read_text(encoding="utf-8"))
    }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _finding(
    model_id: str,
    field_id: str,
    code: str,
    source: dict,
    detail: str = "",
) -> ProvenanceFinding:
    return ProvenanceFinding(
        model_id=model_id,
        field_id=field_id,
        code=code,
        source_file=str(source.get("file") or ""),
        source_section=str(source.get("section") or ""),
        detail=detail,
    )


def _is_official_url(canonical_url: str) -> bool:
    return is_promotable_publisher_url(canonical_url, publisher="Orbbec")


def _review_source_finding(
    *,
    knowledge_dir: Path,
    knowledge_root: Path,
    model_id: str,
    field_id: str,
    review_source: object,
) -> ProvenanceFinding | None:
    if review_source is None:
        return None
    if not isinstance(review_source, dict):
        return ProvenanceFinding(
            model_id,
            field_id,
            "review_source_not_mapping",
            "",
            "",
        )
    if review_source.get("source_type") != "global_trace":
        return _finding(
            model_id,
            field_id,
            "review_source_type_invalid",
            review_source,
        )
    relative = Path(str(review_source.get("file") or ""))
    resolved = (knowledge_dir / relative).resolve()
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not _is_within(resolved, knowledge_root)
    ):
        return _finding(
            model_id,
            field_id,
            "review_source_path_escape",
            review_source,
        )
    if relative.suffix.casefold() != ".md" or not resolved.is_file():
        return _finding(
            model_id,
            field_id,
            "review_source_file_missing",
            review_source,
        )
    section = str(review_source.get("section") or "")
    heading_parts = tuple(part.strip() for part in section.split(" > ") if part.strip())
    headings = _headings(resolved)
    if not heading_parts or any(part not in headings for part in heading_parts):
        return _finding(
            model_id,
            field_id,
            "review_source_section_missing",
            review_source,
        )
    return None


def audit_fact_provenance(knowledge_dir: Path) -> tuple[ProvenanceFinding, ...]:
    knowledge_root = knowledge_dir.resolve()
    findings: list[ProvenanceFinding] = []
    for model_dir in sorted(knowledge_dir.iterdir()):
        facts_path = model_dir / "facts.yaml"
        if not model_dir.is_dir() or not facts_path.exists():
            continue
        rows = yaml.safe_load(facts_path.read_text(encoding="utf-8")) or []
        for row in rows:
            model_id = model_dir.name
            field_id = str(row.get("field") or "")
            source = row.get("source") or {}
            if not isinstance(source, dict):
                findings.append(
                    ProvenanceFinding(
                        model_id,
                        field_id,
                        "source_not_mapping",
                        "",
                        "",
                    )
                )
                continue
            source_type = str(source.get("source_type") or "local")
            if source_type not in _SOURCE_TYPES:
                findings.append(
                    _finding(model_id, field_id, "source_type_unknown", source)
                )
                continue
            source_file = str(source.get("file") or "")
            section = str(source.get("section") or "")
            relative = Path(source_file)
            if relative.is_absolute() or ".." in relative.parts:
                findings.append(
                    _finding(model_id, field_id, "source_path_escape", source)
                )
                continue
            if source_type == "global_trace":
                resolved = (knowledge_dir / relative).resolve()
                if not _is_within(resolved, knowledge_root):
                    findings.append(
                        _finding(model_id, field_id, "source_path_escape", source)
                    )
                    continue
                if relative.suffix.casefold() != ".md":
                    findings.append(
                        _finding(model_id, field_id, "global_source_not_readable", source)
                    )
                    continue
            else:
                if source_file.startswith("_facts/") or source_file.startswith("Knowledge/"):
                    findings.append(
                        _finding(
                            model_id,
                            field_id,
                            "global_source_type_missing",
                            source,
                        )
                    )
                    continue
                resolved = (model_dir / relative).resolve()
                if not _is_within(resolved, model_dir.resolve()):
                    findings.append(
                        _finding(model_id, field_id, "source_path_escape", source)
                    )
                    continue
            if not resolved.is_file():
                findings.append(
                    _finding(model_id, field_id, "source_file_missing", source)
                )
                continue
            heading_parts = tuple(part.strip() for part in section.split(" > ") if part.strip())
            headings = _headings(resolved)
            if not heading_parts or any(part not in headings for part in heading_parts):
                findings.append(
                    _finding(model_id, field_id, "source_section_missing", source)
                )
                continue
            review_finding = _review_source_finding(
                knowledge_dir=knowledge_dir,
                knowledge_root=knowledge_root,
                model_id=model_id,
                field_id=field_id,
                review_source=row.get("review_source"),
            )
            if review_finding is not None:
                findings.append(review_finding)
                continue
            if source_type != "official_web":
                continue
            canonical_url = str(source.get("canonical_url") or "")
            verified_at = str(source.get("verified_at") or "")
            if not _is_official_url(canonical_url) or not verified_at:
                findings.append(
                    _finding(
                        model_id,
                        field_id,
                        "official_source_invalid",
                        source,
                    )
                )
                continue
            sources_path = model_dir / "sources_and_assets.md"
            sources_text = (
                sources_path.read_text(encoding="utf-8")
                if sources_path.is_file()
                else ""
            )
            if canonical_url not in sources_text or verified_at not in sources_text:
                findings.append(
                    _finding(
                        model_id,
                        field_id,
                        "canonical_source_not_localized",
                        source,
                    )
                )
    return tuple(findings)
