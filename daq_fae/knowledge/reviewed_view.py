"""Fail-closed, request-independent view of one immutable DAQ release."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from daq_fae.knowledge.records import (KINDS, ROLES, STATUSES,
                                       access_fingerprint, record_fingerprint)
from daq_fae.knowledge.releases import read_active_release


_SHA = re.compile(r"^[0-9a-f]{64}$")
_LINK_LIKE = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.-]*://|//[a-z0-9][a-z0-9.-]*\.[a-z]{2,}"
    r"|\b[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?=$|[\s/?#)\]>]))"
)


def _url_bearing_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if _LINK_LIKE.search(value) else []
    if isinstance(value, dict):
        return [item for key, child in value.items()
                for item in (_url_bearing_strings(key) + _url_bearing_strings(child))]
    if isinstance(value, list):
        return [item for child in value for item in _url_bearing_strings(child)]
    return []


def _scannable_data(row: dict, source_hashes: dict[str, str]) -> dict:
    """Exclude only validated conflict provenance from the delivery URL scan."""
    data = row["data"]
    if row["status"] != "conflict":
        return data
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or len(candidates) < 2:
        raise ValueError("knowledge conflict candidates invalid")
    scannable = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("knowledge conflict candidate invalid")
        ref = candidate.get("source_ref")
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "locator"} or \
                not isinstance(ref.get("path"), str) or \
                source_hashes.get(ref["path"]) != ref.get("sha256") or \
                not isinstance(ref.get("locator"), dict) or \
                _url_bearing_strings(ref["locator"]):
            raise ValueError("knowledge conflict source reference invalid")
        scannable.append({key: value for key, value in candidate.items()
                          if key != "source_ref"})
    return {**data, "candidates": scannable}


class ReviewedKnowledge:
    def __init__(self, release_id: str, manifest: dict):
        self.release_id = release_id
        self.manifest = deepcopy(manifest)
        self._records = tuple(deepcopy(manifest["records"]))
        self._sections = tuple(deepcopy(manifest.get("sections", [])))

    @classmethod
    def load_active(cls, root: Path) -> "ReviewedKnowledge | None":
        if root.is_symlink():
            raise ValueError("knowledge release root must not be a symlink")
        active = read_active_release(root)
        if active is None:
            return None
        return cls.from_manifest(active["release_id"], active["manifest"])

    @classmethod
    def from_manifest(cls, release_id: str, manifest: dict) -> "ReviewedKnowledge":
        if not isinstance(release_id, str) or not _SHA.fullmatch(release_id):
            raise ValueError("knowledge release identity invalid")
        if not isinstance(manifest, dict) or type(manifest.get("format_version")) is not int \
                or manifest["format_version"] not in {1, 2}:
            raise ValueError("knowledge release format invalid")
        if manifest["format_version"] == 1 and any(key in manifest for key in
                ("sections", "section_count", "source_locations", "runtime_contract",
                 "section_status_counts", "record_kind_counts", "role_summary",
                 "source_index", "dependency_index")):
            raise ValueError("knowledge release format v1 is records only")
        if manifest["format_version"] == 2 and manifest.get("runtime_contract") != "daq-reviewed-sections-v2":
            raise ValueError("knowledge release format runtime contract invalid")
        if not isinstance(manifest.get("sources"), list) or not isinstance(manifest.get("records"), list):
            raise ValueError("knowledge release inventory invalid")
        if manifest.get("source_count") != len(manifest["sources"]) or \
                manifest.get("record_count") != len(manifest["records"]):
            raise ValueError("knowledge release count invalid")
        source_hashes: dict[str, str] = {}
        for source in manifest["sources"]:
            if not isinstance(source, dict) or not isinstance(source.get("path"), str) or \
                    source["path"].startswith("/") or \
                    any(part in {"", ".", ".."} for part in source["path"].split("/")) or \
                    not isinstance(source.get("sha256"), str) or not \
                    _SHA.fullmatch(source["sha256"]) or source["path"] in source_hashes:
                raise ValueError("knowledge source inventory invalid")
            source_hashes[source["path"]] = source["sha256"]
        seen: set[str] = set()
        answerable = 0
        for row in manifest["records"]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in seen:
                raise ValueError("knowledge record identity invalid")
            seen.add(row["id"])
            if row.get("kind") not in KINDS or row.get("status") not in STATUSES or \
                    not isinstance(row.get("scope"), dict) or not row["scope"] or \
                    not isinstance(row.get("data"), dict):
                raise ValueError("knowledge record schema invalid")
            expected_answerable = row.get("status") in {"verified", "unsupported"}
            if row.get("answerable") is not expected_answerable:
                raise ValueError("knowledge record answerability invalid")
            refs = row.get("source_refs")
            if not isinstance(refs, list) or not refs or any(
                not isinstance(ref, dict) or source_hashes.get(ref.get("path")) != ref.get("sha256")
                or not isinstance(ref.get("locator"), dict) for ref in refs
            ):
                raise ValueError("knowledge source reference invalid")
            urls = _url_bearing_strings(row["scope"]) + _url_bearing_strings(
                _scannable_data(row, source_hashes))
            if (row["kind"] != "link" and urls) or (row["kind"] == "link" and
                    any(value != row["data"].get("url") for value in urls)):
                raise ValueError("knowledge URL must be a reviewed link record")
            if not expected_answerable:
                continue
            answerable += 1
            try:
                fact = row["fact_review"]
                access = row["access_review"]
                views = access["view_roles"]
                forwards = access["forward_roles"]
                if not isinstance(views, list) or not isinstance(forwards, list) or \
                        not views or any(not isinstance(role, str) or role not in ROLES
                                         for role in views + forwards):
                    raise ValueError("knowledge review invalid")
                fact_ok = fact["record_sha256"] == record_fingerprint(row)
                access_ok = access["record_sha256"] == access_fingerprint(row)
            except (KeyError, TypeError, ValueError):
                raise ValueError("knowledge review invalid") from None
            if not fact_ok or not access_ok or set(forwards) - set(views) or \
                    len(set(views)) != len(views) or len(set(forwards)) != len(forwards):
                raise ValueError("knowledge review invalid")
            if row.get("kind") == "link":
                review = row.get("link_review")
                if not isinstance(review, dict) or review.get("record_sha256") != \
                        record_fingerprint(row) or review.get("final_url") != row.get("data", {}).get("url"):
                    raise ValueError("knowledge link review invalid")
        if manifest.get("answerable_count") != answerable:
            raise ValueError("knowledge release answerable count invalid")
        if manifest["format_version"] == 2:
            from .section_release import validate_section_manifest
            validate_section_manifest(manifest)
        return cls(release_id, manifest)

    def records_for(self, role: str, *, for_delivery: bool = False) -> list[dict]:
        if role not in ROLES:
            raise ValueError("knowledge role invalid")
        visible = []
        for row in self._records:
            if not row["answerable"]:
                continue
            access = row["access_review"]
            if role not in access["view_roles"]:
                continue
            if (row["kind"] == "link" or for_delivery) and role not in access["forward_roles"]:
                continue
            visible.append(deepcopy(row))
        return visible

    def sections_for(self, role: str, *, for_delivery: bool = False) -> list[dict]:
        """Filter whole sections before retrieval; never expose a mixed paragraph."""
        if role not in ROLES:
            raise ValueError("knowledge role invalid")
        return [deepcopy(section) for section in self._sections
                if role in section["view_roles"]
                and (not for_delivery or role in section["forward_roles"])]
