"""How many jobs this machine runs at once, and who waits.

## What this is for

Every other ceiling in this gateway bounds a customer: a device, an account, a window, a minute.
None of them bounds the MACHINE. A hundred customers allowed two runs each are allowed two
hundred, and two cores serve two hundred by making everybody slower.

That is not a performance problem, it is a billing problem. Measured on the closed alpha: four
identical jobs on two cores each took 7.76 s of wall clock for work that took 3.44 s when run
alone -- while each used the same 3.37 s of CPU. Billed by elapsed time, the customer pays 2.25x
for the same job because the gateway admitted more work than it could run. **The gateway's
arithmetic would appear on the customer's invoice.**

So: a ceiling on what runs at once, and a bounded queue in front of it. And the billed clock does
not start until a slot is actually held, which is the other half and is enforced in `server.py`
where the clock lives.

## What is deliberately NOT here

No thread pool, no worker, no execution. This hands out and takes back permission, and that is
all. The run still happens where it happened before; it simply waits here first.

## Fairness, stated in one sentence

**A freed slot goes to the account that holds the fewest slots and, among those, to whichever was
given a slot longest ago; within one account the job that arrived first goes first.**

Arrival order alone is not fairness between accounts: one account submitting fifty jobs would put
fifty ahead of everybody else's first.

**Counting only the slots held right now is not enough either, and a test caught that.** At the
moment a slot is freed the holder has already let go, so every account is holding zero and the
tie falls back to arrival -- which hands the machine straight back to whoever flooded it. So each
account also carries WHEN IT WAS LAST GIVEN A SLOT, and the one served longest ago goes first. An
account that has never been served sorts before every account that has.

It is not a general scheduler and does not try to be -- no weights, no priorities, no preemption,
because none of those has a customer asking for it yet.

## Order within the queue comes from a counter, not a clock

Position is decided by a strictly increasing arrival number this object hands out, not by a
timestamp. A system clock that steps backwards -- an ntp correction, a suspended VM waking up --
would otherwise reorder a queue, and the reordering would look exactly like correct behaviour.
`arrived_at` is still recorded, because a customer is entitled to know how long THEY have waited;
it just decides nothing.

## What a waiting customer learns about everybody else

Nothing. No position, no queue length, no count of who is ahead. A position is by construction a
count of other people's jobs, so there is no way to report one that does not disclose them.
`waiting_since` is the caller's own clock and tells them nothing they did not already know.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field


def what_this_machine_can_serve() -> int:
    """How many runs this machine has a core for, or 0 when that cannot be established.

    Each sandbox is allotted `limits.cpu = 1.0` by default, so the number of runs this machine
    can serve WITHOUT making them slower is the number of cores it has. That is the comparison,
    and it is stated here rather than left implicit, because it is only true while the default
    holds.

    `sched_getaffinity` where it exists, because a process confined to two cores of a
    thirty-two-core machine can serve two. `os.cpu_count()` otherwise. Zero when neither
    answers -- and zero here means "not established", which is why the caller below treats it as
    "say nothing" rather than as "nothing can run".
    """
    try:
        return len(os.sched_getaffinity(0))                   # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass
    try:
        return int(os.cpu_count() or 0)
    except Exception:                                         # noqa: BLE001
        return 0


def more_than_this_machine_can_serve(ceiling: int) -> int:
    """How many runs the ceiling allows beyond what the machine has a core for. 0 when it does
    not, or when the machine's capacity could not be established.

    ## What is deliberately NOT done with this number

    **It is not clamped.** Serving fewer runs than the operator asked for, while reporting the
    number they asked for, is the gateway deciding for them and then hiding it. It would also
    put back exactly what this file exists to remove: a machine quietly serving less than its
    configuration claims, with the difference landing on somebody's invoice.

    **It is not refused.** A core count is not the whole of what a machine can serve. Work that
    waits on a network or a disk uses almost no CPU, and an operator who knows their jobs are
    shaped like that is not wrong to allow more runs than cores. Turning that judgement into a
    gateway that will not start would make a configuration choice into an outage.

    So: it is SAID. At the moment the value is set, and every time the limits are shown, with
    both numbers named. An operator who means it carries on; one who typed a zero too many finds
    out immediately rather than from a customer's bill.
    """
    have = what_this_machine_can_serve()
    if not have or not ceiling:
        return 0
    return max(0, int(ceiling) - have)


class QueueIsFull(Exception):
    """This machine is at its ceiling and its queue is full, so nothing was accepted.

    Its own type rather than a generic refusal, because a caller has to tell it from a ceiling it
    could fix itself -- a device limit is the caller's own doing and this one is not.
    """

    def __init__(self, message: str, remedy: str, retry_after_s: float = 0.0) -> None:
        super().__init__(message)
        self.remedy = remedy
        #: A hint, deliberately coarse. It is derived from the machine ceiling and the longest
        #: wall clock a job may have -- not from what is currently running, which would tell the
        #: caller about other customers' jobs.
        self.retry_after_s = retry_after_s


@dataclass
class _Waiting:
    """One job in the queue. Holds no artefact, no policy and no output -- only what deciding
    needs, so that nothing about a job is duplicated here and able to drift from the record."""

    run_id: str
    account_id: str
    #: Strictly increasing, handed out by `Slots`. THIS decides order.
    seq: int
    #: When it arrived, for saying how long this job has waited. Decides nothing -- see the
    #: module docstring on why order does not come from a clock.
    arrived_at: float
    granted: threading.Event = field(default_factory=threading.Event)
    #: Set when the job will never run: cancelled, suspended, revoked, or the gateway stopping.
    #: Kept separate from `granted` so a waiter that wakes can tell which happened.
    dropped: str = ""


class Slots:
    """The machine ceiling and the queue in front of it.

    Every decision that changes who holds what is made under one lock. Reading the count, doing
    something else, and then taking a slot is how a ceiling of two lets three through -- the same
    mistake `reserve()` in the server was fixed for, and it would be no better here.
    """

    def __init__(self, ceiling: int = 0, queue_depth: int = 0,
                 now=time.monotonic) -> None:
        #: 0 means no machine ceiling, and then this whole object is a pass-through. That is the
        #: same reading as every other ceiling in `allowance.py`.
        self.ceiling = int(ceiling or 0)
        #: 0 means nobody waits. See `allowance.py` for why this one inverts the convention.
        self.queue_depth = int(queue_depth or 0)
        self._now = now
        self._lock = threading.Lock()
        self._held: dict[str, str] = {}          # run_id -> account_id
        self._waiting: list[_Waiting] = []
        #: The next arrival number. Never reused, never reset.
        self._next_seq = 0
        #: When each account was last given a slot, as an arrival number. An account absent from
        #: here has never been given one and therefore goes ahead of every account that has.
        self._last_served: dict[str, int] = {}

    # ---------------------------------------------------------------- what is going on

    def held_by_account(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for account in self._held.values():
                counts[account] = counts.get(account, 0) + 1
            return counts

    def in_use(self) -> int:
        with self._lock:
            return len(self._held)

    def waiting(self) -> int:
        with self._lock:
            return len(self._waiting)

    # ---------------------------------------------------------------- taking one

    def take_or_queue(self, run_id: str, account_id: str) -> _Waiting | None:
        """A slot now, or a place in the queue, or `QueueIsFull`.

        Returns None when the slot was taken immediately. Returns the ticket to wait on when it
        was not. Raises when neither was possible -- and raising here means nothing was started,
        nothing was recorded as running, and nothing will be billed.
        """
        with self._lock:
            if not self.ceiling:
                self._take_locked(run_id, account_id)
                return None
            if len(self._held) < self.ceiling:
                self._take_locked(run_id, account_id)
                return None
            if len(self._waiting) >= self.queue_depth:
                raise QueueIsFull(
                    "this sandbox is already running as many jobs as it will run at once, and "
                    "its queue is full. Nothing was started and nothing will be charged for.",
                    "Send it again in a moment. If this keeps happening, whoever runs this "
                    "sandbox has it set to run %d at once and to let %d wait."
                    % (self.ceiling, self.queue_depth),
                    retry_after_s=5.0)
            self._next_seq += 1
            ticket = _Waiting(run_id=run_id, account_id=account_id, seq=self._next_seq,
                              arrived_at=self._now())
            self._waiting.append(ticket)
            return ticket

    def wait_for_slot(self, ticket: _Waiting, timeout: float | None = None) -> bool:
        """Block until this ticket holds a slot, or until it is dropped.

        True means the slot is held and the caller must eventually `give_back`. False means it
        never will be, and `ticket.dropped` says why.
        """
        got = ticket.granted.wait(timeout=timeout)
        if not got:
            return False
        return not ticket.dropped

    # ---------------------------------------------------------------- giving one back

    def give_back(self, run_id: str) -> None:
        """Release a slot and hand it to whoever should have it next.

        Safe to call for a run that never held one: a caller that cannot tell whether it got as
        far as holding a slot would otherwise have to guess, and guessing wrong either leaks a
        slot forever or hands out one that is still in use.
        """
        with self._lock:
            self._held.pop(run_id, None)
            self._promote_locked()

    def drop(self, run_id: str, why: str) -> bool:
        """Take a waiting job out of the queue without ever running it.

        This is cancellation, suspension and revocation, all of which have to reach a job that
        has not started. True when it was waiting and is now gone.
        """
        with self._lock:
            for i, ticket in enumerate(self._waiting):
                if ticket.run_id == run_id:
                    ticket.dropped = why or "dropped"
                    self._waiting.pop(i)
                    # Woken so the waiter returns rather than sitting until a timeout. It will
                    # see `dropped` and know it holds nothing.
                    ticket.granted.set()
                    return True
            return False

    def drop_every(self, matches, why: str) -> list[str]:
        """Drop every waiting job for which `matches(ticket)` is true. Returns their ids.

        One pass under one lock: dropping an account's jobs one at a time would let a job slip
        from waiting into running between two calls, which is exactly the case a suspension has
        to prevent.
        """
        gone: list[str] = []
        with self._lock:
            keep: list[_Waiting] = []
            for ticket in self._waiting:
                if matches(ticket):
                    ticket.dropped = why or "dropped"
                    ticket.granted.set()
                    gone.append(ticket.run_id)
                else:
                    keep.append(ticket)
            self._waiting = keep
        return gone

    # ---------------------------------------------------------------- who goes next

    def _take_locked(self, run_id: str, account_id: str) -> None:
        """Record that this account holds a slot. One place, so a slot taken immediately and one
        taken from the queue update the same two things -- an account whose immediate slots were
        not counted as being served would be served first for ever."""
        self._next_seq += 1
        self._held[run_id] = account_id
        self._last_served[account_id] = self._next_seq

    def _promote_locked(self) -> None:
        """Fill free slots from the queue. Called with the lock held, never without it."""
        while self._waiting and (not self.ceiling or len(self._held) < self.ceiling):
            counts: dict[str, int] = {}
            for account in self._held.values():
                counts[account] = counts.get(account, 0) + 1
            chosen = min(self._waiting, key=lambda t: (
                # 1. the account holding fewest right now
                counts.get(t.account_id, 0),
                # 2. among those, the one served longest ago. -1 for an account never served,
                #    so a newcomer is never stuck behind whoever has been using the machine.
                self._last_served.get(t.account_id, -1),
                # 3. within one account, the job that arrived first. A counter, not a clock.
                t.seq))
            self._waiting.remove(chosen)
            self._take_locked(chosen.run_id, chosen.account_id)
            chosen.granted.set()
