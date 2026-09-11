"""One string a person hands over, carrying everything a client needs to arrive safely.

Pairing used to need three things a person had to get right separately: the gateway's address, a
one-time code, and -- for anything but a public certificate -- some way of knowing that the thing
answering was the right thing. The first two were typed; the third was hoped.

An invitation is the three of them together, produced by the gateway, copied once:

    agentnode-invite-1.<base64url of a small document>

Base64 of a document rather than fields separated by punctuation, because an address can be an
IPv6 literal and IPv6 literals are full of colons. A reader that split on one would work until
somebody deployed it properly.

## What it carries and what that means

  where        the address a client connects to
  code         the one-time pairing code, which is still single-use and still expires
  certificate  the digest of the certificate the client must find at that address

The client pins the certificate BEFORE it sends the code. That ordering is the point: a client
that sent its code first and checked afterwards would have handed a one-time credential to
whatever answered.

## It is a credential

Not in the sense that it opens anything by itself -- the code is single-use and short-lived -- but
it is the thing that would let somebody else pair as you. It is not written into any log this
build keeps, and `agentnode gateway pair` prints it once.
"""
from __future__ import annotations

import base64
import json

PREFIX = "agentnode-invite-1."


class NotAnInvitation(Exception):
    """That string is not one, or is one this build does not understand."""


def write(where: str, code: str, certificate_sha256: str) -> str:
    """The string an operator hands over."""
    for name, value in (("where", where), ("code", code),
                        ("certificate", certificate_sha256)):
        if not value:
            raise NotAnInvitation(
                "an invitation carries where to connect, the code, and the certificate to expect."
                " This one has no " + name + ", and a client that accepted it would be trusting "
                "whatever answered.")
    body = json.dumps({"where": where, "code": code, "certificate": certificate_sha256},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def read(invitation: str) -> tuple[str, str, str]:
    """(where, code, certificate) -- or a refusal that says what is missing.

    Every refusal here is about the invitation's shape and never repeats its contents: an error
    message is something people paste into issues.
    """
    text = (invitation or "").strip()
    if not text.startswith(PREFIX):
        raise NotAnInvitation(
            "that does not look like an invitation. Ask whoever runs the sandbox for one:\n"
            "  agentnode gateway pair --dir <state>\n"
            "and paste what it prints.")
    raw = text[len(PREFIX):]
    try:
        body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
    except Exception as exc:                                  # noqa: BLE001
        raise NotAnInvitation(
            "that invitation could not be read; it may have been cut short when it was copied."
        ) from exc
    if not isinstance(body, dict):
        raise NotAnInvitation("that invitation is not one this build understands.")
    where = str(body.get("where") or "")
    code = str(body.get("code") or "")
    certificate = str(body.get("certificate") or "")
    if not where or not code:
        raise NotAnInvitation("that invitation does not say where to connect, or has no code.")
    if not certificate:
        # Refused rather than treated as "no pinning wanted". An invitation without a certificate
        # is one a client cannot check the far side against, and accepting it would make the
        # pinning optional in exactly the situation where it matters.
        raise NotAnInvitation(
            "that invitation does not say which certificate to expect, so a client using it could "
            "not tell the sandbox from anything else answering at that address. Ask for a new one "
            "from a gateway that has a certificate:\n  agentnode gateway init --tls-self-signed")
    if len(certificate) != 64 or any(c not in "0123456789abcdef" for c in certificate.lower()):
        raise NotAnInvitation("the certificate in that invitation is not a sha256 digest.")
    return where, code, certificate.lower()
