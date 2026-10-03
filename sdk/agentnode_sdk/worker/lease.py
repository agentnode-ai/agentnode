"""Which control plane this worker takes orders from, and how an old one stops being it.

A heartbeat is not enough, and that is the whole design note. A heartbeat answers "is my control
plane still there"; it does not answer "am I still ITS worker". Those come apart exactly when it
matters: a gateway is replaced, or partitioned and restarted elsewhere, and now two processes
each believe they own this worker. Both can heartbeat. Both would be obeyed.

So the worker hands out a FENCING TOKEN. Taking the lease returns an epoch, the epoch only ever
goes up, and every instruction that carries or acts on work must name the epoch it was issued
under. A gateway holding epoch 5 after somebody else has taken epoch 6 is not slowed down or
warned -- its instructions stop being valid, at the worker, without the worker needing to know
why there are two of them.

The worker assigns the epoch rather than accepting one. A number the caller chooses is a number
the caller can choose badly: two gateways that had each persisted their own counter could both
present 7. The thing being fenced is the right place to order access to it.

## Time

Expiry is judged on the MONOTONIC clock, never the wall clock. A lease that could be extended by
setting a clock back would be no lease at all, and this deployment moves clocks backwards on
purpose to test its floors.

A monotonic clock does not survive a restart, and that is not a gap -- it is the correct
behaviour. A worker that has just restarted holds no lease and takes no work until a control
plane takes one. It does not resume obeying whoever it obeyed before it went down, because it
cannot know whether that process still exists.

## The numbers, and where they come from

They are derived from the health window rather than invented. `health.MAX_DETECTION_SECONDS` is
the fifteen seconds in which a gateway promises to notice that its worker has gone. The lease
must outlive a healthy gap comfortably -- otherwise a slow moment ends a run -- so it is longer
than that window, and the heartbeat is short enough that several are missed before a lease
lapses. One missed beat is a busy machine; four is a control plane that is not there.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

#: How often the holder renews. Short enough that several are missed before a lease lapses.
HEARTBEAT_EVERY_SECONDS = 5.0

#: How long a lease lives without a renewal. Longer than the health window
#: (`health.MAX_DETECTION_SECONDS`, 15 s) so that a gateway which is merely slow to notice
#: something does not also lose its worker, and long enough for four missed heartbeats.
LEASE_SECONDS = 20.0

#: Where the epoch counter lives, under the worker's own state.
COUNTER_NAME = "lease-epoch.json"

#: How long to wait for a container to appear before giving up on stopping it, when a
#: lapsed lease is what is ending the run. The same allowance the TLS door uses when a
#: revoked caller's connection is cut: the two are the same situation -- work whose
#: principal is no longer entitled to have asked for it.
STOP_APPEAR_SECONDS = 10.0
FILE_MODE = 0o600


class LeaseRefused(Exception):
    """An instruction that is not covered by a live lease held by whoever sent it."""

    def __init__(self, cause: str, because: str, what_to_do: str) -> None:
        super().__init__(because)
        self.cause = cause
        self.because = because
        self.what_to_do = what_to_do


NO_LEASE = "no_lease"
STALE_EPOCH = "stale_epoch"
NOT_THE_HOLDER = "not_the_holder"
EXPIRED = "lease_expired"


@dataclass(frozen=True)
class Held:
    """A lease, as the worker sees it."""

    holder: str
    epoch: int
    #: The monotonic instant it lapses at. Not a wall-clock time, and deliberately not
    #: serialisable into anything that outlives this process.
    until: float

    def alive(self, now: float) -> bool:
        return now < self.until


class Leases:
    """One worker's view of who is entitled to give it work.

    The epoch counter is durable; the lease itself is not. A restarted worker remembers which
    numbers it has already handed out -- so it can never issue one twice, and an instruction
    from before the restart can never become valid again -- and it remembers no holder, so it
    takes no work until somebody takes a lease.
    """

    def __init__(self, at: str | os.PathLike[str] | None = None, *,
                 clock=time.monotonic, ttl: float = LEASE_SECONDS,
                 legacy: str | os.PathLike[str] | None = None) -> None:
        self.at = str(at) if at else ""
        #: Where this counter used to be kept: inside the run journal, where the journal read
        #: it as a run record with no run id and the worker died trying to settle it. The path
        #: is passed in rather than derived, so that the only place that knows the old layout
        #: is the one place that has to.
        self.legacy = str(legacy) if legacy else ""
        self._clock = clock
        self._ttl = float(ttl)
        self._held: Held | None = None
        self.carried_from = ""
        self._last_epoch = self._read_counter()

    # ------------------------------------------------------------------ the durable counter

    def _read_one(self, path: str) -> int | None:
        """The number in one counter file, None when there is no file, or a refusal.

        A counter that EXISTS and cannot be read is never treated as zero: re-issuing an
        epoch would make a retired gateway's instructions valid again.
        """
        if not path:
            return None
        try:
            with open(path, "rb") as handle:
                return int(json.loads(handle.read().decode("utf-8")).get("last_epoch") or 0)
        except (OSError, UnicodeDecodeError, ValueError):
            if os.path.exists(path):
                raise LeaseRefused(
                    "lease_counter_unreadable",
                    "this worker's lease counter at %s cannot be read, so it cannot promise "
                    "never to issue the same epoch twice." % path,
                    "Establish what happened to the file before starting the worker again. "
                    "Deleting it would let a control plane from before the restart give orders "
                    "again.") from None
            return None

    def _read_counter(self) -> int:
        """The highest epoch this worker has ever issued, wherever it was written down.

        CARRIED, NOT RESTARTED. The counter used to live inside the run journal directory;
        moving it without bringing the number along would read a missing file as zero and
        hand out epoch 1 again, and an epoch handed out twice is the one thing this file
        exists to prevent.

        This does the carrying automatically, which is deliberately UNLIKE `pki floor adopt`.
        The floor's adopt is an operator step because re-initialising a floor grants a fresh
        tolerance -- a thing of value, so somebody has to ask for it. Carrying a counter
        cannot grant anything: it takes the MAXIMUM of what it finds, so it can only preserve
        or raise, never lower. Refusing to start instead would take every existing deployment
        down to protect a property that `max` already guarantees.
        """
        here = self._read_one(self.at)
        there = self._read_one(self.legacy) if self.legacy != self.at else None
        if there is None:
            return here or 0
        highest = max(here or 0, there)
        if here is None or here < there:
            # Write the carried value where it now belongs BEFORE removing the old file, so a
            # crash in between leaves the number in at least one of the two places.
            self._write_counter(highest)
            self.carried_from = self.legacy
        try:
            os.unlink(self.legacy)
        except OSError:                                       # pragma: no cover - best effort
            pass
        return highest

    def _write_counter(self, epoch: int) -> None:
        if not self.at:
            return
        temporary = "%s.new" % self.at
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        with os.fdopen(handle, "wb") as fh:
            fh.write(json.dumps({"last_epoch": int(epoch)}).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, self.at)

    # ------------------------------------------------------------------ taking and keeping

    def take(self, holder: str) -> Held:
        """Grant the next epoch to this holder. Whoever held it before is fenced off.

        Always granted, and always with a HIGHER number. Refusing a takeover would leave a
        worker owned by a control plane that no longer exists, which is the failure this is
        for; the protection is not that a takeover is hard, it is that the previous holder's
        instructions stop counting the instant one happens.
        """
        if not str(holder or "").strip():
            raise LeaseRefused(NOT_THE_HOLDER, "a lease is held by a named gateway",
                               "This is a programming error.")
        epoch = self._last_epoch + 1
        # Durable BEFORE it is granted: a crash between granting and recording would let the
        # same epoch be handed out twice after a restart.
        self._write_counter(epoch)
        self._last_epoch = epoch
        self._held = Held(holder=str(holder), epoch=epoch, until=self._clock() + self._ttl)
        return self._held

    def renew(self, holder: str, epoch: int) -> Held:
        """Push the expiry out. Only the current holder, and only for the current epoch."""
        self.check(holder, epoch)
        self._held = Held(holder=str(holder), epoch=int(epoch),
                          until=self._clock() + self._ttl)
        return self._held

    def check(self, holder: str, epoch: int | None) -> Held:
        """May this caller give this worker work right now? Returns the lease, or refuses.

        Called immediately before a container is started as well as while one runs, because a
        lease that was alive when a request arrived may have lapsed while the request was being
        prepared -- and starting foreign code for a control plane that has since gone is the
        thing this exists to prevent.
        """
        held = self._held
        if held is None:
            raise LeaseRefused(
                NO_LEASE,
                "this worker holds no lease, so nothing may give it work. A worker that has "
                "just restarted does not resume obeying whoever it obeyed before: it cannot "
                "know whether that process still exists.",
                "Take a lease before sending work.")
        if not held.alive(self._clock()):
            raise LeaseRefused(
                EXPIRED,
                "the lease held by %s (epoch %d) lapsed: nothing renewed it within %.0f "
                "seconds." % (held.holder, held.epoch, self._ttl),
                "Take a lease again. Work that was running when it lapsed was stopped.")
        if str(holder) != held.holder:
            raise LeaseRefused(
                NOT_THE_HOLDER,
                "this worker's lease is held by %s, and %s is not it. Only one control plane "
                "gives a worker work." % (held.holder, holder or "an unnamed caller"),
                "Take the lease, which ends the other holder's authority, or stop sending "
                "work to a worker that is not yours.")
        if epoch is None or int(epoch) != held.epoch:
            raise LeaseRefused(
                STALE_EPOCH,
                "this instruction names epoch %s and the live lease is epoch %d. An epoch only "
                "ever goes up, so this one is from before somebody else took over -- or from "
                "before this holder itself reconnected."
                % ("none" if epoch is None else int(epoch), held.epoch),
                "Take a lease again and reissue the work under the epoch it returns.")
        return held

    # ------------------------------------------------------------------ looking

    def current(self) -> Held | None:
        """The live lease, or None. An expired one is not a lease."""
        held = self._held
        if held is None or not held.alive(self._clock()):
            return None
        return held

    def lapsed(self) -> Held | None:
        """A lease that existed and has run out, once, so a caller can act on the transition."""
        held = self._held
        if held is not None and not held.alive(self._clock()):
            return held
        return None

    def release(self) -> None:
        """Give it up deliberately. The epoch is NOT reused."""
        self._held = None


__all__ = ["EXPIRED", "STOP_APPEAR_SECONDS", "HEARTBEAT_EVERY_SECONDS", "Held", "LEASE_SECONDS", "Leases",
           "LeaseRefused", "NOT_THE_HOLDER", "NO_LEASE", "STALE_EPOCH"]
