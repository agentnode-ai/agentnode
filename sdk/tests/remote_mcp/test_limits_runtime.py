"""R13: the decided limits (Q-RMCP-0001 §6) enforced at the app layer, measured under real
concurrency in one event loop with a blocking adapter that records the true peak:

  * at most max_concurrent_calls RUN at once (process cap), extra calls wait in a bounded
    queue for at most max_queue_wait_seconds; beyond the queue or past the wait -> 429 +
    Retry-After;
  * at most max_concurrent_calls_per_principal calls in flight per account -> 429;
  * a timed-out call keeps its slots until its worker call has really finished;
  * tools/call 10/min/account + 30/min/token with burst 3; discovery/list 60/min burst 15;
  * JSON depth / member / string caps and the 1 MiB result cap refuse, never serve.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx

from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
from agentnode_sdk.remote_mcp.limits import Limits, RateLimiter, json_limit_violation
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.tokens import TokenStore

HOST = "mcp.agentnode.net"
RESOURCE = "https://mcp.agentnode.net/mcp"
FAST = dict(calls_per_minute_per_principal=1000, calls_per_minute_per_token=1000, call_burst=1000,
            call_burst_per_token=1000)


class BlockingAdapter:
    def __init__(self, payload=None):
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.running = 0
        self.peak = 0
        self.done = 0
        self.payload = payload

    def handle(self, msg):
        if msg.get("method") == "tools/list":
            return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": []}}
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        self.release.wait(10)
        with self.lock:
            self.running -= 1
            self.done += 1
        return {"jsonrpc": "2.0", "id": msg.get("id"),
                "result": {"isError": False, "content": [{"type": "text", "text": self.payload or "ok"}]}}


def app_with(limits, payload=None):
    store = TokenStore(b"k" * 32)
    adapter = BlockingAdapter(payload)
    app = RemoteMCPApp(adapter=adapter, token_store=store,
                       authz=AuthorizationServer(issuer="https://mcp.agentnode.net", resource=RESOURCE, token_store=store),
                       resource=ResourceConfig(resource=RESOURCE, authorization_server="https://mcp.agentnode.net"),
                       allowed_hosts=(HOST,), limits=limits)

    def tok(principal="p"):
        return store.mint(principal_id=principal, audience=RESOURCE, scopes=("mcp:use",),
                          issuance_kind="oauth", ttl_seconds=600).plaintext
    return app, adapter, tok


def call_body(mid, method="tools/call", params=None):
    p = params if params is not None else {"name": "sequentialthinking", "arguments": {}}
    return json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": p}).encode()


def hdr(token):
    return {"content-type": "application/json", "accept": "application/json, text/event-stream",
            "mcp-protocol-version": "2025-11-25", "authorization": "Bearer %s" % token}


async def _wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while not pred() and time.monotonic() < end:
        await asyncio.sleep(0.02)
    return pred()


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://%s" % HOST, timeout=30)


def test_process_cap_runs_at_most_n_and_queue_overflow_is_429():
    app, adapter, tok = app_with(Limits(max_concurrent_calls=2, max_queued_calls=1, **FAST))
    toks = [tok("p%d" % i) for i in range(4)]                      # distinct accounts

    async def go():
        async with client(app) as c:
            tasks = [asyncio.ensure_future(c.post("/mcp", headers=hdr(toks[i]), content=call_body(i)))
                     for i in range(3)]
            assert await _wait_until(lambda: adapter.running == 2)  # two RUN, one waits
            await asyncio.sleep(0.2)
            assert adapter.running == 2
            over = await c.post("/mcp", headers=hdr(toks[3]), content=call_body(9))
            adapter.release.set()
            return over, await asyncio.gather(*tasks)

    over, done = asyncio.run(go())
    assert over.status_code == 429 and over.json()["error"] == "overloaded" and over.headers["retry-after"] == "5"
    assert [r.status_code for r in done] == [200, 200, 200]
    assert adapter.peak == 2 and adapter.done == 3


def test_per_account_in_flight_cap_is_2():
    app, adapter, tok = app_with(Limits(max_concurrent_calls=8, **FAST))
    t = tok("same")

    async def go():
        async with client(app) as c:
            tasks = [asyncio.ensure_future(c.post("/mcp", headers=hdr(t), content=call_body(i))) for i in range(2)]
            assert await _wait_until(lambda: adapter.running == 2)
            third = await c.post("/mcp", headers=hdr(t), content=call_body(3))
            other = asyncio.ensure_future(c.post("/mcp", headers=hdr(tok("else")), content=call_body(4)))
            assert await _wait_until(lambda: adapter.running == 3)  # another account is not blocked
            adapter.release.set()
            return third, await asyncio.gather(*tasks, other)

    third, done = asyncio.run(go())
    assert third.status_code == 429 and third.json()["error"] == "too_many_concurrent_calls"
    assert [r.status_code for r in done] == [200, 200, 200]


def test_queue_wait_is_bounded():
    app, adapter, tok = app_with(Limits(max_concurrent_calls=1, max_queued_calls=4,
                                        max_queue_wait_seconds=0.3, **FAST))

    async def go():
        async with client(app) as c:
            first = asyncio.ensure_future(c.post("/mcp", headers=hdr(tok("a")), content=call_body(1)))
            assert await _wait_until(lambda: adapter.running == 1)
            t0 = time.monotonic()
            waited = await c.post("/mcp", headers=hdr(tok("b")), content=call_body(2))
            elapsed = time.monotonic() - t0
            adapter.release.set()
            await first
            return waited, elapsed

    waited, elapsed = asyncio.run(go())
    assert waited.status_code == 429 and waited.json()["error"] == "queue_wait_exceeded"
    assert elapsed < 2.0 and adapter.done == 1                    # the waiter never ran


def test_timed_out_call_keeps_its_slot_until_it_really_finishes():
    app, adapter, tok = app_with(Limits(max_concurrent_calls=1, max_queued_calls=0,
                                        call_timeout_seconds=0.3, **FAST))

    async def go():
        async with client(app) as c:
            first = await c.post("/mcp", headers=hdr(tok("a")), content=call_body(1))   # times out, still running
            busy = await c.post("/mcp", headers=hdr(tok("b")), content=call_body(2))    # its slot is still held
            adapter.release.set()
            assert await _wait_until(lambda: adapter.done == 1)
            await asyncio.sleep(0.1)
            after = await c.post("/mcp", headers=hdr(tok("c")), content=call_body(3))
            return first, busy, after

    first, busy, after = asyncio.run(go())
    assert first.json()["error"]["code"] == -32001
    assert busy.status_code == 429
    assert after.status_code == 200 and adapter.peak == 1


def test_rate_limits_and_bursts():
    clock = [0.0]
    rl = RateLimiter(10, lambda: clock[0], burst=3)
    assert [rl.allow("a") for _ in range(4)] == [True, True, True, False]   # burst 3
    assert 5.9 < rl.wait_for("a") <= 6.0                                     # one token per 6 s
    clock[0] += 6.0
    assert rl.allow("a") and not rl.allow("a")
    assert rl.allow("b")                                                    # per key
    app, adapter, tok = app_with(Limits())
    adapter.release.set()
    t = tok()

    async def go():
        async with client(app) as c:
            calls = [await c.post("/mcp", headers=hdr(t), content=call_body(i)) for i in range(4)]
            lists = [await c.post("/mcp", headers=hdr(t), content=call_body(i, "tools/list", {}))
                     for i in range(16)]
            return calls, lists
    calls, lists = asyncio.run(go())
    assert [r.status_code for r in calls] == [200, 200, 200, 429]
    assert int(calls[-1].headers["retry-after"]) >= 1
    assert [r.status_code for r in lists][:15] == [200] * 15 and lists[15].status_code == 429


def test_per_token_rate_is_separate_from_per_account():
    app, adapter, tok = app_with(Limits(calls_per_minute_per_principal=1000, calls_per_minute_per_token=30,
                                        call_burst=1000, call_burst_per_token=2))
    adapter.release.set()
    t = tok()

    async def go():
        async with client(app) as c:
            return [(await c.post("/mcp", headers=hdr(t), content=call_body(i))).status_code for i in range(3)]
    assert asyncio.run(go()) == [200, 200, 429]


def test_per_account_rate_counts_across_its_tokens():
    app, adapter, tok = app_with(Limits(calls_per_minute_per_principal=10, calls_per_minute_per_token=1000,
                                        call_burst=2, call_burst_per_token=1000))
    adapter.release.set()
    t1, t2 = tok("acct"), tok("acct")

    async def go():
        async with client(app) as c:
            return [(await c.post("/mcp", headers=hdr(t), content=call_body(i))).status_code
                    for i, t in enumerate((t1, t1, t2))]
    assert asyncio.run(go()) == [200, 200, 429]          # a fresh token does not reset the account


def test_json_shape_limits_refuse():
    lim = Limits()
    deep = {}
    node = deep
    for _ in range(40):
        node["a"] = {}
        node = node["a"]
    assert "deeper" in json_limit_violation(deep, lim)
    assert "members" in json_limit_violation({"a": list(range(2001))}, lim)
    assert "string" in json_limit_violation({"a": "x" * (64 * 1024 + 1)}, lim)
    assert json_limit_violation({"a": list(range(100)), "b": "x" * 1000}, lim) is None
    app, adapter, tok = app_with(Limits(**FAST))

    async def go():
        async with client(app) as c:
            return await c.post("/mcp", headers=hdr(tok()), content=call_body(
                1, params={"name": "sequentialthinking", "arguments": {"t": "x" * (64 * 1024 + 10)}}))
    r = asyncio.run(go())
    assert r.status_code == 400 and r.json()["error"]["code"] == -32600 and adapter.done == 0


def test_result_over_1_mib_is_a_safe_error_not_served():
    app, adapter, tok = app_with(Limits(**FAST), payload="x" * (1024 * 1024 + 10))
    adapter.release.set()

    async def go():
        async with client(app) as c:
            return await c.post("/mcp", headers=hdr(tok()), content=call_body(1))
    r = asyncio.run(go())
    assert r.json()["error"]["code"] == -32603 and len(r.content) < 1024
