"""A dial-back slot over the public 443 path (local-worker slice 2, Codex Q-LW2-0001 D1-D3).

Slice 1's agent dialled a literal private address and ran the slot's TLS on the raw TCP connection. Here the agent
reaches the control plane's PUBLIC name, through whatever NAT is in between, on port 443 -- beside `/mcp` on the same
vhost -- and the slot itself does not change at all:

    agent --TCP--> mcp.agentnode.net:443
          outer TLS (agent = client): TLS 1.2/1.3, SNI + hostname mcp.agentnode.net, ALPN http/1.1, OS WebPKI
          GET /agent/v1/slot HTTP/1.1, Upgrade: agentnode-slot/1, the signed slot proof (below), nothing else
          <- 101 Switching Protocols
          inner TLS over the upgraded stream (agent = SERVER, control plane = client, deployment mTLS):
          exactly slice 1's slot -- one sealed agentnode-worker/1 frame each way, then closed

So the outer TLS authenticates the public NAME and carries the bytes through NAT; the inner, unchanged mTLS
authenticates the control plane's gateway instance and this worker's identity end to end, and the sealed frames
stay sealed: nginx and the hub see only the inner ciphertext. DNS selects an address and grants nothing.

The slot proof binds the edge's admission to this worker's identity before any inner TLS is spent on it:

    AgentNode-Slot: v1.<instance>.<unix-seconds>.<nonce>.<signature>
    AgentNode-Slot-Certificate: <the worker's certificate, DER, unpadded base64url>

    signature = ECDSA-P256-SHA256 (DER, unpadded base64url) by the worker certificate's key over
                b"agentnode-slot/1\\n" + host + "\\n" + path + "\\n" + instance + "\\n" + seconds + "\\n" + nonce
    nonce     = 32 random bytes, unpadded base64url

The inner TLS server end is a socket pair: one end is handed to the unchanged `TlsListener._one`, the other is pumped
to and from the outer TLS by ONE thread (an SSL object is never read and written from two threads at once).
"""
from __future__ import annotations

import base64
import os
import re
import select
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

SLOT_PATH = "/agent/v1/slot"
UPGRADE = "agentnode-slot/1"
PROOF_HEADER = "AgentNode-Slot"
CERT_HEADER = "AgentNode-Slot-Certificate"
PROOF_DOMAIN = b"agentnode-slot/1\n"
#: Response header block the agent reads at most, and how long it waits for it.
MAX_RESPONSE_HEADER = 8192
RESPONSE_SECONDS = 15.0
#: Proxy variables the agent refuses to ignore silently (Codex D1: no proxy support in this beta).
PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
_HOST = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+\Z")
_B64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_PUMP_CHUNK = 65536


class EdgeRefused(ConnectionError):
    """The 443 path refused or failed, by name. A ConnectionError, so the dial-back agent backs off from it exactly
    as from any failed dial -- and never falls back to anything else."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ControlUrl:
    host: str
    port: int
    path: str

    @property
    def text(self) -> str:
        return "https://%s%s" % (self.host, self.path)


def control_url(address: str) -> ControlUrl:
    """The public control endpoint, strictly: https, a lower-case ASCII DNS name (never a literal IP, never a
    single-label, loopback or wildcard name, never an IDN alias), port 443, path `/agent/v1/slot` (or none, meaning
    it), no userinfo, query or fragment. Anything else is `control_address_unusable`, never repaired."""
    text = str(address or "")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise EdgeRefused("control_address_unusable", "not a URL") from None
    host = parts.hostname or ""
    if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment \
            or "#" in text or "?" in text or "@" in parts.netloc:
        raise EdgeRefused("control_address_unusable", "the control plane is https://<name> with nothing else")
    if port not in (None, 443):
        raise EdgeRefused("control_address_unusable", "the control plane is reached on port 443 only")
    if parts.path not in ("", SLOT_PATH):
        raise EdgeRefused("control_address_unusable", "the slot path is %s" % SLOT_PATH)
    if not host.isascii() or host != host.lower() or host.startswith("xn--") or ".xn--" in host \
            or not _HOST.match(host) or host == "localhost" or host.endswith(".localhost") or "*" in host:
        raise EdgeRefused("control_address_unusable", "the control plane is named by a public ASCII DNS name")
    import ipaddress
    try:
        ipaddress.ip_address(host)
        raise EdgeRefused("control_address_unusable", "a literal address is not a name")
    except ValueError:
        pass
    # the netloc must be exactly the name (or name:443): no trailing dot, no case games, no stray characters
    if parts.netloc not in (host, host + ":443"):
        raise EdgeRefused("control_address_unusable", "the authority must be exactly the name")
    return ControlUrl(host=host, port=443, path=SLOT_PATH)


def refuse_configured_proxy(environ=None) -> None:
    """A proxy the operating system was told to use cannot be honoured in this beta; say so instead of dialling
    around it (`proxy_unsupported`)."""
    env = os.environ if environ is None else environ
    named = [v for v in PROXY_VARIABLES if env.get(v)]
    if named:
        raise EdgeRefused("proxy_unsupported", "a proxy is configured (%s); this beta connects only directly"
                          % ", ".join(sorted(set(n.upper() for n in named))))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not _B64URL.match(text or ""):
        raise ValueError("not unpadded base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def proof_message(host: str, path: str, instance: str, seconds: str, nonce: str) -> bytes:
    return PROOF_DOMAIN + "\n".join((host, path, instance, seconds, nonce)).encode("ascii")


def make_proof(key, certificate_der: bytes, instance: str, host: str, *, now: float | None = None,
               nonce: bytes | None = None) -> dict:
    """The two proof headers for one slot request, signed with the worker's own key."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    seconds = str(int(now if now is not None else time.time()))
    n = _b64(nonce if nonce is not None else os.urandom(32))
    sig = key.sign(proof_message(host, SLOT_PATH, instance, seconds, n), ec.ECDSA(hashes.SHA256()))
    return {PROOF_HEADER: "v1.%s.%s.%s.%s" % (instance, seconds, n, _b64(sig)), CERT_HEADER: _b64(certificate_der)}


def parse_proof(value: str) -> tuple[str, str, str, bytes]:
    """(instance, seconds, nonce, signature) of a proof header, in the closed grammar, or ValueError."""
    parts = str(value or "").split(".")
    if len(parts) != 5 or parts[0] != "v1":
        raise ValueError("not a v1 slot proof")
    _v, instance, seconds, nonce, sig = parts
    from agentnode_sdk.pki import identity as _identity
    if not _identity.is_component(instance):
        raise ValueError("instance outside the grammar")
    if not seconds.isdigit() or seconds != str(int(seconds)) or len(seconds) > 12:
        raise ValueError("timestamp is not canonical decimal")
    if len(_unb64(nonce)) != 32 or _b64(_unb64(nonce)) != nonce:
        raise ValueError("nonce is not 32 bytes of canonical base64url")
    signature = _unb64(sig)
    if _b64(signature) != sig or not 8 <= len(signature) <= 80:
        raise ValueError("signature is not canonical base64url")
    return instance, seconds, nonce, signature


def outer_context() -> ssl.SSLContext:
    """The OS's WebPKI trust, hostname checked, TLS 1.2 or 1.3, ALPN http/1.1 -- nothing relaxed."""
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.set_alpn_protocols(["http/1.1"])
    return ctx


def _read_response_head(sock) -> bytes:
    data = b""
    ends = time.monotonic() + RESPONSE_SECONDS
    while b"\r\n\r\n" not in data:
        if len(data) > MAX_RESPONSE_HEADER:
            raise EdgeRefused("edge_bad_response", "the response header block is too large")
        left = ends - time.monotonic()
        if left <= 0:
            raise EdgeRefused("edge_bad_response", "no response header within %.0f s" % RESPONSE_SECONDS)
        sock.settimeout(left)
        chunk = sock.recv(1)                                   # byte by byte: nothing after the head is consumed
        if not chunk:
            raise EdgeRefused("edge_bad_response", "the control plane closed before answering")
        data += chunk
    return data


def _refusal_code(head: bytes, sock) -> str:
    """The named code of a refused upgrade: the edge answers `{"error": "<code>"}`; nginx's own pages name none."""
    import json

    try:
        sock.settimeout(2.0)
        body = sock.recv(2048)
        code = json.loads(body.decode("utf-8")).get("error")
        if isinstance(code, str) and re.match(r"[a-z_]{3,64}\Z", code):
            return code
    except Exception:                                           # noqa: BLE001 - unnamed refusal below
        pass
    status = head.split(b"\r\n", 1)[0].decode("latin1", "replace")
    return "edge_refused_" + (status.split(" ")[1] if len(status.split(" ")) > 1 else "unknown")


class EdgeDialer:
    """`dial((host, port), timeout)` for `DialBackAgent`: returns the inner TLS server's raw end of one upgraded slot.

    `key` and `certificate_der` are this worker's (read fresh each dial by `material()` so a renewed certificate is
    used at once). `connect` is injectable for tests and for the stand, whose agent unit resolves the public name
    through its own private hosts file; the agent itself never takes an address from anywhere but DNS."""

    def __init__(self, url: ControlUrl, material, *, context: ssl.SSLContext | None = None, connect=None,
                 say=None) -> None:
        self.url = url
        self.material = material
        self.context = context or outer_context()
        self._connect = connect or socket.create_connection
        self.say = say or (lambda text: print(text, flush=True))

    def __call__(self, _address, timeout: float):
        key, der, instance = self.material()
        raw = self._connect((self.url.host, self.url.port), timeout)
        try:
            raw.settimeout(timeout)
            try:
                outer = self.context.wrap_socket(raw, server_hostname=self.url.host)
            except ssl.SSLCertVerificationError as exc:
                raise EdgeRefused("control_tls_untrusted", getattr(exc, "verify_message", "") or "certificate") \
                    from None
            except (ssl.SSLError, OSError) as exc:
                raise EdgeRefused("control_tls_failed", type(exc).__name__) from None
            if outer.selected_alpn_protocol() not in (None, "http/1.1"):
                outer.close()
                raise EdgeRefused("control_tls_failed", "the server chose another protocol")
            headers = make_proof(key, der, instance, self.url.host)
            request = ("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: Upgrade\r\nUpgrade: %s\r\n"
                       "%s: %s\r\n%s: %s\r\n\r\n" % (self.url.path, self.url.host, UPGRADE,
                                                     PROOF_HEADER, headers[PROOF_HEADER],
                                                     CERT_HEADER, headers[CERT_HEADER])).encode("ascii")
            outer.sendall(request)
            head = _read_response_head(outer)
            status_line = head.split(b"\r\n", 1)[0]
            fields = {}
            for line in head.split(b"\r\n")[1:]:
                if b":" in line:
                    k, v = line.split(b":", 1)
                    fields[k.strip().lower()] = v.strip()
            if not status_line.startswith(b"HTTP/1.1 101 ") \
                    or fields.get(b"upgrade", b"").lower() != UPGRADE.encode("ascii"):
                code = _refusal_code(head, outer)
                outer.close()
                raise EdgeRefused(code)
            outer.settimeout(None)
            inner_agent_end, pumped_end = socket.socketpair()
            threading.Thread(target=pump, args=(outer, pumped_end), name="edge-pump", daemon=True).start()
            return inner_agent_end
        except EdgeRefused:
            raise
        except OSError as exc:
            try:
                raw.close()
            except OSError:
                pass
            raise EdgeRefused("control_unreachable", type(exc).__name__) from None


def pump(outer, inner, *, idle_limit: float | None = None) -> None:
    """Move bytes both ways between the outer TLS and the inner socket in ONE thread until either side ends; then
    close both. Bounded buffers: a side that does not read stops the other from being read."""
    limit = 4 * _PUMP_CHUNK
    to_outer = b""
    to_inner = b""
    outer_open = inner_open = True
    try:
        outer.setblocking(False)
        inner.setblocking(False)
        while (outer_open or to_outer) and (inner_open or to_inner):
            readers = []
            if outer_open and len(to_inner) < limit:
                readers.append(outer)
            if inner_open and len(to_outer) < limit:
                readers.append(inner)
            writers = ([outer] if to_outer else []) + ([inner] if to_inner else [])
            pending = outer_open and len(to_inner) < limit and getattr(outer, "pending", lambda: 0)() > 0
            if pending:
                readable, writable = [outer], []
            else:
                readable, writable, _ = select.select(readers, writers, [], 1.0)
            if outer in readable:
                try:
                    chunk = outer.recv(_PUMP_CHUNK)
                    if chunk:
                        to_inner += chunk
                    else:
                        outer_open = False
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                    pass
                except (OSError, ssl.SSLError):
                    outer_open = False
            if inner in readable:
                try:
                    chunk = inner.recv(_PUMP_CHUNK)
                    if chunk:
                        to_outer += chunk
                    else:
                        inner_open = False
                except BlockingIOError:
                    pass
                except OSError:
                    inner_open = False
            if to_outer and outer in writable:
                try:
                    sent = outer.send(to_outer)
                    to_outer = to_outer[sent:]
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                    pass
                except (OSError, ssl.SSLError):
                    return
            if to_inner and inner in writable:
                try:
                    sent = inner.send(to_inner)
                    to_inner = to_inner[sent:]
                except BlockingIOError:
                    pass
                except OSError:
                    return
    finally:
        for s in (outer, inner):
            try:
                s.close()
            except OSError:
                pass


__all__ = ["CERT_HEADER", "ControlUrl", "EdgeDialer", "EdgeRefused", "PROOF_HEADER", "SLOT_PATH", "UPGRADE",
           "control_url", "make_proof", "outer_context", "parse_proof", "proof_message", "pump",
           "refuse_configured_proxy"]
