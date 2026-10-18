"""One scheduler for every call that runs code: global, per-target and per-account at once.

Target-router profile T12, decision Q-TGT-0001 D7 (replaced), resolution R4. The transport used
to queue every call behind ONE process-wide gate. With several execution targets, a second gate
per target in front of or behind it would reserve one resource while waiting for another --
head-of-line blocking, and a hot target starving every other. This grants everything a call needs
in ONE step, on the event loop, and nothing is held while waiting:

* a call is refused at once (never queued) when its account already has its in-flight limit, when
  the global queue is full (`overloaded`) or when its target's queue is full (`target_full`);
* otherwise it waits in its TARGET's FIFO queue. Whenever capacity frees, grants go round-robin
  across the targets that have waiters, each grant taking a global running slot AND a slot of that
  target together -- so a target at its own cap never blocks another target, and no target
  exceeds its cap;
* a waiter that is not granted within the queue-wait bound is removed (`queue_wait_exceeded`);
* release is exactly once and cancellation-safe: a waiter cancelled before its grant leaves no
  trace, one cancelled after its grant gives the grant back.

Single process, as the existing limits are (`limits.py`); a second process needs a shared
semaphore first.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field


class SchedulerRefused(Exception):
    def __init__(self, code: str, retry_after: float):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass
class _Waiter:
    account: str
    target_id: str
    queued_at: float
    future: asyncio.Future = field(repr=False)


class Grant:
    """What a granted call holds. `release()` gives everything back, once."""

    def __init__(self, scheduler: "Scheduler", account: str, target_id: str, queued_at: float, granted_at: float):
        self._scheduler = scheduler
        self.account = account
        self.target_id = target_id
        self.queued_at = queued_at
        self.granted_at = granted_at
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._scheduler._give_back(self)


class Scheduler:
    def __init__(self, *, global_slots: int, global_queue: int, max_wait: float, per_account: int,
                 clock=time.monotonic):
        self.global_slots = int(global_slots)
        self.global_queue = int(global_queue)
        self.max_wait = float(max_wait)
        self.per_account = int(per_account)
        self._clock = clock
        self._running_global = 0
        self._running: dict[str, int] = {}
        self._limits: dict[str, tuple[int, int]] = {}            # target -> (concurrency, queue)
        self._queues: dict[str, deque] = {}
        self._order: deque = deque()                           # round-robin order of targets with waiters
        self._accounts: dict[str, int] = {}                    # in flight: queued + running

    # ------------------------------------------------------------ introspection (tests, audit)
    def running(self, target_id: str | None = None) -> int:
        return self._running_global if target_id is None else self._running.get(target_id, 0)

    def queued(self, target_id: str | None = None) -> int:
        if target_id is None:
            return sum(len(q) for q in self._queues.values())
        return len(self._queues.get(target_id, ()))

    def in_flight(self, account: str) -> int:
        return self._accounts.get(account, 0)

    # ------------------------------------------------------------ acquire
    async def acquire(self, account: str, target_id: str, *, concurrency: int, queue: int) -> Grant:
        self._limits[target_id] = (max(1, int(concurrency)), max(1, int(queue)))
        if self._accounts.get(account, 0) >= self.per_account:
            raise SchedulerRefused("too_many_concurrent_calls", self.max_wait)
        now = self._clock()
        if self._can_run_now(target_id):
            self._take(account, target_id)
            return Grant(self, account, target_id, now, now)
        if self.queued() >= self.global_queue:
            raise SchedulerRefused("overloaded", self.max_wait)
        if self.queued(target_id) >= self._limits[target_id][1]:
            raise SchedulerRefused("target_full", self.max_wait)
        fut = asyncio.get_running_loop().create_future()
        waiter = _Waiter(account, target_id, now, fut)
        self._queues.setdefault(target_id, deque()).append(waiter)
        if target_id not in self._order:
            self._order.append(target_id)
        self._accounts[account] = self._accounts.get(account, 0) + 1     # a queued call is in flight
        try:
            await asyncio.wait_for(asyncio.shield(fut), timeout=self.max_wait)
        except asyncio.TimeoutError:
            if fut.done() and not fut.cancelled():
                return fut.result()                                       # granted at the last moment
            self._withdraw(waiter)
            raise SchedulerRefused("queue_wait_exceeded", self.max_wait) from None
        except BaseException:
            if fut.done() and not fut.cancelled():
                fut.result().release()                                    # granted, then cancelled: give back
            else:
                self._withdraw(waiter)
            raise
        return fut.result()

    # ------------------------------------------------------------ internals (event loop only)
    def _can_run_now(self, target_id: str) -> bool:
        conc, _ = self._limits[target_id]
        return (self._running_global < self.global_slots and self._running.get(target_id, 0) < conc
                and not self._queues.get(target_id))           # FIFO: never overtake this target's queue

    def _take(self, account: str, target_id: str, *, already_counted: bool = False) -> None:
        self._running_global += 1
        self._running[target_id] = self._running.get(target_id, 0) + 1
        if not already_counted:
            self._accounts[account] = self._accounts.get(account, 0) + 1

    def _withdraw(self, waiter: _Waiter) -> None:
        q = self._queues.get(waiter.target_id)
        if q is not None and waiter in q:
            q.remove(waiter)
            self._accounts[waiter.account] = max(0, self._accounts.get(waiter.account, 0) - 1)
            if not q:
                self._queues.pop(waiter.target_id, None)
                if waiter.target_id in self._order:
                    self._order.remove(waiter.target_id)
        if not waiter.future.done():
            waiter.future.cancel()

    def _give_back(self, grant: Grant) -> None:
        self._running_global = max(0, self._running_global - 1)
        self._running[grant.target_id] = max(0, self._running.get(grant.target_id, 0) - 1)
        self._accounts[grant.account] = max(0, self._accounts.get(grant.account, 0) - 1)
        self._dispatch()

    def _dispatch(self) -> None:
        """Grant round-robin across targets with waiters, one at a time, while capacity lasts."""
        while self._running_global < self.global_slots and self._order:
            granted = False
            for _ in range(len(self._order)):
                target_id = self._order[0]
                self._order.rotate(-1)
                q = self._queues.get(target_id)
                if not q:
                    continue
                if self._running.get(target_id, 0) >= self._limits[target_id][0]:
                    continue                                       # at its own cap: the next target goes
                waiter = q.popleft()
                if not q:
                    self._queues.pop(target_id, None)
                    self._order.remove(target_id)
                if waiter.future.done():                           # timed out / cancelled meanwhile
                    continue
                self._take(waiter.account, target_id, already_counted=True)
                waiter.future.set_result(Grant(self, waiter.account, target_id, waiter.queued_at, self._clock()))
                granted = True
                break
            if not granted:
                return


__all__ = ["Grant", "Scheduler", "SchedulerRefused"]
