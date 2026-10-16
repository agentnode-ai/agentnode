"""Independent certificate monitor for the remote MCP origin (profile R12).

Run at least daily (``rmcp-cert-check.timer``) and, ideally, also from a machine other than the
one that renews. It fetches the certificate the origin ACTUALLY serves (TLS + SNI, as a client
sees it) and fails -- non-zero exit, one line per problem -- when any of these does not hold:

  * the served certificate names the domain (SAN) and chains to the expected issuer;
  * it is the certificate installed by the renewal job (SHA-256 fingerprint), so a stale
    process or a wrong vhost serving an old/foreign certificate is caught;
  * it has more than ``--min-days`` (default 21) of validity left;
  * the renewal timer is active and the renewal job's last success is recent.

A failure is an alert: the unit's ``OnFailure=`` hands it to ``rmcp-alert@.service``. The
monitor never changes anything.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import socket
import ssl
import subprocess
import sys
import time
from typing import Callable, List, Optional

from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID

DEFAULT_MIN_DAYS = 21


def fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def served_certificate(host: str, port: int, sni: str, *, cafile: Optional[str] = None,
                       timeout: float = 15.0) -> bytes:
    """The leaf certificate (DER) the endpoint presents for ``sni``. The chain is verified
    against the system store, or ``cafile`` for a private-CA drill; hostname is verified too."""
    ctx = ssl.create_default_context(cafile=cafile)
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=sni) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise ssl.SSLError("no peer certificate")
    return der


def _not_after(cert: x509.Certificate) -> _dt.datetime:
    na = getattr(cert, "not_valid_after_utc", None)
    if na is None:  # cryptography < 42
        na = cert.not_valid_after.replace(tzinfo=_dt.timezone.utc)
    return na


def evaluate(der: bytes, *, domain: str, min_days: int = DEFAULT_MIN_DAYS,
             expected_fingerprint: Optional[str] = None,
             issuer_contains: Optional[str] = None,
             now: Optional[_dt.datetime] = None) -> List[str]:
    """Problems with a served certificate, empty when it is acceptable."""
    problems: List[str] = []
    cert = x509.load_der_x509_certificate(der)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        names = san.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    if domain not in names:
        problems.append("served certificate SAN %s does not name %s" % (names, domain))
    if issuer_contains:
        issuer = ", ".join(a.value for a in cert.issuer.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
                           + cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME))
        if issuer_contains not in issuer:
            problems.append("served certificate issuer %r does not contain %r" % (issuer, issuer_contains))
    fp = fingerprint(der)
    if expected_fingerprint and fp != expected_fingerprint.lower():
        problems.append("served certificate %s is not the installed one %s" % (fp[:16], expected_fingerprint[:16]))
    left = _not_after(cert) - now
    if left < _dt.timedelta(days=min_days):
        problems.append("served certificate expires in %.1f days (< %d)" % (left.total_seconds() / 86400, min_days))
    return problems


def installed_fingerprint(path: str) -> str:
    """Fingerprint of the leaf in the installed full-chain PEM (the first certificate)."""
    with open(path, "rb") as fh:
        leaf = x509.load_pem_x509_certificate(fh.read())
    from cryptography.hazmat.primitives.serialization import Encoding
    return fingerprint(leaf.public_bytes(Encoding.DER))


def timer_problems(timer: str, stamp_path: str, max_age_hours: float,
                   run: Callable[[list], str] = None, now: Optional[float] = None) -> List[str]:
    """The renewal timer must be active, and the job must have succeeded recently (its last
    success is the stamp file the renewal wrapper writes only after a verified install or a
    verified no-change run)."""
    run = run or (lambda argv: subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                              universal_newlines=True).stdout.strip())
    problems: List[str] = []
    state = run(["systemctl", "show", "-p", "ActiveState", "--value", timer])
    if state != "active":
        problems.append("renewal timer %s is %r, not active" % (timer, state or "unknown"))
    try:
        last = float(open(stamp_path).read().strip())
    except (OSError, ValueError):
        problems.append("renewal has no recorded success (%s)" % stamp_path)
    else:
        age_h = ((now if now is not None else time.time()) - last) / 3600.0
        if age_h > max_age_hours:
            problems.append("last successful renewal run was %.1f h ago (> %.0f h)" % (age_h, max_age_hours))
    return problems


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="rmcp-cert-check", description=__doc__.splitlines()[0])
    ap.add_argument("--domain", required=True)
    ap.add_argument("--connect", default=None, help="host to connect to (default: the domain)")
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--min-days", type=int, default=DEFAULT_MIN_DAYS)
    ap.add_argument("--issuer-contains", default=None)
    ap.add_argument("--installed", default=None, help="installed fullchain.pem to compare against")
    ap.add_argument("--cafile", default=None, help="trust anchor for a private-CA drill")
    ap.add_argument("--timer", default=None, help="renewal timer that must be active")
    ap.add_argument("--stamp", default="/var/lib/agentnode-mcp/cert/last-success")
    ap.add_argument("--max-renew-age-hours", type=float, default=36.0)
    a = ap.parse_args(argv)
    problems: List[str] = []
    try:
        der = served_certificate(a.connect or a.domain, a.port, a.domain, cafile=a.cafile)
        expected = installed_fingerprint(a.installed) if a.installed else None
        problems += evaluate(der, domain=a.domain, min_days=a.min_days,
                             expected_fingerprint=expected, issuer_contains=a.issuer_contains)
        print("served sha256=%s" % fingerprint(der))
    except (OSError, ssl.SSLError, ValueError) as exc:
        problems.append("could not fetch a valid served certificate: %s: %s" % (type(exc).__name__, exc))
    if a.timer:
        problems += timer_problems(a.timer, a.stamp, a.max_renew_age_hours)
    for p in problems:
        print("ALERT %s" % p)
    if not problems:
        print("OK %s" % a.domain)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
