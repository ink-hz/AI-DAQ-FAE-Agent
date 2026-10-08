"""Deterministic object-plane sampling projection from governed profiles.

The calculation describes angular sampling only.  It does not estimate depth,
spatial, localization, or measurement accuracy and never ranks products.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import yaml


class SamplingProfileError(ValueError):
    """Raised when the governed profile registry is structurally unsafe."""


@dataclass(frozen=True)
class SamplingProfile:
    model_id: str
    profile_id: str
    stream: str
    evidence_state: str
    width_pixels: int
    height_pixels: int
    horizontal_fov_deg: float
    vertical_fov_deg: float
    sources: tuple[dict[str, str], ...]
    qualifier: dict[str, object] | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return self.model_id, self.stream, self.profile_id

    def public_profile(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "stream": self.stream,
            "width_pixels": self.width_pixels,
            "height_pixels": self.height_pixels,
            "horizontal_fov_deg": self.horizontal_fov_deg,
            "vertical_fov_deg": self.vertical_fov_deg,
        }


@dataclass(frozen=True)
class SamplingProfileRegistry:
    profiles: tuple[SamplingProfile, ...]
    known_model_ids: frozenset[str]

    def matches(
        self, model_id: str, stream: str, profile_id: str | None,
    ) -> tuple[SamplingProfile, ...]:
        return tuple(
            profile for profile in self.profiles
            if profile.model_id == model_id
            and profile.stream == stream
            and (profile_id is None or profile.profile_id == profile_id)
        )


def _required_text(raw: dict, key: str, label: str) -> str:
    value = str(raw.get(key) or "").strip()
    if not value:
        raise SamplingProfileError(f"{label}: missing {key}")
    return value


def _positive_int(raw: dict, key: str, label: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool):
        raise SamplingProfileError(f"{label}: {key} must be positive")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SamplingProfileError(f"{label}: {key} must be positive") from exc
    if parsed <= 0:
        raise SamplingProfileError(f"{label}: {key} must be positive")
    return parsed


def _fov(raw: dict, key: str, label: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool):
        raise SamplingProfileError(f"{label}: {key} must be within (0, 180)")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SamplingProfileError(
            f"{label}: {key} must be within (0, 180)"
        ) from exc
    if not 0 < parsed < 180:
        raise SamplingProfileError(f"{label}: {key} must be within (0, 180)")
    return parsed


def _sources(raw: dict, label: str) -> tuple[dict[str, str], ...]:
    entries = raw.get("sources")
    if not isinstance(entries, list) or not entries:
        raise SamplingProfileError(f"{label}: sources must contain source refs")
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise SamplingProfileError(f"{label}: sources must contain source refs")
        path = str(entry.get("path") or "").strip()
        section = str(entry.get("section") or "").strip()
        if not path or not section:
            raise SamplingProfileError(f"{label}: sources must contain path and section")
        result.append({"path": path, "section": section})
    return tuple(result)


def load_sampling_profiles(
    path: Path,
    known_model_ids: set[str] | frozenset[str],
) -> SamplingProfileRegistry:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if data.get("version") != 1 or not isinstance(data.get("profiles"), list):
        raise SamplingProfileError("sampling profile registry requires version 1 profiles")
    known = frozenset(str(model_id) for model_id in known_model_ids)
    profiles: list[SamplingProfile] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(data["profiles"], 1):
        if not isinstance(raw, dict):
            raise SamplingProfileError(f"profile {index}: profile must be an object")
        label = f"profile {index}"
        model_id = _required_text(raw, "model_id", label)
        if model_id not in known:
            raise SamplingProfileError(f"{label}: unknown model {model_id}")
        profile = SamplingProfile(
            model_id=model_id,
            profile_id=_required_text(raw, "profile_id", label),
            stream=_required_text(raw, "stream", label),
            evidence_state=_required_text(raw, "evidence_state", label),
            width_pixels=_positive_int(raw, "width_pixels", label),
            height_pixels=_positive_int(raw, "height_pixels", label),
            horizontal_fov_deg=_fov(raw, "horizontal_fov_deg", label),
            vertical_fov_deg=_fov(raw, "vertical_fov_deg", label),
            sources=_sources(raw, label),
            qualifier=dict(raw.get("qualifier") or {}) or None,
        )
        if profile.evidence_state not in {"found", "conflict"}:
            raise SamplingProfileError(
                f"{label}: evidence_state must be found or conflict"
            )
        if profile.key in seen:
            raise SamplingProfileError(
                "duplicate sampling profile " + "/".join(profile.key)
            )
        seen.add(profile.key)
        profiles.append(profile)
    return SamplingProfileRegistry(tuple(profiles), known)


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be positive")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be positive") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _found_cell(
    profile: SamplingProfile,
    distance_m: float,
    target_width_mm: float | None,
) -> dict[str, object]:
    width_m = 2 * distance_m * math.tan(math.radians(profile.horizontal_fov_deg) / 2)
    height_m = 2 * distance_m * math.tan(math.radians(profile.vertical_fov_deg) / 2)
    mm_per_pixel_x = width_m * 1000 / profile.width_pixels
    mm_per_pixel_y = height_m * 1000 / profile.height_pixels
    cell: dict[str, object] = {
        "evidence_state": "found",
        "profile": profile.public_profile(),
        "distance_m": distance_m,
        "coverage": {
            "width_mm": round(width_m * 1000, 6),
            "height_mm": round(height_m * 1000, 6),
        },
        "sampling": {
            "mm_per_pixel_x": round(mm_per_pixel_x, 6),
            "mm_per_pixel_y": round(mm_per_pixel_y, 6),
        },
        "sources": [dict(source) for source in profile.sources],
        "interpretation_boundary": (
            "横向采样/物面点间距估算，不是深度精度、定位精度或测量精度；"
            "现场结果还受材质、光照、角度、标定和算法影响"
        ),
    }
    if profile.qualifier:
        cell["qualifier"] = dict(profile.qualifier)
    if target_width_mm is not None:
        cell["target"] = {
            "width_mm": target_width_mm,
            "pixels_x": round(target_width_mm / mm_per_pixel_x, 6),
        }
    return cell


def evaluate_sampling_geometry(
    registry: SamplingProfileRegistry,
    models: list[str],
    distance_m: float,
    stream: str,
    target_width_mm: float | None = None,
    profile_id: str | None = None,
) -> dict[str, object]:
    distance = _positive_number(distance_m, "distance_m")
    target = (
        _positive_number(target_width_mm, "target_width_mm")
        if target_width_mm is not None else None
    )
    cells: dict[str, dict[str, object]] = {}
    for model_id in models:
        matches = registry.matches(model_id, stream, profile_id)
        if not matches:
            cells[model_id] = {
                "evidence_state": "no_data",
                "note": "受治理 profile 未命中；缺失不等于不支持",
            }
            continue
        if len(matches) != 1 or matches[0].evidence_state == "conflict":
            sources = [
                dict(source) for profile in matches for source in profile.sources
            ]
            cells[model_id] = {
                "evidence_state": "conflict",
                "profiles": [profile.public_profile() for profile in matches],
                "sources": sources,
                "note": "profile 有冲突或未指定唯一 profile；不得跨 profile 拼接",
            }
            continue
        cells[model_id] = _found_cell(matches[0], distance, target)
    return {
        "stream": stream,
        "profile_id": profile_id,
        "distance_m": distance,
        "target_width_mm": target,
        "cells": cells,
        "note": "Runtime 只计算横向采样证据，不排名、不推荐型号",
    }
