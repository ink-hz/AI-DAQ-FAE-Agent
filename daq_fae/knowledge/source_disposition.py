"""Offline source disposition; candidate metadata grants no answer or delivery rights."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import posixpath
import re
from urllib.parse import unquote, urlsplit
from pathlib import Path

from .source_import import import_archive

STATUSES = {'curated', 'historical_duplicate', 'needs_ocr_or_parse',
            'needs_fact_adjudication', 'out_of_first_scope'}


def _unique(rows):
    result = {row['path']: row for row in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate source identity')
    return result


def _category(path):
    suffix = Path(path).suffix.casefold()
    if suffix in {'.png', '.jpg', '.jpeg'}:
        return 'image', 'Diagram/screenshot candidate; inspect visual content before use.'
    if suffix in {'.mp4', '.wmv'}:
        return 'video', 'Demonstration candidate; no transcript or procedure evidence yet.'
    if suffix == '.docx':
        return 'document_asset', 'Requires explicit document parsing and source review.'
    if suffix in {'.bin', '.img'}:
        return 'firmware_binary_candidate', 'Binary inventory only; device/revision and flashing safety require review.'
    if suffix in {'.zip', '.gz', '.rar', '.aar', '.apk'}:
        return 'software_package_candidate', 'Package inventory only; contents, compatibility and delivery rights require review.'
    return 'unclassified_asset', 'Manual format and purpose inspection required.'


def build_inventory(snapshot: dict, decisions: dict) -> dict:
    """Join explicit content judgments to every text identity; keep assets metadata only."""
    for key in ('archive_manifest_sha256', 'extractor_version'):
        if decisions.get(key) != snapshot[key]:
            raise ValueError('decision input version mismatch')
    sources = _unique(snapshot['sources'])
    judged = _unique(decisions['text'])
    text_paths = {p for p, s in sources.items() if s['kind'] != 'asset'}
    if set(judged) != text_paths:
        raise ValueError('text decisions must cover exactly all text sources')
    chunks = {p: [] for p in sources}
    for chunk in snapshot['chunks']:
        p = chunk['source_path']
        if p not in text_paths or chunk['source_sha256'] != sources[p]['sha256']:
            raise ValueError('chunk identity mismatch or binary text indexing')
        chunks[p].append(chunk['locator'])
    references = {p: [] for p in sources}
    for chunk in snapshot['chunks']:
        for label, target in re.findall(r'\[([^\]]*)\]\(([^)]*)\)', chunk.get('text', '')):
            if urlsplit(target).scheme or target.startswith('/'):
                continue
            target_path = posixpath.normpath(posixpath.join(posixpath.dirname(chunk['source_path']), unquote(target)))
            if target_path in sources and sources[target_path]['kind'] == 'asset':
                references[target_path].append({'source_path': chunk['source_path'],
                    'source_sha256': chunk['source_sha256'], 'locator': deepcopy(chunk['locator']), 'label': label})
    text, assets = [], []
    for path, source in sorted(sources.items()):
        row = deepcopy(source)
        row['same_bytes_sources'] = sorted(p for p, s in sources.items()
                                           if p != path and s['sha256'] == source['sha256'])
        row['authorization_status'] = 'pending_review'
        row['view_roles'] = []
        row['forward_roles'] = []
        if path in text_paths:
            d = judged[path]
            if d.get('sha256') != source['sha256'] or d.get('status') not in STATUSES:
                raise ValueError('invalid or stale text decision')
            for key in ('reason', 'owner', 'reviewer', 'reviewed_on'):
                if not isinstance(d.get(key), str) or not d[key].strip():
                    raise ValueError('missing decision responsibility or rationale')
            locators = d.get('evidence_locators', [])
            if not locators or any(loc not in chunks[path] for loc in locators):
                # Unextractable documents may only be identified at file level.
                if chunks[path] or locators != [{'kind': 'file'}] or d['status'] != 'needs_ocr_or_parse':
                    raise ValueError('decision locator not in source extraction')
            relation = d.get('update_relation', {})
            if relation.get('status') != 'unresolved' or not relation.get('reason'):
                raise ValueError('supersession requires separately reviewed version evidence')
            if d['status'] == 'curated':
                raise ValueError('A1 disposition does not certify cleaned chapters; use A2/A3 review')
            if d['status'] == 'historical_duplicate' and not row['same_bytes_sources']:
                raise ValueError('duplicate decision lacks byte identity evidence')
            row.update({k: deepcopy(d[k]) for k in ('status', 'reason', 'owner', 'reviewer',
                       'reviewed_on', 'evidence_locators', 'update_relation')})
            row['extracted_locators'] = deepcopy(chunks[path])
            text.append(row)
        else:
            category, purpose = _category(path)
            row.update(category=category, intended_use=purpose, purpose_basis='extension_metadata_only',
                       locator={'kind': 'file'}, owner='DAQ repository maintainer; FAE access reviewer pending',
                       compatibility_status='not_established', default_text_index=False,
                       document_references=references[path], content_review_status='not_reviewed',
                       update_relation={'status': 'unresolved', 'reason': 'No reviewed supersession evidence'})
            assets.append(row)
    return {'format_version': 1, 'archive_manifest_sha256': snapshot['archive_manifest_sha256'],
            'extractor_version': snapshot['extractor_version'],
            'decision_sha256': hashlib.sha256(json.dumps(decisions, sort_keys=True, ensure_ascii=False,
                                  separators=(',', ':')).encode()).hexdigest(),
            'text_dispositions': text, 'asset_inventory': assets,
            'summary': {'sources': len(sources), 'text': len(text), 'assets': len(assets),
                        'text_statuses': dict(sorted(Counter(r['status'] for r in text).items())),
                        'asset_categories': dict(sorted(Counter(r['category'] for r in assets).items())),
                        'authorized_for_delivery': 0}}


def verified_inventory(archive: Path, snapshot: dict, decisions: dict) -> dict:
    """Re-extract the original manifest before trusting snapshot metadata or locators."""
    if import_archive(archive, snapshot['archive_manifest_sha256']) != snapshot:
        raise ValueError('candidate snapshot differs from verified archive extraction')
    return build_inventory(snapshot, decisions)


def write_inventory(output: Path, inventory: dict) -> None:
    """Create a private new output directory, preserving any existing run."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for filename, value in (
        ('source-disposition.json', {k: v for k, v in inventory.items() if k != 'asset_inventory'}),
        ('asset-inventory.json', {k: v for k, v in inventory.items() if k != 'text_dispositions'}),
    ):
        path = output / filename
        with path.open('x', encoding='utf-8') as stream:
            path.chmod(0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write('\n')
