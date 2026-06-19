"""Stage 2: pure unit tests for the CONNECT proxy decision + SSRF screening (no daemon)."""
from __future__ import annotations

import socket

import pytest

from agentnode_sdk.sandbox.egress_proxy import (
    EgressBlocked,
    classify,
    ip_is_public,
    normalize_host,
    parse_allowlist,
    resolve_and_screen,
    screen_addrinfos,
)

ALLOW = {"example.com", "api.example.com"}


# ---- request-line decision (method / port / host allowlist) ----

def test_connect_allowed_443():
    assert classify("CONNECT example.com:443 HTTP/1.1", ALLOW) == 200


def test_connect_wrong_port_denied():
    assert classify("CONNECT example.com:8443 HTTP/1.1", ALLOW) == 403
    assert classify("CONNECT example.com:22 HTTP/1.1", ALLOW) == 403
    assert classify("CONNECT example.com:80 HTTP/1.1", ALLOW) == 403


def test_connect_non_allowlisted_denied():
    assert classify("CONNECT google.com:443 HTTP/1.1", ALLOW) == 403


def test_plain_http_methods_denied():
    assert classify("GET / HTTP/1.1", ALLOW) == 405
    assert classify("POST /x HTTP/1.1", ALLOW) == 405
    assert classify("CONNECTX example.com:443 HTTP/1.1", ALLOW) == 405


def test_malformed_denied():
    assert classify("garbage", ALLOW) == 405
    assert classify("CONNECT example.com HTTP/1.1", ALLOW) == 405  # no port
    assert classify("CONNECT example.com:https HTTP/1.1", ALLOW) == 405  # non-int port


def test_host_normalization():
    assert classify("CONNECT EXAMPLE.COM:443 HTTP/1.1", ALLOW) == 200
    assert classify("CONNECT example.com.:443 HTTP/1.1", ALLOW) == 200


def test_no_substring_or_suffix_match():
    assert classify("CONNECT evil-example.com:443 HTTP/1.1", ALLOW) == 403
    assert classify("CONNECT sub.example.com:443 HTTP/1.1", ALLOW) == 403
    assert classify("CONNECT example.com.evil.test:443 HTTP/1.1", ALLOW) == 403


def test_parse_allowlist():
    assert parse_allowlist("a.com, b.com ,, A.COM ") == {"a.com", "b.com"}
    assert parse_allowlist("") == set()


def test_normalize_host():
    assert normalize_host("  EXAMPLE.com.  ") == "example.com"


# ---- SSRF / DNS-rebinding screening ----

def test_ip_is_public_accepts_public():
    assert ip_is_public("8.8.8.8")
    assert ip_is_public("93.184.216.34")
    assert ip_is_public("2001:4860:4860::8888")


@pytest.mark.parametrize("bad", [
    "127.0.0.1",          # loopback
    "10.0.0.1",           # private
    "172.16.0.1",         # private
    "192.168.0.1",        # private
    "169.254.169.254",    # link-local (cloud metadata)
    "0.0.0.0",            # unspecified
    "224.0.0.1",          # multicast
    "240.0.0.1",          # reserved
    "::1",                # IPv6 loopback
    "fe80::1",            # IPv6 link-local
    "fc00::1",            # IPv6 unique-local
    "::ffff:127.0.0.1",   # IPv4-mapped loopback
    "::ffff:10.0.0.1",    # IPv4-mapped private
    "100.64.0.1",         # CGNAT / shared address space (RFC 6598) -> not global
    "198.18.0.1",         # benchmarking (RFC 2544) -> not global
    "192.0.2.1",          # TEST-NET-1 documentation -> not global
    "198.51.100.1",       # TEST-NET-2 documentation -> not global
    "203.0.113.1",        # TEST-NET-3 documentation -> not global
    "2001:db8::1",        # IPv6 documentation -> not global
    "not-an-ip",          # unparseable -> not public
])
def test_ip_is_public_rejects_non_public(bad):
    assert not ip_is_public(bad)


def _ai(*ips):
    """Build a getaddrinfo-style list for the given IP strings."""
    out = []
    for ip in ips:
        if ":" in ip:
            out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 443, 0, 0)))
        else:
            out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)))
    return out


def test_screen_public_ok():
    vetted = screen_addrinfos(_ai("93.184.216.34"))
    assert vetted and vetted[0][1][0] == "93.184.216.34"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "192.168.0.1", "169.254.169.254", "::1", "fe80::1"])
def test_screen_private_raises(ip):
    with pytest.raises(EgressBlocked):
        screen_addrinfos(_ai(ip))


def test_screen_mixed_is_fail_closed():
    # one public + one private record -> deny the whole CONNECT
    with pytest.raises(EgressBlocked):
        screen_addrinfos(_ai("93.184.216.34", "10.0.0.1"))


def test_screen_empty_raises():
    with pytest.raises(EgressBlocked):
        screen_addrinfos([])


def test_resolve_and_screen_public(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _ai("93.184.216.34"))
    assert resolve_and_screen("example.com", 443)


def test_resolve_and_screen_private_denied(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _ai("127.0.0.1"))
    with pytest.raises(EgressBlocked):
        resolve_and_screen("rebind.example.com", 443)


def test_resolve_and_screen_resolution_failure_denied(monkeypatch):
    def _boom(*a, **k):
        raise socket.gaierror("nxdomain")
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(EgressBlocked):
        resolve_and_screen("nx.example.com", 443)
