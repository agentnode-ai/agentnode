"""The second way to reach a worker: TCP, mutual TLS, loopback only.

Built to `mtls-transport-decision.md`, stages 1 to 5. The unix socket stays the default; this is
chosen only by an address that says `tcps://`, and nothing falls back from one to the other.

## What changes and what does not

Only how a connection is OPENED. On the unix socket the kernel says which account is at the other
end. Here each side presents a certificate from this deployment's issuer and checks the other's:

    1  chain to exactly this deployment's CA       OpenSSL, in the handshake
    2  inside its validity at the EFFECTIVE time   `pki.identity.check_peer` -- the later of the
       (certificate and CA certificate)            system clock and the root-written floor
    3  extended key usage fits the direction       `pki.identity.check_peer`
    4  URI SAN in the grammar, our deployment,     `pki.identity.check_peer`
       the role this side expects
    5  an instance this side was told to accept    `pki.identity.check_peer`
    6  not revoked, by a list this side believes   `pki.identity.check_peer`

and only then is a single application byte written or read. Everything after that is the same code
as on the socket: the MAC over every message, the nonce memory, the replay floor. A handshake that
succeeded is no reason to skip any of them (decision 2.2 and 7).

## Each check once

The chain is trusted from ONE file, loaded ONCE, and no default certificate store is ever loaded:
a context that trusted anything else would accept a certificate this deployment never issued.
OpenSSL's own TIME check is switched off (`NO_CHECK_TIME`): it can only judge by the system clock,
which is the clock a rollback has already fooled, and a validity check made twice -- once there,
once here at the effective time -- could not be shown to work in either place. So the window is
judged in exactly one place, `check_peer`, and so are checks 3 to 6.

## Stage 5: what a service reads, and when

The effective time and the revocation list come from root's files (`pki/trust.py`), which a
`TlsSettings` must name -- there is no TLS configuration without them. They are read afresh for
every connection. Open connections are re-evaluated by a `Watch`: it reloads the files every
`reload_seconds` (and whenever one of them changes), and every `reevaluate_seconds` judges each
open connection again -- checks 2 and 6, with a floor and a list it can still use -- and cuts the
ones that no longer pass. The longest a revocation takes to act on an open connection, once its
list is published, is the sum of the two, and that sum is what is promised.

A renewed certificate is taken up without a restart (`Contexts`): when the files change, the next
connection builds a new context from them, and if the pair is half-moved -- a new key beside the
old certificate -- it keeps the previous context, so no connection ever presents a certificate
with a key that is not its own.

## Loopback, in code

A listener binds only to a literal loopback address, and a gateway only connects to one. There is
no option that widens either: crossing a machine boundary is a separate decision (the decision's
post-loopback gate), not a configuration value.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from agentnode_sdk.pki import identity as _identity
from agentnode_sdk.pki.trust import TrustView
from agentnode_sdk.worker import WorkerUnreachable

SCHEME = "tcps"

#: How long a handshake may take before the connection is given up. Short: nothing legitimate
#: needs longer on loopback, and a peer that holds a handshake open is holding a thread.
HANDSHAKE_SECONDS = 10.0

#: OpenSSL's X509_V_FLAG_NO_CHECK_TIME. Python names no constant for it; the number is OpenSSL's.
#: Validity is judged by `check_peer` at the effective time instead -- see "Each check once".
NO_CHECK_TIME = 0x200000

#: The two numbers whose sum is the longest a revocation takes to reach an open connection.
DEFAULT_RELOAD_SECONDS = 10.0
DEFAULT_REEVALUATE_SECONDS = 5.0

#: How long a worker waits for a revoked caller's container to appear before it stops it.
STOP_APPEAR_SECONDS = 10.0


class NotLoopback(ValueError):
    """An address this transport will not use, because it could leave the machine."""


@dataclass(frozen=True)
class TlsSettings:
    """What one side needs: its own certificate and key, the one anchor it trusts, who it will
    accept at the other end -- and, from stage 5, the revocation list and the floor it judges
    them by. Neither of the last two has a default: a TLS side without them would be one that
    cannot tell revoked from valid or a set-back clock from the right one."""

    certificate: str
    key: str
    anchor: str
    deployment: str
    accept: frozenset = field(default_factory=frozenset)
    revocation_list: str = ""
    floor: str = ""
    reload_seconds: float = DEFAULT_RELOAD_SECONDS
    reevaluate_seconds: float = DEFAULT_REEVALUATE_SECONDS

    def __post_init__(self) -> None:
        if not self.revocation_list or not self.floor:
            raise ValueError(
                "mutual TLS needs a revocation list and a time floor to judge the other side by; "
                "without them it could not tell a revoked certificate from a valid one, or a "
                "clock set back from the right time, so it is not started")
        if not float(self.reload_seconds) > 0 or not float(self.reevaluate_seconds) > 0:
            raise ValueError("the reload and re-evaluation intervals must be positive")

    def trust(self, role: str) -> TrustView:
        """The anchor, the list and the floor, read now, for a side of `role`."""
        return TrustView.read(anchor=self.anchor, revocation_list=self.revocation_list,
                              floor=self.floor, role=role)

    def promised_seconds(self) -> float:
        """The longest a published revocation takes to cut an open connection."""
        return float(self.reload_seconds) + float(self.reevaluate_seconds)


def endpoint(address: str) -> tuple[str, int]:
    """Host and port of a `tcps://` address, or `NotLoopback` for anything that is not a literal
    loopback address. A name is refused too: a name resolves to wherever its owner points it."""
    parsed = urlparse(address or "")
    if parsed.scheme != SCHEME:
        raise NotLoopback("not a %s:// address: %r" % (SCHEME, (address or "")[:60]))
    host = (parsed.hostname or "").strip("[]")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError as exc:
        raise NotLoopback(
            "this transport is loopback only, and %r is not a literal loopback address. Crossing "
            "a machine boundary is a separate decision, not a setting." % host) from exc
    if not literal.is_loopback:
        raise NotLoopback(
            "this transport is loopback only, and %s is not a loopback address. Crossing a "
            "machine boundary is a separate decision, not a setting." % host)
    if parsed.port is None:
        raise NotLoopback("a %s:// address names its port" % SCHEME)
    return str(literal), int(parsed.port)


def _context(side: int, settings: TlsSettings) -> ssl.SSLContext:
    context = ssl.SSLContext(side)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    # Check 2 is `check_peer`'s, at the effective time. OpenSSL still builds and verifies the
    # chain to the one anchor below (check 1); it only stops comparing dates with the system clock.
    context.verify_flags |= NO_CHECK_TIME
    if side == ssl.PROTOCOL_TLS_CLIENT:
        # The name checked is the URI SAN, by `check_peer`, not a hostname: this is not the web,
        # and the peer's name is not a DNS name.
        context.check_hostname = False
    # Check 1. Exactly one anchor, loaded exactly once. No `load_default_certs`, ever.
    context.load_verify_locations(cafile=settings.anchor)
    context.load_cert_chain(certfile=settings.certificate, keyfile=settings.key)
    return context


def server_context(settings: TlsSettings) -> ssl.SSLContext:
    return _context(ssl.PROTOCOL_TLS_SERVER, settings)


def client_context(settings: TlsSettings) -> ssl.SSLContext:
    return _context(ssl.PROTOCOL_TLS_CLIENT, settings)


class Contexts:
    """This side's TLS context, rebuilt when its certificate or key changes -- so that a renewed
    certificate is taken up on the next connection without a restart.

    OpenSSL refuses a key that does not belong to the certificate beside it. A pair found
    half-moved (`pki.issuer.install_renewal` moves the key, then the certificate) therefore fails
    to build, and the previous context -- whose certificate is still valid, it is overlapping --
    is kept until the pair is whole. The first build has no previous context and raises.
    """

    def __init__(self, side: int, settings: TlsSettings, say=None) -> None:
        self.side = side
        self.settings = settings
        self.say = say or (lambda text: None)
        self._lock = threading.Lock()
        self._stamp = None
        self._context: ssl.SSLContext | None = None
        self.current()

    def _files_now(self):
        return TrustView.stamp(self.settings.certificate, self.settings.key)

    def current(self) -> ssl.SSLContext:
        with self._lock:
            stamp = self._files_now()
            if stamp != self._stamp or self._context is None:
                try:
                    built = _context(self.side, self.settings)
                except (ssl.SSLError, OSError, ValueError) as exc:
                    if self._context is None:
                        raise
                    self.say("  kept the previous certificate: the new pair could not be loaded "
                             "(%s)" % _reason(exc))
                    return self._context
                if self._context is not None:
                    self.say("  took up a renewed certificate without a restart")
                self._context, self._stamp = built, stamp
            return self._context


def own_instance(settings: TlsSettings) -> str:
    """The instance this side's own certificate names. What a TLS worker calls itself."""
    from cryptography import x509

    with open(settings.certificate, "rb") as handle:
        certificate = x509.load_pem_x509_certificate(handle.read())
    names = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value.get_values_for_type(x509.UniformResourceIdentifier)
    return _identity.parse(names[0]).instance


def cut(connection) -> None:
    """End a live connection from outside the thread using it.

    On Linux -- where the gateway and the worker run -- a shutdown on the socket itself wakes
    whatever is blocked reading or writing it, and the owner then fails and closes as usual. On
    Windows a shutdown does NOT wake a blocked read (measured: the reader slept through it), and
    only closing the handle does -- the handle itself, at the C level, because `socket.close()`
    defers while a `makefile()` stream is open, and the reader holds exactly such a stream. So
    there, and only there, the handle is closed as well.
    """
    try:
        socket.socket.shutdown(connection, socket.SHUT_RDWR)
    except OSError:
        pass
    if os.name == "nt":                                       # pragma: no cover - not a server
        import _socket

        try:
            _socket.socket.close(connection)
        except OSError:
            pass


class Watch:
    """The open connections of one side, re-evaluated (decision 5.3, stage 5 (d)).

    Every `reevaluate_seconds` it judges each open connection again -- checks 2 and 6 at the
    effective time of that moment, with a floor and a list it can still use -- against a
    `TrustView` it rereads every `reload_seconds` and whenever one of the files changes. A
    connection that no longer passes is CUT, and `on_cut` is told why. A floor that aged out or a
    list that expired fails every connection: then this side does not serve at all.
    """

    def __init__(self, settings: TlsSettings, role: str, say=None) -> None:
        self.settings = settings
        self.role = role
        self.say = say or (lambda text: None)
        self._open: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._next = 0
        self._view: TrustView | None = None
        self._loaded_at = -1e18
        self._stamp = None
        self._thread: threading.Thread | None = None
        self._stopped = False
        #: How many passes have run; a test waits on it instead of on the clock.
        self.passes = 0

    def add(self, connection, der: bytes, who, on_cut=None) -> int:
        with self._lock:
            self._next += 1
            handle = self._next
            self._open[handle] = {"connection": connection, "der": der, "who": who,
                                  "on_cut": on_cut}
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True,
                                                name="tls-watch-" + self.role)
                self._thread.start()
        return handle

    def remove(self, handle: int) -> None:
        with self._lock:
            self._open.pop(handle, None)

    def stop(self) -> None:
        self._stopped = True

    def _loop(self) -> None:
        while not self._stopped:
            time.sleep(float(self.settings.reevaluate_seconds))
            try:
                self.pass_once()
            except Exception as exc:                          # noqa: BLE001 - keep watching
                self.say("  the re-evaluation pass failed: %s" % type(exc).__name__)

    def _current_view(self) -> TrustView:
        stamp = TrustView.stamp(self.settings.anchor, self.settings.revocation_list,
                                self.settings.floor)
        due = time.monotonic() - self._loaded_at >= float(self.settings.reload_seconds)
        if self._view is None or due or stamp != self._stamp:
            self._view = self.settings.trust(self.role)
            self._loaded_at = time.monotonic()
            self._stamp = stamp
        return self._view

    def pass_once(self) -> list:
        """One re-evaluation of every open connection. Returns what was cut and why."""
        view = self._current_view()
        with self._lock:
            watched = list(self._open.items())
        cuts = []
        for handle, item in watched:
            connection = item["connection"]
            if connection.fileno() == -1:                     # closed by its owner meanwhile
                self.remove(handle)
                continue
            presented = item["who"].uri() if item["who"] is not None else ""
            try:
                _identity.standing(item["der"], view, presented)
            except _identity.PeerRefused as refused:
                self.remove(handle)
                cut(connection)
                self.say("  cut an open connection: " + str(refused))
                cuts.append((presented, refused.check))
                if item["on_cut"] is not None:
                    try:
                        item["on_cut"](refused)
                    except Exception:                         # noqa: BLE001
                        pass
        self.passes += 1
        return cuts


# ---------------------------------------------------------------------- the gateway's end

def open_to_worker(address: str, settings: TlsSettings, contexts: Contexts,
                   connect_timeout: float):
    """Connect, shake hands, check the worker. Returns (connection, identity, der). Raises
    `WorkerUnreachable` with the failed check named, and never falls back to anything."""
    host, port = endpoint(address)
    context = contexts.current()
    try:
        raw = socket.create_connection((host, port), timeout=connect_timeout)
    except OSError as exc:
        raise WorkerUnreachable("the sandbox worker at %s could not be reached: %s"
                                % (address, exc)) from exc
    try:
        raw.settimeout(HANDSHAKE_SECONDS)
        connection = context.wrap_socket(raw, server_side=False)
    except (ssl.SSLError, OSError) as exc:
        raw.close()
        raise WorkerUnreachable(
            "the sandbox worker at %s was refused in the TLS handshake (%s). Nothing was sent, "
            "and nothing else was tried." % (address, _reason(exc))) from exc
    der = connection.getpeercert(binary_form=True)
    try:
        who = _identity.check_peer(der,
                                   deployment=settings.deployment,
                                   expected_role=_identity.WORKER,
                                   accept_instances=settings.accept,
                                   trust=settings.trust(_identity.GATEWAY))
    except _identity.PeerRefused as refused:
        connection.close()
        raise WorkerUnreachable(
            "the sandbox worker at %s was refused: %s. Nothing was sent, and nothing else was "
            "tried." % (address, refused)) from refused
    return connection, who, der


def _reason(exc: BaseException) -> str:
    """What OpenSSL said, without anything it quoted from a certificate."""
    return str(getattr(exc, "reason", "") or type(exc).__name__)


# ---------------------------------------------------------------------- the worker's end

class TlsListener:
    """The worker's second door. Shares the worker, the nonce memory and the replay floor with
    the socket's `Bench`, so a message accepted through one door is a replay at the other."""

    def __init__(self, bench, address: str, settings: TlsSettings, say=None) -> None:
        self.bench = bench
        self.address = address
        self.settings = settings
        #: Where a refusal is said. Flushed line by line: under systemd stdout is a pipe, and a
        #: refusal that waits in a buffer for the next few kilobytes -- or for the process to
        #: exit -- is not a log anybody can read when it matters. The first alpha run found
        #: exactly that: the worker's journal held none of its own lines.
        self.say = say or (lambda text: print(text, flush=True))
        self.contexts = Contexts(ssl.PROTOCOL_TLS_SERVER, settings, say=self.say)
        self.watch = Watch(settings, _identity.WORKER, say=self.say)
        self._socket: socket.socket | None = None
        self._stopped = False

    def open(self) -> tuple[str, int]:
        host, port = endpoint(self.address)
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(16)
        listener.settimeout(1.0)
        self._socket = listener
        return listener.getsockname()[:2]

    def serve_forever(self) -> None:
        if self._socket is None:
            self.open()
        while not self._stopped:
            try:
                raw, _ = self._socket.accept()
            except OSError:
                if self._stopped:
                    return
                continue
            threading.Thread(target=self._one, args=(raw,), daemon=True).start()

    def stop_serving(self) -> None:
        self._stopped = True
        self.watch.stop()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:                                   # pragma: no cover
                pass

    def admit(self, raw):
        """The handshake and checks 2-6. Returns the TLS connection, or None -- after saying
        which check failed, on this side only. The peer is told nothing."""
        try:
            raw.settimeout(HANDSHAKE_SECONDS)
            connection = self.contexts.current().wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError) as exc:
            self.say("  refused a connection in the TLS handshake: " + _reason(exc))
            raw.close()
            return None
        der = connection.getpeercert(binary_form=True)
        try:
            who = _identity.check_peer(der,
                                       deployment=self.settings.deployment,
                                       expected_role=_identity.GATEWAY,
                                       accept_instances=self.settings.accept,
                                       trust=self.settings.trust(_identity.WORKER))
        except _identity.PeerRefused as refused:
            self.say("  refused a connection: " + str(refused))
            connection.close()
            return None
        connection.gateway = who                               # type: ignore[attr-defined]
        connection.gateway_der = der                           # type: ignore[attr-defined]
        return connection

    def _one(self, raw) -> None:
        connection = self.admit(raw)
        if connection is None:
            return
        carrying: dict = {}

        def noted(method: str, params: dict) -> None:
            # Which run this connection carries, if any -- so that a cut stops it (below).
            if method == "run":
                job = params.get("job") or {}
                carrying.update(run_id=str(job.get("run_id") or ""),
                                container_name=str(job.get("container_name") or ""))

        def on_cut(refused) -> None:
            # The caller of this run no longer passes -- revoked, say. Cutting the connection
            # ends the run for the gateway; the container would otherwise go on running foreign
            # code for a caller this worker no longer accepts, until its own wall clock.
            if carrying.get("container_name"):
                self.say("  stopping the run the cut connection carried")
                threading.Thread(target=self.bench.worker.stop,
                                 args=(carrying["run_id"], carrying["container_name"],
                                       STOP_APPEAR_SECONDS), daemon=True).start()

        handle = self.watch.add(connection, connection.gateway_der, connection.gateway, on_cut)
        try:
            # From here it is the socket's code, unchanged: MAC, nonce, floor, the closed list.
            self.bench.converse(connection, noted=noted)
        finally:
            self.watch.remove(handle)


__all__ = ["Contexts", "DEFAULT_REEVALUATE_SECONDS", "DEFAULT_RELOAD_SECONDS",
           "HANDSHAKE_SECONDS", "NO_CHECK_TIME", "NotLoopback", "SCHEME", "TlsListener",
           "TlsSettings", "Watch", "client_context", "cut", "endpoint", "open_to_worker",
           "own_instance", "server_context"]

