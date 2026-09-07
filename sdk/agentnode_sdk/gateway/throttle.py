"""Making a wrong guess expensive.

A pairing code is short enough for a person to read aloud, which is the whole point of it and also
its weakness. Two things protect it. The code is consumed by a single attempt, right or wrong, so
an attacker never gets a second try at the same code. And attempts themselves are limited, so an
attacker cannot sit in a loop hoping the operator issues a code while they are guessing.

The second one is what this module does. It is deliberately not a general-purpose rate limiter:

* It counts **failures**, not requests. A client that pairs successfully should never be slowed
  down, and counting successes would punish the ordinary case to inconvenience the rare one.
* The lock is **temporary and it lengthens**. A fixed short lock is a speed bump; a permanent one
  turns a nuisance into an outage the attacker chose for you. Doubling costs a patient attacker
  everything and costs someone who mistyped twice about a minute.
* Time is injected. A lockout tested with `sleep` is a slow test that still does not prove the
  boundary, and the boundary is exactly what is worth pinning.

The counter is per gateway rather than per source address, because the thing being guessed is one
global code. Per-address counting would let anyone with a handful of addresses multiply their
attempts by the number of addresses they have, which for the kind of attacker this matters against
is not a limit at all.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Throttle:
    """Failure counting with a lengthening temporary lock.

    `allowed_failures` may be exhausted freely; the one after that locks. Each further failure
    while locked doubles the wait, up to `max_lock_seconds`.
    """

    allowed_failures: int = 5
    base_lock_seconds: float = 30.0
    max_lock_seconds: float = 15 * 60.0
    #: Failures older than this are forgotten, so an occasional typo never accumulates into a lock.
    window_seconds: float = 15 * 60.0

    _failures: list[float] = field(default_factory=list, repr=False)
    _locked_until: float = field(default=0.0, repr=False)
    _consecutive_locks: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self._failures = [t for t in self._failures if t > cutoff]

    def locked_for(self, now: float | None = None) -> float:
        """Seconds remaining on the lock, or 0.0. Never negative."""
        now = time.time() if now is None else now
        with self._lock:
            return max(0.0, self._locked_until - now)

    def check(self, now: float | None = None) -> None:
        """Raise if attempts are locked out. Called before the attempt is even looked at."""
        remaining = self.locked_for(now)
        if remaining > 0:
            raise Locked(remaining)

    def record_failure(self, now: float | None = None) -> float:
        """Count a failed attempt. Returns the seconds now locked (0.0 if not yet locked)."""
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            self._failures.append(now)
            if len(self._failures) <= self.allowed_failures:
                return 0.0
            self._consecutive_locks += 1
            wait = min(
                self.base_lock_seconds * (2 ** (self._consecutive_locks - 1)),
                self.max_lock_seconds,
            )
            self._locked_until = now + wait
            return wait

    def record_success(self, now: float | None = None) -> None:
        """Clear the slate. Someone who proved they know the code is not who this guards against."""
        with self._lock:
            self._failures = []
            self._locked_until = 0.0
            self._consecutive_locks = 0


class Locked(Exception):
    """Attempts are temporarily locked. Carries how long, so a person can be told."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        whole = max(1, int(round(seconds)))
        unit = "second" if whole == 1 else "seconds"
        super().__init__(
            f"too many failed pairing attempts. Try again in {whole} {unit}. "
            "If this was not you, the code being guessed is already consumed -- issue a new one "
            "with `agentnode gateway pair` when you are ready."
        )
