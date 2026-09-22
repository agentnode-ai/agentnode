"""The signed revocation list: made from the inventory by the issuer, read by both services.

Decision 5.3. The inventory is what decides whether a certificate is revoked; the list is a signed
copy of that decision, made so that two services that may not read the inventory can act on it.
It is an ordinary X.509 CRL, signed with the issuing key, published in two durable stages
(`files.durable_publish`) at

    /etc/agentnode/trust/revoked.crl     root 0644 -- read by both services, written by neither

## What a service does with it

On every connection, and on every open connection at every re-evaluation, a service reads the
list and believes it only if

    * it parses,
    * it names this deployment's CA as its issuer and its signature verifies against the CA's key,
    * it has not expired at the EFFECTIVE time -- the later of the system clock and the floor
      (decision 5.7) -- so that a clock set back cannot make an expired list current again.

Otherwise there is no list, and **no list is not an empty list**: the connection is refused
(`ListUnusable`). A certificate whose serial is in a list that passed all three is refused too.

## Why the list keeps every serial it ever had

A revoked certificate that has since expired would be refused by the validity check anyway -- at
the effective time. But the floor may lag the true time (it moves only as fast as the machine ran),
so with the clock set back a certificate that expired between the floor and now could look valid
again. Keeping its serial in the list costs a few bytes and closes that.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path

LIST_NAME = "revoked.crl"

#: How long a published list is valid, and how old it may get before the root run signs a fresh
#: one. The second must be well inside the first, so a root run that misses a few ticks does not
#: take the services down; and a service loads far more often than either (seconds, not days).
VALID_SECONDS = 7 * 24 * 3600
REFRESH_AFTER_SECONDS = 24 * 3600

# The names of the failures, as a refusal states them. Each is its own check, so a refusal says
# exactly which one it was and a test can assert on it.
UNREADABLE = "revocation-list-unreadable"
SIGNATURE = "revocation-list-signature"
EXPIRED = "revocation-list-expired"


class ListUnusable(Exception):
    """There is no list this side can believe. Carries which failure, and no list body."""

    def __init__(self, check: str, detail: str) -> None:
        self.check = check
        self.detail = detail
        super().__init__("%s: %s" % (check, detail))


@dataclass(frozen=True)
class RevocationList:
    number: int
    this_update: float
    next_update: float
    #: Serial numbers in lower-case hex without leading zeros -- the inventory's spelling.
    serials: frozenset


def _stamp(crl, name: str) -> float:
    """A CRL time as a timestamp, on every cryptography the SDK allows (see issuer._not_after):
    the aware `<name>_utc` where it exists, the naive UTC `<name>` where it does not. The naive one
    is only touched when the aware one is missing, because newer releases deprecate it."""
    aware = getattr(crl, name + "_utc", None)
    if aware is not None:
        return aware.timestamp()
    return getattr(crl, name).replace(tzinfo=_dt.timezone.utc).timestamp()


def serial_text(number: int) -> str:
    return format(int(number), "x")


def build(ca_key, ca_cert, revoked: dict, *, number: int, now: float,
          valid_seconds: float = VALID_SECONDS) -> bytes:
    """A list naming every serial in `revoked` (hex serial -> revocation timestamp), signed.

    Called only by the issuer, under its lock, from the inventory.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization

    at = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    builder = (x509.CertificateRevocationListBuilder()
               .issuer_name(ca_cert.subject)
               .last_update(at)
               .next_update(at + _dt.timedelta(seconds=float(valid_seconds)))
               .add_extension(x509.CRLNumber(int(number)), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                   ca_key.public_key()), critical=False))
    for serial, when in sorted(revoked.items()):
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(int(serial, 16))
            .revocation_date(_dt.datetime.fromtimestamp(float(when), _dt.timezone.utc))
            .build())
    return builder.sign(ca_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)


def number_of(data: bytes | None) -> int | None:
    """The list number in `data`, WITHOUT judging the list. For the root run to tell which of two
    stages it wrote is the newer one, and for nothing else."""
    if not data:
        return None
    from cryptography import x509

    try:
        crl = x509.load_pem_x509_crl(data)
        return int(crl.extensions.get_extension_for_class(x509.CRLNumber).value.crl_number)
    except Exception:                                         # noqa: BLE001
        return None


def read(data: bytes | None, anchor_cert, effective_time: float) -> RevocationList:
    """The list in `data`, if this side can believe it at `effective_time`; `ListUnusable` if not.

    The three conditions are checked in this order and each refuses on its own: a list that does
    not parse, a list the CA did not sign, a list that has expired.
    """
    from cryptography import x509

    if not data:
        raise ListUnusable(UNREADABLE, "there is no revocation list to read")
    try:
        crl = x509.load_pem_x509_crl(data)
        number = int(crl.extensions.get_extension_for_class(x509.CRLNumber).value.crl_number)
        this_update = _stamp(crl, "last_update")
        next_update = _stamp(crl, "next_update")
        serials = frozenset(serial_text(r.serial_number) for r in crl)
    except Exception as exc:                                  # noqa: BLE001
        raise ListUnusable(UNREADABLE, "the revocation list does not parse (%s)"
                           % type(exc).__name__) from exc
    if crl.issuer != anchor_cert.subject or not crl.is_signature_valid(anchor_cert.public_key()):
        raise ListUnusable(SIGNATURE, "the revocation list is not signed by this deployment's CA")
    if effective_time > next_update:
        raise ListUnusable(EXPIRED, "the revocation list expired %d seconds before the effective "
                           "time" % int(effective_time - next_update))
    return RevocationList(number=number, this_update=this_update, next_update=next_update,
                          serials=serials)


def load(path, anchor_cert, effective_time: float) -> RevocationList:
    """`read` on the file at `path`. A file that cannot be opened is a list that cannot be read."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ListUnusable(UNREADABLE, "the revocation list could not be read (%s)"
                           % type(exc).__name__) from exc
    return read(data, anchor_cert, effective_time)


__all__ = ["EXPIRED", "LIST_NAME", "ListUnusable", "REFRESH_AFTER_SECONDS", "RevocationList",
           "SIGNATURE", "UNREADABLE", "VALID_SECONDS", "build", "load", "number_of", "read",
           "serial_text"]
