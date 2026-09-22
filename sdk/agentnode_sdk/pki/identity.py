"""Who a certificate says its holder is, and whether that is who this side expected.

The transport decision (`mtls-transport-decision.md`, 3.1 and 3.4) names one identity per service
and six checks each side makes of the other. One of them is OpenSSL's and is done in the handshake
-- the chain to exactly this deployment's CA. The other five are here, after the handshake and
before a single application byte:

    2  both the certificate and the CA certificate are inside their validity at the EFFECTIVE
       time -- the later of the system clock and the root-written floor (decision 5.7)
    3  the extended key usage fits the direction
    4  the URI SAN is in the grammar, the deployment is ours, the role is the expected one
    5  the instance is one this side was configured to accept
    6  it is not revoked, according to a revocation list this side can believe (decision 5.3)

Check 2 used to be OpenSSL's too, judged by the system clock. It moved here in stage 5, and
OpenSSL's own time check is switched off (`worker/tls.py`): a check made in two places cannot be
shown to work, and OpenSSL can only judge by the clock that a set-back clock has already fooled.

`standing` is checks 2 and 6 again, for a connection that is already open: the identity on a live
connection cannot change, but whether it is still valid and still unrevoked can.

## Each check in exactly one place

Deliberately. A check made twice cannot be shown to work: remove one copy and the other still
refuses, so a counter-check that takes it away stays green and proves nothing. That is why the
extended key usage is checked HERE and required to be present, even though OpenSSL also looks at
it -- OpenSSL only rejects a certificate whose usage extension is present and wrong, and accepts
one that has none at all. A certificate with no usage extension is therefore something only this
module refuses, which is what makes this check observable on its own.

## The grammar, and why there is no normalisation

    agentnode://<deployment>/<role>/<instance>

Three components, each lower-case ASCII letters, digits, `-` and `_`, one to 64 long; the role is
`gateway` or `worker`; the whole thing at most 255. There is no percent-decoding, no case folding
and no second spelling of anything: what is not written exactly like that is refused, not
repaired. Two parsers that normalise differently would let one encoding name two identities, and
the cheapest way to have no such disagreement is to have nothing to normalise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SCHEME = "agentnode://"

GATEWAY = "gateway"
WORKER = "worker"
ROLES = (GATEWAY, WORKER)

#: One component: lower-case ASCII, digits, `-`, `_`. Anchored at both ends with \Z, not $, which
#: would also accept a trailing newline.
_COMPONENT = re.compile(r"[a-z0-9_-]{1,64}\Z")

MAX_LENGTH = 255

#: The dotted OIDs, spelled out rather than imported, so that this module can say what it checks
#: without the reader opening a library.
SERVER_AUTH = "1.3.6.1.5.5.7.3.1"
CLIENT_AUTH = "1.3.6.1.5.5.7.3.2"

#: Which usage a holder of each role carries. The worker is what the gateway connects TO, so it is
#: the TLS server; the gateway is the client.
USAGE_OF = {WORKER: SERVER_AUTH, GATEWAY: CLIENT_AUTH}

# The names of the checks, as a refusal states them. Stable strings, because an operator reads
# them and a test asserts on them.
CHECK_USAGE = "extended-key-usage"
CHECK_SAN = "subject-alternative-name"
CHECK_DEPLOYMENT = "deployment"
CHECK_ROLE = "role"
CHECK_INSTANCE = "instance"
CHECK_NO_CERTIFICATE = "no-certificate"
CHECK_VALIDITY = "validity"
CHECK_REVOKED = "revoked"


class NotAnIdentity(ValueError):
    """A string that is not an identity in the grammar. Raised, never repaired."""


class PeerRefused(Exception):
    """The other end is not who this side will talk to.

    Carries WHICH check failed and the identity the peer presented, and nothing else -- no key
    material and no certificate body. The presented identity is at most a SAN string, which is a
    name and not a secret.
    """

    def __init__(self, check: str, presented: str = "", detail: str = "") -> None:
        self.check = check
        self.presented = presented
        self.detail = detail
        text = "the peer was refused at the %s check" % check
        if presented:
            text += " (it presented %s)" % presented
        if detail:
            text += ": " + detail
        super().__init__(text)


@dataclass(frozen=True)
class Identity:
    deployment: str
    role: str
    instance: str

    def uri(self) -> str:
        return SCHEME + self.deployment + "/" + self.role + "/" + self.instance


def is_component(value: str) -> bool:
    return isinstance(value, str) and bool(_COMPONENT.match(value))


def identity_of(deployment: str, role: str, instance: str) -> Identity:
    """An identity from its parts, or `NotAnIdentity`. Used when an entry is created, so that a
    label that does not fit the grammar is refused there and never rewritten."""
    for name, value in (("deployment", deployment), ("instance", instance)):
        if not is_component(value):
            raise NotAnIdentity(
                "%s %r is not an identity component: lower-case ASCII letters, digits, '-' and "
                "'_', one to 64 characters, and nothing is rewritten to fit" % (name, value))
    if role not in ROLES:
        raise NotAnIdentity("role %r is neither %s nor %s" % (role, GATEWAY, WORKER))
    identity = Identity(deployment, role, instance)
    if len(identity.uri()) > MAX_LENGTH:                      # pragma: no cover - 3*64 + 30 < 255
        raise NotAnIdentity("an identity is at most %d characters" % MAX_LENGTH)
    return identity


def parse(uri: str) -> Identity:
    """The identity a SAN names, or `NotAnIdentity`. Byte-exact; nothing decoded or folded."""
    if not isinstance(uri, str) or len(uri) > MAX_LENGTH:
        raise NotAnIdentity("not an identity: too long or not text")
    if "%" in uri:
        # Refused before anything else looks at it. Not decoded and then judged: a decoder is a
        # second spelling, and a second spelling is what this grammar exists not to have.
        raise NotAnIdentity("an identity contains no percent sign, encoded or otherwise")
    if not uri.startswith(SCHEME):
        raise NotAnIdentity("an identity begins with " + SCHEME)
    parts = uri[len(SCHEME):].split("/")
    if len(parts) != 3:
        raise NotAnIdentity("an identity has exactly three components, this has %d" % len(parts))
    deployment, role, instance = parts
    return identity_of(deployment, role, instance)


def _usages(certificate) -> tuple[str, ...] | None:
    from cryptography import x509

    try:
        extension = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound:
        return None
    return tuple(oid.dotted_string for oid in extension.value)


def _uri_sans(certificate) -> list[str]:
    from cryptography import x509

    try:
        extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return []
    return list(extension.value.get_values_for_type(x509.UniformResourceIdentifier))


def _window(certificate) -> tuple[float, float]:
    """(notBefore, notAfter) as timestamps, on every cryptography the SDK allows."""
    import datetime as _dt

    def stamp(name):
        aware = getattr(certificate, name + "_utc", None)
        if aware is not None:
            return aware.timestamp()
        return getattr(certificate, name).replace(tzinfo=_dt.timezone.utc).timestamp()
    return stamp("not_valid_before"), stamp("not_valid_after")


def _valid_at(certificate, anchor, effective_time: float, presented: str) -> None:
    """Check 2. The certificate and the CA certificate, each inside its window at the effective
    time. The ONLY place a validity window is judged: OpenSSL's time check is off."""
    for what, subject in (("its certificate", certificate), ("the CA certificate", anchor)):
        not_before, not_after = _window(subject)
        if effective_time < not_before:
            raise PeerRefused(CHECK_VALIDITY, presented, "%s is not yet valid at the effective "
                              "time" % what)
        if effective_time > not_after:
            raise PeerRefused(CHECK_VALIDITY, presented, "%s expired %d seconds before the "
                              "effective time" % (what, int(effective_time - not_after)))


def _not_revoked(certificate, trust, effective_time: float, presented: str) -> None:
    """Check 6. A list this side can believe at the effective time, and the serial not in it.
    No believable list is a refusal, never 'nothing is revoked'."""
    serials = trust.revoked_serials(effective_time, presented)
    if format(certificate.serial_number, "x") in serials:
        raise PeerRefused(CHECK_REVOKED, presented, "its certificate is revoked")


def standing(der: bytes, trust, presented: str = "") -> None:
    """Checks 2 and 6 for a connection that is already open, against the current trust state.
    Raises `PeerRefused` when the connection must be cut."""
    from cryptography import x509

    certificate = x509.load_der_x509_certificate(der)
    effective_time = trust.effective_time(presented)
    _valid_at(certificate, trust.anchor(), effective_time, presented)
    _not_revoked(certificate, trust, effective_time, presented)


def check_peer(der: bytes | None, *, deployment: str, expected_role: str,
               accept_instances, trust) -> Identity:
    """Checks 2 to 6 of decision 3.4, in that order, on a certificate the handshake accepted.

    `der` is what the TLS layer handed over as the peer's certificate. It has already chained to
    this deployment's CA -- OpenSSL established that and would not have completed the handshake
    otherwise. Everything else is judged here: whether it is valid at the effective time, whether
    it is the RIGHT certificate from that CA, and whether it has been revoked. `trust` is where
    the effective time and the revocation list come from (`pki/trust.py`); there is no default,
    because a call without one would be a call that skips checks 2 and 6.
    """
    if not der:
        raise PeerRefused(CHECK_NO_CERTIFICATE, detail="the peer presented no certificate")
    from cryptography import x509

    certificate = x509.load_der_x509_certificate(der)
    sans = _uri_sans(certificate)
    presented = sans[0] if len(sans) == 1 else ""

    # 2. Valid at the effective time. The floor is read here; no usable floor, no judgement.
    effective_time = trust.effective_time(presented)
    _valid_at(certificate, trust.anchor(), effective_time, presented)

    # 3. The usage. Present, and exactly the one this direction needs. A certificate carrying
    #    both would be one that can stand on either end, which is the thing the split prevents.
    wanted = USAGE_OF.get(expected_role)
    usages = _usages(certificate)
    if usages is None:
        raise PeerRefused(CHECK_USAGE, presented, "it carries no extended key usage")
    if tuple(usages) != (wanted,):
        raise PeerRefused(CHECK_USAGE, presented,
                          "it is for %s, and this side needs exactly %s"
                          % (",".join(usages) or "nothing", wanted))

    # 4. The name. Exactly one URI SAN, in the grammar, naming our deployment and the role this
    #    side expects at the other end.
    if len(sans) != 1:
        raise PeerRefused(CHECK_SAN, presented,
                          "it carries %d URI names and an identity is exactly one" % len(sans))
    try:
        identity = parse(sans[0])
    except NotAnIdentity as exc:
        raise PeerRefused(CHECK_SAN, presented, str(exc)) from exc
    if identity.deployment != deployment:
        raise PeerRefused(CHECK_DEPLOYMENT, presented,
                          "it belongs to another deployment")
    if identity.role != expected_role:
        raise PeerRefused(CHECK_ROLE, presented,
                          "this side expects a %s at the other end" % expected_role)

    # 5. Which one. Byte comparison against what this side was configured to accept.
    if identity.instance not in set(accept_instances or ()):
        raise PeerRefused(CHECK_INSTANCE, presented,
                          "this side was not configured to accept that %s" % expected_role)

    # 6. Not revoked.
    _not_revoked(certificate, trust, effective_time, presented)
    return identity


__all__ = [
    "CHECK_DEPLOYMENT", "CHECK_INSTANCE", "CHECK_NO_CERTIFICATE", "CHECK_REVOKED", "CHECK_ROLE",
    "CHECK_SAN", "CHECK_USAGE", "CHECK_VALIDITY", "CLIENT_AUTH", "GATEWAY", "Identity",
    "MAX_LENGTH", "NotAnIdentity", "PeerRefused", "ROLES", "SCHEME", "SERVER_AUTH", "USAGE_OF", "WORKER", "check_peer",
    "identity_of", "is_component", "parse", "standing",
]
