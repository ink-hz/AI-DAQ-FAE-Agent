"""Shared official URL and link-type governance."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, urlsplit

_ORBBEC_DISCOVERY_HOSTS = frozenset(
    {
        "developer.orbbec.com.cn",
        "doc.orbbec.com",
        "github.com",
        "orbbec.com",
        "orbbec.github.io",
        "orbbec-debian-repos-aws.s3.amazonaws.com",
        "vcp.developer.orbbec.com.cn",
        "www.orbbec.com",
        "www.orbbec.com.cn",
    }
)
_PUBLISHER_HOSTS = {
    "Orbbec": _ORBBEC_DISCOVERY_HOSTS | {"store.orbbec.com"},
    "NVIDIA": frozenset({"docs.isaacsim.omniverse.nvidia.com"}),
}

_GITHUB_DOC_ROOTS = (
    re.compile(r"^/OrbbecSDK_v2/?$", re.IGNORECASE),
    re.compile(
        r"^/OrbbecSDK_v2/docs/tutorial/orbbec_camera_params\.html$",
        re.IGNORECASE,
    ),
    re.compile(r"^/docs/OrbbecSDKv2/index\.html$", re.IGNORECASE),
    re.compile(r"^/docs/OrbbecSDKv2_API_User_Guide/?$", re.IGNORECASE),
    re.compile(
        r"^/OrbbecSDK_ROS[12]/en/source/camera_devices/index\.html$",
        re.IGNORECASE,
    ),
)
_DOWNLOAD_SUFFIXES = (".bin", ".deb", ".exe", ".tar.gz", ".zip")
_CN_PRODUCT_PATH = "/index/Product/info.html"
_CN_GEMINI330_PATH = "/index/Gemini330/info.html"
_CN_DOWNLOAD_CENTER_PATH = "/index/Download2025/info.html"
_ORBBEC_ASSET_HOST = "orbbec-debian-repos-aws.s3.amazonaws.com"
_INTEGRATION_REPOS = frozenset({"cameramodel", "isaac_ros-dev"})
_LINK_TYPES = frozenset(
    {
        "download",
        "firmware_release",
        "integration_repo",
        "official_docs",
        "product_page",
        "sdk_repo",
    }
)
_MAX_PERCENT_DECODE_PASSES = 8
_INVALID_PERCENT_ESCAPE_RE = re.compile(r"%(?![0-9a-fA-F]{2})")


def _has_control(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _component_is_safe(value: str, *, check_traversal: bool = False) -> bool:
    if _INVALID_PERCENT_ESCAPE_RE.search(value):
        return False
    candidate = str(value or "")
    for _ in range(_MAX_PERCENT_DECODE_PASSES):
        if (
            _INVALID_PERCENT_ESCAPE_RE.search(candidate)
            or "\\" in candidate
            or _has_control(candidate)
        ):
            return False
        if check_traversal:
            segments = candidate.split("/")
            if any(segment in {".", ".."} for segment in segments):
                return False
        try:
            decoded = unquote(candidate, errors="strict")
        except UnicodeDecodeError:
            return False
        if _INVALID_PERCENT_ESCAPE_RE.search(decoded):
            return False
        if check_traversal and decoded.count("/") != candidate.count("/"):
            return False
        if "\\" in decoded or _has_control(decoded):
            return False
        if decoded == candidate:
            return True
        candidate = decoded
    return False


def _safe_https_parts(url: str, *, allowed_hosts: frozenset[str]):
    value = str(url or "")
    if (
        not value
        or value != value.strip()
        or _has_control(value)
        or "\\" in value
        or "#" in value
        or _INVALID_PERCENT_ESCAPE_RE.search(value)
    ):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    host = (parts.hostname or "").casefold()
    if parts.scheme.casefold() != "https" or host not in allowed_hosts:
        return None
    if "@" in parts.netloc or ":" in parts.netloc:
        return None
    if parts.netloc.casefold() != host:
        return None
    if "?" in value and not parts.query:
        return None
    if not _component_is_safe(parts.path, check_traversal=True):
        return None
    if not _component_is_safe(parts.query):
        return None
    return parts, host


def _has_exact_numeric_cn_query(query: str) -> bool:
    raw_pairs = query.split("&")
    if len(raw_pairs) != 2:
        return False
    pairs: list[tuple[str, str]] = []
    for raw_pair in raw_pairs:
        if raw_pair.count("=") != 1:
            return False
        key, value = raw_pair.split("=", maxsplit=1)
        if key not in {"cate", "id"} or not re.fullmatch(r"[0-9]+", value):
            return False
        pairs.append((key, value))
    return {key for key, _ in pairs} == {"cate", "id"}


def _is_cn_upload(path: str) -> bool:
    return (
        path.startswith("/uploads/")
        and path != "/uploads/"
        and "//" not in path
    )


def _has_download_suffix(path: str) -> bool:
    return path.casefold().endswith(_DOWNLOAD_SUFFIXES)


def _classify_cn_parts(parts) -> str | None:
    path = parts.path or "/"
    if path == _CN_PRODUCT_PATH and _has_exact_numeric_cn_query(parts.query):
        return "product_page"
    if path == _CN_GEMINI330_PATH and _has_exact_numeric_cn_query(parts.query):
        return "official_docs"
    if path == _CN_DOWNLOAD_CENTER_PATH and _has_exact_numeric_cn_query(parts.query):
        return "official_docs"
    if parts.query or not _is_cn_upload(path):
        return None
    if path.endswith(".pdf"):
        return "official_docs"
    if path.endswith(_DOWNLOAD_SUFFIXES):
        return "download"
    return None


def _is_promotable_orbbec_parts(parts, host: str) -> bool:
    path = parts.path or "/"

    if host == "github.com":
        segments = [segment for segment in path.split("/") if segment]
        return len(segments) >= 2 and segments[0].casefold() == "orbbec"
    if host in {
        "orbbec.com",
        "www.orbbec.com",
        "store.orbbec.com",
        "doc.orbbec.com",
    }:
        return True
    if host == "orbbec.github.io":
        return any(pattern.fullmatch(path) for pattern in _GITHUB_DOC_ROOTS)
    if host == "developer.orbbec.com.cn":
        return not parts.query and path.rstrip("/") in {"", "/download.html"}
    if host == "vcp.developer.orbbec.com.cn":
        normalized = path.rstrip("/")
        return not parts.query and (
            normalized == "/resourceCenter"
            or normalized.startswith("/resourceCenter/")
        )
    if host == "www.orbbec.com.cn":
        return _classify_cn_parts(parts) is not None
    if host == _ORBBEC_ASSET_HOST:
        return (
            not parts.query
            and path.startswith("/product/")
            and path != "/product/"
            and "//" not in path
            and (_has_download_suffix(path) or path.casefold().endswith(".pdf"))
        )
    return False


def _classify_orbbec_parts(parts, host: str) -> str | None:
    path = parts.path or "/"
    path_key = path.casefold()

    if host == "www.orbbec.com.cn":
        return _classify_cn_parts(parts)
    if host == _ORBBEC_ASSET_HOST:
        if not _is_promotable_orbbec_parts(parts, host):
            return None
        return "official_docs" if path.casefold().endswith(".pdf") else "download"
    if host == "github.com":
        if parts.query:
            return None
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 2 or segments[0].casefold() != "orbbec":
            return None
        repo_key = segments[1].casefold()
        if (
            len(segments) >= 5
            and segments[2].casefold() == "releases"
            and segments[3].casefold() == "download"
            and _has_download_suffix(path)
        ):
            return "download"
        if len(segments) >= 3 and segments[2].casefold() == "releases":
            return "firmware_release"
        if len(segments) != 2:
            return None
        if repo_key in _INTEGRATION_REPOS:
            return "integration_repo"
        if "firmware" in repo_key:
            return "firmware_release"
        return "sdk_repo"
    if host in {"doc.orbbec.com", "orbbec.github.io"}:
        return "official_docs"
    if host in {"orbbec.com", "www.orbbec.com", "store.orbbec.com"}:
        if _has_download_suffix(path):
            return "download"
        if "firmware" in path_key or "release" in path_key:
            return "firmware_release"
        if "/products/" in path_key or re.fullmatch(r"/[a-z0-9-]+/?", path_key):
            return "product_page"
    return None


def is_official_discovery_url(url: str) -> bool:
    """Return whether a URL belongs to an approved Orbbec discovery host."""

    parsed = _safe_https_parts(url, allowed_hosts=_ORBBEC_DISCOVERY_HOSTS)
    if parsed is None:
        return False
    parts, host = parsed
    if host == "www.orbbec.com.cn":
        return _classify_cn_parts(parts) is not None
    return True


def is_promotable_official_url(url: str) -> bool:
    """Return whether an Orbbec URL is inside the formal path/query policy."""

    parsed = _safe_https_parts(url, allowed_hosts=_PUBLISHER_HOSTS["Orbbec"])
    if parsed is None:
        return False
    parts, host = parsed
    return _is_promotable_orbbec_parts(parts, host)


def is_promotable_publisher_url(url: str, *, publisher: str) -> bool:
    """Validate a formal URL against its exact declared publisher boundary."""

    publisher_key = str(publisher or "").strip()
    allowed_hosts = _PUBLISHER_HOSTS.get(publisher_key)
    if allowed_hosts is None:
        return False
    parsed = _safe_https_parts(url, allowed_hosts=allowed_hosts)
    if parsed is None:
        return False
    if publisher_key == "Orbbec":
        parts, host = parsed
        return _is_promotable_orbbec_parts(parts, host)
    return True


def classify_promotable_official_url(
    url: str,
    *,
    publisher: str = "Orbbec",
) -> str | None:
    """Return the single approved link type for a formal URL, if any."""

    publisher_key = str(publisher or "").strip()
    allowed_hosts = _PUBLISHER_HOSTS.get(publisher_key)
    if allowed_hosts is None:
        return None
    parsed = _safe_https_parts(url, allowed_hosts=allowed_hosts)
    if parsed is None:
        return None
    parts, host = parsed
    if publisher_key == "NVIDIA":
        return "official_docs"
    if not _is_promotable_orbbec_parts(parts, host):
        return None
    return _classify_orbbec_parts(parts, host)


def validate_official_url_for_link_type(
    url: str,
    *,
    link_type: str,
    publisher: str = "Orbbec",
) -> None:
    """Raise ``ValueError`` unless URL, publisher, and link type agree."""

    type_key = str(link_type or "").strip()
    if type_key not in _LINK_TYPES:
        raise ValueError(f"official link has unsupported link_type={link_type!r}")

    value = str(url or "")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ValueError(f"official link URL is invalid: {url}") from exc
    if parts.scheme.casefold() != "https":
        raise ValueError(f"official link must use HTTPS: {url}")
    if not is_promotable_publisher_url(value, publisher=publisher):
        host = (parts.hostname or "").casefold()
        raise ValueError(
            f"official link publisher/URL allowlist mismatch for {publisher}: {host}"
        )

    actual_type = classify_promotable_official_url(value, publisher=publisher)
    if actual_type is None:
        raise ValueError(f"official URL has no approved link type: {url}")
    if type_key != actual_type:
        raise ValueError(
            f"official URL requires link_type={actual_type}, got {type_key}: {url}"
        )


__all__ = [
    "classify_promotable_official_url",
    "is_official_discovery_url",
    "is_promotable_official_url",
    "is_promotable_publisher_url",
    "validate_official_url_for_link_type",
]
