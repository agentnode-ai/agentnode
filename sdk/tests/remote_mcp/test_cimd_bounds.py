"""R3 "safe fetch/caching" + R13 time bounds for the CIMD client-document fetch: only globally
routable addresses, a TOTAL deadline a trickling peer cannot stretch, transport failures refuse
the client (400) instead of crashing (500), a bounded number of fetches at once, caching that
follows the document's HTTP directives, and a slow fetch never stalls the event loop."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest

from agentnode_sdk.remote_mcp import oauth_as as O
from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
from agentnode_sdk.remote_mcp.limits import Limits
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.tokens import TokenStore

CID = "https://client.example/cimd.json"
RESOURCE = "https://mcp.agentnode.net/mcp"
HOST = "mcp.agentnode.net"
DOC = {"client_id": CID, "client_name": "c", "redirect_uris": ["https://client.example/cb"]}


def gai(*ips):
    return lambda host, port, proto=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]


def ok_conn(cache_control=""):
    return lambda ip, port, host, path, timeout, max_bytes: (200, "application/json", json.dumps(DOC).encode(), cache_control)


@pytest.mark.parametrize("ip", ["100.64.0.1", "100.127.255.254", "192.0.2.10", "198.51.100.7", "203.0.113.9"])
def test_non_global_addresses_are_refused(ip):
    with pytest.raises(O.CIMDError, match="non-public"):
        O.fetch_cimd_document(CID, getaddrinfo=gai(ip), connector=ok_conn())


def test_transport_failure_refuses_the_client_not_a_crash():
    def broken(*a):
        raise ConnectionResetError("peer reset")
    with pytest.raises(O.CIMDError, match="fetch failed"):
        O.fetch_cimd_document(CID, getaddrinfo=gai("93.184.216.34"), connector=broken)
    authz = O.AuthorizationServer(issuer="https://mcp.agentnode.net", resource=RESOURCE,
                                  token_store=TokenStore(b"k" * 32),
                                  cimd_fetcher=lambda u: (_ for _ in ()).throw(OSError("tls")))
    norm, err = authz.validate({"client_id": CID, "redirect_uri": "https://client.example/cb",
                                "response_type": "code", "code_challenge": "c" * 43,
                                "code_challenge_method": "S256", "resource": RESOURCE})
    assert norm is None and err[0] == 400 and json.loads(err[2])["error"] == "invalid_client"


def test_total_deadline_cannot_be_stretched_by_a_trickling_peer():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()

    def trickle():
        conn, _ = srv.accept()
        try:
            while not stop.is_set():                 # one byte every 0.2 s, forever
                conn.sendall(b"\x16")
                time.sleep(0.2)
        except OSError:
            pass
        finally:
            conn.close()
    threading.Thread(target=trickle, daemon=True).start()
    t0 = time.monotonic()
    try:
        with pytest.raises(O.CIMDError):
            O._pinned_https_get("127.0.0.1", srv.getsockname()[1], "client.example", "/", 1.0, 1024)
        assert time.monotonic() - t0 < 3.0
    finally:
        stop.set()
        srv.close()


def test_concurrent_fetches_are_bounded():
    slots = O._cimd_slots()
    held = [slots.acquire(blocking=False) for _ in range(O.MAX_CONCURRENT_CIMD_FETCHES)]
    try:
        assert all(held)
        with pytest.raises(O.CIMDError, match="too many"):
            O.fetch_cimd_document(CID, getaddrinfo=gai("93.184.216.34"), connector=ok_conn())
    finally:
        for _ in held:
            slots.release()


def test_cache_lifetime_follows_http_directives():
    assert O.cache_lifetime("") == 0
    assert O.cache_lifetime("no-store") == 0
    assert O.cache_lifetime("max-age=120, no-cache") == 0
    assert O.cache_lifetime("public, max-age=120") == 120
    assert O.cache_lifetime("max-age=999999") == O.MAX_CIMD_CACHE_SECONDS
    assert O.cache_lifetime("max-age=abc") == 0


def test_cached_fetch_reuses_only_within_max_age():
    now = [1000.0]
    calls = []

    def fetch(url):
        calls.append(url)
        return DOC, (60 if len(calls) == 1 else 0)
    authz = O.AuthorizationServer(issuer="https://mcp.agentnode.net", resource=RESOURCE,
                                  token_store=TokenStore(b"k" * 32), clock=lambda: now[0])
    assert authz._cached_cimd(CID, fetch=fetch) == DOC
    assert authz._cached_cimd(CID, fetch=fetch) == DOC and len(calls) == 1     # within max-age
    now[0] += 61
    authz._cached_cimd(CID, fetch=fetch)
    assert len(calls) == 2                                                       # expired -> refetch
    authz._cached_cimd(CID, fetch=fetch)
    assert len(calls) == 3                                                       # max-age 0 -> no reuse


def test_a_slow_client_document_does_not_stall_the_event_loop():
    store = TokenStore(b"k" * 32)

    def slow(url):
        time.sleep(1.0)
        return DOC
    authz = O.AuthorizationServer(issuer="https://mcp.agentnode.net", resource=RESOURCE,
                                  token_store=store, cimd_fetcher=slow)

    class Adapter:
        def handle(self, msg):
            return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": []}}
    app = RemoteMCPApp(adapter=Adapter(), token_store=store, authz=authz,
                       resource=ResourceConfig(resource=RESOURCE, authorization_server="https://mcp.agentnode.net"),
                       allowed_hosts=(HOST,), principal_resolver=lambda s, h: "u1", limits=Limits())
    fetched = []
    authz.cimd_fetcher = lambda url: (fetched.append(url), slow(url))[1]
    q = ("client_id=%s&redirect_uri=https://client.example/cb&response_type=code&code_challenge=%s"
         "&code_challenge_method=S256&resource=%s" % (CID, "c" * 43, RESOURCE))
    order = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://%s" % HOST) as c:
            async def authorize():
                await c.get("/authorize?" + q)
                order.append("authorize-done")

            async def mcp():
                await asyncio.sleep(0.2)
                await c.post("/mcp", headers={"content-type": "application/json",
                                              "accept": "application/json, text/event-stream"}, content=b"{}")
                order.append("mcp-done")
            await asyncio.gather(authorize(), mcp())
    asyncio.run(go())
    assert fetched == [CID]                       # the slow document fetch really happened
    assert order == ["mcp-done", "authorize-done"]
