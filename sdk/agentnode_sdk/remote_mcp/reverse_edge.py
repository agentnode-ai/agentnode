"""The 443 edge of the reverse hub: where nginx hands over `/agent/v1/slot` (local-worker slice 2, Codex Q-LW2-0001
D1/D2 as amended by Q-LW2-0003).

nginx terminates the PUBLIC TLS of mcp.agentnode.net and passes exactly one location, `/agent/v1/slot`, as an
HTTP/1.1 upgrade to this listener (a unix socket the `rmcp-agent` service owns; never a public address). For every
connection, before anything of the hub is spent on it beyond one bounded header parser:

  1. the header block is read within EDGE_HEADER_SECONDS and at most EDGE_HEADER_BYTES
     (`edge_header_timeout`, `edge_header_too_large`);
  2. the request is exactly `GET /agent/v1/slot HTTP/1.1` with no body, `Host` exactly the configured public name
     (the ORIGINAL authority: nginx passes `$http_host`), `Upgrade: agentnode-slot/1`, and nothing a browser would
     bring -- any `Origin` is `edge_origin_refused`; `Cookie`, `Authorization`, `Sec-WebSocket-*`, a body or any header
     outside the closed set is `edge_bad_request`; a wrong Host is `edge_host_refused`;
  3. the slot proof (`worker/edge.py`): the certificate header is ONE worker leaf certificate issued by this
     deployment's anchor and passes the very `pki.identity.check_peer` policy the inner TLS applies (validity at the
     effective time, serverAuth, one URI SAN, WORKER, deployment, instance, CRL, tombstones); the proof's signature
     verifies under that certificate's key (`edge_proof_invalid`); its time is within +-EDGE_SKEW_SECONDS
     (`edge_proof_stale`); its nonce was not seen in EDGE_NONCE_SECONDS (`edge_proof_replayed`, bounded memory);
  4. the instance is a parkable dial-back target that is eligible NOW -- account, device, target, kill switch, read
     afresh (`edge_not_parkable`; unreadable authority `edge_authority_unavailable`);
  5. a place is reserved against the instance's `2P` and the hub's `max_total` (`edge_slot_bound`).

Only then `101 Switching Protocols`, and the connection goes to the hub's UNCHANGED inner admission -- which must find
the same instance and the same key in the inner certificate (`edge_identity_mismatch`), or nothing is parked. A
refusal is an HTTP status with `{"error": "<code>"}` and the connection is closed.
"""
from __future__ import annotations

import base64
import collections
import json
import os
import socket
import stat
import threading
import time
from typing import Callable

from agentnode_sdk.remote_mcp import reverse_hub as R
from agentnode_sdk.worker import edge as E

EDGE_HEADER_BYTES = 8192
EDGE_HEADER_SECONDS = 5.0
EDGE_SKEW_SECONDS = 60
EDGE_NONCE_SECONDS = 120
EDGE_NONCE_MAX = 65536
EDGE_MAX_PENDING = 128
MAX_CERTIFICATE_BYTES = 2048
#: The only header names a slot request may carry (lower case). nginx adds X-Forwarded-For.
ALLOWED_HEADERS = frozenset({"host", "connection", "upgrade", "agentnode-slot", "agentnode-slot-certificate",
                             "x-forwarded-for"})
BROWSER_HEADERS = ("cookie", "authorization")
STATUS = {"edge_bad_request": 400, "edge_proof_invalid": 401, "edge_proof_stale": 401, "edge_proof_replayed": 401,
          "edge_origin_refused": 403, "edge_not_parkable": 403, "edge_header_timeout": 408,
          "edge_host_refused": 421, "edge_slot_bound": 429, "edge_header_too_large": 431,
          "edge_authority_unavailable": 503}
REASON = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 408: "Request Timeout",
          421: "Misdirected Request", 429: "Too Many Requests", 431: "Request Header Fields Too Large",
          503: "Service Unavailable"}


class EdgeRefusal(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


class NonceMemory:
    """(instance, nonce) seen within `seconds`, at most `limit` entries; full = refuse (never forget early)."""

    def __init__(self, seconds: float = EDGE_NONCE_SECONDS, limit: int = EDGE_NONCE_MAX,
                 clock: Callable[[], float] = time.time) -> None:
        self.seconds = float(seconds)
        self.limit = int(limit)
        self.clock = clock
        self._until: "collections.OrderedDict[tuple, float]" = collections.OrderedDict()
        self._lock = threading.Lock()

    def first_time(self, instance: str, nonce: str) -> bool:
        with self._lock:
            now = self.clock()
            while self._until:
                key, until = next(iter(self._until.items()))
                if until > now:
                    break
                self._until.popitem(last=False)
            key = (instance, nonce)
            if key in self._until:
                return False
            if len(self._until) >= self.limit:
                raise EdgeRefusal("edge_slot_bound", "the replay memory is full")
            self._until[key] = now + self.seconds
            return True


def read_head(sock, *, limit: int = EDGE_HEADER_BYTES, seconds: float = EDGE_HEADER_SECONDS,
              clock=time.monotonic) -> bytes:
    """The header block, read within a TOTAL deadline and a size bound; nothing beyond it is consumed."""
    data = b""
    ends = clock() + seconds
    while b"\r\n\r\n" not in data:
        if len(data) > limit:
            raise EdgeRefusal("edge_header_too_large")
        left = ends - clock()
        if left <= 0:
            raise EdgeRefusal("edge_header_timeout")
        sock.settimeout(left)
        try:
            chunk = sock.recv(min(4096, limit + 1 - len(data)))
        except socket.timeout:
            raise EdgeRefusal("edge_header_timeout") from None
        if not chunk:
            raise EdgeRefusal("edge_bad_request", "closed before the header ended")
        data += chunk
    head, _sep, rest = data.partition(b"\r\n\r\n")
    if len(head) + 4 > limit:
        raise EdgeRefusal("edge_header_too_large")
    if rest:
        raise EdgeRefusal("edge_bad_request", "a slot request carries no body")
    return head


def parse_request(head: bytes, host: str) -> dict:
    """The closed request grammar; returns the lower-cased header map."""
    try:
        text = head.decode("ascii")
    except UnicodeDecodeError:
        raise EdgeRefusal("edge_bad_request", "non-ASCII header") from None
    lines = text.split("\r\n")
    if lines[0] != "GET %s HTTP/1.1" % E.SLOT_PATH:
        raise EdgeRefusal("edge_bad_request", "the request line")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line or line[:1] in (" ", "\t"):
            raise EdgeRefusal("edge_bad_request", "a malformed header line")
        name, value = line.split(":", 1)
        name = name.strip().lower()
        if not name or name != name.strip() or name in fields:
            raise EdgeRefusal("edge_bad_request", "a repeated or empty header")
        fields[name] = value.strip()
    if "origin" in fields:
        raise EdgeRefusal("edge_origin_refused")
    if any(n in fields for n in BROWSER_HEADERS) or any(n.startswith("sec-websocket-") for n in fields):
        raise EdgeRefusal("edge_bad_request", "a browser or WebSocket header")
    if "content-length" in fields or "transfer-encoding" in fields:
        raise EdgeRefusal("edge_bad_request", "a slot request carries no body")
    if set(fields) - ALLOWED_HEADERS:
        raise EdgeRefusal("edge_bad_request", "a header outside the slot grammar")
    if fields.get("host") != host:
        raise EdgeRefusal("edge_host_refused")
    if fields.get("upgrade") != E.UPGRADE or "upgrade" not in [t.strip().lower() for t in
                                                               fields.get("connection", "").split(",")]:
        raise EdgeRefusal("edge_bad_request", "not an %s upgrade" % E.UPGRADE)
    if "agentnode-slot" not in fields or "agentnode-slot-certificate" not in fields:
        raise EdgeRefusal("edge_proof_invalid", "no slot proof")
    return fields


def _canonical_b64(text: str) -> bytes:
    if not E._B64URL.match(text or ""):
        raise ValueError("not base64url")
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != text:
        raise ValueError("not canonical")
    return raw


class EdgeServer:
    """Accepts nginx's upgraded connections on `path` (unix socket) -- or, in tests only, on a loopback TCP address --
    and hands each admitted one to `hub._admit`."""

    def __init__(self, hub: R.ReverseHub, *, host: str, path: str = "", tcp_loopback: tuple | None = None,
                 group: str = "", nonces: NonceMemory | None = None, clock: Callable[[], float] = time.time,
                 max_pending: int = EDGE_MAX_PENDING, say=None) -> None:
        if bool(path) == bool(tcp_loopback):
            raise ValueError("the edge listens on exactly one of a unix socket or (tests) a loopback address")
        self.hub = hub
        self.host = host
        self.path = path
        self.tcp_loopback = tcp_loopback
        self.group = group
        self.clock = clock
        self.nonces = nonces or NonceMemory(clock=clock)
        self._slots = threading.BoundedSemaphore(int(max_pending))
        self.header_bytes = EDGE_HEADER_BYTES
        self.header_seconds = EDGE_HEADER_SECONDS
        self._socket = None
        self._stopped = threading.Event()
        self.refused = collections.Counter()
        self.say = say or hub.say

    # ------------------------------------------------------------ listening
    def start(self) -> None:
        if self.path:
            if os.path.lexists(self.path):
                if not stat.S_ISSOCK(os.lstat(self.path).st_mode):
                    raise RuntimeError("%s exists and is not a socket" % self.path)
                os.unlink(self.path)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            old = os.umask(0o117)
            try:
                s.bind(self.path)
            finally:
                os.umask(old)
            if self.group:
                import grp
                os.chown(self.path, -1, grp.getgrnam(self.group).gr_gid)
            os.chmod(self.path, 0o660)
        else:
            import ipaddress
            if not ipaddress.ip_address(self.tcp_loopback[0]).is_loopback:
                raise ValueError("a TCP edge is for tests and binds loopback only")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(tuple(self.tcp_loopback))
            self.tcp_loopback = s.getsockname()
        s.listen(128)
        s.settimeout(1.0)
        self._socket = s
        self._thread = threading.Thread(target=self._loop, name="reverse-edge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._socket is not None:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            R._close(self._socket)
            t = getattr(self, "_thread", None)
            if t is not None and t is not threading.current_thread():
                t.join(timeout=3.0)
        if self.path and os.path.lexists(self.path):
            try:
                os.unlink(self.path)
            except OSError:
                pass

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                conn, _ = self._socket.accept()
            except OSError:
                continue
            if not self._slots.acquire(blocking=False):
                self._answer(conn, "edge_slot_bound")             # bounded parsers: never queue them
                continue
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    # ------------------------------------------------------------ one connection
    def _answer(self, conn, code: str) -> None:
        self.refused[code] += 1
        status = STATUS.get(code, 400)
        body = json.dumps({"error": code}).encode("ascii")
        try:
            conn.settimeout(2.0)
            conn.sendall(("HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
                          "Connection: close\r\n\r\n" % (status, REASON[status], len(body))).encode("ascii") + body)
        except OSError:
            pass
        R._close(conn)

    def _one(self, conn) -> None:
        try:
            try:
                head = read_head(conn, limit=self.header_bytes, seconds=self.header_seconds)
                fields = parse_request(head, self.host)
                instance, spki, concurrency = self.judge(fields)
            except EdgeRefusal as refusal:
                self._answer(conn, refusal.code)
                return
            if not self.hub.reserve(instance, concurrency):
                self._answer(conn, "edge_slot_bound")
                return
            try:
                conn.settimeout(5.0)
                conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: %s\r\n\r\n"
                              % E.UPGRADE).encode("ascii"))
            except OSError:
                self.hub._unreserve(instance)
                R._close(conn)
                return
            self.hub._admit(conn, reserved=instance, expect_spki=spki)
        finally:
            self._slots.release()

    def judge(self, fields: dict) -> tuple[str, bytes, int]:
        """Checks 3-4: (instance, the proof key's SPKI, the target's concurrency), or EdgeRefusal."""
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        from agentnode_sdk.pki import identity as _identity

        try:
            instance, seconds, nonce, signature = E.parse_proof(fields["agentnode-slot"])
            der = _canonical_b64(fields["agentnode-slot-certificate"])
            if not 64 <= len(der) <= MAX_CERTIFICATE_BYTES:
                raise ValueError("certificate size")
            certificate = x509.load_der_x509_certificate(der)
        except (ValueError, TypeError):
            raise EdgeRefusal("edge_proof_invalid", "malformed proof") from None
        try:
            known = self.hub._targets() or {}
        except Exception:                                      # noqa: BLE001 - unreadable register
            raise EdgeRefusal("edge_authority_unavailable") from None
        trust = self.hub.tls.trust(_identity.GATEWAY)
        try:
            certificate.verify_directly_issued_by(trust.anchor())
        except (InvalidSignature, ValueError, TypeError):
            raise EdgeRefusal("edge_proof_invalid", "not issued by this deployment") from None
        except Exception:                                      # noqa: BLE001 - unreadable anchor
            raise EdgeRefusal("edge_authority_unavailable") from None
        try:
            who = _identity.check_peer(der, deployment=self.hub.tls.deployment, expected_role=_identity.WORKER,
                                       accept_instances=frozenset(known), trust=trust)
        except _identity.PeerRefused as refused:
            # a genuine certificate of an instance that is not (or no longer) a parkable target here -- unknown, or
            # revoked / withdrawn for good -- is not a broken proof: it is an instance that may not park
            raise EdgeRefusal("edge_not_parkable" if refused.check in (_identity.CHECK_INSTANCE, _identity.CHECK_REVOKED)
                              else "edge_proof_invalid", refused.check) from None
        except Exception:                                      # noqa: BLE001 - trust state unreadable
            raise EdgeRefusal("edge_authority_unavailable") from None
        if str(who.instance) != instance:
            raise EdgeRefusal("edge_proof_invalid", "the proof names another instance")
        key = certificate.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            raise EdgeRefusal("edge_proof_invalid", "not a P-256 key")
        try:
            key.verify(signature, E.proof_message(self.host, E.SLOT_PATH, instance, seconds, nonce),
                       ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            raise EdgeRefusal("edge_proof_invalid", "signature") from None
        if abs(self.clock() - int(seconds)) > EDGE_SKEW_SECONDS:
            raise EdgeRefusal("edge_proof_stale")
        if not self.nonces.first_time(instance, nonce):
            raise EdgeRefusal("edge_proof_replayed")
        t = known.get(instance)
        if t is None or t.get("status") not in R.PARKABLE:
            raise EdgeRefusal("edge_not_parkable")
        try:
            why = self.hub._eligible(t["target_id"])
        except Exception:                                      # noqa: BLE001
            raise EdgeRefusal("edge_authority_unavailable") from None
        if why == "target_authority_unverifiable":
            raise EdgeRefusal("edge_authority_unavailable")
        if why:
            raise EdgeRefusal("edge_not_parkable", why)
        return instance, R.spki_of(der), int(t.get("concurrency") or 1)


__all__ = ["ALLOWED_HEADERS", "EDGE_HEADER_BYTES", "EDGE_HEADER_SECONDS", "EDGE_NONCE_SECONDS", "EDGE_SKEW_SECONDS",
           "EdgeRefusal", "EdgeServer", "NonceMemory", "STATUS", "parse_request", "read_head"]
