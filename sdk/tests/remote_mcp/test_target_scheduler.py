"""The one scheduler (target-router profile T12; Codex D7 replaced, R4)."""
from __future__ import annotations

import asyncio

import pytest

from agentnode_sdk.remote_mcp.scheduler import Scheduler, SchedulerRefused


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def sched(**kw):
    base = dict(global_slots=2, global_queue=8, max_wait=2.0, per_account=10)
    base.update(kw)
    return Scheduler(**base)


def test_scheduler_acquires_resources_atomically():
    async def go():
        s = sched(global_slots=2)
        a1 = await s.acquire("acct-a", "A", concurrency=1, queue=4)
        # A is at its own cap: the next A call waits -- WITHOUT taking the free global slot
        waiting = asyncio.ensure_future(s.acquire("acct-a", "A", concurrency=1, queue=4))
        await asyncio.sleep(0.01)
        assert s.running() == 1 and s.running("A") == 1 and s.queued("A") == 1
        b1 = await s.acquire("acct-b", "B", concurrency=1, queue=4)  # B gets the free slot at once
        assert s.running() == 2 and s.running("B") == 1
        # B finishes while A is still at its cap: the freed global slot must NOT go to A's waiter
        b1.release()
        await asyncio.sleep(0.01)
        assert not waiting.done() and s.running("A") == 1 and s.running() == 1
        b1 = await s.acquire("acct-b", "B", concurrency=1, queue=4)
        a1.release()
        a2 = await asyncio.wait_for(waiting, 1)
        assert s.running("A") == 1 and s.running() == 2 and s.queued() == 0
        for g in (a2, b1):
            g.release()
        assert s.running() == 0 and s.in_flight("acct-a") == 0 and s.in_flight("acct-b") == 0
    run(go())


def test_no_cross_gate_slot_holding():
    async def go():
        s = sched(global_slots=1)
        a1 = await s.acquire("acct-a", "A", concurrency=1, queue=4)
        wa = asyncio.ensure_future(s.acquire("acct-a", "A", concurrency=1, queue=4))
        wb = asyncio.ensure_future(s.acquire("acct-b", "B", concurrency=1, queue=4))
        await asyncio.sleep(0.01)
        a1.release()
        await asyncio.sleep(0.01)
        # exactly one of the two waiters holds the single global slot; the other holds NOTHING
        done = [f for f in (wa, wb) if f.done()]
        assert len(done) == 1 and s.running() == 1
        assert s.running("A") + s.running("B") == 1
        done[0].result().release()
        await asyncio.sleep(0.01)
        rest = [f for f in (wa, wb) if f is not done[0]][0]
        assert rest.done()
        rest.result().release()
        assert s.running() == 0
    run(go())


def test_round_robin_bounds_starvation_of_a_quiet_target():
    async def go():
        s = sched(global_slots=1, global_queue=20)
        holder = await s.acquire("acct-h", "HOT", concurrency=1, queue=10)
        hot = [asyncio.ensure_future(s.acquire("acct-%d" % i, "HOT", concurrency=1, queue=10)) for i in range(5)]
        await asyncio.sleep(0.01)
        quiet = asyncio.ensure_future(s.acquire("acct-q", "QUIET", concurrency=1, queue=10))
        await asyncio.sleep(0.01)
        order = []
        holder.release()
        for _ in range(6):
            await asyncio.sleep(0.01)
            for name, f in [("quiet", quiet)] + [("hot%d" % i, f) for i, f in enumerate(hot)]:
                if f.done() and name not in order:
                    order.append(name)
                    f.result().release()
                    break
        assert order.index("quiet") <= 1, order          # QUIET is served within one HOT grant
        assert order[0] == "hot0" or order[0] == "quiet"
        assert [n for n in order if n.startswith("hot")] == sorted(n for n in order if n.startswith("hot"))
    run(go())


def test_refusals_are_named():
    async def go():
        s = sched(global_slots=1, global_queue=1, per_account=1)
        g = await s.acquire("acct-a", "A", concurrency=1, queue=1)
        with pytest.raises(SchedulerRefused) as r:
            await s.acquire("acct-a", "A", concurrency=1, queue=1)
        assert r.value.code == "too_many_concurrent_calls"
        w = asyncio.ensure_future(s.acquire("acct-b", "A", concurrency=1, queue=1))
        await asyncio.sleep(0.01)
        with pytest.raises(SchedulerRefused) as r:
            await s.acquire("acct-c", "B", concurrency=1, queue=1)
        assert r.value.code == "overloaded"                    # the global queue is full
        g.release()
        (await w).release()
        s2 = sched(global_slots=1, global_queue=10)
        g2 = await s2.acquire("acct-a", "A", concurrency=1, queue=1)
        w2 = asyncio.ensure_future(s2.acquire("acct-b", "A", concurrency=1, queue=1))
        await asyncio.sleep(0.01)
        with pytest.raises(SchedulerRefused) as r:
            await s2.acquire("acct-c", "A", concurrency=1, queue=1)
        assert r.value.code == "target_full"
        g2.release()
        (await w2).release()
    run(go())


def test_queue_wait_is_bounded_and_leaves_nothing():
    async def go():
        s = sched(global_slots=1, max_wait=0.2)
        g = await s.acquire("acct-a", "A", concurrency=1, queue=4)
        with pytest.raises(SchedulerRefused) as r:
            await s.acquire("acct-b", "A", concurrency=1, queue=4)
        assert r.value.code == "queue_wait_exceeded"
        assert s.queued() == 0 and s.in_flight("acct-b") == 0
        g.release()
        assert s.running() == 0
    run(go())


def test_cancellation_before_and_after_grant_leaks_nothing():
    async def go():
        s = sched(global_slots=1)
        g = await s.acquire("acct-a", "A", concurrency=1, queue=4)
        w = asyncio.ensure_future(s.acquire("acct-b", "A", concurrency=1, queue=4))
        await asyncio.sleep(0.01)
        w.cancel()                                            # before its grant
        await asyncio.sleep(0.01)
        assert s.queued() == 0 and s.in_flight("acct-b") == 0
        w2 = asyncio.ensure_future(s.acquire("acct-c", "A", concurrency=1, queue=4))
        await asyncio.sleep(0.01)
        g.release()                                           # grants w2 ...
        w2.cancel()                                           # ... which is cancelled right after
        await asyncio.sleep(0.01)
        assert s.running() == 0 and s.in_flight("acct-c") == 0
        g.release()                                           # a second release is a no-op
        assert s.running() == 0
    run(go())
