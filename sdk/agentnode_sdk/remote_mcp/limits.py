"""Concrete request limits and their enforcement (R13).

Values are deliberately explicit and live here so the review can read them in one place and
a counter-check can revert any one of them. The HTTP layer enforces size/method/content-type
before auth; rate/concurrency/queue after identifying the principal; runtime timeout around
the worker submit. Enforcement must REFUSE (429/413/503/408), never silently serve.

The limiter takes an injectable ``clock`` so counter-checks are deterministic.
"""
from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Deque


@dataclass(frozen=True)
class Limits:
    max_body_bytes: int = 256 * 1024          # 256 KiB request body cap (413)
    calls_per_minute_per_principal: int = 10   # tools/call rate (429)
    call_timeout_seconds: float = 125.0        # per tools/call runtime budget (408/refuse)
    max_concurrent_calls: int = 8              # global in-flight tools/call cap
    max_queued_calls: int = 32                 # queue depth beyond concurrency before 503


class RateLimiter:
    """Fixed-window-per-minute counter per principal. Deterministic with an injected clock."""

    def __init__(self, per_minute: int, clock: Callable[[], float]):
        self._per_minute = per_minute
        self._clock = clock
        self._events: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, principal_id: str) -> bool:
        now = self._clock()
        cutoff = now - 60.0
        with self._lock:
            q = self._events[principal_id]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self._per_minute:
                return False
            q.append(now)
            return True


class ConcurrencyGate:
    """Bounded in-flight count + a bounded queue. ``try_enter_queue`` returns False (-> 503) when
    both the active slots and the queue are full, so overload is refused rather than unbounded.
    It only counts; the transport makes a queued call actually WAIT for one of the
    ``max_concurrent`` run slots before it is promoted, and frees that slot only when the call
    has really finished (not when its caller stopped waiting)."""

    def __init__(self, max_concurrent: int, max_queued: int):
        self._max_concurrent = max_concurrent
        self._max_queued = max_queued
        self._active = 0
        self._queued = 0
        self._lock = threading.Lock()

    def try_enter_queue(self) -> bool:
        with self._lock:
            if self._active + self._queued >= self._max_concurrent + self._max_queued:
                return False
            self._queued += 1
            return True

    def leave_queue(self) -> None:
        """A queued call that gave up (cancelled) without ever running."""
        with self._lock:
            self._queued -= 1

    def promote_to_active(self) -> None:
        with self._lock:
            self._queued -= 1
            self._active += 1

    def release(self) -> None:
        with self._lock:
            self._active -= 1

    @property
    def active(self) -> int:
        return self._active
