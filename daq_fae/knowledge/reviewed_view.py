"""Fail-closed, request-independent view of one immutable DAQ release."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import unicodedata

from daq_fae.knowledge.records import (KINDS, ROLES, STATUSES,
                                       _data_valid, _entity_label, access_fingerprint,
                                       record_fingerprint, link_review_valid)
from daq_fae.knowledge.releases import read_active_release
from daq_fae.knowledge.source_paths import record_contains_source_path


_SHA = re.compile(r"^[0-9a-f]{64}$")
_LINK_LIKE = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.-]*://|//[a-z0-9][a-z0-9.-]*\.[a-z]{2,}"
    r"|\b[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?=$|[\s/?#)\]>]))"
)


class StaleVariantSelector(ValueError):
    """A schema selector conflicts with the prior entity but not this turn."""


def reviewed_name_in_query(name: str, query: str,
                           *, selector_values=()) -> bool:
    """Recognize a reviewed label followed by a selector or clear grammar."""
    normalized_name = unicodedata.normalize('NFKC', name)
    normalized_query = unicodedata.normalize('NFKC', query)
    pattern = re.escape(normalized_name).replace(r'\ ', r'\s*')
    boundary = re.compile(r'(?<![A-Za-z0-9-])' + pattern + r'(?![A-Za-z0-9-])', re.I)
    selectors = {re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value))).casefold()
                 for value in selector_values}
    for match in boundary.finditer(normalized_query):
        if re.search(r'(?:[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+|'
                     r'\b(?:Quad|RGB[-\s]+D))\s+$',
                     normalized_query[:match.start()], re.I):
            # A qualified name such as RGB-D EGO is a distinct unresolved
            # identity; its EGO suffix does not name the reviewed EGO SKU.
            continue
        tail = normalized_query[match.end():]
        stripped = tail.lstrip()
        if not stripped or stripped[0] in ',.;:!?，。；：！？、）)]}':
            return True
        if stripped.startswith('有'):
            proposed = stripped[1:].lstrip()
            if re.match(r'\d+(?:[x×]\d+)+', proposed, re.I):
                stripped = proposed
        numeric = re.match(r'\d+(?:[x×]\d+)+', stripped, re.I)
        if numeric and numeric.group().casefold().replace('×', 'x') in selectors:
            remaining = stripped[numeric.end():].lstrip()
            if not remaining or remaining.startswith(('版', '版本', '变体')):
                return True
            stripped = remaining
        if re.match(r'(?:的|是|在|与|和|有几|能否|是否|可以|供电|电源|功率|'
                    r'每口|每个下游口|单口|单机|独立|设备端|不连接|不接|无需)'
                    r'(?![A-Za-z0-9])',
                    stripped):
            return True
        if re.match(r'加[一二两三四五六七八九十0-9]|接\s+[A-Za-z]', stripped):
            return True
        english = re.match(r'([A-Za-z]+)\b', stripped)
        if english and english.group(1).casefold() in {
            'on', 'in', 'with', 'for', 'to', 'is', 'are', 'can', 'has', 'supports',
            'and', 'or',
        }:
            return True
    return False


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
        operator = manifest.get('review', {}).get('operator_release', {})
        domain = manifest.get('review', {}).get('readiness', {}).get('scope', {}).get('domain_evidence')
        self._domain_evidence = deepcopy(domain if domain is not None else operator)
        self.is_operator_pro_v2 = (self._domain_evidence.get('contract') == 'daq-reviewed-operating-scope/v1' or
                                  operator.get('mode')
                                   in {'owner_directed_source_checked_v2',
                                       'owner_directed_source_checked_specs_v4'})

    def operating_veto_entity_records(self) -> list[dict]:
        """Reviewed v2 exclusion labels are identity vetoes, never answer sources."""
        if not self.is_operator_pro_v2:
            return []
        return [{'id': item['id'], 'scope': {},
                 'data': {'name': item['name'], 'aliases': item['aliases']}}
                for item in self._domain_evidence['veto_entities']]

    @staticmethod
    def _identity_text(value: str) -> str:
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).casefold().replace('×', 'x'))

    def ground_question(self, question: str, *, role: str) -> dict:
        """Ground only role-visible exact entities and required variant selectors."""
        text = self._identity_text(question)
        matches = []
        rows = self.records_for(role)
        for row in rows:
            if row['kind'] != 'entity':
                continue
            labels = [row['data']['name'], *row['data'].get('aliases', [])]
            family_selectors = [other['scope'][key] for other in rows
                                if other['kind'] == 'entity'
                                and other['scope'].get('product') == row['scope'].get('product')
                                for key in other['scope'].get('required_selectors', [])]
            if not any(reviewed_name_in_query(label, question,
                                              selector_values=family_selectors)
                       for label in labels):
                continue
            scope = row['scope']
            if any(self._identity_text(str(scope[key])) not in text
                   for key in scope.get('required_selectors', [])):
                continue
            matches.append(row)
        if matches:
            names = {self._identity_text(row['data']['name']) for row in matches}
            retained = []
            for row in matches:
                name = self._identity_text(row['data']['name'])
                longer = [other for other in names if len(other) > len(name) and name in other]
                residual = text
                for other in longer:
                    residual = residual.replace(other, '')
                if not longer or name in residual:
                    retained.append(row)
            matches = retained
        if len(matches) != 1:
            return {}
        row = matches[0]
        return {'entities': [row['id']], 'conditions': {
            key: row['scope'][key] for key in row['scope'].get('required_selectors', [])}}

    def grounding_matches_goals(self, grounded: dict, goals, *, role: str) -> bool:
        """Reject a side-mentioned product as the evidence key for another goal."""
        ids = grounded.get('entities', []) if isinstance(grounded, dict) else []
        if len(ids) != 1:
            return False
        row = next((item for item in self.records_for(role)
                    if item['kind'] == 'entity' and item['id'] == ids[0]), None)
        if row is None:
            return False
        labels = {self._identity_text(str(value)) for value in (
            row['id'], row['data']['name'], row['scope'].get('product', ''),
            *row['data'].get('aliases', [])) if value}
        selector_keys = row['scope'].get('required_selectors', [])
        if len(selector_keys) == 1:
            selector = self._identity_text(str(row['scope'][selector_keys[0]]))
            labels.update(label + selector + suffix for label in tuple(labels)
                          for suffix in ('', '版', '版本', '变体'))
        product_goals = [goal for goal in goals if goal.kind == 'product']
        return bool(product_goals) and all(
            goal.entities and all(self._identity_text(name) in labels
                                  for name in goal.entities)
            and all(self._identity_text(str(value)) == self._identity_text(
                str(row['scope'].get(key, '')))
                for key, value in goal.condition_map().items()
                if key in {'variant', 'resolution_variant', 'hardware_revision'})
            for goal in product_goals)

    def rebind_variant(self, previous_scope: dict, goals, *, role: str,
                       current_question: str) -> dict | None:
        """Apply a new explicit selector within the prior reviewed product family.

        None means no selector change. An empty result means the new selector
        cannot identify one role-visible variant; the old binding must be
        discarded rather than combined with the new condition.
        """
        previous_ids = previous_scope.get('entities', []) if isinstance(previous_scope, dict) else []
        if len(previous_ids) != 1:
            return None
        entities = [row for row in self.records_for(role) if row['kind'] == 'entity']
        previous = next((row for row in entities if row['id'] == previous_ids[0]), None)
        if previous is None:
            return {}
        product = previous['scope'].get('product')
        if not isinstance(product, str) or not product.strip():
            return {}
        family = [row for row in entities
                  if row['scope'].get('product') == product]
        selector_keys = {key for row in family
                         for key in row['scope'].get('required_selectors', [])}
        product_goals = [goal for goal in goals if goal.kind == 'product']
        requested: dict[str, str] = {}
        conflicting = False
        for goal in product_goals:
            for key, value in goal.condition_map().items():
                if key not in selector_keys:
                    continue
                normalized = self._identity_text(str(value))
                if key in requested and requested[key] != normalized:
                    conflicting = True
                requested[key] = normalized
        if not requested:
            return None
        changed = conflicting or any(
            self._identity_text(str(previous['scope'].get(key, ''))) != value
            for key, value in requested.items())
        if not changed:
            return None
        labels = {self._identity_text(str(label)) for row in family for label in (
            row['id'], row['data']['name'], row['scope'].get('product', ''),
            *row['data'].get('aliases', [])) if label}
        if conflicting or any(
            self._identity_text(label) not in labels
            for goal in product_goals for label in goal.entities):
            return {}
        matches = [row for row in family if all(
            self._identity_text(str(row['scope'].get(key, ''))) == value
            for key, value in requested.items())
            and all(key in requested for key in row['scope'].get('required_selectors', []))]
        if len(matches) != 1:
            return {}
        row = matches[0]
        explicit_ids = {candidate['id'] for candidate in family
                        for goal in product_goals for label in goal.entities
                        if self._identity_text(label) == self._identity_text(candidate['id'])}
        if explicit_ids and explicit_ids != {row['id']}:
            return {}
        current = unicodedata.normalize('NFKC', current_question).casefold().replace('×', 'x')
        compact_current = re.sub(r'\s+', '', current)
        for value in requested.values():
            present = (bool(re.search(r'(?<![a-z0-9])' + re.escape(value)
                                      + r'(?![a-z0-9])', current, re.I))
                       if len(value) <= 2 and value.isascii() else
                       value in compact_current)
            if not present:
                raise StaleVariantSelector('stale_variant_selector')
        return {'entities': [row['id']], 'conditions': {
            key: row['scope'][key] for key in row['scope'].get('required_selectors', [])}}

    def resolve_field(self, field: str, entity_id: str | None = None) -> str | None:
        evidence = self._domain_evidence
        normalized = self._identity_text(field)
        scoped = evidence.get('scoped_field_aliases', {}).get(entity_id, {})
        for alias, canonical in scoped.items():
            if self._identity_text(alias) == normalized:
                return canonical
        fields = {row['data']['field'] for row in self._records
                  if row['kind'] == 'claim' and
                  (entity_id is None or row['data']['entity_id'] == entity_id)}
        # An added canonical field must not be captured by an older global alias.
        # Explicit entity-scoped aliases above retain their reviewed precedence.
        for canonical in sorted(fields):
            if self._identity_text(canonical) == normalized:
                return canonical
        for canonical, aliases in evidence.get('field_aliases', {}).items():
            if canonical in fields and normalized in {
                    self._identity_text(item) for item in [canonical, *aliases]}:
                return canonical
        return None

    def verified_variant_scopes(self, entity: str, field: str, *, role: str) -> dict[str, list[str]]:
        """Expose role-visible selector choices without releasing their values."""
        query = self._identity_text(entity)
        rows = self.records_for(role)
        names = {row['id']: {self._identity_text(label) for label in
                            [row['data']['name'], *row['data'].get('aliases', [])]}
                 for row in rows if row['kind'] == 'entity'}
        exact_ids = {rid for rid in names if self._identity_text(rid) == query}
        if exact_ids:
            entity_ids = exact_ids
        elif query.startswith('entity:'):
            return {}
        else:
            # This parameter is an entity selector, not free-text retrieval.
            # Descriptors require a reviewed alias before they can inherit a
            # published family's variant choices.
            entity_ids = {rid for rid, labels in names.items() if query in labels}
            if not entity_ids:
                return {}
        scopes: dict[str, set[str]] = {}
        for row in rows:
            if row['kind'] != 'claim' or row['data']['entity_id'] not in entity_ids:
                continue
            if (field != 'all_reviewed_specs' and row['data']['field'] not in {
                    field, self.resolve_field(field, row['data']['entity_id'])}):
                continue
            scope = row['scope']
            for selector in scope.get('required_selectors', []):
                value = str(scope[selector])
                if self._identity_text(value) not in query:
                    scopes.setdefault(selector, set()).add(value)
        return {key: sorted(values) for key, values in sorted(scopes.items())}

    @classmethod
    def load_active(cls, root: Path, *, verify_approval=None, runtime_release=None, upstream_sha=None,
                    allow_local_dev=False) -> "ReviewedKnowledge | None":
        if root.is_symlink():
            raise ValueError("knowledge release root must not be a symlink")
        active = read_active_release(root)
        if active is None:
            return None
        from .release_readiness import is_local_dev_bundle
        readiness = active['manifest'].get('review', {}).get('readiness', {})
        if is_local_dev_bundle(readiness) and not allow_local_dev:
            raise ValueError('local Dev knowledge requires explicit opt-in')
        if any(active['manifest'].get(key) for key in ('sources', 'records', 'sections')):
            if active['manifest'].get('review', {}).get('operator_release', {}).get('mode') in {\
                    'owner_directed_source_checked_v1', 'owner_directed_source_checked_v2',
                    'owner_directed_runtime_rebind_v3',
                    'owner_directed_source_checked_specs_v4'}:
                from .operator_release import _checked_operator
                _checked_operator(root, active['release_id'], runtime_release=runtime_release,
                                  upstream_sha=upstream_sha)
            else:
                from .release_readiness import _checked
                manifest = _checked(root, active['release_id'], verify_approval, require_current_links=False,
                                    allow_local_dev=allow_local_dev)
                runtime = manifest['review']['readiness']['runtime']
                if runtime_release is None or upstream_sha is None or runtime['release'] != runtime_release \
                        or runtime['upstream_sha'] != upstream_sha:
                    raise ValueError('release readiness runtime identity mismatch')
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
        entity_labels: dict[str, dict[str, tuple[tuple[str, str], set[str]]]] = {}
        answerable = 0
        for row in manifest["records"]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in seen:
                raise ValueError("knowledge record identity invalid")
            seen.add(row["id"])
            if row.get("kind") not in KINDS or row.get("status") not in STATUSES or \
                    not isinstance(row.get("scope"), dict) or not row["scope"] or \
                    not isinstance(row.get("data"), dict):
                raise ValueError("knowledge record schema invalid")
            if row['kind'] == 'entity':
                if not _data_valid('entity', row['status'], row['data']):
                    raise ValueError('knowledge entity labels invalid')
                identity = (_entity_label(row['data']['name']),
                            _entity_label(str(row['scope'].get('product', ''))))
                labels = [('id', row['id']), ('name', row['data']['name']),
                          *(("alias", alias) for alias in row['data'].get('aliases', []))]
                product = row['scope'].get('product')
                if isinstance(product, str) and product.strip():
                    labels.append(('product', product))
                for source, label in labels:
                    key = _entity_label(label)
                    by_id = entity_labels.setdefault(key, {})
                    if row['id'] not in by_id:
                        by_id[row['id']] = (identity, set())
                    by_id[row['id']][1].add(source)
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
            if record_contains_source_path(row):
                raise ValueError('knowledge local source path in typed content')
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
                if not link_review_valid(row, require_current=False) or \
                        review.get("record_sha256") != record_fingerprint(row):
                    raise ValueError("knowledge link review invalid")
        for key, by_id in entity_labels.items():
            if len(by_id) < 2:
                continue
            identities = [item[0] for item in by_id.values()]
            products = {item[1] for item in identities}
            names = {item[0] for item in identities}
            only_shared_product_key = all(
                sources <= {'name', 'product'} and identity[1] == key
                for identity, sources in by_id.values())
            if any('id' in sources for _, sources in by_id.values()) or \
                    len(products) > 1 or (len(names) > 1 and not only_shared_product_key):
                raise ValueError('knowledge entity alias collision')
        if manifest.get("answerable_count") != answerable:
            raise ValueError("knowledge release answerable count invalid")
        domain = manifest.get('review', {}).get('readiness', {}).get('scope', {}).get('domain_evidence')
        if domain is not None:
            if manifest.get('review', {}).get('operator_release'):
                raise ValueError('mixed domain authority in knowledge release')
            from .domain_metadata import validate_domain_metadata
            validate_domain_metadata(domain, manifest['records'])
        if manifest["format_version"] == 2:
            from .section_release import validate_section_manifest
            validate_section_manifest(manifest)
        if manifest.get('review', {}).get('operator_release', {}).get('mode') in {\
                'owner_directed_source_checked_v2', 'owner_directed_runtime_rebind_v3',
                'owner_directed_source_checked_specs_v4'}:
            from .operator_release import verify_operator_manifest
            runtime = manifest['review']['operator_release']['runtime']
            verify_operator_manifest(manifest, runtime_release=runtime['release'],
                                     upstream_sha=runtime['upstream_sha'])
        return cls(release_id, manifest)

    def records_for(self, role: str, *, for_delivery: bool = False) -> list[dict]:
        if role not in ROLES:
            raise ValueError("knowledge role invalid")
        visible = []
        for row in self._records:
            if not row["answerable"] or record_contains_source_path(row):
                continue
            if row["kind"] == "link" and not link_review_valid(row):
                continue
            access = row["access_review"]
            if role not in access["view_roles"]:
                continue
            if (row["kind"] == "link" or for_delivery) and role not in access["forward_roles"]:
                continue
            visible.append(deepcopy(row))
        return visible

    def conflict_notices_for(self, role: str) -> list[dict]:
        from .conflict_visibility import conflict_notice
        if role not in ROLES:
            raise ValueError("knowledge role invalid")
        return [notice for row in self._records
                if not record_contains_source_path(row)
                and (notice := conflict_notice(row, role)) is not None]

    def sections_for(self, role: str, *, for_delivery: bool = False) -> list[dict]:
        """Filter whole sections before retrieval; never expose a mixed paragraph."""
        if role not in ROLES:
            raise ValueError("knowledge role invalid")
        return [{key: deepcopy(value) for key, value in section.items()
                 if key != 'narrative_units'} for section in self._sections
                if role in section["view_roles"]
                and (not for_delivery or role in section["forward_roles"])]
