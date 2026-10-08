"""Catalog-backed product entity resolution.

This layer turns product anchors extracted from user language into catalog keys
before routing/capabilities decide how to answer. It intentionally returns
structured resolution results so "single model", "series", and "unresolved"
are visible instead of being collapsed into a brittle bucket fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

EntityKind = Literal["model", "series"]


@dataclass(frozen=True)
class ProductEntity:
    raw: str
    kind: EntityKind
    models: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProductEntityResolution:
    entities: list[ProductEntity] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


_SERIES_MARKER_RE = re.compile(r"(系列|series|family)", re.IGNORECASE)
_NOISE_WORD_RE = re.compile(
    r"(系列|series|family|型号|相机|camera|产品|product)",
    re.IGNORECASE,
)


def resolve_product_entities(
    raw_names: list[str],
    catalog: dict[str, dict],
    resolver=None,
) -> ProductEntityResolution:
    """Resolve user-mentioned product names into catalog keys.

    Resolution order is conservative:
      0. curated catalog aliases / explicit ambiguity (when `resolver` given,
         from Knowledge/_facts/catalog.yaml via src.facts.resolver);
      1. explicit series/family mention expands to a catalog-backed prefix;
      2. exact catalog model match;
      3. implicit numeric family like "Gemini 330" expands to "Gemini 33*";
      4. unique substring/suffix match, e.g. "335L" -> "Gemini_335L";
      5. otherwise unresolved.

    The function never invents product names. Every returned model exists in
    `catalog`.
    """
    entities: list[ProductEntity] = []
    resolved: list[str] = []
    unresolved: list[str] = []

    for raw in raw_names:
        name = str(raw).strip()
        if not name:
            continue
        entity = _resolve_curated(name, resolver, catalog) if resolver else None
        if entity is None:
            entity = _resolve_one(name, catalog)
        if entity is None:
            unresolved.append(name)
            continue
        entities.append(entity)
        resolved.extend(entity.models)

    return ProductEntityResolution(
        entities=entities,
        resolved=_dedupe_preserve_order(resolved),
        unresolved=_dedupe_preserve_order(unresolved),
    )


def extract_product_mentions(
    user_message: str,
    catalog: dict[str, dict],
    resolver=None,
) -> list[str]:
    """Find catalog-backed product anchors directly from raw user text.

    This is used as schema repair when the LLM extractor fails or misses an
    obvious product mention. It is intentionally catalog-backed and generic:
    exact catalog labels are detected first, then numeric family mentions using
    prefixes derived from catalog keys, e.g. "Gemini 330 系列". When a curated
    `resolver` (src.facts.resolver.ModelResolver) is given, its hand-maintained
    aliases (e.g. "XL", "G305", "335网口") are scanned as an extra pass and
    canonicalized to the entry display name.
    """
    text = str(user_message or "")
    if not text or not catalog:
        return []

    mentions: list[str] = []

    for key in sorted(catalog, key=len, reverse=True):
        for variant in _display_variants_for_key(key):
            if variant and variant.lower() in text.lower():
                mentions.append(variant)
                break

    lowered_mentions = [mention.lower() for mention in mentions]
    for key in sorted(catalog, key=len, reverse=True):
        for alias in _base_token_aliases_for_key(key):
            if not alias:
                continue
            if any(alias.lower() in mention for mention in lowered_mentions):
                continue
            pattern = _compile_base_alias_pattern(alias)
            if pattern.search(text):
                mentions.append(alias)
                lowered_mentions.append(alias.lower())
                break

    for prefix in _catalog_prefixes(catalog):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9])({re.escape(prefix)})[\s_-]*"
            r"([0-9]{2,4}[A-Za-z]{0,3})"
            r"\s*((?:系列|series|family)?)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            raw_prefix = match.group(1)
            suffix = match.group(2)
            marker = match.group(3)
            display_prefix = _display_prefix(raw_prefix, catalog)
            separator = "" if display_prefix.upper() == "MS" else " "
            mention = f"{display_prefix}{separator}{suffix}"
            if marker:
                mention = f"{mention} 系列"
            mentions.append(mention)

    if resolver is not None:
        # R2(20260714,sentinel prod-1b9db884):裸数字系列提及("330系列",无
        # Gemini 前缀、无空格)此前提取不到 → schema products 空 → 模型自己猜
        # 系列归属,把 Gemini 335 误写成"Gemini 300 系列成员"。catalog 有 series
        # 字段(Gemini 300 与 Gemini 330 是两个不同系列),据此把裸数字系列接地。
        series_by_num: dict[str, set[str]] = {}
        for entry in getattr(resolver, "entries", {}).values():
            series = str(getattr(entry, "series", "") or "").strip()
            m = re.search(r"(\d{2,4})", series)
            if m:
                series_by_num.setdefault(m.group(1), set()).add(series)
        lowered_mentions_all = [m.lower() for m in mentions]
        for num, series_set in series_by_num.items():
            # 数字须唯一对应一个 catalog 系列,否则不猜
            if len(series_set) != 1:
                continue
            series_name = next(iter(series_set))
            if any(series_name.lower() in m for m in lowered_mentions_all):
                continue
            pat = re.compile(rf"(?<![A-Za-z0-9]){num}\s*系列")
            if pat.search(text):
                mention = f"{series_name} 系列"
                mentions.append(mention)
                lowered_mentions_all.append(mention.lower())

        lowered_text = text.lower()
        lowered_mentions_all = [m.lower() for m in mentions]
        for entry in getattr(resolver, "entries", {}).values():
            for alias in entry.aliases:
                alias = str(alias).strip()
                if not alias:
                    continue
                if any(entry.display_name.lower() in m for m in lowered_mentions_all):
                    break
                if re.search(r"[a-zA-Z0-9]", alias):
                    pattern = re.compile(
                        rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
                        re.IGNORECASE,
                    )
                    hit = bool(pattern.search(text))
                else:
                    hit = alias in text
                if hit:
                    mentions.append(entry.display_name)
                    lowered_mentions_all.append(entry.display_name.lower())
                    break

    return _dedupe_mentions(mentions)


def resolve_single_model(name: str, catalog: dict[str, dict]) -> str | None:
    """Backward-compatible single-model resolver.

    It deliberately does not return a model for a series or ambiguous mention.
    Existing callers that need one exact product keep their current behavior,
    while new capability code should use `resolve_product_entities`.
    """
    entity = _resolve_one(str(name).strip(), catalog)
    if entity is None or entity.kind != "model" or len(entity.models) != 1:
        return None
    return entity.models[0]


def _resolve_curated(name: str, resolver, catalog: dict[str, dict]) -> ProductEntity | None:
    """catalog.yaml curated 别名/显式歧义优先;返回的 model 必须存在于 catalog。"""
    result = resolver.resolve(name)
    if result.status == "resolved":
        if result.entity_kind == "series":
            models = [model for model in result.model_ids if model in catalog]
            if len(models) > 1:
                return ProductEntity(raw=name, kind="series", models=models)
            return None
        model = result.model_ids[0]
        if model in catalog:
            return ProductEntity(raw=name, kind="model", models=[model])
        return None
    if result.status == "ambiguous":
        models = [m for m in result.model_ids if m in catalog]
        if len(models) > 1:
            return ProductEntity(raw=name, kind="series", models=models)
        if len(models) == 1:
            return ProductEntity(raw=name, kind="model", models=models)
    return None


def _resolve_one(name: str, catalog: dict[str, dict]) -> ProductEntity | None:
    if not name or not catalog:
        return None

    explicit_series = bool(_SERIES_MARKER_RE.search(name))
    canonical_name = _canonical_name(name)

    if explicit_series:
        series_models = _resolve_series(canonical_name, catalog)
        if series_models:
            return ProductEntity(raw=name, kind="series", models=series_models)

    exact = _resolve_exact(canonical_name, catalog)
    if exact:
        return ProductEntity(raw=name, kind="model", models=[exact])

    base_alias = _resolve_base_token_alias(canonical_name, catalog)
    if base_alias:
        return ProductEntity(raw=name, kind="model", models=[base_alias])

    series_models = _resolve_series(canonical_name, catalog)
    if series_models:
        return ProductEntity(raw=name, kind="series", models=series_models)

    unique = _resolve_unique_substring(canonical_name, catalog)
    if unique:
        return ProductEntity(raw=name, kind="model", models=[unique])

    return None


def _resolve_exact(canonical_name: str, catalog: dict[str, dict]) -> str | None:
    for key in catalog:
        if _canonical_key(key) == canonical_name:
            return key
    return None


def _resolve_base_token_alias(canonical_name: str, catalog: dict[str, dict]) -> str | None:
    """Resolve bare base-model tokens such as "Mega" or "Bolt".

    Variant keys often extend a base model with an extra suffix, for example
    `Femto_Mega_I`. A bare token should resolve to `Femto_Mega` when that exact
    base key exists, instead of becoming ambiguous with the suffixed variant.
    """
    if len(canonical_name) < 2:
        return None
    matches: list[str] = []
    for key in catalog:
        parts = [_canonical_name(part) for part in key.split("_") if part]
        if len(parts) >= 2 and parts[-1] == canonical_name:
            matches.append(key)
    if len(matches) == 1:
        return matches[0]

    typo_matches: list[str] = []
    if len(canonical_name) >= 4:
        for key in catalog:
            for alias in _base_token_aliases_for_key(key):
                if _edit_distance_at_most_one(canonical_name, _canonical_name(alias)):
                    typo_matches.append(key)
                    break
    typo_matches = _dedupe_preserve_order(typo_matches)
    return typo_matches[0] if len(typo_matches) == 1 else None


def _resolve_unique_substring(canonical_name: str, catalog: dict[str, dict]) -> str | None:
    if len(canonical_name) < 3:
        return None
    matches = [
        key for key in catalog
        if canonical_name in _canonical_key(key)
    ]
    return matches[0] if len(matches) == 1 else None


def _resolve_series(canonical_name: str, catalog: dict[str, dict]) -> list[str]:
    prefixes = _series_prefixes(canonical_name)
    if not prefixes:
        return []

    for prefix in prefixes:
        matches = [
            key for key in catalog
            if _canonical_key(key).startswith(prefix)
        ]
        if len(matches) > 1:
            return matches
    return []


def _series_prefixes(canonical_name: str) -> list[str]:
    match = re.fullmatch(r"([a-z]+)(\d+)", canonical_name)
    if not match:
        return []

    family, digits = match.groups()
    prefixes: list[str] = []
    if digits.endswith("0") and len(digits) >= 3:
        prefixes.append(f"{family}{digits[:-1]}")
    prefixes.append(f"{family}{digits}")
    return _dedupe_preserve_order(prefixes)


def _canonical_name(value: str) -> str:
    without_noise = _NOISE_WORD_RE.sub("", value)
    return re.sub(r"[^a-z0-9]+", "", without_noise.lower())


def _canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _display_variants_for_key(key: str) -> list[str]:
    spaced = key.replace("_", " ")
    return _dedupe_preserve_order([spaced, key])


def _base_token_aliases_for_key(key: str) -> list[str]:
    parts = [part for part in key.split("_") if part]
    if len(parts) < 2:
        return []
    alias = parts[-1]
    if len(_canonical_name(alias)) < 2:
        return []
    return [alias]


def _compile_base_alias_pattern(alias: str) -> re.Pattern[str]:
    canonical = _canonical_name(alias)
    match = re.fullmatch(r"(\d{2,4})([a-z]{1,3})", canonical, re.IGNORECASE)
    if match:
        digits, letters = match.groups()
        return re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(digits)}[\s_-]*{re.escape(letters)}(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1

    short, long = (left, right) if len(left) < len(right) else (right, left)
    i = j = edits = 0
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        j += 1
    return True


def _catalog_prefixes(catalog: dict[str, dict]) -> list[str]:
    prefixes: list[str] = []
    for key in catalog:
        token = key.split("_", 1)[0]
        match = re.match(r"[A-Za-z]+", token)
        if match:
            prefixes.append(match.group(0))
    return sorted(_dedupe_preserve_order(prefixes), key=len, reverse=True)


def _display_prefix(raw_prefix: str, catalog: dict[str, dict]) -> str:
    raw_lower = raw_prefix.lower()
    for prefix in _catalog_prefixes(catalog):
        if prefix.lower() == raw_lower:
            return prefix
    return raw_prefix


def _dedupe_mentions(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
