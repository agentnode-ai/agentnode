"""The second way to reach a worker: TCP, mutual TLS, loopback only.

Built to `mtls-transport-decision.md`, stages 1 to 4. The unix socket stays the default; this is
chosen only by an address that says `tcps://`, and nothing falls back from one to the other.

## What changes and what does not

Only how a connection is OPENED. On the unix socket the kernel says which account is at the other
end. Here each side presents a certificate from this deployment's issuer and checks the other's:

    1  chain to exactly this deployment's CA       OpenSSL, in the handshake
    2  inside its validity window                  OpenSSL, in the handshake, by the system clock
    3  extended key usage fits the direction       `pki.identity.check_peer`
    4  URI SAN in the grammar, our deployment,     `pki.identity.check_peer`
       the role this side expects
    5  an instance this side was told to accept    `pki.identity.check_peer`

and only then is a single application byte written or read. Everything after that is the same code
as on the socket: the MAC over every message, the nonce memory, the replay floor. A handshake that
succeeded is no reason to skip any of them (decision 2.2 and 7).

Check 6, "not revoked", is stage 5 of the decision and does not exist here. Validity is judged by
the system clock until that stage adds the rollback-resistant floor. Both are stated, not hidden.

## Each check once

The chain is trusted from ONE file, loaded ONCE, and no default certificate store is ever loaded:
a context that trusted anything else would accept a certificate this deployment never issued. The
three checks after the handshake live only in `check_peer`. A check made in two places cannot be
shown to work, because removing one copy leaves the other refusing.

## Loopback, in code

A listener binds only to a literal loopback address, and a gateway only connects to one. There is
no option that widens either: crossing a machine boundary is a separate decision (the decision's
post-loopback gate), not a configuration value.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
import threading
from dataclasses import dataclass, field
from urllib.parse import urlparse

from agentnode_sdk.pki import identity as _identity
from agentnode_sdk.worker import WorkerUnreachable

SCHEME = "tcps"

#: How long a handshake may take before the connection is given up. Short: nothing legitimate
#: needs longer on loopback, and a peer that holds a handshake open is holding a thread.
HANDSHAKE_SECONDS = 10.0


class NotLoopback(ValueError):
    """An address this transport will not use, because it could leave the machine."""


@dataclass(frozen=True)
class TlsSettings:
    """What one side needs: its own certificate and key, the one anchor it trusts, and who it
    will accept at the other end."""

    certificate: str
    key: str
    anchor: str
    deployment: str
    accept: frozenset = field(default_factory=frozenset)


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


def own_instance(settings: TlsSettings) -> str:
    """The instance this side's own certificate names. What a TLS worker calls itself."""
    from cryptography import x509

    with open(settings.certificate, "rb") as handle:
        certificate = x509.load_pem_x509_certificate(handle.read())
    names = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value.get_values_for_type(x509.UniformResourceIdentifier)
    return _identity.parse(names[0]).instance


# ---------------------------------------------------------------------- the gateway's end

def open_to_worker(address: str, settings: TlsSettings, context: ssl.SSLContext,
                   connect_timeout: float):
    """Connect, shake hands, check the worker. Returns (connection, identity). Raises
    `WorkerUnreachable` with the failed check named, and never falls back to anything."""
    host, port = endpoint(address)
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
    try:
        who = _identity.check_peer(connection.getpeercert(binary_form=True),
                                   deployment=settings.deployment,
                                   expected_role=_identity.WORKER,
                                   accept_instances=settings.accept)
    except _identity.PeerRefused as refused:
        connection.close()
        raise WorkerUnreachable(
            "the sandbox worker at %s was refused: %s. Nothing was sent, and nothing else was "
            "tried." % (address, refused)) from refused
    return connection, who


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
        self.context = server_context(settings)
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
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:                                   # pragma: no cover
                pass

    def admit(self, raw):
        """The handshake and checks 3-5. Returns the TLS connection, or None -- after saying
        which check failed, on this side only. The peer is told nothing."""
        try:
            raw.settimeout(HANDSHAKE_SECONDS)
            connection = self.context.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError) as exc:
            self.say("  refused a connection in the TLS handshake: " + _reason(exc))
            raw.close()
            return None
        try:
            who = _identity.check_peer(connection.getpeercert(binary_form=True),
                                       deployment=self.settings.deployment,
                                       expected_role=_identity.GATEWAY,
                                       accept_instances=self.settings.accept)
        except _identity.PeerRefused as refused:
            self.say("  refused a connection: " + str(refused))
            connection.close()
            return None
        connection.gateway = who                               # type: ignore[attr-defined]
        return connection

    def _one(self, raw) -> None:
        connection = self.admit(raw)
        if connection is None:
            return
        # From here it is the socket's code, unchanged: MAC, nonce, floor, the closed list.
        self.bench.converse(connection)


__all__ = ["HANDSHAKE_SECONDS", "NotLoopback", "SCHEME", "TlsListener", "TlsSettings",
           "client_context", "endpoint", "open_to_worker", "own_instance", "server_context"]
