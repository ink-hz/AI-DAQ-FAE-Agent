"""Strict runtime index for browser-verified official Orbbec links."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from src.official_url_policy import validate_official_url_for_link_type

LINK_TYPES = {
    "sdk_repo",
    "firmware_release",
    "download",
    "official_docs",
    "product_page",
    "integration_repo",
}
STATUSES = {"verified", "needs_review", "stale", "retired", "rejected"}
APPLICABILITY_SCOPES = {"general_entry"}
ASSET_KINDS = {"cad"}
ASSET_QUERY_MARKERS = {
    "cad": (
        "cad", "step", "stp", "dwg", "3d数模", "3d模型", "数模", "结构模型", "机械图纸",
    ),
}
VERIFICATION_REF_RE = re.compile(r"^verify-([0-9a-f]{12})-([0-9]{8})$")
CONTENT_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MIME_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
MIME_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+$")
TOKEN_RE = re.compile(r"[a-z0-9_+.#-]+|[\u3400-\u9fff]+", re.IGNORECASE)
LITERAL_HTTPS_URL_RE = re.compile(
    r"https://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+"
)
GENERIC_LINK_TOKENS = {
    "api", "binding", "camera", "clone", "device", "download", "firmware",
    "link", "pipeline", "plugin", "repo", "repository", "sdk", "start", "url",
    "viewer", "wrapper", "下载", "产品页", "仓库", "固件", "官网", "官方",
    "手册", "接入", "接入入口", "插件", "文档", "链接", "入口",
}


def extract_literal_https_urls(text: str) -> list[str]:
    """Extract literal ASCII HTTPS URLs without swallowing Markdown/CJK prose."""

    urls: list[str] = []
    seen: set[str] = set()
    for raw in LITERAL_HTTPS_URL_RE.findall(str(text or "")):
        url = raw.rstrip(".,;:!?")
        for opener, closer in (("(", ")"), ("[", "]")):
            while url.endswith(closer) and url.count(closer) > url.count(opener):
                url = url[:-1]
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


@dataclass(frozen=True)
class OfficialLinkApplicability:
    scope: str
    boundary: str


@dataclass(frozen=True)
class OfficialLink:
    id: str
    link_type: str
    title: str
    url: str
    publisher: str = "Orbbec"
    repo: str = ""
    sdk_layers: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    status: str = "needs_review"
    verified_at: str = ""
    verified_by: str = ""
    verification_ref: str = ""
    applicability: OfficialLinkApplicability | None = None
    content_type: str = ""
    content_sha256: str = ""
    discovery_parent_url: str = ""
    asset_kind: str = ""


class OfficialLinkCatalog:
    """Strict immutable index loaded only from reviewed official-link facts."""

    def __init__(self, links: tuple[OfficialLink, ...]) -> None:
        self._links = tuple(link for link in links if link.status == "verified")

    @property
    def verified_count(self) -> int:
        return len(self._links)

    @property
    def links(self) -> tuple[OfficialLink, ...]:
        return self._links

    @classmethod
    def load(cls, root: Path) -> "OfficialLinkCatalog":
        root = Path(root)
        catalog_path = root / "official_links.yaml"
        audit_path = root / "audit" / "verification_records.jsonl"
        if not catalog_path.is_file():
            raise ValueError(f"official link catalog is missing: {catalog_path}")
        if not audit_path.is_file():
            raise ValueError(f"verification records are missing: {audit_path}")

        try:
            payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"invalid official link catalog: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("official link catalog requires version: 1")
        raw_links = payload.get("links")
        if not isinstance(raw_links, list):
            raise ValueError("official link catalog links must be a list")

        verification_records = _load_verification_records(audit_path)
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        links: list[OfficialLink] = []
        for index, raw_link in enumerate(raw_links, start=1):
            if not isinstance(raw_link, dict):
                raise ValueError(f"link row {index} must be an object")
            link = _parse_link(raw_link, row_number=index)
            if link.id in seen_ids:
                raise ValueError(f"duplicate id: {link.id}")
            canonical_key = link.url.casefold()
            if canonical_key in seen_urls:
                raise ValueError(f"duplicate canonical URL: {link.url}")
            seen_ids.add(link.id)
            seen_urls.add(canonical_key)
            _validate_source_refs(root.parent, link)
            _validate_verification(link, verification_records)
            links.append(link)
        return cls(tuple(links))

    def search(
        self,
        query: str,
        model_id: str | None = None,
        link_types: tuple[str, ...] = (),
        limit: int = 5,
    ) -> list[OfficialLink]:
        if limit <= 0:
            return []
        requested_types = {value.casefold() for value in link_types}
        model_key = (model_id or "").casefold()
        query_key = str(query or "").casefold().strip()
        query_tokens = TOKEN_RE.findall(query_key)
        specific_tokens = [
            token for token in query_tokens if token not in GENERIC_LINK_TOKENS
        ]
        requested_asset_kinds = {
            asset_kind
            for asset_kind, markers in ASSET_QUERY_MARKERS.items()
            if any(marker in query_key for marker in markers)
        }
        scored: list[tuple[int, str, OfficialLink]] = []
        for link in self._links:
            if requested_types and link.link_type.casefold() not in requested_types:
                continue
            if requested_asset_kinds and link.asset_kind not in requested_asset_kinds:
                continue
            model_values = {value.casefold() for value in link.models}
            if model_key and model_values and model_key not in model_values:
                continue
            fields = (
                link.title,
                link.repo,
                *link.sdk_layers,
                *link.models,
                *link.keywords,
            )
            haystack = " ".join(fields).casefold()
            # 通用词（如 SDK/下载）不能覆盖未命中的具体产品或 SDK 层，
            # 否则不存在的交付目标会拿到一个貌似合理但无关的 URL。
            if (
                not model_key
                and specific_tokens
                and not any(token in haystack for token in specific_tokens)
            ):
                continue
            score = 0
            if model_key and model_key in model_values:
                score += 100
            if query_key and query_key in haystack:
                score += 50
            score += sum(10 for token in query_tokens if token and token in haystack)
            if not query_key and not model_key:
                score = 1
            if score > 0:
                scored.append((score, link.id, link))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[:limit]]

    def repo_url(
        self,
        repo: str,
        sdk_layers: tuple[str, ...] = (),
    ) -> str | None:
        repo_key = str(repo or "").casefold()
        layer_keys = {layer.casefold() for layer in sdk_layers}
        for link in sorted(self._links, key=lambda row: row.id):
            if link.link_type != "sdk_repo" or link.repo.casefold() != repo_key:
                continue
            link_layers = {layer.casefold() for layer in link.sdk_layers}
            if layer_keys and not layer_keys.intersection(link_layers):
                continue
            return link.url
        return None


def validate_catalog(root: Path) -> OfficialLinkCatalog:
    """Validate the complete checked-in contract and return its runtime index."""

    return OfficialLinkCatalog.load(root)


def _load_verification_records(path: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid verification JSONL line {line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"verification line {line_number} must be an object")
        ref = str(row.get("verification_ref") or "")
        if not ref:
            raise ValueError(f"verification line {line_number} has no verification_ref")
        if ref in records:
            raise ValueError(f"duplicate verification_ref: {ref}")
        records[ref] = row
    return records


def _parse_link(raw: dict[str, object], *, row_number: int) -> OfficialLink:
    def required_text(field: str) -> str:
        value = str(raw.get(field) or "").strip()
        if not value:
            raise ValueError(f"link row {row_number} requires {field}")
        return value

    def text_tuple(field: str) -> tuple[str, ...]:
        value = raw.get(field, [])
        if not isinstance(value, list) or any(not str(item).strip() for item in value):
            raise ValueError(f"link row {row_number} {field} must be a string list")
        return tuple(str(item).strip() for item in value)

    link_type = required_text("link_type")
    if link_type not in LINK_TYPES:
        raise ValueError(f"link row {row_number} has invalid link_type: {link_type}")
    status = required_text("status")
    if status not in STATUSES:
        raise ValueError(f"link row {row_number} has invalid status: {status}")
    url = required_text("url")
    publisher = str(raw.get("publisher") or "Orbbec").strip()
    validate_official_url_for_link_type(
        url,
        link_type=link_type,
        publisher=publisher,
    )
    source_refs = text_tuple("source_refs")
    if not source_refs:
        raise ValueError(f"link row {row_number} requires source_refs")
    models = text_tuple("models")
    if link_type == "product_page" and not models:
        raise ValueError(f"link row {row_number} product_page requires models")
    applicability = _parse_applicability(
        raw,
        row_number=row_number,
        models=models,
    )
    content_type = _optional_declared_text(
        raw,
        field="content_type",
        row_number=row_number,
    )
    if content_type:
        _validate_content_type(
            url,
            link_type=link_type,
            content_type=content_type,
            row_number=row_number,
        )
    content_sha256 = _optional_declared_text(
        raw,
        field="content_sha256",
        row_number=row_number,
    )
    if content_sha256 and CONTENT_SHA256_RE.fullmatch(content_sha256) is None:
        raise ValueError(
            f"link row {row_number} content_sha256 must be 64 lowercase hex"
        )

    discovery_parent_url = _optional_declared_text(
        raw,
        field="discovery_parent_url",
        row_number=row_number,
    )
    asset_kind = str(raw.get("asset_kind") or "").strip()
    if asset_kind and asset_kind not in ASSET_KINDS:
        raise ValueError(f"link row {row_number} asset_kind is invalid")
    if asset_kind and link_type not in {"download", "official_docs"}:
        raise ValueError(
            f"link row {row_number} asset_kind requires download or official_docs"
        )
    asset_host = (urlsplit(url).hostname or "").casefold()
    requires_parent = asset_host == "orbbec-debian-repos-aws.s3.amazonaws.com"
    if requires_parent and not discovery_parent_url:
        raise ValueError(
            f"link row {row_number} discovery_parent_url is required"
        )
    if discovery_parent_url:
        try:
            validate_official_url_for_link_type(
                discovery_parent_url,
                link_type="official_docs",
                publisher=publisher,
            )
        except ValueError as exc:
            raise ValueError(
                f"link row {row_number} discovery_parent_url is invalid"
            ) from exc

    verified_at = str(raw.get("verified_at") or "").strip()
    verified_by = str(raw.get("verified_by") or "").strip()
    verification_ref = required_text("verification_ref")
    if status == "verified" and not verified_at:
        raise ValueError(f"link row {row_number} verified_at is required")
    if status == "verified" and not verified_by:
        raise ValueError(f"link row {row_number} verified_by is required")

    return OfficialLink(
        id=required_text("id"),
        link_type=link_type,
        title=required_text("title"),
        url=url,
        publisher=publisher,
        repo=str(raw.get("repo") or "").strip(),
        sdk_layers=text_tuple("sdk_layers"),
        models=models,
        keywords=text_tuple("keywords"),
        source_refs=source_refs,
        status=status,
        verified_at=verified_at,
        verified_by=verified_by,
        verification_ref=verification_ref,
        applicability=applicability,
        content_type=content_type,
        content_sha256=content_sha256,
        discovery_parent_url=discovery_parent_url,
        asset_kind=asset_kind,
    )


def _optional_declared_text(
    raw: dict[str, object],
    *,
    field: str,
    row_number: int,
) -> str:
    if field not in raw:
        return ""
    value = raw[field]
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"link row {row_number} {field} must be non-empty text")
    return value


def _parse_applicability(
    raw: dict[str, object],
    *,
    row_number: int,
    models: tuple[str, ...],
) -> OfficialLinkApplicability | None:
    if "applicability" not in raw:
        return None
    value = raw["applicability"]
    if not isinstance(value, dict):
        raise ValueError(f"link row {row_number} applicability must be a mapping")
    if set(value) != {"scope", "boundary"}:
        raise ValueError(
            f"link row {row_number} applicability requires only scope and boundary"
        )
    scope = value.get("scope")
    boundary = value.get("boundary")
    if not isinstance(scope, str) or scope not in APPLICABILITY_SCOPES:
        raise ValueError(f"link row {row_number} applicability scope is invalid")
    if not isinstance(boundary, str) or not boundary.strip():
        raise ValueError(f"link row {row_number} applicability boundary is required")
    if scope == "general_entry" and models:
        raise ValueError(
            f"link row {row_number} general_entry applicability requires models=[]"
        )
    return OfficialLinkApplicability(scope=scope, boundary=boundary.strip())


def _validate_content_type(
    url: str,
    *,
    link_type: str,
    content_type: str,
    row_number: int,
) -> None:
    components = content_type.split(";")
    base_type = components[0].strip()
    if MIME_TYPE_RE.fullmatch(base_type) is None:
        raise ValueError(f"link row {row_number} content_type is invalid")
    for component in components[1:]:
        parameter = component.strip()
        if parameter.count("=") != 1:
            raise ValueError(f"link row {row_number} content_type is invalid")
        name, value = parameter.split("=", maxsplit=1)
        if MIME_PARAMETER_NAME_RE.fullmatch(name) is None or not value.strip():
            raise ValueError(f"link row {row_number} content_type is invalid")
    path = urlsplit(url).path.casefold()
    expected_type = ""
    if path.endswith(".pdf"):
        expected_type = "application/pdf"
    elif link_type == "product_page" or path.endswith("/info.html"):
        expected_type = "text/html"
    if expected_type and base_type.casefold() != expected_type:
        raise ValueError(
            f"link row {row_number} content_type must be {expected_type}"
        )
    if expected_type == "application/pdf" and len(components) != 1:
        raise ValueError(
            f"link row {row_number} content_type must be application/pdf"
        )


def _validate_source_refs(source_root: Path, link: OfficialLink) -> None:
    source_root = source_root.resolve()
    for source_ref in link.source_refs:
        relative = Path(source_ref)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"invalid source_refs path for {link.id}: {source_ref}")
        resolved = (source_root / relative).resolve()
        if not resolved.is_relative_to(source_root) or not resolved.is_file():
            raise ValueError(f"missing source_refs path for {link.id}: {source_ref}")


def _validate_verification(
    link: OfficialLink,
    records: dict[str, dict[str, object]],
) -> None:
    digest = hashlib.sha256(link.url.encode("utf-8")).hexdigest()[:12]
    ref_match = VERIFICATION_REF_RE.fullmatch(link.verification_ref)
    if not ref_match or ref_match.group(1) != digest:
        raise ValueError(f"invalid verification_ref for {link.id}: {link.verification_ref}")
    record = records.get(link.verification_ref)
    if record is None:
        raise ValueError(f"missing verification_ref for {link.id}: {link.verification_ref}")
    if record.get("requested_url") != link.url:
        raise ValueError(f"verification requested_url mismatch for {link.id}")
    if link.content_type:
        verification_content_type = record.get("content_type")
        if verification_content_type != link.content_type:
            raise ValueError(f"verification content_type mismatch for {link.id}")
    if link.content_sha256:
        verification_sha256 = record.get("content_sha256")
        if (
            not isinstance(verification_sha256, str)
            or CONTENT_SHA256_RE.fullmatch(verification_sha256) is None
            or verification_sha256 != link.content_sha256
        ):
            raise ValueError(f"verification content_sha256 mismatch for {link.id}")
    elif "content_sha256" in record:
        raise ValueError(
            f"verification content_sha256 has no catalog match for {link.id}"
        )
    if link.discovery_parent_url:
        if record.get("discovery_parent_url") != link.discovery_parent_url:
            raise ValueError(
                f"verification discovery_parent_url mismatch for {link.id}"
            )
    elif "discovery_parent_url" in record:
        raise ValueError(
            f"verification discovery_parent_url has no catalog match for {link.id}"
        )
    decision = str(record.get("decision") or "")
    if decision != link.status:
        raise ValueError(f"verification decision mismatch for {link.id}")
    if not str(record.get("page_title") or "").strip():
        raise ValueError(f"verification page_title is required for {link.id}")
    if not str(record.get("opened_at") or "").strip():
        raise ValueError(f"verification opened_at is required for {link.id}")
    if not str(record.get("reviewer") or "").strip():
        raise ValueError(f"verification reviewer is required for {link.id}")
    if not str(record.get("notes") or "").strip():
        raise ValueError(f"verification notes are required for {link.id}")
    final_url = str(record.get("final_url") or "")
    validate_official_url_for_link_type(
        final_url,
        link_type=link.link_type,
        publisher=link.publisher,
    )
    if link.status != "verified":
        return
    if record.get("domain_verified") is not True:
        raise ValueError(f"domain_verified must be true for {link.id}")
    if record.get("semantic_match") is not True:
        raise ValueError(f"semantic_match must be true for {link.id}")
    if record.get("download_or_content_available") is not True:
        raise ValueError(f"download_or_content_available must be true for {link.id}")
    if record.get("opened_at") != link.verified_at:
        raise ValueError(f"verified_at mismatch for {link.id}")
    if record.get("reviewer") != link.verified_by:
        raise ValueError(f"verified_by mismatch for {link.id}")
    http_status = record.get("http_status")
    if not isinstance(http_status, int) or not 200 <= http_status < 400:
        raise ValueError(f"verified http_status is invalid for {link.id}")
