"""Identities that may never be accepted again, whatever certificate they present.

Revocation is by SERIAL. That is correct and it is not sufficient, and the gap is exactly one
step wide: issue a new certificate for the same instance name and it has a new serial, which is
not in the revocation list, so the worker that was taken away is back. Nothing in the existing
checks notices, because every one of them passes -- the chain is this deployment's, the validity
is current, the usage fits, the name is in the accept list, and the serial is not revoked.

`recover_entry` in the issuer already does the right thing for one case: it revokes every serial
an entry ever had and locks renewal. What it cannot do is stop a fresh `pki add` for the same
instance name, and it cannot tell a REMOTE verifier anything at all -- a worker on another
machine has no issuer inventory to consult and must be able to prove the revocation from
something it holds.

So a revoked identity gets a tombstone: the URI itself, in a list signed by the deployment CA,
distributed alongside the revocation list and judged the same way.

    /etc/agentnode/trust/revoked-identities.json

## The rules, and why each

* **Signed by the CA, verified against the anchor.** A remote verifier must not have to trust
  the channel the list arrived on, or the machine that handed it over. It verifies the signature
  against the same anchor it already verifies certificates against, so distribution can be a
  file copy, a configuration manager, or anything else, and none of them has to be trusted.
* **It expires.** Like the revocation list, so a list that stopped being published stops being
  believed rather than quietly ageing into permanent permission.
* **Judged at the EFFECTIVE time** -- the later of the clock and the root-written floor -- so a
  clock set backwards cannot make an expired list current again.
* **A list that cannot be believed is a refusal, not an empty list.** The same rule the
  revocation list has, and for the same reason: "I could not read the list of who is banned" is
  not "nobody is banned".
* **A tombstone is permanent.** There is no un-tombstone. A replacement worker takes a NEW
  instance name, which is better than reinstating an old one for a second reason: the health
  binding notices a different name and re-measures, where reinstating the old one would look
  like nothing had changed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

#: Where root publishes it, beside the revocation list.
LIST_NAME = "revoked-identities.json"

#: How long a published list is valid, and how old it may get before root signs a fresh one.
#: The same shape as the revocation list's, and for the same reasons.
VALID_SECONDS = 7 * 24 * 3600
REFRESH_AFTER_SECONDS = 24 * 3600

FORMAT = 1

#: Why a list was not believed. Each is its own name so a refusal says which.
UNREADABLE = "identity-tombstones-unreadable"
MALFORMED = "identity-tombstones-malformed"
NOT_OURS = "identity-tombstones-not-this-deployment"
BAD_SIGNATURE = "identity-tombstones-signature"
EXPIRED = "identity-tombstones-expired"


class ListUnusable(Exception):
    """A tombstone list that exists and cannot be believed. Refuses, never defaults to empty."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason + (": " + detail if detail else ""))
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Tombstones:
    """The identities this deployment has permanently withdrawn."""

    deployment: str
    published_at: float
    not_after: float
    uris: frozenset

    def holds(self, uri: str) -> bool:
        return str(uri) in self.uris

    def usable_at(self, effective_time: float) -> None:
        if effective_time > self.not_after:
            raise ListUnusable(
                EXPIRED,
                "the list of withdrawn identities expired at %.0f and the effective time is "
                "%.0f. A list that stopped being published stops being believed."
                % (self.not_after, effective_time))


def body_to_sign(deployment: str, published_at: float, not_after: float, uris) -> bytes:
    """The exact bytes the signature is over. One serialisation, so both sides agree."""
    return json.dumps({
        "format": FORMAT,
        "deployment": str(deployment),
        "published_at": float(published_at),
        "not_after": float(not_after),
        "uris": sorted({str(u) for u in uris}),
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")


def publish(deployment: str, uris, *, now: float, signer) -> bytes:
    """The signed document root writes. `signer` takes bytes and returns a signature."""
    body = body_to_sign(deployment, now, now + VALID_SECONDS, uris)
    return json.dumps({
        "body": json.loads(body.decode("utf-8")),
        "signature": signer(body).hex(),
    }, sort_keys=True, indent=1).encode("utf-8")


def read(raw: bytes, *, deployment: str, verifier) -> Tombstones:
    """Parse and AUTHENTICATE. `verifier(body, signature)` raises or returns.

    Nothing here trusts the file's own claims until the signature over them has been checked
    against the deployment's anchor -- which is the point: the list can then be distributed by
    any means at all, because none of those means has to be trustworthy.
    """
    try:
        said = json.loads(raw.decode("utf-8"))
        body = said["body"]
        signature = bytes.fromhex(str(said["signature"]))
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise ListUnusable(MALFORMED, str(exc)) from exc
    if int(body.get("format") or 0) != FORMAT:
        raise ListUnusable(MALFORMED, "not a format %d list" % FORMAT)

    exact = body_to_sign(body.get("deployment"), body.get("published_at"),
                         body.get("not_after"), body.get("uris") or ())
    if exact != json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"):
        # The document carries a field the signature was not over, or is missing one it was.
        raise ListUnusable(MALFORMED, "the document is not the shape its signature covers")
    try:
        verifier(exact, signature)
    except Exception as exc:                                  # noqa: BLE001 - any failure is one
        raise ListUnusable(BAD_SIGNATURE, type(exc).__name__) from exc

    if str(body.get("deployment")) != str(deployment):
        raise ListUnusable(
            NOT_OURS, "it is for deployment %r and this is %r"
                      % (body.get("deployment"), deployment))
    return Tombstones(deployment=str(body["deployment"]),
                      published_at=float(body["published_at"]),
                      not_after=float(body["not_after"]),
                      uris=frozenset(str(u) for u in body.get("uris") or ()))


__all__ = ["BAD_SIGNATURE", "EXPIRED", "FORMAT", "LIST_NAME", "ListUnusable", "MALFORMED",
           "NOT_OURS", "REFRESH_AFTER_SECONDS", "Tombstones", "UNREADABLE", "VALID_SECONDS",
           "body_to_sign", "publish", "read"]
