"""Deterministic, local-only ranking over temporary attachment chunks."""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from src.attachments.models import AttachmentChunk, AttachmentError, AttachmentLocator

_LATIN_TOKEN = re.compile(r"[A-Za-z0-9_./:+-]+")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


def _tokens(text: str) -> Counter[str]:
    normalized = text.lower()
    values = [
        cleaned
        for token in _LATIN_TOKEN.findall(normalized)
        if (cleaned := token.strip(".:"))
    ]
    for run in _CJK_RUN.findall(normalized):
        if len(run) == 1:
            values.append(run)
        else:
            values.extend(run[index:index + 2] for index in range(len(run) - 1))
    return Counter(values)


class AttachmentSearch:
    def search(
        self,
        chunks: Iterable[AttachmentChunk],
        query: str,
        *,
        limit: int = 8,
    ) -> list[AttachmentChunk]:
        query_tokens = _tokens(query)
        if not query_tokens or limit <= 0:
            return []
        ranked: list[tuple[int, str, AttachmentChunk]] = []
        for chunk in chunks:
            chunk_tokens = _tokens(chunk.text)
            score = sum(min(count, chunk_tokens.get(token, 0)) for token, count in query_tokens.items())
            if score > 0:
                ranked.append((-score, chunk.chunk_id, chunk))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in ranked[:limit]]

    def read(
        self,
        chunks: Iterable[AttachmentChunk],
        locator: AttachmentLocator,
    ) -> AttachmentChunk:
        for chunk in chunks:
            if chunk.locator == locator:
                return chunk
        raise AttachmentError("attachment_locator_not_found")
