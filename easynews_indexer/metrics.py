from __future__ import annotations

import threading
import time
from collections import Counter


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters = Counter()
        self._latency_sum = 0.0
        self._latency_count = 0

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def observe_search(self, seconds: float) -> None:
        with self._lock:
            self._latency_sum += seconds
            self._latency_count += 1

    def render(self) -> str:
        with self._lock:
            lines = []
            for key, value in sorted(self._counters.items()):
                lines.append(f"easynews_indexer_{key} {value}")
            lines.append(f"easynews_indexer_search_latency_seconds_sum {self._latency_sum:.6f}")
            lines.append(f"easynews_indexer_search_latency_seconds_count {self._latency_count}")
            return "\n".join(lines) + "\n"


METRICS = Metrics()


class SearchTimer:
    def __enter__(self):
        self.started = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        METRICS.observe_search(time.monotonic() - self.started)
