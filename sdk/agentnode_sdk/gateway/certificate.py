"""A certificate the gateway makes for itself, and the fingerprint a client pins it by.

The three ways to reach a gateway on another machine that this build already documents all start
by requiring something: a private tunnel, a domain name, or a certificate you already have. For a
managed sandbox that people are meant to be able to use, none of those is a first step -- and the
one thing every client already does before it can send anything is PAIR, out of band, with a code
somebody handed over.

So the code carries the certificate's fingerprint. The client pins it before it sends the code,
and every later connection is to that certificate or to nothing. What authenticates the gateway is
then the same thing that authorised the client: the invitation, which came from a person.

## What this is and is not

It is a pin to a KEY. It says the thing you are talking to now is the thing that issued your
invitation, and nothing else -- not who owns it, not what organisation it belongs to, not that a
certificate authority has ever heard of it. That is exactly what a closed alpha needs and exactly
what a public web certificate is for; the two are not in competition, and an operator who has a
real certificate can still use one.

It is also not a reason to open a port. Reachability is arranged by whoever runs the machine.

What a client does with it is a pin to a KEY, in `gateway/pinning.py`, and the limits of that are
stated there.
"""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
import os
from pathlib import Path

#: How long a certificate the gateway made for itself is good for. Long enough that an alpha does
#: not spend its life re-pairing, short enough that a key which leaked stops mattering.
DAYS = 365

#: What the files are called inside the gateway's own directory. They live there because that
#: directory is already owner-only and is already watched: the gateway stops itself if it ever
#: stops being private. A key somewhere else would be a key nothing was looking after.
CERT_NAME = "tls-cert.pem"
KEY_NAME = "tls-key.pem"


def fingerprint(certificate_pem: bytes) -> str:
    """The digest a client pins. Over the certificate as an X.509 document, not as a file.

    The DER encoding rather than the PEM text, because PEM is a text wrapper -- a different line
    ending would be a different file and the same certificate, and a pin that moved when somebody
    copied a file through the wrong tool would be a pin nobody could rely on.
    """
    from cryptography import x509

    loaded = x509.load_pem_x509_certificate(certificate_pem)
    from cryptography.hazmat.primitives import serialization

    return hashlib.sha256(loaded.public_bytes(serialization.Encoding.DER)).hexdigest()


def fingerprint_of_der(der: bytes) -> str:
    """The same digest, from what a TLS handshake hands back."""
    return hashlib.sha256(der).hexdigest()


def make(directory: str | os.PathLike[str], advertise: str, *, days: int = DAYS,
         now: datetime.datetime | None = None) -> tuple[Path, Path, str]:
    """Write a certificate and its key, and return where they went and what to pin.

    `advertise` is the name or address clients will use. It goes in the certificate so that an
    operator who later puts a real certificate on the same name does not have to change anything
    else; a pinning client does not check it, and says so.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    at = now or datetime.datetime.now(datetime.timezone.utc)
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)

    private = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, advertise or "agentnode-gateway")])
    try:
        where = [x509.IPAddress(ipaddress.ip_address(advertise))]
    except ValueError:
        where = [x509.DNSName(advertise)] if advertise else []

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(at - datetime.timedelta(minutes=5))
        .not_valid_after(at + datetime.timedelta(days=days))
        # A leaf and only a leaf. It cannot sign another certificate, so a client that pins it is
        # pinning one key rather than an authority that could issue more.
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_encipherment=False, key_agreement=True,
            content_commitment=False, data_encipherment=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                       critical=False)
    )
    if where:
        certificate = certificate.add_extension(x509.SubjectAlternativeName(where), critical=False)
    signed = certificate.sign(private, hashes.SHA256())

    cert_path = folder / CERT_NAME
    key_path = folder / KEY_NAME
    cert_path.write_bytes(signed.public_bytes(serialization.Encoding.PEM))
    # The key is written with its permissions from the instant it exists rather than written and
    # then narrowed: there is no moment in which it is readable by anyone else.
    handle = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "wb") as fh:
        fh.write(private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()))
    try:
        os.chmod(cert_path, 0o644)
    except OSError:                                           # pragma: no cover - not ours to fix
        pass
    return cert_path, key_path, fingerprint_of_der(
        signed.public_bytes(serialization.Encoding.DER))


def belongs_together(cert_path: str | os.PathLike[str], key_path: str | os.PathLike[str]) -> bool:
    """Whether that key is the key for that certificate.

    Asked by loading both and comparing the public halves, because being told a pair belongs
    together proves nothing -- which is the same reason `TlsFiles.context` loads rather than
    checks that the files exist.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    try:
        certificate = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
        private = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    except Exception:                                         # noqa: BLE001
        return False
    shape = serialization.PublicFormat.SubjectPublicKeyInfo
    return (certificate.public_key().public_bytes(serialization.Encoding.DER, shape)
            == private.public_key().public_bytes(serialization.Encoding.DER, shape))
