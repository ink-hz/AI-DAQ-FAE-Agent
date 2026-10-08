from __future__ import annotations

from dataclasses import asdict, dataclass

from src.agent.official_links import extract_literal_https_urls
from src.agent.schema import RequestSchema


@dataclass(frozen=True)
class LinkDeliveryRequirement:
    id: str
    product_page_required: bool
    integration_entry_required: bool
    integration_terms: tuple[str, ...]

    def to_payload(self) -> dict:
        payload = asdict(self)
        payload["integration_terms"] = list(self.integration_terms)
        return payload


def derive_link_delivery_requirements(schema: RequestSchema) -> list[dict]:
    if schema.intent != "selection" or not schema.platforms:
        return []
    requirement = LinkDeliveryRequirement(
        id="selection_platform_delivery",
        product_page_required=True,
        integration_entry_required=True,
        integration_terms=tuple(schema.platforms),
    )
    return [requirement.to_payload()]


def verified_urls_by_link_type(value: object) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    if isinstance(value, dict):
        link_type = str(value.get("link_type") or "")
        if link_type:
            for key in ("url", "repo_url"):
                url = str(value.get(key) or "")
                if url.startswith("https://"):
                    grouped.setdefault(link_type, set()).add(url)
        for child in value.values():
            for kind, urls in verified_urls_by_link_type(child).items():
                grouped.setdefault(kind, set()).update(urls)
    elif isinstance(value, list):
        for child in value:
            for kind, urls in verified_urls_by_link_type(child).items():
                grouped.setdefault(kind, set()).update(urls)
    return grouped


def missing_link_deliveries(
    answer: str,
    requirements: list[dict],
    urls_by_type: dict[str, set[str]],
) -> list[str]:
    if not requirements:
        return []
    answer_urls = set(extract_literal_https_urls(answer))
    missing: list[str] = []
    if any(row.get("product_page_required") for row in requirements):
        if not answer_urls.intersection(urls_by_type.get("product_page", set())):
            missing.append("product_page")
    if any(row.get("integration_entry_required") for row in requirements):
        integration_urls = set(urls_by_type.get("sdk_repo", set()))
        integration_urls.update(urls_by_type.get("official_docs", set()))
        integration_urls.update(urls_by_type.get("integration_repo", set()))
        if not answer_urls.intersection(integration_urls):
            missing.append("integration_entry")
    return missing
