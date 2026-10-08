"""Deterministic fact-matrix constraint evaluation.

This module does not rank or recommend models. It only produces auditable
verdicts and keeps missing data distinct from negative evidence.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

from src.facts.schema import ProfileSet, RangeValue, parse_range
from src.facts.store import FactRow, FactStore

VERDICTS = frozenset({"satisfied", "unsatisfied", "no_data", "unparsed"})
NOTE = (
    "verdict=no_data 表示矩阵无完整数据,不等于不满足;"
    "排序与推荐由你基于判定表判断"
)

_POINT_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)?\s*(\+)?\s*$", re.IGNORECASE)
_UNIT_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0}
_IP_REQUEST_RE = re.compile(r"^\s*IP\s*(\d)(\d)\s*$", re.IGNORECASE)
_IP_FACT_RE = re.compile(r"\bIP\s*(\d)(\d)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ConstraintVerdict:
    verdict: str
    value: object = None
    raw_value: str = ""
    qualifier: dict | None = None
    reason: str = ""
    matched: object = None
    range_relation: str = ""
    supported_intersection: dict | None = None
    requested_range: dict | None = None

    def to_payload(self) -> dict:
        payload = asdict(self)
        return {key: value for key, value in payload.items()
                if value not in (None, "", {})}


def _norm_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def _row_value(row: FactRow) -> object:
    if isinstance(row.value, RangeValue):
        return asdict(row.value)
    return row.value


def _base(row: FactRow, verdict: str, **kwargs) -> ConstraintVerdict:
    return ConstraintVerdict(
        verdict=verdict,
        value=_row_value(row),
        raw_value=row.raw_value,
        qualifier=dict(row.qualifier) or None,
        **kwargs,
    )


def _parse_range_requirement(value: object) -> RangeValue | None:
    if isinstance(value, dict):
        try:
            # The tool contract accepts both the canonical typed range
            # (min/max/unit) and an explicit-metre transport shape.  Models
            # commonly emit min_m/max_m when the unit is already encoded in
            # the key; treating that unambiguous shape as unparsed turns an
            # otherwise valid full-catalog scan into an unrecoverable loop.
            if "min_m" in value or "max_m" in value:
                if not {"min_m", "max_m"}.issubset(value):
                    return None
                if value.get("unit") not in (None, "", "m", "M"):
                    return None
                lo = float(value["min_m"])
                hi = float(value["max_m"])
                if lo > hi:
                    return None
                return RangeValue(
                    lo, hi, "m", bool(value.get("open_ended", False)),
                )
            unit = str(value.get("unit") or "m").casefold()
            if unit not in _UNIT_TO_M:
                return None
            factor = _UNIT_TO_M[unit]
            lo = float(value["min"]) * factor
            hi = float(value["max"]) * factor
            if lo > hi:
                return None
            return RangeValue(lo, hi, "m", bool(value.get("open_ended", False)))
        except (KeyError, TypeError, ValueError):
            return None
    text = str(value or "")
    parsed = parse_range(text)
    if parsed is not None:
        return parsed
    match = _POINT_RE.fullmatch(text)
    if not match:
        return None
    number, unit, plus = match.groups()
    factor = _UNIT_TO_M[(unit or "m").casefold()]
    point = round(float(number) * factor, 6)
    return RangeValue(point, point, "m", bool(plus))


def _evaluate_covers(row: FactRow, requested: object) -> ConstraintVerdict:
    target = _parse_range_requirement(requested)
    if target is None or not isinstance(row.value, RangeValue):
        return _base(
            row,
            "unparsed",
            reason="range requirement or fact is not typed",
            range_relation="unknown",
        )
    actual = row.value
    lower_ok = target.min >= actual.min
    if target.open_ended:
        upper_ok = actual.open_ended
    else:
        upper_ok = actual.open_ended or target.max <= actual.max
    full_cover = lower_ok and upper_ok
    actual_upper_is_open = actual.open_ended
    target_upper_is_open = target.open_ended
    disjoint = (
        (not actual_upper_is_open and actual.max < target.min)
        or (not target_upper_is_open and target.max < actual.min)
    )
    relation = (
        "full_cover" if full_cover
        else "disjoint" if disjoint
        else "partial_overlap"
    )
    intersection = None
    if not disjoint:
        lower = max(actual.min, target.min)
        if actual_upper_is_open and target_upper_is_open:
            upper = max(lower, actual.max, target.max)
            open_ended = True
        elif actual_upper_is_open:
            upper = target.max
            open_ended = False
        elif target_upper_is_open:
            upper = actual.max
            open_ended = False
        else:
            upper = min(actual.max, target.max)
            open_ended = False
        intersection = asdict(RangeValue(
            round(lower, 6), round(upper, 6), "m", open_ended,
        ))
    return _base(
        row,
        "satisfied" if full_cover else "unsatisfied",
        matched=asdict(target),
        range_relation=relation,
        supported_intersection=intersection,
        requested_range=asdict(target),
    )


def _validate_constraint_role(constraint: dict) -> str:
    role = str(constraint.get("role") or "hard_requirement")
    if role not in {"hard_requirement", "operating_boundary", "preferred_range"}:
        return "unknown constraint role"
    field = str(constraint.get("field") or "")
    op = str(constraint.get("op") or "")
    if role == "operating_boundary" and (field, op) != (
        "depth_range_max", "covers",
    ):
        return "operating_boundary requires depth_range_max/covers"
    if role == "preferred_range" and (field, op) != (
        "depth_range_ideal", "covers",
    ):
        return "preferred_range requires depth_range_ideal/covers"
    return ""


def _parse_profile_request(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        width, height = int(value["width"]), int(value["height"])
        if width <= 0 or height <= 0:
            return None
        fps = value.get("fps")
        if fps is not None:
            fps = int(fps)
            if fps <= 0:
                return None
        fmt = value.get("format")
        if fmt is not None and not str(fmt).strip():
            return None
        return {
            "width": width,
            "height": height,
            "format": _norm_text(fmt) if fmt is not None else None,
            "fps": fps,
        }
    except (KeyError, TypeError, ValueError):
        return None


def _evaluate_profile(row: FactRow, requested: object) -> ConstraintVerdict:
    request = _parse_profile_request(requested)
    if request is None:
        return _base(row, "unparsed", reason="profile requires width/height and optional format/fps")
    profile_set: ProfileSet | None = row.profile_set
    if profile_set is None:
        return _base(row, "no_data", reason="typed profile table is absent")
    matches = []
    for profile in profile_set.profiles:
        if profile.width != request["width"] or profile.height != request["height"]:
            continue
        if request["format"] is not None and not any(
            _norm_text(fmt) == request["format"] for fmt in profile.formats
        ):
            continue
        if request["fps"] is not None and request["fps"] not in profile.fps:
            continue
        matches.append(asdict(profile))
    if matches:
        return _base(row, "satisfied", matched=matches)
    if profile_set.coverage != "complete":
        return _base(
            row, "no_data",
            reason="profile table is partial; absence cannot prove unsupported",
        )
    return _base(row, "unsatisfied", reason="no matching profile in complete table")


def _parse_profile_minimum(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    allowed = {"min_pixels", "min_width", "min_height", "min_fps", "format"}
    if set(value) - allowed:
        return None
    try:
        parsed: dict[str, int | str | None] = {
            "min_pixels": None,
            "min_width": None,
            "min_height": None,
            "min_fps": None,
            "format": None,
        }
        for key in ("min_pixels", "min_width", "min_height", "min_fps"):
            if key not in value:
                continue
            number = int(value[key])
            if isinstance(value[key], bool) or number <= 0:
                return None
            parsed[key] = number
        has_pixels = parsed["min_pixels"] is not None
        has_dimensions = (
            parsed["min_width"] is not None and parsed["min_height"] is not None
        )
        incomplete_dimensions = (
            (parsed["min_width"] is None) != (parsed["min_height"] is None)
        )
        if (not has_pixels and not has_dimensions) or incomplete_dimensions:
            return None
        if "format" in value:
            fmt = _norm_text(value["format"])
            if not fmt:
                return None
            parsed["format"] = fmt
        return parsed
    except (TypeError, ValueError):
        return None


def _evaluate_profile_minimum(row: FactRow, requested: object) -> ConstraintVerdict:
    request = _parse_profile_minimum(requested)
    if request is None:
        return _base(
            row,
            "unparsed",
            reason="minimum profile requires positive geometry and optional fps/format",
        )
    profile_set: ProfileSet | None = row.profile_set
    if profile_set is None:
        return _base(row, "no_data", reason="typed profile table is absent")

    matches = []
    for profile in profile_set.profiles:
        min_pixels = request["min_pixels"]
        if isinstance(min_pixels, int) and profile.width * profile.height < min_pixels:
            continue
        min_width = request["min_width"]
        if isinstance(min_width, int) and profile.width < min_width:
            continue
        min_height = request["min_height"]
        if isinstance(min_height, int) and profile.height < min_height:
            continue
        fmt = request["format"]
        if isinstance(fmt, str) and not any(
            _norm_text(actual) == fmt for actual in profile.formats
        ):
            continue
        min_fps = request["min_fps"]
        if isinstance(min_fps, int) and not any(fps >= min_fps for fps in profile.fps):
            continue
        matches.append(asdict(profile))
    if matches:
        return _base(row, "satisfied", matched=matches)
    if profile_set.coverage != "complete":
        return _base(
            row, "no_data",
            reason="profile table is partial; absence cannot prove unsupported",
        )
    return _base(row, "unsatisfied", reason="no profile meets all minimums")


def _evaluate_ip(row: FactRow, op: str, requested: object) -> ConstraintVerdict:
    target = _IP_REQUEST_RE.fullmatch(str(requested or ""))
    actual = _IP_FACT_RE.search(f"{row.value} {row.raw_value}")
    if target is None or actual is None:
        return _base(row, "unparsed", reason="IP value must use standard IPxy form")
    tx, ty = int(target.group(1)), int(target.group(2))
    ax, ay = int(actual.group(1)), int(actual.group(2))
    satisfied = ((ax >= tx and ay >= ty) if op == "gte"
                 else (ax <= tx and ay <= ty))
    return _base(
        row, "satisfied" if satisfied else "unsatisfied",
        matched=f"IP{ax}{ay}",
    )


def evaluate_constraint(
    store: FactStore,
    model_id: str,
    constraint: dict,
) -> ConstraintVerdict:
    op = str(constraint.get("op") or "")
    role_error = _validate_constraint_role(constraint)
    if role_error:
        return ConstraintVerdict("unparsed", reason=role_error)
    if op == "requires_assessment":
        requirement = str(constraint.get("requirement") or "").strip()
        if not requirement:
            return ConstraintVerdict(
                "unparsed", reason="requires_assessment needs requirement text")
        return ConstraintVerdict(
            "no_data",
            reason="requires scenario or onsite assessment",
            matched=requirement,
        )
    field = str(constraint.get("field") or "")
    requested = constraint.get("value")
    if not field or field not in store.fields:
        return ConstraintVerdict("unparsed", reason="unknown or empty field")
    result = store.get_spec(model_id, field)
    if result.status != "found" or result.row is None:
        target = _parse_range_requirement(requested) if op == "covers" else None
        return ConstraintVerdict(
            "no_data",
            reason=result.status,
            range_relation="unknown" if op == "covers" else "",
            requested_range=asdict(target) if target is not None else None,
        )
    row = result.row
    if row.status == "conflict":
        target = _parse_range_requirement(requested) if op == "covers" else None
        return _base(
            row,
            "no_data",
            reason="fact sources conflict",
            range_relation="unknown" if op == "covers" else "",
            requested_range=asdict(target) if target is not None else None,
        )

    if op == "covers":
        return _evaluate_covers(row, requested)
    if op == "supports_profile" and field in {
        "rgb_resolution_fps", "depth_resolution_fps"
    }:
        return _evaluate_profile(row, requested)
    if op == "meets_profile_minimum" and field in {
        "rgb_resolution_fps", "depth_resolution_fps"
    }:
        return _evaluate_profile_minimum(row, requested)
    if op in {"gte", "lte"} and field == "ip_rating":
        return _evaluate_ip(row, op, requested)
    if op in {"gte", "lte"} and isinstance(row.value, (int, float)):
        try:
            target = float(requested)
        except (TypeError, ValueError):
            return _base(row, "unparsed", reason="numeric requirement is invalid")
        satisfied = row.value >= target if op == "gte" else row.value <= target
        return _base(row, "satisfied" if satisfied else "unsatisfied", matched=target)
    if op in {"contains", "not_contains"}:
        needle = _norm_text(requested)
        if not needle or not isinstance(row.value, str):
            return _base(row, "unparsed", reason="contains requires text fact and value")
        matched = needle in _norm_text(f"{row.value} {row.raw_value}")
        satisfied = matched if op == "contains" else not matched
        return _base(
            row, "satisfied" if satisfied else "unsatisfied",
            matched=str(requested) if matched else None,
        )
    if op == "eq":
        if not isinstance(row.value, str) or not str(requested or "").strip():
            return _base(row, "unparsed", reason="eq requires text fact and value")
        satisfied = _norm_text(row.value) == _norm_text(requested)
        return _base(row, "satisfied" if satisfied else "unsatisfied")
    return _base(row, "unparsed", reason=f"unsupported op {op!r} for field")


def _constraint_id(item: dict, fallback: str) -> str:
    return str(item.get("id") or fallback)


def _normalize_constraints(constraints: list[dict]) -> tuple[list[dict], list[dict]]:
    """Normalize one-level groups and collect schema errors without raising."""
    normalized: list[dict] = []
    invalid: list[dict] = []
    seen_ids: set[str] = set()

    def claim_id(item: dict, fallback: str) -> bool:
        item["id"] = _constraint_id(item, fallback)
        if item["id"] in seen_ids:
            invalid.append({**item, "validation_error": "duplicate constraint id"})
            return False
        seen_ids.add(item["id"])
        return True

    for index, raw in enumerate(constraints or [], 1):
        item = dict(raw or {})
        if not claim_id(item, f"c{index}"):
            continue
        has_group = "any_of" in item
        has_leaf = any(key in item for key in ("field", "op", "value"))
        if has_group and has_leaf:
            item["validation_error"] = "constraint cannot mix leaf fields with any_of"
            normalized.append(item)
            continue
        if not has_group:
            normalized.append(item)
            continue
        children = item.get("any_of")
        if not isinstance(children, list) or not children:
            item["validation_error"] = "any_of must contain at least one leaf constraint"
            normalized.append(item)
            continue
        normalized_children: list[dict] = []
        for child_index, raw_child in enumerate(children, 1):
            child = dict(raw_child or {})
            if not claim_id(child, f"{item['id']}.{child_index}"):
                child["validation_error"] = "duplicate constraint id"
            elif "any_of" in child:
                child["validation_error"] = "nested any_of is not supported"
            normalized_children.append(child)
        item["any_of"] = normalized_children
        normalized.append(item)

    for item in normalized:
        if item.get("validation_error") and item not in invalid:
            invalid.append(item)
        children = item.get("any_of")
        if not isinstance(children, list):
            continue
        for child in children:
            if child.get("validation_error") and child not in invalid:
                invalid.append(child)
    return normalized, invalid


def _evaluate_group(
    store: FactStore,
    model_id: str,
    constraint: dict,
) -> tuple[dict, list[dict]]:
    error = str(constraint.get("validation_error") or "")
    if error:
        return {"verdict": "unparsed", "reason": error}, [constraint]
    children = constraint.get("any_of")
    if not isinstance(children, list) or not children:
        reason = "any_of must contain at least one leaf constraint"
        return {"verdict": "unparsed", "reason": reason}, [constraint]

    cells: dict[str, dict] = {}
    unparsed: list[dict] = []
    verdicts: list[str] = []
    for child in children:
        child_error = str(child.get("validation_error") or "")
        if child_error:
            verdict = ConstraintVerdict("unparsed", reason=child_error)
        elif "any_of" in child:
            verdict = ConstraintVerdict("unparsed", reason="nested any_of is not supported")
        else:
            verdict = evaluate_constraint(store, model_id, child)
        cells[str(child["id"])] = verdict.to_payload()
        verdicts.append(verdict.verdict)
        if verdict.verdict == "unparsed" and child not in unparsed:
            unparsed.append(child)

    if "satisfied" in verdicts:
        group_verdict = "satisfied"
    elif verdicts and all(verdict == "unsatisfied" for verdict in verdicts):
        group_verdict = "unsatisfied"
    elif "no_data" in verdicts:
        group_verdict = "no_data"
    else:
        group_verdict = "unparsed"
    return {
        "verdict": group_verdict,
        "children": cells,
        "reason": "any_of: at least one child must be satisfied",
    }, unparsed


def _overall_verdict(verdicts: list[str]) -> str:
    if "unsatisfied" in verdicts:
        return "unsatisfied"
    if "unparsed" in verdicts:
        return "unparsed"
    if "no_data" in verdicts:
        return "no_data"
    return "satisfied"


def _range_adjustment(constraint_id: str, cell: dict) -> dict:
    return {
        "constraint_id": constraint_id,
        "requested_range": cell["requested_range"],
        "supported_intersection": cell["supported_intersection"],
    }


def _fit_for_model(constraints: list[dict], cells: dict[str, dict]) -> dict:
    """Project verified fit without selecting or ranking a model."""
    excluded = False
    evidence_gap = False
    adjustments: list[dict] = []
    for constraint in constraints:
        constraint_id = str(constraint["id"])
        cell = cells[constraint_id]
        children = cell.get("children")
        if isinstance(children, dict):
            if cell.get("verdict") == "satisfied":
                continue
            group_has_gap = any(
                child.get("verdict") in {"no_data", "unparsed"}
                for child in children.values()
            )
            if group_has_gap:
                evidence_gap = True
            partials = [
                (child_id, child)
                for child_id, child in children.items()
                if child.get("range_relation") == "partial_overlap"
                and child.get("supported_intersection") is not None
            ]
            if partials:
                adjustments.extend(
                    _range_adjustment(child_id, child)
                    for child_id, child in partials
                )
            elif not group_has_gap:
                excluded = True
            continue

        relation = cell.get("range_relation")
        verdict = cell.get("verdict")
        if relation == "partial_overlap":
            adjustments.append(_range_adjustment(constraint_id, cell))
        elif relation == "disjoint" or verdict == "unsatisfied":
            excluded = True
        elif relation == "unknown" or verdict in {"no_data", "unparsed"}:
            evidence_gap = True

    if excluded:
        fit = "excluded"
    elif evidence_gap:
        fit = "evidence_gap"
    elif adjustments:
        fit = "conditional_fit"
    else:
        fit = "verified_full_fit"
    payload: dict[str, object] = {"fit": fit}
    if adjustments:
        payload["required_adjustments"] = adjustments
    return payload


def filter_models(
    store: FactStore,
    constraints: list[dict],
    models: list[str] | None = None,
) -> dict:
    """Return a constraint-by-model table without ranking or filtering it."""
    model_ids = sorted(store.known_models) if models is None else list(dict.fromkeys(models))
    normalized, invalid = _normalize_constraints(constraints)
    if invalid:
        return {
            "status": "invalid_constraints",
            "constraints": normalized,
            "validation_errors": invalid,
            "verdict_summary": {},
            "overall_verdicts": {},
            "fit_summary": {},
            "table": {},
            "unparsed": invalid,
            "note": NOTE,
        }
    table = {}
    unparsed = list(invalid)
    overall_verdicts = {}
    for model_id in model_ids:
        cells = {}
        verdicts = []
        for constraint in normalized:
            if "any_of" in constraint or constraint.get("validation_error"):
                cell, group_unparsed = _evaluate_group(store, model_id, constraint)
                cells[constraint["id"]] = cell
                verdicts.append(str(cell["verdict"]))
                for item in group_unparsed:
                    if item not in unparsed:
                        unparsed.append(item)
            else:
                verdict = evaluate_constraint(store, model_id, constraint)
                cells[constraint["id"]] = verdict.to_payload()
                verdicts.append(verdict.verdict)
                if verdict.verdict == "unparsed" and constraint not in unparsed:
                    unparsed.append(constraint)
        table[model_id] = cells
        overall_verdicts[model_id] = _overall_verdict(verdicts)
    fit_summary = {
        model_id: _fit_for_model(normalized, table[model_id])
        for model_id in model_ids
    }
    verdict_summary = {
        verdict: [
            model_id for model_id, actual in overall_verdicts.items()
            if actual == verdict
        ]
        for verdict in ("satisfied", "no_data", "unsatisfied", "unparsed")
    }
    return {
        "status": "ok",
        "constraints": normalized,
        # Keep compact aggregate data before the potentially large detail table.
        # LoopRuntime clips tool payloads by character count, so putting this first
        # preserves every candidate's feasibility during full-catalog scans.
        "verdict_summary": verdict_summary,
        "overall_verdicts": overall_verdicts,
        "fit_summary": fit_summary,
        "table": table,
        "unparsed": unparsed,
        "note": NOTE,
    }
