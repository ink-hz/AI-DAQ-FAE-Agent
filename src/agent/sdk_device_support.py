"""Strict, branch-aware device support evidence for SDK and wrappers.

Product facts answer what a device is.  This matrix answers whether a named
software surface/implementation/branch supports it, plus separately sourced
firmware, launch, platform, and configuration defaults.  Missing records stay
unknown; broad queries return all matching facets and are never folded into a
single boolean.
"""
from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from src.facts.resolver import ModelResolver
from src.official_url_policy import is_promotable_publisher_url

SUPPORT_STATES = {
    "supported",
    "recommended_for_new_designs",
    "not_supported",
    "unknown",
}
ROSTER_ENTRY_STATES = {"mapped", "pending", "informational"}
EXHAUSTIVE_ROSTER_COVERAGE = "exhaustive_supported_hardware_list"
_UNKNOWN = "unknown"


class DeviceSupportError(ValueError):
    pass


def _parse_date(value: object, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DeviceSupportError(f"{field}: invalid date {value!r}") from exc


def _official_source(raw: object, field: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise DeviceSupportError(f"{field}: source must be a mapping")
    source = {
        "url": str(raw.get("url") or ""),
        "ref": str(raw.get("ref") or "").strip(),
        "path": str(raw.get("path") or "").strip(),
    }
    if not all(source.values()):
        raise DeviceSupportError(f"{field}: source requires url/ref/path")
    if not is_promotable_publisher_url(source["url"], publisher="Orbbec"):
        raise DeviceSupportError(f"{field}: source must be an official Orbbec GitHub URL")
    parsed = urlsplit(source["url"])
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != "github.com"
        or not parsed.path.casefold().startswith("/orbbec/")
    ):
        raise DeviceSupportError(f"{field}: source must be an official Orbbec GitHub URL")
    commit = str(raw.get("commit") or "").strip()
    if commit:
        source["commit"] = commit
    return source


def _official_roster_source(raw: object, field: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise DeviceSupportError(f"{field}: source must be a mapping")
    source = {
        "url": str(raw.get("url") or ""),
        "ref": str(raw.get("ref") or "").strip(),
        "path": str(raw.get("path") or "").strip(),
        "commit": str(raw.get("commit") or "").strip(),
    }
    if not all(source.values()):
        raise DeviceSupportError(f"{field}: source requires url/ref/path/commit")
    if not is_promotable_publisher_url(source["url"], publisher="Orbbec"):
        raise DeviceSupportError(f"{field}: source must be an official Orbbec URL")
    parsed = urlsplit(source["url"])
    hostname = (parsed.hostname or "").casefold()
    github_owned = (
        hostname == "github.com"
        and parsed.path.casefold().startswith("/orbbec/")
    )
    if parsed.scheme != "https" or not github_owned:
        raise DeviceSupportError(f"{field}: source must be an official Orbbec URL")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source["commit"]):
        raise DeviceSupportError(f"{field}: commit must be a pinned 40-character SHA")
    return source


def _optional_bool(value: object, field: str) -> bool | None:
    if value is None or str(value).casefold() == _UNKNOWN:
        return None
    if isinstance(value, bool):
        return value
    raise DeviceSupportError(f"{field}: expected true/false/unknown")


@dataclass(frozen=True)
class DeviceConfiguration:
    capability: str
    supported: bool | None
    default_enabled: bool | None
    parameter: str = ""
    activation: str = ""
    default_mode: str = ""
    source: dict[str, str] | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "capability": self.capability,
            "supported": self.supported if self.supported is not None else _UNKNOWN,
            "default_enabled": (
                self.default_enabled
                if self.default_enabled is not None
                else _UNKNOWN
            ),
        }
        for key, value in (
            ("parameter", self.parameter),
            ("activation", self.activation),
            ("default_mode", self.default_mode),
        ):
            if value:
                payload[key] = value
        if self.source:
            payload["source"] = dict(self.source)
        return payload


@dataclass(frozen=True)
class DeviceSupportRecord:
    model: str
    surface: str
    implementation: str
    branch: str
    support_state: str
    verified_at: date
    source: dict[str, str]
    platforms: tuple[str, ...] = ()
    firmware: dict[str, str] | None = None
    launch: str = ""
    configurations: tuple[DeviceConfiguration, ...] = ()
    maintenance_level: str = ""

    def configuration(self, capability: str) -> DeviceConfiguration | None:
        key = str(capability).casefold()
        return next(
            (row for row in self.configurations if row.capability.casefold() == key),
            None,
        )

    def to_payload(
        self,
        *,
        as_of: date | None = None,
        freshness_days: int = 120,
    ) -> dict[str, object]:
        today = as_of or date.today()
        age_days = (today - self.verified_at).days
        payload: dict[str, object] = {
            "model": self.model,
            "surface": self.surface,
            "implementation": self.implementation,
            "branch": self.branch,
            "support_state": self.support_state,
            "verified_at": self.verified_at.isoformat(),
            "evidence_state": "stale" if age_days > freshness_days else "current",
            "age_days": age_days,
            "source": dict(self.source),
            "platforms": list(self.platforms),
            "configuration": [row.to_payload() for row in self.configurations],
        }
        if self.firmware:
            payload["firmware"] = dict(self.firmware)
        if self.launch:
            payload["launch"] = self.launch
        if self.maintenance_level:
            payload["maintenance_level"] = self.maintenance_level
        return payload


@dataclass(frozen=True)
class SupportRosterEntry:
    official_name: str
    entity_kind: str
    status: str
    model_ids: tuple[str, ...] = ()
    upstream_fields: dict[str, str] | None = None
    reason: str = ""

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "official_name": self.official_name,
            "entity_kind": self.entity_kind,
            "status": self.status,
            "model_ids": list(self.model_ids),
        }
        if self.upstream_fields:
            payload["upstream_fields"] = dict(self.upstream_fields)
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class SupportRoster:
    surface: str
    implementation: str
    branch: str
    coverage: str
    verified_at: date
    entries: tuple[SupportRosterEntry, ...]
    source: dict[str, str]


@dataclass(frozen=True)
class DeviceSupportRosterEvidence:
    surface: str
    implementation: str
    branch: str
    coverage: str
    verified_at: date
    source: dict[str, str]
    entries: tuple[SupportRosterEntry, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "surface": self.surface,
            "implementation": self.implementation,
            "branch": self.branch,
            "coverage": self.coverage,
            "verified_at": self.verified_at.isoformat(),
            "source": dict(self.source),
            "entries": [entry.to_payload() for entry in self.entries],
        }


@dataclass(frozen=True)
class DeviceSupportMatch:
    status: str
    model_ids: tuple[str, ...] = ()
    records: tuple[DeviceSupportRecord, ...] = ()
    roster_evidence: DeviceSupportRosterEvidence | None = None

    def to_payload(
        self,
        *,
        as_of: date | None = None,
        freshness_days: int = 120,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "model_ids": list(self.model_ids),
            "records": [
                record.to_payload(as_of=as_of, freshness_days=freshness_days)
                for record in self.records
            ],
        }
        if self.roster_evidence is not None:
            payload["roster_evidence"] = self.roster_evidence.to_payload()
        return payload


@dataclass(frozen=True)
class DeviceSupportQueryResult:
    records: tuple[DeviceSupportRecord, ...] = ()
    matches: tuple[DeviceSupportMatch, ...] = ()
    surface: str = ""


class DeviceSupportMatrix:
    def __init__(
        self,
        records: tuple[DeviceSupportRecord, ...],
        *,
        support_rosters: tuple[SupportRoster, ...] = (),
        freshness_days: int,
    ):
        self.records = records
        self.support_rosters = support_rosters
        self.freshness_days = freshness_days

    def query(
        self,
        model_text: str,
        *,
        resolver: ModelResolver,
        surface: str | None = None,
        implementation: str | None = None,
        branch: str | None = None,
    ) -> DeviceSupportMatch:
        resolved = resolver.resolve(model_text)
        if resolved.status != "resolved":
            return DeviceSupportMatch(resolved.status, resolved.model_ids)
        model_id = resolved.model_ids[0]
        records = tuple(
            row
            for row in self.records
            if row.model == model_id
            and _matches(row.surface, surface)
            and _matches(row.implementation, implementation)
            and _matches(row.branch, branch)
        )
        if records:
            return DeviceSupportMatch("matched", (model_id,), records)
        roster_status, roster_evidence = self._roster_match(
            model_id,
            surface=surface,
            implementation=implementation,
            branch=branch,
        )
        return DeviceSupportMatch(
            roster_status,
            (model_id,),
            roster_evidence=roster_evidence,
        )

    def match_query(
        self,
        query: str,
        *,
        resolver: ModelResolver,
    ) -> DeviceSupportQueryResult:
        surface = _surface_in_query(query)
        models = _models_in_query(query, resolver, set(resolver.entries))
        if not models:
            return DeviceSupportQueryResult(surface=surface or "")
        branch = _branch_in_query(query)
        matches: list[DeviceSupportMatch] = []
        records: list[DeviceSupportRecord] = []
        for model_id in sorted(models):
            model_records = tuple(
                row
                for row in self.records
                if row.model == model_id
                and _matches(row.surface, surface)
                and _matches(row.branch, branch)
            )
            if model_records:
                match = DeviceSupportMatch("matched", (model_id,), model_records)
                records.extend(model_records)
            else:
                roster_status, roster_evidence = self._roster_match(
                    model_id,
                    surface=surface,
                    implementation=None,
                    branch=branch,
                )
                match = DeviceSupportMatch(
                    roster_status,
                    (model_id,),
                    roster_evidence=roster_evidence,
                )
            matches.append(match)
        return DeviceSupportQueryResult(
            tuple(records),
            tuple(matches),
            surface=surface or "",
        )

    def _roster_match(
        self,
        model_id: str,
        *,
        surface: str | None,
        implementation: str | None,
        branch: str | None,
    ) -> tuple[str, DeviceSupportRosterEvidence | None]:
        if surface is None and implementation is None:
            return "not_found", None
        rosters = tuple(
            roster
            for roster in self.support_rosters
            if roster.coverage == EXHAUSTIVE_ROSTER_COVERAGE
            and _matches(roster.surface, surface)
            and _matches(roster.implementation, implementation)
            and _matches(roster.branch, branch)
        )
        if not rosters:
            return "not_found", None
        roster = rosters[0]
        entries = tuple(
            entry
            for entry in roster.entries
            if entry.status == "mapped" and model_id in entry.model_ids
        )
        evidence = DeviceSupportRosterEvidence(
            surface=roster.surface,
            implementation=roster.implementation,
            branch=roster.branch,
            coverage=roster.coverage,
            verified_at=roster.verified_at,
            source=roster.source,
            entries=entries,
        )
        return ("roster_listed" if entries else "surface_not_listed"), evidence


def _matches(actual: str, wanted: str | None) -> bool:
    return wanted is None or actual.casefold() == str(wanted).casefold()


def _branch_in_query(query: str) -> str | None:
    q = query.casefold()
    if re.search(r"(?<![a-z0-9])v2[-_. ]?main(?![a-z0-9])", q):
        return "v2-main"
    if re.search(r"(?<![a-z0-9])v2(?:\.x)?(?![a-z0-9])", q):
        return "v2.x"
    if re.search(r"(?<![a-z0-9])main(?![a-z0-9])", q):
        return "main"
    return None


def _surface_in_query(query: str) -> str | None:
    q = query.casefold()
    if re.search(
        r"(?<![a-z0-9])(?:orbbec[\s_-]*)?"
        r"unity(?:[\s_-]*(?:wrapper|sdk))?(?![a-z0-9])",
        q,
    ):
        return "unity"
    if re.search(r"isaac[\s_-]*sim", q):
        return "isaac_sim"
    if re.search(r"isaac[\s_-]*ros", q):
        return "isaac_ros"
    if re.search(r"ros\s*2", q):
        return "ros2"
    # Bare "SDK" is an ecosystem-level request and can include ROS wrappers;
    # narrowing it to the native SDK would hide valid branch records.  Only an
    # explicit native/v2 request selects the core SDK surface.
    if (
        re.search(r"(?:orbbec\s*)?sdk\s*(?:v?2(?:\.x)?)", q)
        or re.search(r"(?:c\+\+|cpp|native).{0,12}sdk", q)
        or re.search(r"sdk.{0,12}(?:c\+\+|cpp|native)", q)
    ):
        return "sdk"
    return None


def _alias_pattern(alias: str) -> re.Pattern[str]:
    parts = re.findall(r"[A-Za-z0-9]+", alias)
    if not parts:
        return re.compile(r"(?!)")
    joined = r"[\s_.-]*".join(re.escape(part) for part in parts)
    return re.compile(rf"(?<![A-Za-z0-9]){joined}(?![A-Za-z0-9])", re.IGNORECASE)


def _models_in_query(
    query: str,
    resolver: ModelResolver,
    matrix_models: set[str],
) -> set[str]:
    candidates: list[tuple[str, int, int]] = []
    for model_id in sorted(matrix_models):
        entry = resolver.entries.get(model_id)
        if entry is None:
            continue
        aliases = {model_id, entry.display_name, *entry.aliases}
        for alias in sorted(aliases, key=len, reverse=True):
            resolved = resolver.resolve(alias)
            if resolved.status != "resolved" or resolved.model_ids != (model_id,):
                continue
            candidates.extend(
                (model_id, match.start(), match.end())
                for match in _alias_pattern(alias).finditer(query)
                if not _is_prefix_of_unknown_model_name(
                    query,
                    match.start(),
                    match.end(),
                    model_id,
                    resolver,
                )
            )
    return {
        model_id
        for model_id, start, end in candidates
        if not any(
            other_model != model_id
            and other_start <= start
            and other_end >= end
            and (other_start < start or other_end > end)
            for other_model, other_start, other_end in candidates
        )
    }


def _is_prefix_of_unknown_model_name(
    query: str,
    start: int,
    end: int,
    model_id: str,
    resolver: ModelResolver,
) -> bool:
    """Do not let a shorter product inherit an unknown suffixed model's facts.

    Product families can also be concrete products (for example ``Gemini``).
    When a query contains a model-like suffix such as ``Gemini 999`` or
    ``Gemini Pro``, matching only the shorter concrete product would falsely
    attach its roster state to an unrecognized model.  Known longer names still
    resolve normally and win through the existing longest-span rule.
    """
    matched_text = query[start:end]
    if re.search(r"[\s_.-]|\d", matched_text):
        return False
    suffix = re.match(r"[\s_.-]+([A-Z0-9][A-Za-z0-9]*)", query[end:])
    if suffix is None:
        return False
    extended_end = end + suffix.end()
    resolved = resolver.resolve(query[start:extended_end])
    return not (
        resolved.status == "resolved"
        and resolved.model_ids == (model_id,)
    )


def _parse_configuration(raw: object, record_name: str) -> tuple[DeviceConfiguration, ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise DeviceSupportError(f"{record_name}.configuration must be a list")
    rows: list[DeviceConfiguration] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        field = f"{record_name}.configuration[{index}]"
        if not isinstance(item, dict):
            raise DeviceSupportError(f"{field} must be a mapping")
        capability = str(item.get("capability") or "").strip()
        if not capability or capability.casefold() in seen:
            raise DeviceSupportError(f"{field}.capability missing or duplicate")
        seen.add(capability.casefold())
        config_source = item.get("source")
        rows.append(DeviceConfiguration(
            capability=capability,
            supported=_optional_bool(item.get("supported"), f"{field}.supported"),
            default_enabled=_optional_bool(
                item.get("default_enabled"), f"{field}.default_enabled"
            ),
            parameter=str(item.get("parameter") or ""),
            activation=str(item.get("activation") or ""),
            default_mode=str(item.get("default_mode") or ""),
            source=(
                _official_source(config_source, f"{field}.source")
                if config_source is not None
                else None
            ),
        ))
    return tuple(rows)


def _parse_support_rosters(
    raw: object,
    *,
    known_model_ids: Collection[str] | None,
) -> tuple[SupportRoster, ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise DeviceSupportError("support_rosters must be a list")
    rosters: list[SupportRoster] = []
    seen_rosters: set[tuple[str, str, str]] = set()
    known = set(known_model_ids) if known_model_ids is not None else None
    for roster_index, raw_roster in enumerate(raw):
        field = f"support_rosters[{roster_index}]"
        if not isinstance(raw_roster, dict):
            raise DeviceSupportError(f"{field} must be a mapping")
        required = {
            key: str(raw_roster.get(key) or "").strip()
            for key in ("surface", "implementation", "branch", "coverage")
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise DeviceSupportError(f"{field}: missing {', '.join(missing)}")
        if required["coverage"] != EXHAUSTIVE_ROSTER_COVERAGE:
            raise DeviceSupportError(
                f"{field}.coverage: unsupported value {required['coverage']!r}"
            )
        roster_key = (
            required["surface"].casefold(),
            required["implementation"].casefold(),
            required["branch"].casefold(),
        )
        if roster_key in seen_rosters:
            raise DeviceSupportError(f"{field}: duplicate support roster {roster_key}")
        seen_rosters.add(roster_key)
        raw_entries = raw_roster.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise DeviceSupportError(f"{field}.entries must be a non-empty list")
        entries: list[SupportRosterEntry] = []
        seen_names: set[str] = set()
        for entry_index, raw_entry in enumerate(raw_entries):
            entry_field = f"{field}.entries[{entry_index}]"
            if not isinstance(raw_entry, dict):
                raise DeviceSupportError(f"{entry_field} must be a mapping")
            official_name = str(raw_entry.get("official_name") or "").strip()
            entity_kind = str(raw_entry.get("entity_kind") or "").strip()
            status = str(raw_entry.get("status") or "").strip()
            if not official_name or not entity_kind or not status:
                raise DeviceSupportError(
                    f"{entry_field}: official_name/entity_kind/status are required"
                )
            normalized_name = official_name.casefold()
            if normalized_name in seen_names:
                raise DeviceSupportError(
                    f"{entry_field}: duplicate official_name {official_name!r}"
                )
            seen_names.add(normalized_name)
            if status not in ROSTER_ENTRY_STATES:
                raise DeviceSupportError(
                    f"{entry_field}.status: unsupported value {status!r}"
                )
            reason = str(raw_entry.get("reason") or "").strip()
            if status == "pending" and not reason:
                raise DeviceSupportError(
                    f"{entry_field}: pending entry requires reason"
                )
            raw_model_ids = raw_entry.get("model_ids") or []
            if not isinstance(raw_model_ids, list):
                raise DeviceSupportError(f"{entry_field}.model_ids must be a list")
            model_ids = tuple(str(value).strip() for value in raw_model_ids)
            if status == "mapped" and not model_ids:
                raise DeviceSupportError(
                    f"{entry_field}: mapped entry requires model_ids"
                )
            if status == "mapped" and known is None:
                raise DeviceSupportError(
                    f"{field}: known_model_ids are required for mapped entries"
                )
            for model_id in model_ids:
                if not model_id:
                    raise DeviceSupportError(
                        f"{entry_field}.model_ids contains an empty value"
                    )
                if status == "mapped" and known is not None and model_id not in known:
                    raise DeviceSupportError(
                        f"{entry_field}: unknown mapped model {model_id}"
                    )
            upstream_fields = raw_entry.get("upstream_fields")
            if upstream_fields is not None and not isinstance(upstream_fields, dict):
                raise DeviceSupportError(
                    f"{entry_field}.upstream_fields must be a mapping"
                )
            entries.append(SupportRosterEntry(
                official_name=official_name,
                entity_kind=entity_kind,
                status=status,
                model_ids=model_ids,
                upstream_fields=(
                    {str(key): str(value) for key, value in upstream_fields.items()}
                    if upstream_fields
                    else None
                ),
                reason=reason,
            ))
        rosters.append(SupportRoster(
            surface=required["surface"],
            implementation=required["implementation"],
            branch=required["branch"],
            coverage=required["coverage"],
            verified_at=_parse_date(
                raw_roster.get("verified_at"), f"{field}.verified_at"
            ),
            entries=tuple(entries),
            source=_official_roster_source(raw_roster.get("source"), f"{field}.source"),
        ))
    return tuple(rosters)


def load_device_support_matrix(
    path: Path,
    *,
    known_model_ids: Collection[str] | None = None,
) -> DeviceSupportMatrix:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise DeviceSupportError(f"device support matrix missing: {path}") from exc
    if data.get("version") != 1:
        raise DeviceSupportError(f"{path}: unsupported version")
    try:
        freshness_days = int(data.get("freshness_days", 120))
    except (TypeError, ValueError) as exc:
        raise DeviceSupportError("freshness_days must be an integer") from exc
    if freshness_days <= 0:
        raise DeviceSupportError("freshness_days must be positive")
    support_rosters = _parse_support_rosters(
        data.get("support_rosters"),
        known_model_ids=known_model_ids,
    )
    raw_records = data.get("records") or []
    if not isinstance(raw_records, list):
        raise DeviceSupportError("records must be a list")

    records: list[DeviceSupportRecord] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(raw_records):
        field = f"records[{index}]"
        if not isinstance(raw, dict):
            raise DeviceSupportError(f"{field} must be a mapping")
        required = {
            key: str(raw.get(key) or "").strip()
            for key in ("model", "surface", "implementation", "support_state")
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise DeviceSupportError(f"{field}: missing {', '.join(missing)}")
        state = required["support_state"]
        if state not in SUPPORT_STATES:
            raise DeviceSupportError(f"{field}: unknown support_state {state!r}")
        branch = str(raw.get("branch") or "").strip()
        key = (
            required["model"], required["surface"], required["implementation"], branch
        )
        if key in seen:
            raise DeviceSupportError(f"{field}: duplicate device support facet {key}")
        seen.add(key)
        firmware = raw.get("firmware")
        if firmware is not None and not isinstance(firmware, dict):
            raise DeviceSupportError(f"{field}.firmware must be a mapping")
        records.append(DeviceSupportRecord(
            model=required["model"],
            surface=required["surface"],
            implementation=required["implementation"],
            branch=branch,
            support_state=state,
            verified_at=_parse_date(raw.get("verified_at"), f"{field}.verified_at"),
            source=_official_source(raw.get("source"), f"{field}.source"),
            platforms=tuple(str(value) for value in (raw.get("platforms") or [])),
            firmware=(
                {str(k): str(v) for k, v in firmware.items()} if firmware else None
            ),
            launch=str(raw.get("launch") or ""),
            configurations=_parse_configuration(raw.get("configuration"), field),
            maintenance_level=str(raw.get("maintenance_level") or ""),
        ))
    return DeviceSupportMatrix(
        tuple(records),
        support_rosters=support_rosters,
        freshness_days=freshness_days,
    )
