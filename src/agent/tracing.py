"""Tracing module for AI-FAE-Agent.

Provides TraceContext, TraceRecorder, TraceSink implementations,
redaction utilities, and ContextVar injection for per-request tracing.
"""
from __future__ import annotations

import copy
import json
import logging
import random
import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|authorization|password|credential|cookie)", re.I
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]+", re.I)


def redact(data, _depth: int = 0):
    """Recursive redaction.

    - dict: keys matching _SENSITIVE_KEY_RE → value replaced with '[REDACTED]'
    - str: Bearer tokens → '[REDACTED]'
    - lists: each item redacted
    - max depth 8, returns '[MAX_DEPTH]' beyond
    - leaves int/float/bool/None untouched
    - returns deep copy, does not mutate input
    """
    if _depth > 8:
        return "[MAX_DEPTH]"
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k):
                result[k] = "[REDACTED]"
            else:
                result[k] = redact(v, _depth + 1)
        return result
    if isinstance(data, list):
        return [redact(item, _depth + 1) for item in data]
    if isinstance(data, str):
        return _BEARER_RE.sub("[REDACTED]", data)
    # int / float / bool / None — untouched
    return data


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceConfig:
    provider: Literal["none", "langfuse"] = "none"
    sample_rate: float = 1.0
    log_path: Path = Path("data/logs/traces.jsonl")
    environment: str = "development"
    release: str = "local"
    langfuse_base_url: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""


# ---------------------------------------------------------------------------
# TraceSpan
# ---------------------------------------------------------------------------


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    node: str
    started_at: str          # ISO8601 with timezone
    ended_at: str | None = None
    duration_ms: int | None = None
    input_summary: dict = field(default_factory=dict)
    output_summary: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# TraceSink base + implementations
# ---------------------------------------------------------------------------


class TraceSink:
    """Abstract — emit_span called once per span on close. Implementations must not raise."""

    def emit_span(self, span: TraceSpan, *, is_root: bool) -> None:
        ...

    def flush(self) -> None:
        ...


class NoopTraceSink(TraceSink):
    def emit_span(self, span: TraceSpan, *, is_root: bool) -> None:
        pass

    def flush(self) -> None:
        pass


class JsonlTraceSink(TraceSink):
    """Append-only JSONL writer; opens file fresh each emit (no long-lived handle).

    Creates parent dir if missing. Logger.warning on any IOError, never raises.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def emit_span(self, span: TraceSpan, *, is_root: bool) -> None:
        record = {
            "type": "root" if is_root else "span",
            **vars(span),
            "is_root": is_root,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except IOError as exc:
            logger.warning("JsonlTraceSink: failed to write span: %s", exc)

    def flush(self) -> None:
        pass


class LangfuseTraceSink(TraceSink):
    """Bridges TraceSpan → Langfuse 4.x OTel-based API.

    Uses low-level OTel tracer so we can preserve the real start_time / end_time
    of the business span (public ``start_observation`` does not accept start_time).
    Relies on two private langfuse internals:
      - ``client._create_remote_parent_span`` to build an OTel parent context from
        our trace_id / parent_span_id
      - ``client._otel_tracer.start_span`` with explicit ``start_time``
    These are private but stable enough for MVP; if langfuse rearranges them on
    upgrade, the warning is logged and traces fall back to jsonl-only.

    On construction, may raise ImportError if langfuse not installed —
    factory should catch and skip. emit_span swallows all exceptions, logs warning.
    """

    def __init__(
        self,
        base_url: str,
        public_key: str,
        secret_key: str,
        environment: str,
        release: str,
    ) -> None:
        import langfuse  # noqa: F401 — raise ImportError if not installed

        self._langfuse = langfuse.Langfuse(
            host=base_url,
            public_key=public_key,
            secret_key=secret_key,
        )
        self._environment = environment
        self._release = release

    @staticmethod
    def _iso_to_ns(iso: str | None) -> int | None:
        if not iso:
            return None
        try:
            return int(datetime.fromisoformat(iso).timestamp() * 1_000_000_000)
        except (ValueError, TypeError):
            return None

    def emit_span(self, span: TraceSpan, *, is_root: bool) -> None:
        try:
            from opentelemetry import trace as otel_trace_api
            from langfuse import LangfuseOtelSpanAttributes

            start_ns = self._iso_to_ns(span.started_at)
            end_ns = self._iso_to_ns(span.ended_at)

            # OTel requires trace_id 32-char hex / parent_span_id 16-char hex.
            # Our generator emits those lengths, but defensively rjust.
            trace_id_otel = span.trace_id.rjust(32, "0")
            parent_span_id_otel = (
                span.parent_span_id.rjust(16, "0") if span.parent_span_id else None
            )

            remote_parent = self._langfuse._create_remote_parent_span(
                trace_id=trace_id_otel,
                parent_span_id=parent_span_id_otel,
            )

            with otel_trace_api.use_span(remote_parent):
                otel_span = self._langfuse._otel_tracer.start_span(
                    name=span.node,
                    start_time=start_ns,
                )
                otel_span.set_attribute(LangfuseOtelSpanAttributes.AS_ROOT, is_root)
                otel_span.set_attribute(
                    LangfuseOtelSpanAttributes.OBSERVATION_TYPE, "span"
                )
                if span.input_summary:
                    otel_span.set_attribute(
                        LangfuseOtelSpanAttributes.OBSERVATION_INPUT,
                        json.dumps(span.input_summary, ensure_ascii=False, default=str),
                    )
                if span.output_summary:
                    otel_span.set_attribute(
                        LangfuseOtelSpanAttributes.OBSERVATION_OUTPUT,
                        json.dumps(
                            span.output_summary, ensure_ascii=False, default=str
                        ),
                    )
                if span.metadata:
                    otel_span.set_attribute(
                        LangfuseOtelSpanAttributes.OBSERVATION_METADATA,
                        json.dumps(span.metadata, ensure_ascii=False, default=str),
                    )
                if span.error:
                    otel_span.set_attribute(
                        LangfuseOtelSpanAttributes.OBSERVATION_STATUS_MESSAGE,
                        span.error,
                    )
                    otel_span.set_attribute(
                        LangfuseOtelSpanAttributes.OBSERVATION_LEVEL, "ERROR"
                    )
                if is_root:
                    otel_span.set_attribute(
                        LangfuseOtelSpanAttributes.ENVIRONMENT, self._environment
                    )
                    otel_span.set_attribute(
                        LangfuseOtelSpanAttributes.RELEASE, self._release
                    )
                otel_span.end(end_time=end_ns)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LangfuseTraceSink: emit_span failed: %s", exc)

    def flush(self) -> None:
        try:
            self._langfuse.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("LangfuseTraceSink: flush failed: %s", exc)


# ---------------------------------------------------------------------------
# SpanHandle
# ---------------------------------------------------------------------------


class SpanHandle:
    """Returned by TraceContext.span(); business code uses set_output / set_metadata / set_error."""

    def __init__(self, span: TraceSpan) -> None:
        self._span = span

    def set_output(self, output_summary: dict) -> None:
        self._span.output_summary = output_summary

    def set_metadata(self, **kwargs) -> None:
        self._span.metadata.update(kwargs)

    def set_error(self, err: str) -> None:
        self._span.error = err

    @property
    def span(self) -> TraceSpan:
        return self._span


# ---------------------------------------------------------------------------
# TraceContext
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _span_id() -> str:
    # OTel spec: 16 hex chars (8 bytes). Required for Langfuse 4.x trace_context.
    return secrets.token_hex(8)


def _trace_id() -> str:
    # OTel spec: 32 hex chars (16 bytes). Must match Langfuse UI search id.
    return secrets.token_hex(16)


class TraceContext:
    """One per /chat request. Holds trace_id + active span stack + sinks broadcast list."""

    def __init__(self, trace_id: str, root_node: str, input_summary: dict, sinks: list[TraceSink]) -> None:
        self.trace_id = trace_id
        self._sinks = sinks
        self._stack: list[SpanHandle] = []
        self._root_emitted = False

        # Create root span eagerly (node + input)
        self._root_span = TraceSpan(
            trace_id=trace_id,
            span_id=_span_id(),
            parent_span_id=None,
            node=root_node,
            started_at=_now_iso(),
            input_summary=copy.deepcopy(input_summary),
        )

    @contextmanager
    def span(
        self,
        node: str,
        *,
        input_summary: dict | None = None,
        metadata: dict | None = None,
    ) -> Iterator[SpanHandle]:
        """Enter: push span on stack, record started_at.
        Exit: pop, record ended_at + duration_ms, emit to all sinks.
        On exception: span.error = repr(exc), emit, then RE-RAISE.
        """
        parent_id = self._stack[-1].span.span_id if self._stack else self._root_span.span_id
        span = TraceSpan(
            trace_id=self.trace_id,
            span_id=_span_id(),
            parent_span_id=parent_id,
            node=node,
            started_at=_now_iso(),
            input_summary=copy.deepcopy(input_summary) if input_summary else {},
            metadata=copy.deepcopy(metadata) if metadata else {},
        )
        handle = SpanHandle(span)
        self._stack.append(handle)
        started_dt = datetime.now(tz=timezone.utc)
        exc_info = None
        try:
            yield handle
        except Exception as exc:  # noqa: BLE001
            span.error = repr(exc)
            exc_info = exc
        finally:
            self._stack.pop()
            ended_dt = datetime.now(tz=timezone.utc)
            span.ended_at = ended_dt.isoformat()
            span.duration_ms = int((ended_dt - started_dt).total_seconds() * 1000)
            # Redact before emitting
            span.input_summary = redact(span.input_summary)
            span.output_summary = redact(span.output_summary)
            span.metadata = redact(span.metadata)
            for sink in self._sinks:
                sink.emit_span(span, is_root=False)
        if exc_info is not None:
            raise exc_info

    def current_span(self) -> SpanHandle | None:
        """The innermost active span."""
        return self._stack[-1] if self._stack else None

    def finalize(self, output_summary: dict, metadata: dict | None = None) -> None:
        """Emit root span with given output. Idempotent — second call is no-op."""
        if self._root_emitted:
            return
        self._root_emitted = True
        ended_dt = datetime.now(tz=timezone.utc)
        self._root_span.ended_at = ended_dt.isoformat()
        # Parse started_at to compute duration
        try:
            started_dt = datetime.fromisoformat(self._root_span.started_at)
            self._root_span.duration_ms = int((ended_dt - started_dt).total_seconds() * 1000)
        except ValueError:
            self._root_span.duration_ms = None
        self._root_span.output_summary = copy.deepcopy(output_summary)
        if metadata:
            self._root_span.metadata.update(metadata)
        # Redact before emitting
        self._root_span.input_summary = redact(self._root_span.input_summary)
        self._root_span.output_summary = redact(self._root_span.output_summary)
        self._root_span.metadata = redact(self._root_span.metadata)
        for sink in self._sinks:
            sink.emit_span(self._root_span, is_root=True)


class _DeadTraceContext(TraceContext):
    """A no-op TraceContext returned when sampling decides to skip this trace."""

    def __init__(self, trace_id: str) -> None:
        # Don't call super().__init__ — we don't need any state
        self.trace_id = trace_id
        self._root_emitted = True  # prevent finalize from doing anything
        self._stack = []

    @contextmanager
    def span(
        self,
        node: str,
        *,
        input_summary: dict | None = None,
        metadata: dict | None = None,
    ) -> Iterator[SpanHandle]:
        # Create a dummy span/handle so code can call set_output etc. without crashing
        dummy_span = TraceSpan(
            trace_id=self.trace_id,
            span_id=_span_id(),
            parent_span_id=None,
            node=node,
            started_at=_now_iso(),
        )
        handle = SpanHandle(dummy_span)
        self._stack.append(handle)
        try:
            yield handle
        except Exception:
            raise
        finally:
            self._stack.pop()

    def current_span(self) -> SpanHandle | None:
        return self._stack[-1] if self._stack else None

    def finalize(self, output_summary: dict, metadata: dict | None = None) -> None:
        pass  # no-op


# ---------------------------------------------------------------------------
# TraceRecorder
# ---------------------------------------------------------------------------


class TraceRecorder:
    """Singleton constructed once at app startup. Factory: build_recorder(config)."""

    def __init__(self, sinks: list[TraceSink], sample_rate: float = 1.0) -> None:
        self._sinks = sinks
        self._sample_rate = sample_rate

    def start_trace(self, root_node: str, input_summary: dict) -> TraceContext:
        trace_id = _trace_id()
        if self._sample_rate <= 0:
            return _DeadTraceContext(trace_id)
        if self._sample_rate < 1.0 and random.random() >= self._sample_rate:
            return _DeadTraceContext(trace_id)
        return TraceContext(
            trace_id=trace_id,
            root_node=root_node,
            input_summary=input_summary,
            sinks=self._sinks,
        )


def build_recorder(config: TraceConfig) -> TraceRecorder:
    """Factory.

    Sinks chosen:
    - JsonlTraceSink always added (even if config.provider == "none")
    - LangfuseTraceSink added iff provider == "langfuse" AND all 3 keys non-empty AND import succeeds
    - If sample_rate == 0: return a TraceRecorder whose start_trace returns a no-op TraceContext
    - sample_rate in (0,1): random per-trace decision; on miss return no-op TraceContext
    """
    sinks: list[TraceSink] = [JsonlTraceSink(config.log_path)]

    if (
        config.provider == "langfuse"
        and config.langfuse_base_url
        and config.langfuse_public_key
        and config.langfuse_secret_key
    ):
        try:
            sink = LangfuseTraceSink(
                base_url=config.langfuse_base_url,
                public_key=config.langfuse_public_key,
                secret_key=config.langfuse_secret_key,
                environment=config.environment,
                release=config.release,
            )
            sinks.append(sink)
        except ImportError:
            logger.warning("langfuse not installed; skipping LangfuseTraceSink")
        except Exception as exc:  # noqa: BLE001
            logger.warning("LangfuseTraceSink init failed: %s", exc)

    return TraceRecorder(sinks=sinks, sample_rate=config.sample_rate)


# ---------------------------------------------------------------------------
# ContextVar injection
# ---------------------------------------------------------------------------

_CURRENT_TRACE_CTX: ContextVar[TraceContext | None] = ContextVar("trace_ctx", default=None)


def current_trace_ctx() -> TraceContext | None:
    return _CURRENT_TRACE_CTX.get()


@contextmanager
def install_trace_ctx(ctx: TraceContext | None) -> Iterator[None]:
    """Set ContextVar on enter, reset on exit. Used by orchestrator.handle_stream."""
    token = _CURRENT_TRACE_CTX.set(ctx)
    try:
        yield
    finally:
        _CURRENT_TRACE_CTX.reset(token)
