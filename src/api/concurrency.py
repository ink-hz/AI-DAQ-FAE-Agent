"""Runtime concurrency gate for long-running chat requests."""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatConcurrencySnapshot:
    max_concurrent: int
    active: int
    waiting: int


class ChatConcurrencyGate:
    def __init__(self, max_concurrent: int) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self._semaphore = threading.BoundedSemaphore(self.max_concurrent)
        self._lock = threading.Lock()
        self._active = 0
        self._waiting = 0

    def acquire(self, *, timeout: float | None = None) -> bool:
        acquired = self._semaphore.acquire(timeout=timeout)
        if acquired:
            with self._lock:
                self._active += 1
        return acquired

    def release(self) -> None:
        with self._lock:
            if self._active <= 0:
                return
            self._active -= 1
        self._semaphore.release()

    def wait_started(self) -> None:
        with self._lock:
            self._waiting += 1

    def wait_finished(self) -> None:
        with self._lock:
            if self._waiting > 0:
                self._waiting -= 1

    def snapshot(self) -> ChatConcurrencySnapshot:
        with self._lock:
            return ChatConcurrencySnapshot(
                max_concurrent=self.max_concurrent,
                active=self._active,
                waiting=self._waiting,
            )
