"""What may travel over plain HTTP, and what must not.

Everything this gateway sends is signed, so an attacker cannot forge a job or a result over a
plain connection. What signing does **not** give is confidentiality: on a plain connection the
pairing code, the bearer token, the artifact and the job's output are all readable by anyone on
the path. A signature proves who wrote something; it does not stop anyone reading it.

So the rule is about *where*, not about *what*:

* **Loopback** — `127.0.0.0/8`, `::1`. Nothing leaves the machine, so there is nothing on the path
  to read it. Plain HTTP is fine and is the default.
* **Anywhere else** — refused unless the transport is encrypted, or unless the operator has said
  in so many words that they accept a plaintext link on a network they control.

Automatic TLS is not part of this track, and pretending otherwise would be worse than refusing:
a self-signed certificate the client cannot verify is confidentiality without authenticity, which
looks like security and is not. So the refusal names the two paths that do work today — a TLS
reverse proxy, or a private tunnel — and the escape hatch is explicit, per-run and loud.
"""
from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse

#: The operator's explicit acceptance of a plaintext link. Deliberately verbose: nobody sets this
#: by accident, and it appears in the refusal so a person can find it without searching.
ALLOW_PLAINTEXT_ENV = "AGENTNODE_GATEWAY_ALLOW_PLAINTEXT"


class InsecureTransportError(Exception):
    """A connection was prevented because it would have sent secrets in the clear."""


def is_loopback(host: str) -> bool:
    """True only for an address that cannot leave the machine.

    A name is not enough: `localhost` is conventional, not guaranteed, and a name that resolves
    off-box must not inherit loopback's exemption. Only literals and the two spellings that are
    fixed by convention are accepted.
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


def plaintext_allowed() -> bool:
    return os.environ.get(ALLOW_PLAINTEXT_ENV, "").strip().lower() in ("1", "true", "yes")


def check_client_url(url: str) -> None:
    """Refuse before a client sends anything to a non-loopback plain-HTTP gateway.

    Raised before pairing, before a token is sent, and before a job -- the three moments where
    something secret would otherwise go out in the clear.
    """
    parsed = urlparse(url or "")
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""
    if scheme == "https":
        return
    if scheme != "http":
        raise InsecureTransportError(
            f"AgentNode does not know how to talk to a gateway over {scheme or 'that'}. "
            "Use an address beginning with https:// or http://."
        )
    if is_loopback(host):
        return
    if plaintext_allowed():
        return
    raise InsecureTransportError(
        f"The connection to {host} is not encrypted, so connecting over the network was "
        "prevented. Your pairing code, your access token and everything your agent sends or "
        "receives would have been readable by anyone between the two machines.\n"
        "\n"
        "Two ways to fix it, both of which keep the gateway itself unchanged:\n"
        "  * Put the gateway behind a TLS reverse proxy and use its https:// address.\n"
        "  * Or reach it through a private tunnel (for example a WireGuard or Tailscale address),\n"
        "    which encrypts the link without a certificate.\n"
        "\n"
        f"If this is a network you control and you accept the risk, set "
        f"{ALLOW_PLAINTEXT_ENV}=1 for this command. It is off by default on purpose."
    )


def check_bind_address(host: str) -> None:
    """Refuse to expose a plain-HTTP gateway beyond the machine it runs on.

    The client-side check protects a client that knows it is talking to something remote. This
    protects the case the operator may not think about: binding to every interface and assuming
    nobody is listening.
    """
    h = (host or "").strip()
    if is_loopback(h):
        return
    if plaintext_allowed():
        return
    where = "every network interface" if h in ("", "0.0.0.0", "::") else h
    raise InsecureTransportError(
        f"Refusing to serve on {where} without encryption. Anyone who can reach this machine "
        "would be able to read the pairing code, the access token and every job's output.\n"
        "\n"
        "  * Leave the gateway on 127.0.0.1 and put a TLS reverse proxy in front of it, or\n"
        "  * reach it over a private tunnel instead of the open network.\n"
        "\n"
        f"To serve in the clear anyway on a network you control, set {ALLOW_PLAINTEXT_ENV}=1."
    )
