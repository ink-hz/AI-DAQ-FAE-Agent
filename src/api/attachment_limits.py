"""Independent, non-blocking rate and concurrency protection for uploads."""
from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from time import monotonic
from typing import Callable


@dataclass
class AttachmentUploadLease:
    acquired: bool
    reason: str | None = None
    _release: Callable[[], None] | None = None

    def release(self) -> None:
        callback, self._release = self._release, None
        if callback is not None:
            callback()


class AttachmentUploadGate:
    def __init__(
        self,
        *,
        max_concurrent: int,
        per_ip_batches_per_minute: int,
        trusted_proxy_hosts: tuple[str, ...] = ("127.0.0.1", "::1"),
        clock: Callable[[], float] = monotonic,
    ):
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._rate = per_ip_batches_per_minute
        self._trusted_proxy_hosts = frozenset(trusted_proxy_hosts)
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def client_key(self, peer_host: str, forwarded_for: str | None = None) -> str:
        if peer_host in self._trusted_proxy_hosts and forwarded_for:
            first = forwarded_for.split(",", 1)[0].strip()
            if first:
                return first
        return peer_host or "unknown"

    def try_acquire(self, client_key: str) -> AttachmentUploadLease:
        if not self._semaphore.acquire(blocking=False):
            return AttachmentUploadLease(False, "attachment_upload_busy")
        now = self._clock()
        with self._lock:
            events = self._events[client_key]
            while events and events[0] <= now - 60:
                events.popleft()
            if len(events) >= self._rate:
                self._semaphore.release()
                return AttachmentUploadLease(False, "attachment_rate_limited")
            events.append(now)
        return AttachmentUploadLease(True, _release=self._semaphore.release)

    def acquire(self, client_key: str) -> AttachmentUploadLease:
        lease = self.try_acquire(client_key)
        if not lease.acquired:
            raise RuntimeError(lease.reason or "attachment_upload_denied")
        return lease
