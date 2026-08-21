from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass


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
