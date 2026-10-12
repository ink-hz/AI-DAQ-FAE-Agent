"""Hash-bound, loopback-only view of real but unreviewed DAQ source chapters.

This view is deliberately separate from ReviewedKnowledge and its active pointer.
It supports Dev retrieval tests without representing candidates as approved facts.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy
from datetime import date
from pathlib import Path

from scripts.archive_candidate_snapshot import verify_archive

from .records import access_fingerprint, record_fingerprint, validate_records
from .section_search import body_text
from .source_paths import contains_source_path


def _load_bound(path: Path, expected: str):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("candidate input hash mismatch")
    return json.loads(raw)


def _identity_text(value: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).casefold().replace('×', 'x'))


class DevCandidateKnowledge:
    is_candidate_preview = True

    def __init__(self, release_id: str, sections: list[dict], records: list[dict],
                 field_aliases: dict[str, str]):
        self.release_id = release_id
        self.manifest = {"format_version": 2, "candidate_preview": True,
                         "section_count": len(sections),
                         "source_checked_count": len(records)}
        self._sections = tuple(deepcopy(sections))
        self._records = tuple(deepcopy(records))
        self._field_aliases = dict(field_aliases)

    @classmethod
    def load(cls, graph_dir: Path, bound_index: Path, archive: Path,
             source_check: Path | None = None):
        summary = json.loads((graph_dir / "summary.json").read_text())
        graph_hash = summary["output_sha256"]["dependency-bundle.json"]
        bundle = _load_bound(graph_dir / "dependency-bundle.json", graph_hash)
        index = _load_bound(bound_index, summary["input_sha256"]["bound_section_index"])
        snapshot = bundle["snapshot"]
        verified = verify_archive(archive, snapshot["archive_manifest_sha256"])
        original = json.loads((archive / "manifest.json").read_text())
        by_path = {row["path"]: row for row in original["files"]}
        if (verified["manifest_sha256"] != snapshot["archive_manifest_sha256"] or
                len(by_path) != len(original["files"]) or
                {(row["path"], row["sha256"], row["size"]) for row in snapshot["sources"]}
                != {(row["path"], row["sha256"], row["size"]) for row in original["files"]}):
            raise ValueError("candidate source inventory differs from verified archive")
        records, findings = validate_records(bundle["records"], snapshot)
        if findings or any(row["answerable"] for row in records):
            raise ValueError("candidate graph contains invalid or answerable records")
        source_hashes = {row["path"]: row["sha256"] for row in snapshot["sources"]}
        locators = {(row["source_path"], row["source_sha256"],
                     json.dumps(row["locator"], sort_keys=True))
                    for row in snapshot["chunks"]}
        if (len(bundle["sections"]) != len(index["sections"]) or
                {row["section_id"]: row for row in bundle["sections"]} == {} or
                set(index["bodies"]) != {row["section_id"] for row in bundle["sections"]}):
            raise ValueError("candidate section inventory mismatch")
        sections = []
        for section in bundle["sections"]:
            sid = section["section_id"]
            body = index["bodies"][sid]
            if (section.get("review_status") != "candidate" or
                    hashlib.sha256(body.encode()).hexdigest() != section["body_sha256"]):
                raise ValueError("candidate section state or body hash mismatch")
            if not section["source_refs"] or any(
                source_hashes.get(ref["path"]) != ref["sha256"] or
                (ref["path"], ref["sha256"], json.dumps(ref["locator"], sort_keys=True))
                not in locators for ref in section["source_refs"]
            ):
                raise ValueError("candidate section source locator mismatch")
            if section.get("proposed_view_roles") != ["internal_fae"]:
                continue
            readable = body_text(body)
            for ref in section["source_refs"]:
                readable = readable.replace(ref["path"], "[来源见结构化元数据]")
            projected = {key: deepcopy(section[key]) for key in
                         ("section_id", "title", "scope", "source_refs", "dependency_claim_ids")}
            projected.update(body=readable, knowledge_type=section.get("knowledge_type", "candidate"),
                             evidence_status="candidate")
            if contains_source_path(projected) or "http://" in readable or "https://" in readable:
                raise ValueError("candidate body contains a path or unreviewed URL")
            sections.append(projected)
        source_checked = []
        field_aliases = {}
        ledger_hash = "0" * 64
        if source_check is not None:
            raw = source_check.read_bytes()
            ledger_hash = hashlib.sha256(raw).hexdigest()
            ledger = json.loads(raw)
            if (ledger.get("format_version") != "daq-dev-source-check/v1" or
                    not re.fullmatch(r"[0-9a-f]{64}", str(ledger.get("graph_sha256", ""))) or
                    ledger.get("scope") != "loopback_dev_internal_fae_only" or
                    ledger.get("reviewer") != "Codex"):
                raise ValueError("Dev source check provenance or scope invalid")
            date.fromisoformat(ledger["reviewed_at"])
            selected = ledger["records"]
            if not isinstance(selected, dict) or not selected:
                raise ValueError("Dev source check inventory missing")
            by_id = {row["id"]: row for row in bundle["records"]}
            for rid, expected in sorted(selected.items()):
                original = by_id.get(rid)
                if original is None or original["status"] != "candidate":
                    raise ValueError("Dev source checked record missing or not candidate")
                row = deepcopy(original)
                row["status"] = "verified"
                if record_fingerprint(row) != expected:
                    raise ValueError("Dev source check record fingerprint mismatch")
                row["fact_review"] = {"reviewer": "Codex source check",
                                      "reviewed_at": ledger["reviewed_at"],
                                      "record_sha256": expected}
                row["access_review"] = {"reviewer": "Dev internal only",
                                        "reviewed_at": ledger["reviewed_at"],
                                        "view_roles": ["internal_fae"],
                                        "forward_roles": ["internal_fae"]}
                row["access_review"]["record_sha256"] = access_fingerprint(row)
                source_checked.append(row)
            _, checked_findings = validate_records(source_checked, snapshot)
            if checked_findings:
                raise ValueError("Dev source checked records fail record contract")
            selected_fields = {row["data"]["field"] for row in source_checked
                               if row["kind"] == "claim"}
            declared = ledger.get("field_aliases", {})
            if not isinstance(declared, dict) or set(declared) != selected_fields:
                raise ValueError("Dev source check field aliases incomplete")
            for field, aliases in declared.items():
                if not isinstance(aliases, list) or not all(
                        isinstance(alias, str) and alias.strip() for alias in aliases):
                    raise ValueError("Dev source check field aliases invalid")
                for alias in [field, *aliases]:
                    normalized = _identity_text(alias)
                    if normalized in field_aliases and field_aliases[normalized] != field:
                        raise ValueError("Dev source check field alias ambiguity")
                    field_aliases[normalized] = field
        release_hash = hashlib.sha256((graph_hash + ledger_hash).encode()).hexdigest()
        return cls("candidate-dev-" + release_hash, sections, source_checked,
                   field_aliases)

    def records_for(self, role: str, *, for_delivery: bool = False):
        return [deepcopy(row) for row in self._records] if role == "internal_fae" else []

    def sections_for(self, role: str, *, for_delivery: bool = False):
        return [deepcopy(section) for section in self._sections] if role == "internal_fae" else []

    def conflict_notices_for(self, role: str):
        return []

    def resolve_field(self, field: str) -> str | None:
        return self._field_aliases.get(_identity_text(field))

    def source_checked_variant_scopes(self, entity: str, field: str) -> dict[str, list[str]]:
        """Expose only the selector choices behind a generic Dev spec question."""
        query = _identity_text(entity)
        names = {row["id"]: _identity_text(row["data"]["name"])
                 for row in self._records if row["kind"] == "entity"}
        matching = [name for name in names.values() if name and name in query]
        if not matching:
            return {}
        longest = max(map(len, matching))
        entity_ids = {rid for rid, name in names.items()
                      if len(name) == longest and name in query}
        scopes: dict[str, set[str]] = {}
        for row in self._records:
            if row["kind"] != "claim" or row["data"]["field"] != field or \
                    row["data"]["entity_id"] not in entity_ids:
                continue
            scope = row["scope"]
            for selector in scope.get("required_selectors", []):
                value = str(scope[selector])
                if _identity_text(value) not in query:
                    scopes.setdefault(selector, set()).add(value)
        return {key: sorted(values) for key, values in sorted(scopes.items())}

    def ground_question(self, question: str) -> dict:
        text = _identity_text(question)
        matches = []
        for row in self._records:
            if row["kind"] != "entity" or _identity_text(row["data"]["name"]) not in text:
                continue
            scope = row["scope"]
            selectors = scope.get("required_selectors", [])
            if any(_identity_text(str(scope[key])) not in text for key in selectors):
                continue
            matches.append(row)
        if len(matches) != 1:
            return {}
        row = matches[0]
        scope = row["scope"]
        return {"entities": [row["id"]],
                "conditions": {key: scope[key] for key in scope.get("required_selectors", [])}}
