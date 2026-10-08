"""Local development transport contracts, with explicitly volatile replay state."""
from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from fastapi import HTTPException
from pydantic import AliasChoices, BaseModel, Field, model_validator


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, validation_alias=AliasChoices('message', 'question'))
    session_id: str | None = None
    channel: Literal['fae', 'ecom'] = 'fae'
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128)
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode='before')
    @classmethod
    def reject_conflicting_aliases(cls, value):
        if isinstance(value, dict) and 'message' in value and 'question' in value:
            if value['message'] != value['question']:
                raise ValueError('message and question disagree')
        return value


def is_local_peer(host: str) -> bool:
    # ASGI TestClient supplies this non-network peer for deterministic contracts.
    if host == 'testclient':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class RequestRecord:
    fingerprint: tuple
    session_id: str
    events: list[str] = field(default_factory=list)
    finished: bool = False
    created_at: float = field(default_factory=time.monotonic)


class LocalRequestRegistry:
    """Process memory only; no cross-worker or restart replay guarantee."""
    def __init__(self, *, ttl_seconds=3600, max_records=1024):
        self.lock = threading.Lock()
        self.records: dict[str, RequestRecord] = {}
        self.active_sessions: set[str] = set()
        self.ttl_seconds = ttl_seconds
        self.max_records = max_records

    def prune(self):
        expired = [key for key, record in self.records.items()
                   if record.finished and time.monotonic() - record.created_at > self.ttl_seconds]
        for key in expired:
            del self.records[key]
        while len(self.records) >= self.max_records:
            completed = next((key for key, record in self.records.items() if record.finished), None)
            if completed is None:
                raise HTTPException(503, 'local_request_registry_full')
            del self.records[completed]
