"""OpenTelemetry tracing for one agent request.

Spans carry the attributes the eval and the threat model rely on: tool name, SQL
hash, row count, token count and cost. Tests use the in-memory exporter, so no
collector is required.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@dataclass(frozen=True)
class RecordedSpan:
    name: str
    attributes: dict[str, Any]


class TraceRecorder:
    def __init__(self, service_name: str = "opsquery") -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer(service_name)

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None) -> Iterator[None]:
        with self.tracer.start_as_current_span(name) as span:
            for key, value in (attributes or {}).items():
                if value is not None:
                    span.set_attribute(key, value)
            yield

    def recorded(self) -> list[RecordedSpan]:
        return [
            RecordedSpan(name=span.name, attributes=dict(span.attributes or {}))
            for span in self.exporter.get_finished_spans()
        ]

    def shutdown(self) -> None:
        self.provider.shutdown()
