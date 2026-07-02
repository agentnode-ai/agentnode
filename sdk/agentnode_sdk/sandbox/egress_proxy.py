"""CONNECT egress proxy for the sandbox egress mode (Stage 2).

OUR trusted gateway, launched (via ``python -c <source>``) inside the pinned image
on the dual-homed proxy container. Application-layer rules enforced here:
  * only HTTP ``CONNECT`` (no plaintext HTTP forwarding) -> 405 otherwise,
  * only destination port 443,
  * destination host must EXACTLY match the (already-validated, canonical) allowlist
    from ``EGRESS_ALLOWLIST`` (comma-separated). Host normalized (lowercase, no trailing
    dot); NO substring / suffix / wildcard matching,
  * SSRF / DNS-rebinding guard: the proxy resolves the target itself, REFUSES if ANY
    resolved address is not publicly routable (loopback/private/link-local/multicast/
    unspecified/reserved, incl. 169.254.169.254 and IPv6 loopback/link-local/ULA and
    IPv4-mapped variants), then connects to a VETTED IP literal — never re-resolving the
    hostname (no check->connect rebinding window). Fail-closed: any private record, or a
    resolve failure, denies the whole CONNECT.

NOTE: the real security boundary is the TOPOLOGY — the payload container sits on a
Docker ``--internal`` network with no route, and this proxy is its only egress (proven
in Stage 0A). The allowlist + SSRF guard are defense-in-depth on the proxy itself (which
IS dual-homed and could otherwise reach private/metadata addresses). Stdlib only, so it
runs standalone via ``python -c`` with no agentnode_sdk install.
"""
from __future__ import annotations

import ipaddress
import os
import select
import socket
import threading

LISTEN = ("0.0.0.0", 8888)
ALLOWED_PORT = 443

_STATUS = {
    200: b"HTTP/1.1 200 Connection Established\r\n\r\n",
    403: b"HTTP/1.1 403 Forbidden\r\n\r\n",
    405: b"HTTP/1.1 405 Method Not Allowed\r\n\r\n",
    502: b"HTTP/1.1 502 Bad Gateway\r\n\r\n",
}


class EgressBlocked(Exception):
    """The CONNECT must be refused (non-public resolved address or resolution failure)."""


def normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def parse_allowlist(value: str) -> set:
    return {normalize_host(p) for p in (value or "").split(",") if p.strip()}


def _split_request_line(first_line: str):
    parts = first_line.split()
    if len(parts) < 2:
        raise ValueError("malformed request line")
    return parts[0], parts[1]


def _split_hostport(target: str):
    if ":" not in target:
        raise ValueError("CONNECT target must be host:port")
    host, _, port = target.rpartition(":")
    if not host:
        raise ValueError("empty host")
    return host, int(port)  # ValueError if port is not an int


def classify(first_line: str, allowlist) -> int:
    """Decide the HTTP status for a request line. PURE (no I/O) -> unit-testable.

    200 = method/port/host OK (still subject to the SSRF screen at connect time),
    403 = denied (host not allowed or port != 443), 405 = bad method / unparseable.
    """
    try:
        method, target = _split_request_line(first_line)
    except ValueError:
        return 405
    if method.upper() != "CONNECT":
        return 405
    try:
        host, port = _split_hostport(target)
    except ValueError:
        return 405
    if port != ALLOWED_PORT:
        return 403
    if normalize_host(host) not in allowlist:
        return 403
    return 200


def ip_is_public(ip_str: str) -> bool:
    """True ONLY for a globally routable unicast address (POSITIVE check via
    ``is_global``). IPv4-mapped IPv6 is normalized to its embedded IPv4 first. Anything
    not unambiguously global is rejected (fail-closed SSRF guard): this covers ranges a
    negative list would miss, e.g. CGNAT / shared address space 100.64.0.0/10, the
    benchmarking 198.18.0.0/15 and TEST-NET documentation ranges. ``is_global`` alone is
    NOT enough (e.g. multicast 224.0.0.1 reports is_global=True), so multicast/
    unspecified/reserved/loopback/link-local are explicitly excluded too."""
    candidate = ip_str.split("%")[0]  # drop any IPv6 zone id
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (
        ip.is_global
        and not ip.is_multicast
        and not ip.is_unspecified
        and not ip.is_reserved
        and not ip.is_loopback
        and not ip.is_link_local
    )


def screen_addrinfos(infos) -> list:
    """Given a ``socket.getaddrinfo``-style list, return the vetted ``(family, sockaddr)``
    entries, or raise :class:`EgressBlocked` if empty or ANY address is non-public
    (fail-closed: a single private record denies the whole CONNECT)."""
    if not infos:
        raise EgressBlocked("no addresses resolved")
    vetted = []
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        if not ip_is_public(ip_str):
            raise EgressBlocked(f"non-public address resolved: {ip_str}")
        vetted.append((info[0], sockaddr))
    return vetted


def resolve_and_screen(host: str, port: int) -> list:
    """Resolve ``host`` ourselves and screen every result. Raise :class:`EgressBlocked`
    on resolution failure (no fallback) or any non-public address."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except Exception as e:
        raise EgressBlocked(f"resolve failed: {type(e).__name__}")
    return screen_addrinfos(infos)


def _handle(client: socket.socket, allowlist) -> None:
    try:
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = client.recv(4096)
            if not chunk:
                client.close()
                return
            buf += chunk
            if len(buf) > 65536:
                client.sendall(_STATUS[405])
                client.close()
                return
        first_line = buf.split(b"\r\n", 1)[0].decode("latin1")
        status = classify(first_line, allowlist)
        if status != 200:
            client.sendall(_STATUS[status])
            client.close()
            return
        host, port = _split_hostport(first_line.split()[1])
        host = normalize_host(host)
        # SSRF / DNS-rebinding guard: resolve + screen BEFORE connecting, then connect
        # to the vetted IP literal (no second, unchecked hostname resolution).
        try:
            vetted = resolve_and_screen(host, port)
        except EgressBlocked:
            client.sendall(_STATUS[403])
            client.close()
            return
        vetted_ip = vetted[0][1][0]
        try:
            upstream = socket.create_connection((vetted_ip, port), timeout=10)
        except Exception:
            client.sendall(_STATUS[502])
            client.close()
            return
        client.sendall(_STATUS[200])
        _tunnel(client, upstream)
    except Exception:
        try:
            client.close()
        except Exception:
            pass


def _tunnel(a: socket.socket, b: socket.socket) -> None:
    socks = [a, b]
    try:
        while True:
            readable, _, _ = select.select(socks, [], [], 60)
            if not readable:
                break
            for s in readable:
                data = s.recv(8192)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    finally:
        for s in socks:
            try:
                s.close()
            except Exception:
                pass


def main() -> None:
    allowlist = parse_allowlist(os.environ.get("EGRESS_ALLOWLIST", ""))
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN)
    srv.listen(64)
    print("egress-proxy listening on 8888 allow=" + ",".join(sorted(allowlist)), flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_handle, args=(conn, allowlist), daemon=True).start()


if __name__ == "__main__":
    main()
