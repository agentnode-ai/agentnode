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


def _decided(what: str, host: str, port, status, why: str = "") -> None:
    """Write down one decision, on the WORKER's side of the line.

    EG17 of this arc's egress profile says every connection and every refusal has to be evidenced
    from worker or kernel observation and not only from what the job reported about itself. The
    kernel's part is strong on its own -- the payload's namespace has no default route at all -- but
    a REFUSAL by name happens here, in this process, and until this existed the only record of one
    was the job's own account of being refused. A boundary whose refusals are only self-reported is
    not evidenced.

    WHAT IS WRITTEN AND WHAT IS NOT. The destination, the port, the decision and the reason: all of
    them things the caller asked for and this process decided. NOT the request bytes, not headers,
    not a path, not a query, and nothing of the tunnel's contents -- a proxy log that grew those
    would be a record of what somebody's code was doing, which is not this product's business and is
    not worth having on the machine that runs other people's code.

    One line, flushed, to stdout, which is where the runtime collects it.
    """
    print("egress-proxy %-8s %s:%s%s" % (what, host, port, (" " + why) if why else ""), flush=True)


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
        # The destination is read out for the log whatever the verdict was, because a refusal that
        # does not say WHAT was refused is not evidence of anything. It is read from the request line
        # only, and a request line that could not be parsed says so instead of guessing.
        try:
            asked_host, asked_port = _split_hostport(first_line.split()[1])
            asked_host = normalize_host(asked_host)
        except Exception:                                     # noqa: BLE001
            asked_host, asked_port = "(unparseable)", "?"
        if status != 200:
            _decided("REFUSED" if status == 403 else "REJECTED", asked_host, asked_port, status,
                     "not on the allowlist or not port 443" if status == 403
                     else "not a CONNECT this proxy will answer")
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
            # ON THE ALLOWLIST AND STILL REFUSED. This is the rebinding case: a name this proxy is
            # willing to reach that resolves to something it is not -- a private address, a loopback,
            # link-local, metadata. Worth its own word in the log, because "refused" and "refused
            # although it was allowed" send a reader to different places.
            _decided("SCREENED", host, port, 403, "allowed by name, but it resolves to an address "
                                                  "this proxy will not reach")
            client.sendall(_STATUS[403])
            client.close()
            return
        vetted_ip = vetted[0][1][0]
        try:
            upstream = socket.create_connection((vetted_ip, port), timeout=10)
        except Exception:
            _decided("UNREACHED", host, port, 502, "allowed, screened, and the destination did not "
                                                   "answer")
            client.sendall(_STATUS[502])
            client.close()
            return
        _decided("ALLOWED", host, port, 200, "to " + str(vetted_ip))
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
