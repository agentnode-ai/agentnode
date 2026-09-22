"""The deployment's own issuer: who may hold which identity, decided in one place.

Decision 3.2 and 3.3, and 5.0 for the file handling. Run by `root` only. It keeps two directories:

    /etc/agentnode/ca      root 0700   the issuing key, the inventory, the log of refusals
    /etc/agentnode/trust   root 0755   the CA certificate, 0644 -- read by both services,
                                       written by neither

## The inventory decides what a certificate says

A request contributes its PUBLIC KEY and nothing else. Every name, usage, constraint and validity
period in it is discarded unread; the certificate is built from the inventory entry the request
was authorised against. So nothing a service writes into a request can change what it is issued.

## Who may claim an entry

An entry is created by root, and at creation a single-use enrollment secret is written into a file
only the intended service's account can read. Claiming the entry means presenting that secret,
together with a request whose self-signature proves the holder has the private key. The secret is
kept here only as a digest; once used it is consumed.

Renewal does not use a secret. It is authorised by a signature over the new request made with the
CURRENT key of that entry, and the entry is looked up from the current certificate -- never from
anything the requester names.

## The transaction

Every call, before it decides anything:

1. takes the exclusive lock,
2. removes any leftover temporary inventory UNREAD -- it was never committed,
3. fsyncs the issuer directory, unconditionally, and acts only if that succeeds.

A commit is the inventory rewritten through `files.durable_replace`, and it counts once the
directory fsync after the rename has returned. The certificate is IN the inventory, so delivery
comes afterwards and can be repeated: a crash between commit and delivery costs nothing, because
the next call hands over the same certificate rather than making a new one. If the directory
fsync fails after a rename, the outcome is indeterminate: nothing is delivered, nothing is
reported as issued, and the next call reconciles from whatever is then durable.

The invariant is the decision's, stated in its two parts:

* per issuance transaction, at most one certificate committed and at most one delivered, and no
  uncommitted certificate surviving the next lock;
* per entry, every committed certificate recorded with its status -- `current`, at most one
  `overlapping`, `superseded` -- and no valid certificate of that entry that is not listed.

Revocation is decision stage 5 and is not implemented here.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path

from agentnode_sdk.pki import files as _files
from agentnode_sdk.pki import identity as _identity

DEFAULT_CA_DIR = "/etc/agentnode/ca"
DEFAULT_TRUST_DIR = "/etc/agentnode/trust"

CA_KEY = "ca.key"
CA_CERT = "ca.pem"
INVENTORY = "inventory.json"
#: The reserved name of the temporary inventory. Anything found under it was never committed.
INVENTORY_TMP = ".inventory.tmp"
REFUSALS = "refused.log"
LOCK = ".lock"

DAYS = 90
SECRET_HOURS = 24
CA_YEARS = 10

CURRENT = "current"
OVERLAPPING = "overlapping"
SUPERSEDED = "superseded"


class IssuanceRefused(Exception):
    """A request that was not granted, with the reason an operator can act on."""


class Indeterminate(Exception):
    """The issuer could not establish that what is on disk is durable, so it did nothing.

    Raised when the directory fsync at the start of a call fails, or when the one after a commit
    fails. Not a refusal of the requester: the same request, repeated once the disk behaves, is
    reconciled against whatever state survived.
    """


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> float:                                          # a seam for tests
    return time.time()


def _not_after(certificate) -> float:
    """The end of a certificate's validity, as a timestamp.

    `not_valid_after_utc` exists from cryptography 42; this package allows 41, where only the naive
    `not_valid_after` (UTC by definition) exists. Read whichever is there.
    """
    aware = getattr(certificate, "not_valid_after_utc", None)
    if aware is not None:
        return aware.timestamp()
    return certificate.not_valid_after.replace(tzinfo=_dt.timezone.utc).timestamp()


def _public_key_fingerprint(public_key) -> str:
    from cryptography.hazmat.primitives import serialization

    der = public_key.public_bytes(serialization.Encoding.DER,
                                  serialization.PublicFormat.SubjectPublicKeyInfo)
    return _sha256(der)


class Issuer:
    def __init__(self, ca_dir=DEFAULT_CA_DIR, trust_dir=DEFAULT_TRUST_DIR, files=None) -> None:
        self.ca_dir = Path(ca_dir)
        self.trust_dir = Path(trust_dir)
        self.files = files or _files.Files()

    # ------------------------------------------------------------------ the start of every call

    def _lock(self):
        from agentnode_sdk.gateway.filelock import ProcessLock

        return ProcessLock(self.ca_dir / LOCK, timeout=30.0)

    def _begin(self) -> None:
        """Steps 2 and 3 of every call. Under the lock."""
        leftover = self.ca_dir / INVENTORY_TMP
        if self.files.exists(leftover):
            # Unread. It was never committed, and a certificate inside it was never delivered.
            self.files.remove(leftover)
        try:
            self.files.fsync_dir(self.ca_dir)
        except OSError as exc:
            raise Indeterminate(
                "the issuer directory could not be made durable, so nothing found in it can be "
                "acted on yet: " + str(exc)) from exc

    def _inventory(self) -> dict:
        return json.loads(self.files.read(self.ca_dir / INVENTORY).decode("utf-8"))

    def _commit(self, inventory: dict, label: str) -> None:
        data = (json.dumps(inventory, indent=1, sort_keys=True) + "\n").encode("utf-8")
        try:
            _files.durable_replace(self.files, self.ca_dir / INVENTORY, data,
                                   temporary=INVENTORY_TMP, label=label)
        except OSError as exc:
            # Before the rename: nothing happened. After it: the new inventory is visible and
            # may or may not survive a crash. Either way nothing may be delivered on the
            # strength of it, and the next call decides from what is durable then.
            raise Indeterminate("the inventory could not be committed durably: " + str(exc)) \
                from exc

    def _refused(self, reason: str, entry: str = "", public_key=None) -> IssuanceRefused:
        """Write the refusal down -- reason, entry, a fingerprint of the key -- and return it.

        Never the secret, never a key, never a request body.
        """
        line = {"at": round(_now(), 3), "reason": reason, "entry": entry,
                "public_key_sha256": _public_key_fingerprint(public_key) if public_key else ""}
        try:
            with open(self.ca_dir / REFUSALS, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:                                       # pragma: no cover - reported below
            pass
        return IssuanceRefused(reason)

    # ------------------------------------------------------------------ setting up

    def initialise(self, deployment: str) -> str:
        """Create the issuing key, the CA certificate, the inventory and the trust anchor."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        if not _identity.is_component(deployment):
            raise _identity.NotAnIdentity("deployment %r is not an identity component" % deployment)
        self.ca_dir.mkdir(parents=True, exist_ok=True)
        self.trust_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.ca_dir, 0o700)
            os.chmod(self.trust_dir, 0o755)
        except OSError:                                       # pragma: no cover
            pass
        with self._lock():
            self._begin()
            if self.files.exists(self.ca_dir / INVENTORY):
                raise IssuanceRefused("this issuer is already initialised; a second CA over the "
                                      "first would be a different deployment")
            key = ec.generate_private_key(ec.SECP256R1())
            name = x509.Name([x509.NameAttribute(
                NameOID.COMMON_NAME, "AgentNode issuer " + deployment)])
            now = _dt.datetime.fromtimestamp(_now(), _dt.timezone.utc)
            ca = (x509.CertificateBuilder()
                  .subject_name(name).issuer_name(name).public_key(key.public_key())
                  .serial_number(x509.random_serial_number())
                  .not_valid_before(now - _dt.timedelta(minutes=5))
                  .not_valid_after(now + _dt.timedelta(days=365 * CA_YEARS))
                  .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                  .add_extension(x509.KeyUsage(
                      digital_signature=False, content_commitment=False, key_encipherment=False,
                      data_encipherment=False, key_agreement=False, key_cert_sign=True,
                      crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
                  .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                                 critical=False)
                  .sign(key, hashes.SHA256()))
            key_pem = key.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
            ca_pem = ca.public_bytes(serialization.Encoding.PEM)
            _files.durable_replace(self.files, self.ca_dir / CA_KEY, key_pem, mode=0o600,
                                   label="init-key")
            _files.durable_replace(self.files, self.trust_dir / CA_CERT, ca_pem, mode=0o644,
                                   label="init-anchor")
            self._commit({"deployment": deployment, "entries": {}}, "init-inventory")
        return deployment

    def add(self, role: str, instance: str, *, secret_at, owner_uid=None, owner_gid=None,
            deliver_to) -> str:
        """Create an entry, or give an unclaimed one a fresh secret. Returns the entry's name.

        The secret is written to `secret_at`, owned by the service's account and readable only
        by it. Only its digest stays here.
        """
        with self._lock():
            self._begin()
            inventory = self._inventory()
            ident = _identity.identity_of(inventory["deployment"], role, instance)
            name = role + "/" + instance
            entry = inventory["entries"].get(name)
            if entry is not None and entry.get("certificates"):
                raise IssuanceRefused(
                    "entry %s already has a certificate; a new key for it is a renewal, or a "
                    "new entry" % name)
            secret = secrets.token_hex(32)
            inventory["entries"][name] = {
                "role": ident.role, "instance": ident.instance, "uri": ident.uri(),
                "usage": _identity.USAGE_OF[ident.role], "days": DAYS,
                "secret_sha256": _sha256(secret.encode("ascii")),
                "secret_expires": round(_now() + SECRET_HOURS * 3600, 3),
                "consumed": [], "certificates": [],
                "deliver_to": str(deliver_to),
                "owner_uid": owner_uid, "owner_gid": owner_gid,
            }
            self._commit(inventory, "add")
            secret_path = Path(secret_at)
            if self.files.exists(secret_path):
                self.files.remove(secret_path)
            _files.durable_replace(self.files, secret_path, secret.encode("ascii"), mode=0o400,
                                   label="add-secret")
            if owner_uid is not None and hasattr(os, "chown"):
                os.chown(secret_path, int(owner_uid), int(owner_gid if owner_gid is not None
                                                          else -1))
        return name

    # ------------------------------------------------------------------ issuing

    def _build(self, inventory: dict, entry: dict, public_key, ca_key, ca_cert):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.x509.oid import NameOID, ObjectIdentifier

        now = _dt.datetime.fromtimestamp(_now(), _dt.timezone.utc)
        return (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(
                    NameOID.COMMON_NAME, entry["role"] + " " + entry["instance"])]))
                .issuer_name(ca_cert.subject)
                .public_key(public_key)
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - _dt.timedelta(minutes=5))
                .not_valid_after(now + _dt.timedelta(days=int(entry["days"])))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=False,
                    crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ObjectIdentifier(entry["usage"])]),
                               critical=False)
                .add_extension(x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(entry["uri"])]), critical=False)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key),
                               critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))

    def _ca(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        ca_key = serialization.load_pem_private_key(self.files.read(self.ca_dir / CA_KEY), None)
        ca_cert = x509.load_pem_x509_certificate(self.files.read(self.trust_dir / CA_CERT))
        return ca_key, ca_cert

    @staticmethod
    def _request_key(csr_pem: bytes):
        """The public key in a request, and only if the request's own signature holds."""
        from cryptography import x509

        request = x509.load_pem_x509_csr(csr_pem)
        if not request.is_signature_valid:
            return None
        return request.public_key()

    def _deliver(self, entry: dict, pem: bytes, suffix: str = "") -> None:
        target = Path(entry["deliver_to"] + suffix)
        # A delivery that crashed halfway leaves its temporary file behind. It holds nothing the
        # inventory does not, so it goes; otherwise the repeat -- which is the whole point of
        # keeping the certificate in the inventory -- would trip over it forever.
        leftover = target.parent / ("." + target.name + ".tmp")
        if self.files.exists(leftover):
            self.files.remove(leftover)
        _files.durable_replace(self.files, target, pem, mode=0o644, label="deliver")

    def enroll(self, csr_pem: bytes, secret: str) -> bytes:
        """First issuance, against the entry the secret belongs to. Returns the certificate."""
        from cryptography.hazmat.primitives import serialization

        with self._lock():
            self._begin()
            inventory = self._inventory()
            presented = _sha256(str(secret).strip().encode("ascii", "replace"))
            public_key = self._request_key(csr_pem)
            if public_key is None:
                raise self._refused("the request's own signature does not verify, so it does not "
                                    "show that its sender holds the key")
            fingerprint = _public_key_fingerprint(public_key)

            # A secret already used for this very key: the commit happened and delivery did not.
            # Hand over the certificate that was committed -- the same one, not a new one.
            for name, entry in inventory["entries"].items():
                for used in entry.get("consumed", []):
                    if used.get("secret_sha256") == presented:
                        if used.get("public_key_sha256") != fingerprint:
                            raise self._refused("this enrollment secret has already been used",
                                                name, public_key)
                        pem = next((c["pem"].encode("ascii") for c in entry["certificates"]
                                    if c["transaction"] == used["transaction"]), None)
                        if pem is None:                       # pragma: no cover - inventory damage
                            raise self._refused("the inventory lost the certificate it issued",
                                                name, public_key)
                        self._deliver(entry, pem)
                        return pem

            matches = [(n, e) for n, e in inventory["entries"].items()
                       if e.get("secret_sha256") and e["secret_sha256"] == presented]
            if not matches:
                raise self._refused("no unclaimed entry has this enrollment secret", "",
                                    public_key)
            name, entry = matches[0]
            if float(entry.get("secret_expires") or 0) < _now():
                raise self._refused("this enrollment secret has expired", name, public_key)

            ca_key, ca_cert = self._ca()
            certificate = self._build(inventory, entry, public_key, ca_key, ca_cert)
            pem = certificate.public_bytes(serialization.Encoding.PEM)
            transaction = uuid.uuid4().hex
            entry["consumed"].append({"secret_sha256": presented, "transaction": transaction,
                                      "public_key_sha256": fingerprint})
            entry["secret_sha256"] = ""
            entry["certificates"].append({
                "transaction": transaction, "serial": format(certificate.serial_number, "x"),
                "public_key_sha256": fingerprint, "status": CURRENT,
                "not_after": _not_after(certificate),
                "pem": pem.decode("ascii")})
            self._commit(inventory, "issue")
            self._deliver(entry, pem)
            return pem

    def renew(self, csr_pem: bytes, current_pem: bytes, signature: bytes) -> bytes:
        """A new key for the same identity, authorised by the key it replaces."""
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        with self._lock():
            self._begin()
            inventory = self._inventory()
            public_key = self._request_key(csr_pem)
            if public_key is None:
                raise self._refused("the renewal request's own signature does not verify")
            ca_key, ca_cert = self._ca()
            try:
                current = x509.load_pem_x509_certificate(current_pem)
                current.verify_directly_issued_by(ca_cert)
            except Exception as exc:                          # noqa: BLE001
                raise self._refused("the certificate presented as current was not issued "
                                    "here: " + type(exc).__name__, "", public_key) from exc
            serial = format(current.serial_number, "x")
            # Looked up from the current certificate. The request names nothing.
            found = [(n, e, c) for n, e in inventory["entries"].items()
                     for c in e.get("certificates", []) if c["serial"] == serial]
            if not found:
                raise self._refused("the presented certificate is not in the inventory", "",
                                    public_key)
            name, entry, record = found[0]
            if record["status"] not in (CURRENT, OVERLAPPING):
                raise self._refused("the presented certificate has been superseded", name,
                                    public_key)
            if float(record["not_after"]) < _now():
                raise self._refused("the presented certificate has expired; a lost or expired "
                                    "key is re-enrolled by root, not renewed", name, public_key)
            try:
                current.public_key().verify(signature, csr_pem, ec.ECDSA(hashes.SHA256()))
            except (InvalidSignature, Exception) as exc:      # noqa: BLE001
                raise self._refused("the renewal is not signed by the current key", name,
                                    public_key) from exc

            certificate = self._build(inventory, entry, public_key, ca_key, ca_cert)
            pem = certificate.public_bytes(serialization.Encoding.PEM)
            for other in entry["certificates"]:
                if other["status"] == OVERLAPPING:
                    other["status"] = SUPERSEDED
                elif other["status"] == CURRENT:
                    other["status"] = OVERLAPPING
            entry["certificates"].append({
                "transaction": uuid.uuid4().hex,
                "serial": format(certificate.serial_number, "x"),
                "public_key_sha256": _public_key_fingerprint(public_key), "status": CURRENT,
                "not_after": _not_after(certificate),
                "pem": pem.decode("ascii")})
            self._commit(inventory, "renew")
            self._deliver(entry, pem, suffix=".next")
            return pem

    # ------------------------------------------------------------------ looking

    def inventory(self) -> dict:
        """The inventory as it is on disk, WITHOUT the certificate bodies or secret digests."""
        with self._lock():
            self._begin()
            raw = self._inventory()
        shown = {"deployment": raw["deployment"], "entries": {}}
        for name, entry in raw["entries"].items():
            shown["entries"][name] = {
                "uri": entry["uri"], "usage": entry["usage"],
                "claimed": not entry.get("secret_sha256"),
                "certificates": [{k: c[k] for k in ("serial", "status", "not_after",
                                                    "public_key_sha256")}
                                 for c in entry["certificates"]]}
        return shown


# ---------------------------------------------------------------------- the service's side

def make_request(tls_dir, secret: str = "", *, renew: bool = False) -> Path:
    """Run AS THE SERVICE: make a key here, and a request the issuer can act on.

    The private key is generated in `tls_dir` and never leaves it. First enrollment puts the
    secret beside the request; a renewal instead signs the request with the current key.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    folder = Path(tls_dir)
    key_name = "key.next.pem" if renew else "key.pem"
    key_path = folder / key_name
    if key_path.exists():
        key = serialization.load_pem_private_key(key_path.read_bytes(), None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "request")]))
           .sign(key, hashes.SHA256()))
    csr_pem = csr.public_bytes(serialization.Encoding.PEM)
    body: dict = {"csr": csr_pem.decode("ascii")}
    if renew:
        current_key = serialization.load_pem_private_key((folder / "key.pem").read_bytes(), None)
        body["current"] = (folder / "cert.pem").read_text(encoding="ascii")
        body["signature"] = current_key.sign(csr_pem, ec.ECDSA(hashes.SHA256())).hex()
    else:
        body["secret"] = str(secret).strip()
    out = folder / ("renewal.json" if renew else "request.json")
    fd = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(body, handle)
    return out


__all__ = ["CURRENT", "DEFAULT_CA_DIR", "DEFAULT_TRUST_DIR", "INVENTORY", "INVENTORY_TMP",
           "Indeterminate", "IssuanceRefused", "Issuer", "OVERLAPPING", "SUPERSEDED",
           "make_request"]
