"""Dedicated SSRF / DNS-rebinding negative tests for the CIMD fetcher (profile R3, /goal #3).

The fetcher is driven with an injected ``getaddrinfo`` and an injected ``connector`` so every
boundary is exercised deterministically without a real network:

  * HTTPS only; no embedded credentials.
  * the host is resolved EXACTLY ONCE; the whole answer is rejected if ANY address is private,
    loopback, link-local, reserved, multicast or unspecified (defeats mixed public+internal).
  * the connection is pinned to that resolved address and never re-resolves (no DNS rebinding).
  * redirects are not followed (any non-200 rejected); response content-type must be JSON;
    response size is bounded.

Each refusal here is also reverted in the counter-check harness to prove it goes red.
"""
from __future__ import annotations

import json
import socket

import pytest

from agentnode_sdk.remote_mcp.oauth_as import (
    AuthorizationServer, CIMDError, default_cimd_fetcher, resolve_public_ips,
)
from agentnode_sdk.remote_mcp.tokens import TokenStore

CID = "https://client.example/cimd.json"


def gai(*ips):
    """A fake getaddrinfo returning the given IPv4 addresses; counts its calls."""
    calls = {"n": 0}

    def _gai(host, port, *a, **k):
        calls["n"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    _gai.calls = calls
    return _gai


def conn(status=200, ctype="application/json", body=None, record=None):
    """A fake connector; records (ip, port, hostname, path) it was asked to fetch."""
    payload = body if body is not None else json.dumps(
        {"client_id": CID, "client_name": "c", "redirect_uris": ["https://client.example/cb"]}
    ).encode()

    def _get(ip, port, hostname, path, timeout, max_bytes):
        if record is not None:
            record.append((ip, port, hostname, path))
        return status, ctype, payload

    return _get


# ---- scheme / credentials ------------------------------------------------------------------ #

def test_rejects_non_https():
    with pytest.raises(CIMDError):
        default_cimd_fetcher("http://client.example/cimd.json", getaddrinfo=gai("93.1.1.1"),
                             connector=conn())


def test_rejects_embedded_credentials():
    with pytest.raises(CIMDError):
        default_cimd_fetcher("https://user:pw@client.example/cimd.json",
                             getaddrinfo=gai("93.1.1.1"), connector=conn())


# ---- SSRF address classes (resolve_public_ips) -------------------------------------------- #

@pytest.mark.parametrize("ip", [
    "10.0.0.5", "172.16.0.1", "192.168.1.1",   # private
    "127.0.0.1",                               # loopback
    "169.254.1.1",                             # link-local
    "240.0.0.1",                               # reserved
    "224.0.0.1",                               # multicast
    "0.0.0.0",                                 # unspecified
])
def test_rejects_non_public_address(ip):
    with pytest.raises(CIMDError):
        resolve_public_ips("client.example", 443, getaddrinfo=gai(ip))
    with pytest.raises(CIMDError):
        default_cimd_fetcher(CID, getaddrinfo=gai(ip), connector=conn())


def test_rejects_mixed_public_and_private():
    # a split answer (one public, one internal) must be rejected wholesale
    with pytest.raises(CIMDError):
        default_cimd_fetcher(CID, getaddrinfo=gai("93.1.1.1", "10.0.0.5"), connector=conn())


def test_rejects_unresolvable():
    def boom(host, port, *a, **k):
        raise OSError("nxdomain")

    with pytest.raises(CIMDError):
        default_cimd_fetcher(CID, getaddrinfo=boom, connector=conn())


# ---- DNS rebinding: single resolution, pinned connect ------------------------------------- #

def test_resolves_exactly_once_and_pins():
    g = gai("93.1.1.1")
    rec: list = []
    default_cimd_fetcher(CID, getaddrinfo=g, connector=conn(record=rec))
    assert g.calls["n"] == 1                       # resolved once, never re-resolved
    assert rec and rec[0][0] == "93.1.1.1"         # connected to the validated address
    assert rec[0][2] == "client.example"           # SNI/Host preserved for cert validation


def test_no_rebind_to_internal_after_first_resolution():
    # getaddrinfo answers public first; a rebinding attacker would flip to loopback on a
    # second lookup. The fetcher must resolve once and connect to the public address only.
    answers = [[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.1.1.1", 443))],
               [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]]
    state = {"i": 0}

    def flip(host, port, *a, **k):
        r = answers[min(state["i"], 1)]
        state["i"] += 1
        return r

    rec: list = []
    default_cimd_fetcher(CID, getaddrinfo=flip, connector=conn(record=rec))
    assert state["i"] == 1                          # exactly one resolution
    assert rec[0][0] == "93.1.1.1"                  # never the rebind target


# ---- response-shape bounds ---------------------------------------------------------------- #

def test_rejects_redirect_status():
    with pytest.raises(CIMDError):
        default_cimd_fetcher(CID, getaddrinfo=gai("93.1.1.1"), connector=conn(status=302))


def test_rejects_non_json_content_type():
    with pytest.raises(CIMDError):
        default_cimd_fetcher(CID, getaddrinfo=gai("93.1.1.1"),
                             connector=conn(ctype="text/html"))


def test_rejects_oversize_body():
    big = b"{" + b" " * (64 * 1024 + 8) + b"}"
    with pytest.raises(CIMDError):
        default_cimd_fetcher(CID, getaddrinfo=gai("93.1.1.1"), connector=conn(body=big))


def test_rejects_non_json_body():
    with pytest.raises(CIMDError):
        default_cimd_fetcher(CID, getaddrinfo=gai("93.1.1.1"),
                             connector=conn(body=b"<html/>"))


def test_happy_path_returns_document():
    doc = default_cimd_fetcher(CID, getaddrinfo=gai("93.1.1.1"), connector=conn())
    assert doc["client_id"] == CID
    assert doc["redirect_uris"] == ["https://client.example/cb"]


# ---- client_id must equal its own URL (through the AS resolver) --------------------------- #

def test_resolver_requires_client_id_equals_url():
    store = TokenStore(b"k" * 32)
    mismatched = json.dumps(
        {"client_id": "https://evil.example/x", "client_name": "c",
         "redirect_uris": ["https://client.example/cb"]}).encode()
    authz = AuthorizationServer(
        issuer=ISSUER_URL, resource=RES_URL, token_store=store,
        cimd_fetcher=lambda cid: default_cimd_fetcher(
            cid, getaddrinfo=gai("93.1.1.1"), connector=conn(body=mismatched)))
    with pytest.raises(CIMDError):
        authz._resolve_client(CID)   # noqa: SLF001


ISSUER_URL = "https://mcp.agentnode.net"
RES_URL = "https://mcp.agentnode.net/mcp"
