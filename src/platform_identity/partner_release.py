"""Fail-closed production gate for the Platform Partner login entry point."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

CONTRACT_VERSION = "orbbec-fae-partner-provider/v1"
PARTNER_AUTH_START_URL = "https://agent.orbbec.com.cn/partner-auth/start"
THRESHOLD_FIELDS = (
    "stable_subject_verified",
    "two_distinct_subjects_verified",
    "active_status_or_local_revocation_verified",
    "shared_password_forbidden",
    "state_and_callback_replay_verified",
)
RELEASE_FIELDS = frozenset(
    {
        "contract_version",
        "provider_kind",
        "dev_real_account_tested_at",
        "evidence_sha256",
        *THRESHOLD_FIELDS,
    }
)
MAX_RELEASE_BYTES = 4096
MAX_EVIDENCE_AGE_DAYS = 180

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROVIDER_KIND = re.compile(r"[a-z][a-z0-9_-]{0,127}\Z")
_RFC3339 = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})\Z"
)


@dataclass(frozen=True)
class PartnerLoginGate:
    """Sanitized result consumed by Config and the public capability route."""

    start_url: str | None
    reason: str


def _reject(reason: str) -> PartnerLoginGate:
    return PartnerLoginGate(start_url=None, reason=reason)


def _release_metadata_is_secure(metadata: os.stat_result) -> bool:
    """Accept a private service-owned file or a root-owned service-group copy."""
    mode = stat.S_IMODE(metadata.st_mode)
    effective_uid = os.getuid()
    service_groups = {os.getgid(), *os.getgroups()}
    if metadata.st_uid == 0 and metadata.st_gid in service_groups:
        if bool(mode & 0o040) and mode & 0o137 == 0:
            return True
    if metadata.st_uid == effective_uid:
        return mode & 0o177 == 0
    if metadata.st_uid != 0 or effective_uid == 0:
        return False
    return (
        metadata.st_gid in service_groups
        and bool(mode & 0o040)
        and mode & 0o137 == 0
    )


def _read_release(path_value: str | None) -> tuple[bytes | None, str | None]:
    if path_value is None or not path_value.strip():
        return None, "partner_release_required"
    path = Path(path_value.strip())
    if not path.is_absolute():
        return None, "partner_release_insecure"
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow == 0:  # pragma: no cover - production is Linux
        return None, "partner_release_insecure"
    non_blocking = getattr(os, "O_NONBLOCK", 0)
    if non_blocking == 0:  # pragma: no cover - production is Linux
        return None, "partner_release_insecure"
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | no_follow | non_blocking | getattr(os, "O_CLOEXEC", 0),
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return None, "partner_release_insecure"
        if not _release_metadata_is_secure(metadata):
            return None, "partner_release_insecure"
        if metadata.st_size > MAX_RELEASE_BYTES:
            return None, "partner_release_insecure"
        chunks: list[bytes] = []
        remaining = MAX_RELEASE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
    except OSError:
        return None, "partner_release_insecure"
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(body) > MAX_RELEASE_BYTES:
        return None, "partner_release_insecure"
    return body, None


def _parse_tested_at(value: object, now: datetime) -> str | None:
    if not isinstance(value, str) or _RFC3339.fullmatch(value) is None:
        return "partner_release_malformed"
    try:
        tested_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "partner_release_malformed"
    if tested_at.tzinfo is None:
        return "partner_release_malformed"
    if tested_at > now or now - tested_at > timedelta(days=MAX_EVIDENCE_AGE_DAYS):
        return "partner_release_stale"
    return None


def resolve_partner_login(
    *,
    start_url: str | None,
    provider_kind: str | None,
    release_file: str | None,
    release_sha256: str | None,
    now: datetime | None = None,
) -> PartnerLoginGate:
    """Return the only production-safe Partner login URL, or a stable reason."""

    if start_url is None:
        return _reject("partner_login_not_configured")
    if start_url != PARTNER_AUTH_START_URL:
        return _reject("partner_auth_start_url_invalid")
    if (
        not isinstance(provider_kind, str)
        or _PROVIDER_KIND.fullmatch(provider_kind) is None
    ):
        return _reject("partner_provider_kind_required")
    if provider_kind == "reference":
        return _reject("partner_reference_provider_forbidden")

    body, error = _read_release(release_file)
    if error is not None or body is None:
        return _reject(error or "partner_release_insecure")
    if not isinstance(release_sha256, str) or _SHA256.fullmatch(release_sha256) is None:
        return _reject("partner_release_digest_invalid")
    if sha256(body).hexdigest() != release_sha256:
        return _reject("partner_release_digest_mismatch")

    try:
        document = json.loads(body)
    except (RecursionError, UnicodeDecodeError, ValueError):
        return _reject("partner_release_malformed")
    if not isinstance(document, dict) or set(document) != set(RELEASE_FIELDS):
        return _reject("partner_release_malformed")
    if document["contract_version"] != CONTRACT_VERSION:
        return _reject("partner_release_contract_mismatch")

    released_kind = document["provider_kind"]
    if not isinstance(released_kind, str) or _PROVIDER_KIND.fullmatch(released_kind) is None:
        return _reject("partner_release_malformed")
    if released_kind == "reference":
        return _reject("partner_reference_provider_forbidden")
    if provider_kind != released_kind:
        return _reject("partner_provider_kind_mismatch")
    for field in THRESHOLD_FIELDS:
        value = document[field]
        if value is True:
            continue
        return _reject(
            "partner_release_threshold_unmet"
            if isinstance(value, bool)
            else "partner_release_malformed"
        )
    evidence_digest = document["evidence_sha256"]
    if not isinstance(evidence_digest, str) or _SHA256.fullmatch(evidence_digest) is None:
        return _reject("partner_release_malformed")
    tested_at_error = _parse_tested_at(
        document["dev_real_account_tested_at"], now or datetime.now(UTC)
    )
    if tested_at_error is not None:
        return _reject(tested_at_error)
    return PartnerLoginGate(
        start_url=PARTNER_AUTH_START_URL,
        reason="partner_release_validated",
    )
