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

It also **survives a restart**, and that is not a refinement. `EM3C-GATEWAY-0008` blocked on the
first version, which held the failure history in memory: restarting the gateway constructed an
empty Throttle and cleared the lock, so an attacker who could cause or wait for a restart -- a
deploy, a crash, a reboot, or simply the machine being rebooted nightly -- got their attempts back.
A limit that resets when the process does is not a limit; it is a delay. The same reasoning already
applied to replay protection, and it applies here for the same reason.

The state is small and is re-read before each decision rather than cached, because `agentnode
gateway pair` and `agentnode gateway start` are different processes and both touch it. Re-reading
is not enough on its own, and `EM3C-GATEWAY-0009` said so: read-check-write is a transaction, and
without mutual exclusion two processes each read the same count, each decide, and the second write
erases the first -- losing exactly the failures this exists to count. Every update therefore runs
under a `ProcessLock`, which the kernel releases even if the holder dies.

Corrupt state fails **closed**. A file that exists but cannot be parsed is not the same as no file:
treating it as absent is how a restart into an unlocked state happens, which is the outcome an
attacker would want from tampering. It is treated as locked instead, for the maximum interval, and
then rewritten as valid state so the lock expires normally rather than bricking the gateway
forever. Writes are atomic, so a torn file should not occur; if one does, something wrote to the
gateway directory that should not have.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from agentnode_sdk.gateway.filelock import LockUnavailable, ProcessLock


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

    #: Where the state lives between processes and across restarts. None keeps it in memory,
    #: which is only ever right for a throttle that guards nothing durable.
    path: str | os.PathLike[str] | None = None

    #: A file that exists once this gateway has been set up. Its presence is what makes a MISSING
    #: state file suspicious rather than ordinary: on a gateway that has never run, no state is
    #: exactly right, while on one that has, it means the file was removed. Without this, deleting
    #: the state file resets the lockout, which is the restart bypass with an extra step.
    #:
    #: This is not a defence against an attacker who can write to the gateway directory. Such an
    #: attacker can rewrite the state to say "unlocked", forge tokens.json, or replace identity
    #: outright, and the throttle is not what stands between them and the gateway. It closes the
    #: cheaper move -- delete one file -- and nothing more, which is worth having and worth not
    #: overstating.
    established_marker: str | os.PathLike[str] | None = None
    #: Supplied by a gateway, so reads and writes go through its verified directory descriptor.
    store: "Store | None" = None

    _failures: list[float] = field(default_factory=list, repr=False)
    _locked_until: float = field(default=0.0, repr=False)
    _consecutive_locks: int = field(default=0, repr=False)
    _last_seen: float = field(default=0.0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    #: Set when corrupt state was read, so the fail-closed decision is written back rather
    #: than being recomputed on every call.
    _dirty: bool = field(default=False, repr=False)

    # ------------------------------------------------------------------ durability

    def _across_processes(self):
        """The lock that actually excludes the other interpreter, or a no-op in memory."""
        if self.path is None:
            return _NoLock()
        return ProcessLock(self.path)

    def _fail_closed(self, now: float) -> None:
        """State exists but cannot be read. Assume the worst and say so by locking."""
        self._failures = [now]
        self._locked_until = now + self.max_lock_seconds
        self._consecutive_locks = max(1, self._consecutive_locks)
        self._dirty = True

    def _read_locked(self, now: float) -> None:
        """Refresh from disk. Called under the process lock, before every decision."""
        if self.path is None:
            return
        target = Path(self.path)
        if not self._exists(target):
            marker = Path(self.established_marker) if self.established_marker else None
            if marker is not None and marker.exists():
                # This gateway has run before, so an absent state file was removed rather than
                # never written. Cannot prove there is no lock, so assume there is one.
                self._fail_closed(now)
            return                                # otherwise: genuinely no history yet
        # A transient sharing violation is not evidence of tampering, and treating it as such
        # locked the gateway out of its own pairing under ordinary concurrency -- so retry first.
        raw = None
        deadline = time.monotonic() + 1.0
        while True:
            try:
                raw = (self.store.read(target.name) if self.store is not None
                       else target.read_text(encoding="utf-8"))
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
        if raw is None:
            # It exists and still will not open. "Keep what this object already knew" was the
            # earlier answer and it was wrong in the case that matters: a FRESH object after a
            # restart knows nothing, so it reported no lock. Not being able to read the state is
            # not the same as the state saying there is no lock.
            self._fail_closed(now)
            return
        try:
            loaded = json.loads(raw)
        except ValueError:
            # It exists and will NOT parse. That is different: a file that is present but
            # meaningless is not the same as no file, and treating it as absent is precisely how a
            # restart into an unlocked state happens.
            self._fail_closed(now)
            return
        if not isinstance(loaded, dict):
            self._fail_closed(now)
            return
        try:
            self._failures = [float(t) for t in (loaded.get("failures") or [])]
            self._locked_until = float(loaded.get("locked_until") or 0.0)
            self._consecutive_locks = int(loaded.get("consecutive_locks") or 0)
            self._last_seen = float(loaded.get("last_seen") or 0.0)
        except (TypeError, ValueError):
            self._fail_closed(now)

    def _exists(self, target: Path) -> bool:
        if self.store is None:
            return target.exists()
        try:
            return self.store.read(target.name) is not None
        except OSError:
            return True                           # cannot tell: treated as present, not absent

    def _write_locked(self) -> None:
        if self.path is None:
            return
        target = Path(self.path)
        if self.store is not None:
            self.store.write(target.name, json.dumps({
                "failures": self._failures,
                "locked_until": self._locked_until,
                "consecutive_locks": self._consecutive_locks,
                "last_seen": self._last_seen,
            }))
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".throttle-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump({
                    "failures": self._failures,
                    "locked_until": self._locked_until,
                    "consecutive_locks": self._consecutive_locks,
                    "last_seen": self._last_seen,
                }, fh)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass

    def ensure_initialised(self) -> None:
        """Write empty-but-valid state if there is none.

        Called at the moment the gateway becomes established, so that from then on an ABSENT
        state file means removed rather than never-written. Without this the marker would make
        every gateway that has simply never had a failed attempt look tampered with -- which it
        did, and which locked three tests out of pairing immediately.
        """
        if self.path is None or self._exists(Path(self.path)):
            return
        with self._lock, self._across_processes():
            try:
                self._write_locked()
            except OSError:
                pass

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self._failures = [t for t in self._failures if t > cutoff]

    def locked_for(self, now: float | None = None) -> float:
        """Seconds remaining on the lock, or 0.0. Never negative."""
        now = time.time() if now is None else now
        with self._lock, self._across_processes():
            self._read_locked(now)
            now = steady(self._last_seen, now)
            self._last_seen = now
            # Persisted whether or not the state was corrupt: the anchor is what lets a lock
            # expire, and an anchor that only moves on a write nobody makes never moves.
            try:
                self._write_locked()
            except OSError:
                pass
            self._dirty = False
            return max(0.0, self._locked_until - now)

    def check(self, now: float | None = None) -> None:
        """Raise if attempts are locked out. Called before the attempt is even looked at."""
        remaining = self.locked_for(now)
        if remaining > 0:
            raise Locked(remaining)

    def record_failure(self, now: float | None = None) -> float:
        """Count a failed attempt. Returns the seconds now locked (0.0 if not yet locked)."""
        now = time.time() if now is None else now
        with self._lock, self._across_processes():
            self._read_locked(now)
            now = steady(self._last_seen, now)
            self._last_seen = now
            self._prune(now)
            self._failures.append(now)
            if len(self._failures) <= self.allowed_failures:
                self._write_locked()
                return 0.0
            self._consecutive_locks += 1
            wait = min(
                self.base_lock_seconds * (2 ** (self._consecutive_locks - 1)),
                self.max_lock_seconds,
            )
            self._locked_until = now + wait
            self._write_locked()
            return wait

    def record_success(self, now: float | None = None) -> None:
        """Clear the slate. Someone who proved they know the code is not who this guards against."""
        with self._lock, self._across_processes():
            self._failures = []
            self._locked_until = 0.0
            self._consecutive_locks = 0
            self._write_locked()


@dataclass
class Budget:
    """How many attempts the gateway will consider at all, in a window.

    `EM3C-STATEDIR-DECISION-0001` chose P2-A: no source identity anywhere. The per-origin counter
    it replaces keyed on the immediate TCP peer, which behind a reverse proxy is the proxy -- so
    every client shared one allowance and any of them could lock out the rest. Trusting a
    forwarding header instead would mean trusting whoever can set one.

    So nothing here depends on where an attempt came from. The Throttle beside this counts
    FAILURES and locks after too many; this counts ATTEMPTS, successful or not, and simply stops
    considering them past a ceiling. Between them, an attacker who can reach the gateway is bounded
    without anyone having to decide whose address to believe.

    What it deliberately does not give is per-user isolation: this ceiling is shared, and exhausting
    it denies pairing to everyone until the window passes. That is the trade P2-A makes, and the
    allowance is set high enough that ordinary use never approaches it while grinding does.
    """

    allowance: int = 30
    window_seconds: float = 10 * 60.0
    path: str | os.PathLike[str] | None = None
    #: Present once the gateway has run. Without it, deleting the budget file is indistinguishable
    #: from first use and hands back every attempt -- which `EM3C-EXTERNAL-0013` found, and which
    #: the failure counter beside this one had already been given a marker to prevent.
    established_marker: str | os.PathLike[str] | None = None
    store: "Store | None" = None

    _spent: list = field(default_factory=list, repr=False)
    _last_seen: float = field(default=0.0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _across_processes(self):
        if self.path is None:
            return _NoLock()
        return ProcessLock(self.path)

    def _read(self, now: float) -> None:
        if self.path is None:
            return
        target = Path(self.path)
        try:
            raw = (self.store.read(target.name) if self.store is not None
                   else (target.read_text(encoding="utf-8") if target.exists() else None))
        except OSError:
            self._spent = [now] * self.allowance
            return
        if raw is None:
            marker = Path(self.established_marker) if self.established_marker else None
            if marker is not None and marker.exists():
                # It was removed rather than never written: the same reasoning, and the same
                # answer, as the failure counter beside this one.
                self._spent = [now] * self.allowance
            return
        try:
            loaded = json.loads(raw)
            self._spent = [float(t) for t in (loaded.get("spent") or [])]
            self._last_seen = float(loaded.get("last_seen") or 0.0)
        except (OSError, ValueError, TypeError):
            # Unreadable is not empty. An attempt budget that forgets is not a budget, so the
            # window is treated as fully spent until it would have expired anyway.
            self._spent = [now] * self.allowance

    def _write(self) -> None:
        if self.path is None:
            return
        target = Path(self.path)
        if self.store is not None:
            self.store.write(target.name,
                             json.dumps({"spent": self._spent, "last_seen": self._last_seen}))
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".budget-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump({"spent": self._spent, "last_seen": self._last_seen}, fh)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass

    def ensure_initialised(self) -> None:
        """Write an empty-but-valid budget, so a later absence means removal.

        Created with the gateway rather than on first spend: otherwise a gateway that has simply
        never had a pairing attempt would look tampered with, which is the mistake the failure
        counter beside this one already made once.
        """
        if self.path is None or self._exists(Path(self.path)):
            return
        with self._lock, self._across_processes():
            try:
                self._write()
            except OSError:
                pass

    def _exists(self, target: Path) -> bool:
        if self.store is None:
            return target.exists()
        try:
            return self.store.read(target.name) is not None
        except OSError:
            return True

    def spend(self, now: float | None = None) -> None:
        """Record one attempt, or raise if the window has no room left."""
        now = time.time() if now is None else now
        with self._lock, self._across_processes():
            self._read(now)
            now = steady(self._last_seen, now)
            self._last_seen = now
            cutoff = now - self.window_seconds
            self._spent = [t for t in self._spent if t > cutoff]
            if len(self._spent) >= self.allowance:
                oldest = min(self._spent)
                # Persist the advance before refusing. The anchor only moves when it is written,
                # and if a refusal did not move it, an exhausted budget would never recover --
                # the only thing that could advance it is the call it is refusing.
                try:
                    self._write()
                except OSError:
                    pass
                raise Locked(max(1.0, (oldest + self.window_seconds) - now))
            self._spent.append(now)
            try:
                self._write()
            except OSError:
                pass

    def remaining(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock, self._across_processes():
            self._read(now)
            now = steady(self._last_seen, now)
            self._last_seen = now
            try:
                self._write()
            except OSError:
                pass
            cutoff = now - self.window_seconds
            return max(0, self.allowance - len([t for t in self._spent if t > cutoff]))


class _NoLock:
    """What `_across_processes` returns when the throttle is memory-only."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


#: The furthest a single step is allowed to advance the clock these counters run on. Beyond this,
#: the jump is not credited. `EM3C-EXTERNAL-0013` found that both counters were evaluated against
#: the wall clock, so setting it forward expired a lockout and emptied a window -- buying back the
#: attempts the limits had just taken away. There is no way to tell a real hour from a claimed one,
#: so the answer is not to trust a large one: time never runs backwards here, and a forward jump is
#: worth at most one window per operation, each of which already costs budget.
MAX_CREDITED_STEP = 5 * 60.0


class Store:
    """How a counter reaches its file.

    `EM3C-EXTERNAL-0014` found the pairing failure counter and the attempt budget resolving the
    state directory by pathname while every other secret had moved to the held descriptor. They are
    gateway security state -- one of them decides whether pairing is locked -- so they belong on the
    same footing. A gateway supplies a store that reads and writes relative to its verified
    descriptor; anything constructed without one falls back to the path, which is what a standalone
    unit test wants and what a gateway never uses.

    The lock file beside them is still addressed by name, and that is deliberate: it holds nothing.
    Its whole content is the fact that somebody has it open.
    """

    def __init__(self, read, write):
        self.read = read
        self.write = write


def steady(last_seen: float, now: float, max_step: float = MAX_CREDITED_STEP) -> float:
    """A clock that only moves forward, and never far in one go."""
    if last_seen <= 0:
        return now
    if now < last_seen:
        return last_seen                          # backwards is refused outright
    return min(now, last_seen + max_step)


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
