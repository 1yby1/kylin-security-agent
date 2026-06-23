from __future__ import annotations

import math
import threading
from collections import defaultdict, deque
from typing import Any


class MetricsCollector:
    """In-process, thread-safe counters + bounded per-tool latency samples."""

    def __init__(self, sample_size: int = 200) -> None:
        self._sample_size = max(1, int(sample_size))
        self._requests: dict[str, int] = defaultdict(int)
        self._blocked = 0
        self._rate_limited = 0
        self._tool_counts: dict[str, int] = defaultdict(int)
        self._tool_samples: dict[str, deque[float]] = defaultdict(self._new_sample_buffer)
        self._llm_success = 0
        self._llm_failure = 0
        self._lock = threading.RLock()

    def _new_sample_buffer(self) -> "deque[float]":
        return deque(maxlen=self._sample_size)

    def record_request(self, endpoint: str) -> None:
        with self._lock:
            self._requests[endpoint] += 1

    def record_rate_limited(self) -> None:
        with self._lock:
            self._rate_limited += 1

    def record_blocked(self) -> None:
        with self._lock:
            self._blocked += 1

    def record_tool(self, tool: str, duration_ms: float) -> None:
        with self._lock:
            self._tool_counts[tool] += 1
            self._tool_samples[tool].append(float(duration_ms))

    def record_llm(self, success: bool) -> None:
        with self._lock:
            if success:
                self._llm_success += 1
            else:
                self._llm_failure += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            tools = {
                tool: {
                    "count": self._tool_counts[tool],
                    "p50_ms": self._percentile(self._tool_samples[tool], 50),
                    "p95_ms": self._percentile(self._tool_samples[tool], 95),
                }
                for tool in self._tool_counts
            }
            total = self._llm_success + self._llm_failure
            return {
                "requests": dict(self._requests),
                "blocked": self._blocked,
                "rate_limited": self._rate_limited,
                "tools": tools,
                "llm": {
                    "success": self._llm_success,
                    "failure": self._llm_failure,
                    "success_rate": round(self._llm_success / total, 3) if total else None,
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._blocked = 0
            self._rate_limited = 0
            self._tool_counts.clear()
            self._tool_samples.clear()
            self._llm_success = 0
            self._llm_failure = 0

    @staticmethod
    def _percentile(samples: "deque[float]", pct: float) -> float | None:
        if not samples:
            return None
        ordered = sorted(samples)
        rank = max(1, math.ceil(pct / 100 * len(ordered)))
        return round(ordered[rank - 1], 3)


_collector = MetricsCollector()


def get_metrics() -> MetricsCollector:
    return _collector
