"""SSE 事件序列化。"""
import json
import queue
import threading
from dataclasses import dataclass
from time import monotonic
from typing import Any


@dataclass(frozen=True)
class StreamHeartbeat:
    elapsed_ms: int
    count: int


def sse_event(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def iter_with_heartbeat(iterable, *, heartbeat_interval_seconds: float):
    """Yield heartbeat ticks while a blocking event iterator is silent."""
    interval = max(float(heartbeat_interval_seconds), 0.001)
    started_at = monotonic()
    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def pump() -> None:
        try:
            for item in iterable:
                events.put(("item", item))
        except BaseException as exc:  # propagate stream failures to the response path
            events.put(("error", exc))
        finally:
            events.put(("done", None))

    threading.Thread(target=pump, daemon=True).start()
    heartbeat_count = 0
    while True:
        try:
            kind, payload = events.get(timeout=interval)
        except queue.Empty:
            heartbeat_count += 1
            yield StreamHeartbeat(
                elapsed_ms=int((monotonic() - started_at) * 1000),
                count=heartbeat_count,
            )
            continue

        if kind == "item":
            yield payload
        elif kind == "error":
            raise payload
        else:
            break
