"""Concrete request limits and their enforcement (R13).

The values are the decided initial limits (Q-RMCP-0001 §6), explicit here so the review can
read them in one place and a counter-check can revert any one of them. Layers:

  nginx (sdk/deploy/remote-mcp/nginx-mcp.conf): header block 16 KiB / 8 KiB per field, body
      256 KiB, header read 10 s, body read 15 s, upstream 135 s, unauthenticated requests
      10/min/IP burst 5.
  app (this module + http_transport): body 256 KiB (byte counter), JSON depth 32 / 2,000
      members / 64 KiB strings, tool output 1 MiB, discovery/list 60/min/account burst 15,
      tools/call 10/min/account + 30/min/token burst 3, 2 calls in flight per account, 8 per
      process, a 32-deep queue with a 5 s maximum wait, 125 s end-to-end call timeout.

This deployment runs ONE service process, so the process caps are also the deployment caps
(the decided 16/deployment is not exceeded); a multi-process deployment needs the shared
Redis lease/semaphore the decision names before it may run a second process.

Overflow is refused, never served: rate/concurrency/queue -> 429 + Retry-After.
Every limiter takes an injectable ``clock`` so counter-checks are deterministic.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Limits:
    max_body_bytes: int = 256 * 1024          # request body (413)
    max_json_depth: int = 32                  # nesting of the parsed JSON-RPC message
    max_json_members: int = 2000              # object members + array elements, total
    max_json_string_bytes: int = 64 * 1024    # any single string (key or value), UTF-8
    max_tool_output_bytes: int = 1024 * 1024  # a result larger than this is a safe error
    list_per_minute_per_principal: int = 60   # discovery/list and every non-call request
    list_burst: int = 15
    calls_per_minute_per_principal: int = 10  # tools/call per account
    calls_per_minute_per_token: int = 30      # tools/call per token
    call_burst: int = 3                       # burst of the per-account tools/call bucket
    call_burst_per_token: int = 3             # burst of the per-token tools/call bucket
    call_timeout_seconds: float = 125.0       # end-to-end per tools/call
    max_concurrent_calls: int = 8             # calls RUNNING at once in this process
    max_concurrent_calls_per_principal: int = 2   # calls in flight (queued or running) per account
    max_queued_calls: int = 32                # calls waiting for a run slot
    max_queue_wait_seconds: float = 5.0       # longest wait for a run slot before 429


class RateLimiter:
    """Token bucket per key: ``burst`` requests at once, refilled at ``per_minute``.
    ``allow`` returns 0.0 when admitted, else the seconds until the next request would be."""

    def __init__(self, per_minute: int, clock: Callable[[], float], burst: Optional[int] = None):
        self._rate = per_minute / 60.0
        self._burst = float(burst if burst is not None else per_minute)
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}   # key -> (tokens, last)
        self._lock = threading.Lock()

    def wait_for(self, key: str) -> float:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (self._burst, now))
            tokens = min(self._burst, tokens + (now - last) * self._rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return 0.0
            self._buckets[key] = (tokens, now)
            return (1.0 - tokens) / self._rate

    def allow(self, key: str) -> bool:
        return self.wait_for(key) == 0.0


def retry_after(seconds: float) -> str:
    return str(max(1, int(math.ceil(seconds))))


class PrincipalInFlight:
    """Calls in flight (queued or running) per account, capped; released exactly when the
    call has really finished."""

    def __init__(self, cap: int):
        self._cap = cap
        self._n: dict[str, int] = {}
        self._lock = threading.Lock()

    def try_enter(self, key: str) -> bool:
        with self._lock:
            if self._n.get(key, 0) >= self._cap:
                return False
            self._n[key] = self._n.get(key, 0) + 1
            return True

    def leave(self, key: str) -> None:
        with self._lock:
            self._n[key] = self._n.get(key, 1) - 1
            if self._n[key] <= 0:
                self._n.pop(key, None)


def json_limit_violation(obj: Any, limits: "Limits") -> Optional[str]:
    """The first JSON shape limit the parsed message exceeds, or None. Iterative (no recursion
    limit to hit), counting members across the whole message."""
    members = 0
    stack = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > limits.max_json_depth:
            return "JSON nesting deeper than %d" % limits.max_json_depth
        if isinstance(node, dict):
            members += len(node)
            for k, v in node.items():
                if len(str(k).encode("utf-8")) > limits.max_json_string_bytes:
                    return "JSON string longer than %d bytes" % limits.max_json_string_bytes
                stack.append((v, depth + 1))
        elif isinstance(node, list):
            members += len(node)
            stack.extend((v, depth + 1) for v in node)
        elif isinstance(node, str) and len(node.encode("utf-8")) > limits.max_json_string_bytes:
            return "JSON string longer than %d bytes" % limits.max_json_string_bytes
        if members > limits.max_json_members:
            return "JSON message has more than %d members" % limits.max_json_members
    return None


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
