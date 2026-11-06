"""Per-conversation provider session affinity.

External Anthropic-compatible gateways route each request to one of several
upstream channels. ``metadata.user_id`` is the documented field gateways use
to keep one user's requests on the same upstream, which preserves prompt
cache reuse and thinking-signature validity across a conversation.

The value is an opaque digest of the FAE session id: stable across turns of
one conversation, distinct between conversations, and carries no identity.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_NAMESPACE = "orbbec-fae-provider-session:v1\0"
_metadata_user_id: ContextVar[str | None] = ContextVar("provider_metadata_user_id", default=None)


def metadata_user_id_for(session_id: str) -> str:
    return hashlib.sha256((_NAMESPACE + session_id).encode("utf-8")).hexdigest()


@contextmanager
def provider_session_scope(session_id: str | None) -> Iterator[None]:
    """Bind provider requests made inside this scope to one conversation."""
    token = _metadata_user_id.set(metadata_user_id_for(session_id) if session_id else None)
    try:
        yield
    finally:
        _metadata_user_id.reset(token)


def current_provider_metadata() -> dict | None:
    """The request ``metadata`` object for the current conversation, if any."""
    user_id = _metadata_user_id.get()
    return {"user_id": user_id} if user_id else None
