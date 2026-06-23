from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from backend.security.auth import session_principal


class RateLimiter:
    """Per-key sliding-window rate limiter (in-process, thread-safe)."""

    def __init__(
        self,
        limit_per_window: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
        max_keys: int = 10000,
    ) -> None:
        self._limit = max(1, int(limit_per_window))
        self._window = float(window_seconds)
        self._clock = clock or time.monotonic
        self._hits: dict[str, deque[float]] = {}
        self._max_keys = max(1, int(max_keys))
        self._lock = threading.RLock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            self._trim(bucket, now)
            if len(bucket) >= self._limit:
                return False
            bucket.append(now)
            if len(self._hits) > self._max_keys:
                self._sweep(now)
            return True

    def retry_after(self, key: str) -> int:
        now = self._clock()
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return 0
            self._trim(bucket, now)
            if len(bucket) < self._limit:
                return 0
            return max(1, int(self._window - (now - bucket[0])) + 1)

    def _trim(self, bucket: "deque[float]", now: float) -> None:
        cutoff = now - self._window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def _sweep(self, now: float) -> None:
        """Trim empty buckets, then evict oldest keys if still over capacity."""
        # Trim all buckets first
        for bucket in self._hits.values():
            self._trim(bucket, now)
        # Delete empty buckets
        empty_keys = [k for k, v in self._hits.items() if not v]
        for k in empty_keys:
            del self._hits[k]
        # If still over capacity, evict by oldest most-recent timestamp
        if len(self._hits) > self._max_keys:
            # Sort by most-recent timestamp (bucket[-1]) ascending
            sorted_keys = sorted(self._hits.items(), key=lambda item: item[1][-1])
            while len(self._hits) > self._max_keys:
                k, _ = sorted_keys.pop(0)
                del self._hits[k]


class ConcurrencyGate:
    """Non-blocking global concurrency cap backed by a bounded semaphore."""

    def __init__(self, max_concurrent: int) -> None:
        self._sem = threading.BoundedSemaphore(max(1, int(max_concurrent)))

    def try_acquire(self) -> bool:
        return self._sem.acquire(blocking=False)

    def release(self) -> None:
        try:
            self._sem.release()
        except ValueError:
            # Released more times than acquired; ignore to stay robust.
            pass


def rate_limit_key(token: str | None, client_host: str | None) -> str:
    """Authenticated callers are keyed by principal; anonymous by client IP."""
    if token:
        return session_principal(token)
    return f"ip:{client_host or 'unknown'}"
