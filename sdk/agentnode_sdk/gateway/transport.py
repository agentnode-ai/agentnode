"""Where a connection may go in the clear -- a boundary, not a setting.

Everything this gateway sends is signed, so nobody can forge a job or a result over a plain
connection. Signing does not give confidentiality: on a plain link the pairing code, the bearer
token, the artifact and the job's output are readable by anyone on the path. A signature proves who
wrote something; it does not stop anyone reading it.

So the rule is about *where* a connection goes:

* **Loopback** -- 127.0.0.0/8 and ::1. Nothing leaves the machine, so there is nothing on the path
  to read it. Plain HTTP is fine and is the default.
* **Anywhere else** -- only over an authenticated, encrypted transport. There is no exception, no
  development mode, and no environment variable.

An earlier version of this module had one: AGENTNODE_GATEWAY_ALLOW_PLAINTEXT=1 let an operator
accept plaintext to a remote host. That was a defect. A boundary with a documented way around it
is a default, and the reason to hold this one is not that plaintext is usually bad -- it is that
the first thing crossing the link is the pairing code and the second is a long-lived token. An
operator who misjudges "a network I control" once loses both. The variable is now inert, and it is
tested for inertness rather than merely deleted, because a machine somewhere still has it set.

Reverse proxies and private tunnels are supported, in the two shapes that give the guarantee
rather than promising it:

* The gateway terminates TLS itself -- give it a certificate and key. It is checked by loading it,
  not by being told about it.
* The gateway stays on loopback behind a terminator -- a TLS reverse proxy, or something like
  "tailscale serve", holds the certificate and forwards to 127.0.0.1. The gateway never binds a
  public interface, so there is no plaintext link to get wrong.

Both are configuration the gateway can verify. What it cannot verify -- "this interface is really
a WireGuard tunnel, trust me" -- is not offered, because a check that takes the operator's word
for it is the escape hatch again with a longer name.

Organisation-wide rules may only tighten. TransportRules can forbid things this module allows,
such as plain HTTP even on loopback for a shared machine. It cannot permit anything this module
forbids: there is no field for that, so loosening is not expressible rather than merely
disallowed, and tighten() refuses to move in the other direction.
"""
from __future__ import annotations

import ipaddress
import os
import ssl
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse

#: Kept only so it can be shown to be inert. Setting it does nothing at all.
LEGACY_PLAINTEXT_ENV = "AGENTNODE_GATEWAY_ALLOW_PLAINTEXT"

#: Query keys that must never carry a credential. URLs end up in proxy logs, browser history,
#: shell history, crash reports and process listings -- none of them places for a token.
CREDENTIAL_QUERY_KEYS = frozenset({
    "token", "access_token", "auth", "authorization", "bearer",
    "code", "pairing_code", "pair_code", "secret", "key", "api_key", "password",
})


class InsecureTransportError(Exception):
    """A connection was prevented because it would have sent secrets in the clear."""


class CredentialInUrlError(Exception):
    """A credential was found in a URL, where it would outlive the request."""


def is_loopback(host: str) -> bool:
    """True only for an address that cannot leave the machine.

    A name is not enough. "localhost" is fixed by convention and is accepted; an arbitrary name
    that merely looks like it resolves wherever its owner points it and must not inherit the
    exemption.
    """
    h = (host or "").strip().strip("[]").lower()
    if not h:
        return False
    if h in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class TransportRules:
    """What an organisation may additionally forbid.

    Every field can only move towards stricter. There is deliberately no field for "allow
    plaintext off-box": the boundary is not represented here, so no configuration can reach it.
    """

    allow_plain_loopback: bool = True

    def tighten(self, *, allow_plain_loopback: bool | None = None) -> "TransportRules":
        if allow_plain_loopback is None:
            return self
        if allow_plain_loopback and not self.allow_plain_loopback:
            raise ValueError(
                "transport rules may only be tightened; plain HTTP on loopback has already been "
                "forbidden and cannot be re-enabled here"
            )
        return TransportRules(allow_plain_loopback=allow_plain_loopback)


DEFAULT_RULES = TransportRules()


def _tls_remedy(host: str) -> str:
    """A refusal that does not say how to proceed is just an obstacle."""
    where = host or "your-gateway"
    return (
        "Two ways to reach a gateway that is not on this machine, both of which encrypt the link:\n"
        "\n"
        "  1. Give the gateway a certificate, and it will serve HTTPS itself:\n"
        "       agentnode gateway init --tls-cert /path/fullchain.pem --tls-key /path/privkey.pem\n"
        "       agentnode gateway start\n"
        "     then connect to  https://" + where + "/\n"
        "\n"
        "  2. Or leave the gateway on 127.0.0.1 and put a TLS terminator in front of it -- a\n"
        "     reverse proxy such as Caddy or nginx, or tailscale serve. The gateway needs no\n"
        "     certificate of its own in that shape, because it never binds a public interface.\n"
    )


def assert_no_credentials_in_url(url: str) -> None:
    """Refuse a URL that carries a secret where it would be logged.

    Credentials belong in a header or a body. A URL is copied into proxy logs, shell history and
    error reports, all of which outlive the request and none of which are protected.
    """
    parsed = urlparse(url or "")
    if parsed.username or parsed.password:
        raise CredentialInUrlError(
            "the gateway address contains a username or password. Credentials belong in the "
            "connection you have already paired, not in the address."
        )
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.strip().lower() in CREDENTIAL_QUERY_KEYS:
            # The value is deliberately not repeated: naming it here would put it in the very
            # logs this check exists to keep it out of.
            raise CredentialInUrlError(
                "the gateway address carries " + repr(key) + " as a query parameter. Anything in "
                "a URL ends up in proxy logs and shell history, so it must not be a credential."
            )


def check_client_url(url: str, rules: TransportRules = DEFAULT_RULES) -> None:
    """Refuse before a client sends anything that must not be read on the way.

    Called from inside the request helpers rather than beside them, so pairing, authentication and
    job submission are all covered by construction instead of by remembering.
    """
    assert_no_credentials_in_url(url)
    parsed = urlparse(url or "")
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""

    if scheme == "https":
        return
    if scheme != "http":
        raise InsecureTransportError(
            "AgentNode does not know how to reach a gateway over " + (scheme or "that scheme") +
            ". Use an address beginning with https:// (or http:// for a gateway on this machine)."
        )
    if is_loopback(host):
        if not rules.allow_plain_loopback:
            raise InsecureTransportError(
                "This installation requires an encrypted connection even to a gateway on this "
                "machine. Use its https:// address."
            )
        return

    hint = ""
    if os.environ.get(LEGACY_PLAINTEXT_ENV):
        hint = (
            "\n" + LEGACY_PLAINTEXT_ENV + " is set in this environment. It used to permit exactly "
            "this and no longer does anything -- it was removed because the first thing to cross "
            "the link is your pairing code and the second is a long-lived token. Unset it.\n"
        )
    raise InsecureTransportError(
        "Refusing to connect: the link to " + host + " would not be encrypted, and your pairing "
        "code, your access token and everything your agent sends or receives would be readable by "
        "anyone between the two machines.\n" + hint + "\n" + _tls_remedy(host)
    )


@dataclass(frozen=True)
class TlsFiles:
    """A certificate and key the gateway will serve with."""

    certfile: str
    keyfile: str

    def context(self) -> ssl.SSLContext:
        """Load it. Being told there is a certificate proves nothing; loading it proves it."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            ctx.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
        except (OSError, ssl.SSLError) as exc:
            raise InsecureTransportError(
                "The TLS certificate could not be loaded, so the gateway was not started: " +
                str(exc) + "\n  certificate: " + self.certfile + "\n  private key: " +
                self.keyfile + "\nBoth files must exist and belong together. Starting without "
                "them would have served in the clear, which is why this is an error and not a "
                "warning."
            ) from exc
        return ctx


def check_bind_address(
    host: str,
    tls: TlsFiles | None = None,
    rules: TransportRules = DEFAULT_RULES,
) -> ssl.SSLContext | None:
    """Decide whether the gateway may listen here, and return the TLS context if it will.

    The client-side check protects a client that knows it is talking to something remote. This
    covers the case an operator may not think about: binding every interface and assuming nobody
    is listening.
    """
    h = (host or "").strip()
    if is_loopback(h):
        if not rules.allow_plain_loopback and tls is None:
            raise InsecureTransportError(
                "This installation requires TLS even on loopback. Start the gateway with a "
                "certificate:\n  agentnode gateway init --tls-cert /path/fullchain.pem "
                "--tls-key /path/privkey.pem"
            )
        return tls.context() if tls is not None else None

    if tls is not None:
        return tls.context()

    where = "every network interface" if h in ("", "0.0.0.0", "::") else h
    hint = ""
    if os.environ.get(LEGACY_PLAINTEXT_ENV):
        hint = (
            "\n" + LEGACY_PLAINTEXT_ENV + " is set. It used to permit this and no longer does "
            "anything; you can unset it.\n"
        )
    raise InsecureTransportError(
        "Refusing to serve on " + where + " without encryption. Anyone who can reach this machine "
        "would be able to read the pairing code, the access token and every job's output.\n" +
        hint + "\n" + _tls_remedy("" if h in ("", "0.0.0.0", "::") else h)
    )


def public_url_for(host: str, port: int, tls: bool) -> str:
    """The address to hand a user, in the scheme that will actually be accepted."""
    shown = host or "127.0.0.1"
    if shown in ("0.0.0.0", "::"):
        shown = "127.0.0.1"
    if ":" in shown and not shown.startswith("["):
        shown = "[" + shown + "]"
    return ("https://" if tls else "http://") + shown + ":" + str(port)
