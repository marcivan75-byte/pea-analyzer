from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MetricEvent:
    run_id: str
    feature: str
    latency_ms: float
    budget_ms: float
    status: str


class JsonMetricSink:
    def __init__(self, emit: Callable[[str], None]):
        self.emit = emit

    def write(self, event: MetricEvent) -> None:
        self.emit(json.dumps(asdict(event), sort_keys=True))


class JsonlMetricSink:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(self, event: MetricEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(event), sort_keys=True) + "\n")


def summarize_metrics(events: list[MetricEvent]) -> dict[str, float | int]:
    latencies = sorted(event.latency_ms for event in events)
    if not latencies:
        return {"count": 0, "p50_latency_ms": 0.0, "p95_latency_ms": 0.0, "breach_rate": 0.0}
    p95_index = min(len(latencies) - 1, max(0, round(0.95 * len(latencies) + 0.5) - 1))
    breaches = sum(event.status == "SLA_BREACH" for event in events)
    return {
        "count": len(events),
        "p50_latency_ms": statistics.median(latencies),
        "p95_latency_ms": latencies[p95_index],
        "breach_rate": breaches / len(events),
    }


@contextmanager
def measured_feature(*, run_id: str, feature: str, budget_ms: float, sink: JsonMetricSink) -> Iterator[None]:
    start = time.perf_counter()
    status = "PASS"
    try:
        yield
    except Exception:
        status = "ERROR"
        raise
    finally:
        latency = (time.perf_counter() - start) * 1000.0
        if status == "PASS" and latency > budget_ms:
            status = "SLA_BREACH"
        sink.write(MetricEvent(run_id, feature, latency, budget_ms, status))
