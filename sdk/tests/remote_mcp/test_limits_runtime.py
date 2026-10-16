"""R13 runtime limits under real concurrency: the concurrency cap is a cap on calls RUNNING at
once (extra calls wait in a bounded queue, beyond it 503), and a timed-out call keeps its run
slot until the worker call has really finished. Driven in one event loop with a blocking adapter
that measures the true peak concurrency."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx

from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
from agentnode_sdk.remote_mcp.limits import Limits
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.tokens import TokenStore

HOST = "mcp.agentnode.net"
RESOURCE = "https://mcp.agentnode.net/mcp"


class BlockingAdapter:
    def __init__(self):
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.running = 0
        self.peak = 0
        self.done = 0

    def handle(self, msg):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        self.release.wait(10)
        with self.lock:
            self.running -= 1
            self.done += 1
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"isError": False, "content": []}}


def app_with(limits):
    store = TokenStore(b"k" * 32)
    adapter = BlockingAdapter()
    app = RemoteMCPApp(adapter=adapter, token_store=store,
                       authz=AuthorizationServer(issuer="https://mcp.agentnode.net", resource=RESOURCE, token_store=store),
                       resource=ResourceConfig(resource=RESOURCE, authorization_server="https://mcp.agentnode.net"),
                       allowed_hosts=(HOST,), limits=limits)
    tok = store.mint(principal_id="p", audience=RESOURCE, scopes=("mcp:use",), issuance_kind="oauth",
                     ttl_seconds=600).plaintext
    return app, adapter, tok


def call_body(mid):
    return json.dumps({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                       "params": {"name": "sequentialthinking", "arguments": {}}}).encode()


HDRS = {"content-type": "application/json", "accept": "application/json, text/event-stream",
        "mcp-protocol-version": "2025-11-25"}


async def _wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while not pred() and time.monotonic() < end:
        await asyncio.sleep(0.02)
    return pred()


def test_concurrency_cap_is_enforced_and_queue_overflow_refuses():
    app, adapter, tok = app_with(Limits(max_concurrent_calls=2, max_queued_calls=1,
                                        calls_per_minute_per_principal=100))
    h = dict(HDRS, authorization="Bearer %s" % tok)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://%s" % HOST,
                                     timeout=30) as c:
            tasks = [asyncio.ensure_future(c.post("/mcp", headers=h, content=call_body(i))) for i in range(3)]
            assert await _wait_until(lambda: adapter.running == 2)          # two RUN, one waits
            await asyncio.sleep(0.2)
            assert adapter.running == 2                                     # never a third at once
            over = await c.post("/mcp", headers=h, content=call_body(9))    # 2 running + 1 queued
            adapter.release.set()
            done = await asyncio.gather(*tasks)
            return over, done

    over, done = asyncio.run(go())
    assert over.status_code == 503 and over.json()["error"] == "overloaded"
    assert [r.status_code for r in done] == [200, 200, 200]
    assert adapter.peak == 2 and adapter.done == 3


def test_timed_out_call_keeps_its_slot_until_it_really_finishes():
    app, adapter, tok = app_with(Limits(max_concurrent_calls=1, max_queued_calls=0,
                                        call_timeout_seconds=0.3, calls_per_minute_per_principal=100))
    h = dict(HDRS, authorization="Bearer %s" % tok)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://%s" % HOST,
                                     timeout=30) as c:
            first = await c.post("/mcp", headers=h, content=call_body(1))   # times out, still running
            busy = await c.post("/mcp", headers=h, content=call_body(2))    # its slot is still held
            adapter.release.set()
            assert await _wait_until(lambda: adapter.done == 1)
            await asyncio.sleep(0.1)                                        # slot freed on completion
            after = await c.post("/mcp", headers=h, content=call_body(3))
            return first, busy, after

    first, busy, after = asyncio.run(go())
    assert first.json()["error"]["code"] == -32001
    assert busy.status_code == 503
    assert after.status_code == 200 and adapter.peak == 1
