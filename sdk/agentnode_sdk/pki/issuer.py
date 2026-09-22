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
  `overlapping`, `superseded`, `revoked` -- and no valid certificate of that entry that is not
  listed.

## Stage 5: revocation, the bounded overlap, recovery, and the root run

Revocation is recorded HERE, in the inventory, with the same commit as an issuance; the signed
list the services read (`revocation.py`) is derived from it and published in two durable stages
(`files.durable_publish`). A revocation is reported effective only when that publication has been
promoted and its directory fsynced -- never at the rename.

A renewal makes the previous certificate `overlapping` until the earlier of its own expiry and
`OVERLAP_SECONDS` from the renewal (the 30 days between the decision's day 60 and day 90), and
whatever was `overlapping` before becomes `superseded` AND revoked. The root run revokes an
overlapping certificate whose overlap has ended. So at most two unrevoked certificates of an entry
are ever usable, and the second only for a bounded time -- enforced through the list the peers
check, not merely noted here.

Recovery from a compromised key (`recover_entry`, decision 5.6) locks the entry against renewal,
revokes every serial it ever had, publishes the list and hands out a fresh enrollment secret, in
one call under the same lock and the same commit.

`tick` is the root run (decision 5.0, 5.3, 5.7): resolve what a crash left of the list and the
floors, end overlaps, reconcile the list with the inventory, and only then -- and only if no
revocation is waiting to be published -- write the floors.
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
from agentnode_sdk.pki import floor as _floor
from agentnode_sdk.pki import identity as _identity
from agentnode_sdk.pki import revocation as _revocation

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

#: How long the previous certificate stays usable after a renewal, at most: the decision's day 60
#: to day 90. It ends earlier if the certificate itself expires earlier.
OVERLAP_SECONDS = 30 * 24 * 3600

CURRENT = "current"
OVERLAPPING = "overlapping"
SUPERSEDED = "superseded"
REVOKED = "revoked"

#: Why a serial is in the list. Written into the inventory next to the revocation.
BY_ROOT = "revoked by root"
BY_RENEWAL = "superseded by a renewal while it was still overlapping"
OVERLAP_ENDED = "its overlap ended"
COMPROMISE = "the entry was recovered from a compromised key"


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
            inventory = {"deployment": deployment, "entries": {}, "list_number": 0}
            self._commit(inventory, "init-inventory")
            # An empty list, signed, so that a service has one to believe from the start. No
            # list would not be "nothing revoked"; it would be every connection refused.
            if not self._publish_list(inventory, key, ca):
                raise Indeterminate("the issuer exists, and its first revocation list could not "
                                    "be published durably; `agentnode pki tick` publishes it")
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
            # A secret root handed out after a recovery is what lifts the lock on renewal: this
            # key is fresh, and every certificate before it is already revoked.
            entry["renewal_locked"] = False
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
            if record.get("revoked_at") is not None:
                raise self._refused("the presented certificate is revoked; a revoked key is "
                                    "re-enrolled by root, not renewed", name, public_key)
            if entry.get("renewal_locked"):
                raise self._refused("this entry is locked against renewal until root re-enrolls "
                                    "it with a fresh key", name, public_key)
            # Only the CURRENT key renews. The overlapping one is on its way out; letting it
            # renew would let whoever still holds an old key keep an identity going.
            if record["status"] != CURRENT:
                raise self._refused("the presented certificate is not the current one of its "
                                    "entry", name, public_key)
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
            now = _now()
            superseded_now = False
            for other in entry["certificates"]:
                if other["status"] == OVERLAPPING:
                    # Still inside its validity, possibly: so it goes into the list, or the
                    # bound on the overlap would be a note and not a limit.
                    other["status"] = SUPERSEDED
                    if other.get("revoked_at") is None:
                        other["revoked_at"] = round(now, 3)
                        other["revocation_reason"] = BY_RENEWAL
                        superseded_now = True
                elif other["status"] == CURRENT:
                    other["status"] = OVERLAPPING
                    other["overlap_until"] = round(min(float(other["not_after"]),
                                                       now + OVERLAP_SECONDS), 3)
            entry["certificates"].append({
                "transaction": uuid.uuid4().hex,
                "serial": format(certificate.serial_number, "x"),
                "public_key_sha256": _public_key_fingerprint(public_key), "status": CURRENT,
                "not_after": _not_after(certificate),
                "pem": pem.decode("ascii")})
            self._commit(inventory, "renew")
            self._deliver(entry, pem, suffix=".next")
            if superseded_now:
                # Not a reason to withhold the renewal -- it is committed and delivered. If this
                # publication fails, the revocation is pending, and the root run neither reports
                # it effective nor promotes a floor until it is published (decision 5.6).
                self._publish_list(inventory, ca_key, ca_cert)
            return pem

    # ------------------------------------------------------------------ revoking (stage 5)

    def _revoked(self, inventory: dict) -> dict:
        """Every revoked serial in the inventory, with when. The list is made from this."""
        return {c["serial"]: float(c["revoked_at"])
                for entry in inventory["entries"].values()
                for c in entry.get("certificates", [])
                if c.get("revoked_at") is not None}

    def _list_path(self) -> Path:
        return self.trust_dir / _revocation.LIST_NAME

    def _published(self, ca_cert):
        """The list the services read now, as root reads it: (RevocationList or None, reason)."""
        path = self._list_path()
        data = self.files.read(path) if self.files.exists(path) else None
        try:
            return _revocation.read(data, ca_cert, _now()), ""
        except _revocation.ListUnusable as unusable:
            return None, str(unusable)

    def _settle_list(self) -> str:
        """Resolve what a crash left of a list publication (`files.settle_stage`)."""
        def newer(staged, current):
            staged_number = _revocation.number_of(staged)
            if staged_number is None:
                return False
            current_number = _revocation.number_of(current)
            return current_number is None or staged_number > current_number
        return _files.settle_stage(self.files, self._list_path(), newer, label="settle-list")

    def _publish_list(self, inventory: dict, ca_key, ca_cert) -> bool:
        """Derive the list from the inventory, number it, sign it, publish it in two stages.
        Under the lock. True only when the promotion is durable -- the one moment a revocation in
        it may be called effective. Every failure is False, with the inventory already holding
        what was revoked, so that the root run tries again and holds back the floor meanwhile."""
        try:
            self._settle_list()
        except OSError:
            return False
        inventory["list_number"] = int(inventory.get("list_number") or 0) + 1
        try:
            self._commit(inventory, "list-number")
        except Indeterminate:
            return False
        data = _revocation.build(ca_key, ca_cert, self._revoked(inventory),
                                 number=inventory["list_number"], now=_now())
        try:
            _files.durable_publish(self.files, self._list_path(), data, mode=0o644, label="list")
        except OSError:
            return False
        return True

    def revoke(self, serial: str, reason: str = BY_ROOT) -> dict:
        """Revoke one certificate, by serial, and publish the list before returning.

        Returns `{"serial", "entry", "effective"}`. `effective` is True only when the published
        list carrying this serial has been promoted and made durable; otherwise the revocation
        is recorded in the inventory and NOT in effect, and the caller is told so.
        """
        serial = str(serial).strip().lower()
        with self._lock():
            self._begin()
            inventory = self._inventory()
            found = [(n, c) for n, e in inventory["entries"].items()
                     for c in e.get("certificates", []) if c["serial"] == serial]
            if not found:
                raise IssuanceRefused("no certificate with serial %s was issued here" % serial)
            name, record = found[0]
            if record.get("revoked_at") is None:
                record["revoked_at"] = round(_now(), 3)
                record["revocation_reason"] = str(reason)
                if record["status"] in (CURRENT, OVERLAPPING):
                    record["status"] = REVOKED
                self._commit(inventory, "revoke")
            ca_key, ca_cert = self._ca()
            effective = self._publish_list(inventory, ca_key, ca_cert)
            return {"serial": serial, "entry": name, "effective": effective}

    def recover_entry(self, role: str, instance: str, *, secret_at, owner_uid=None,
                      owner_gid=None) -> dict:
        """Decision 5.6: the entry's key is compromised. In ONE call, under the issuer lock and
        with one commit: lock the entry against renewal, revoke every serial it ever had, hand it
        a fresh single-use secret -- then publish the list. The secret goes to `secret_at` for a
        fresh key. A renewal racing this either landed before (and is revoked here, because every
        recorded serial is) or comes after (and is refused, whatever key it shows)."""
        with self._lock():
            self._begin()
            inventory = self._inventory()
            name = role + "/" + instance
            entry = inventory["entries"].get(name)
            if entry is None:
                raise IssuanceRefused("there is no entry %s to recover" % name)
            now = round(_now(), 3)
            revoked = []
            for record in entry.get("certificates", []):
                if record.get("revoked_at") is None:
                    record["revoked_at"] = now
                    record["revocation_reason"] = COMPROMISE
                    revoked.append(record["serial"])
                if record["status"] in (CURRENT, OVERLAPPING):
                    record["status"] = REVOKED
            entry["renewal_locked"] = True
            secret = secrets.token_hex(32)
            entry["secret_sha256"] = _sha256(secret.encode("ascii"))
            entry["secret_expires"] = round(now + SECRET_HOURS * 3600, 3)
            self._commit(inventory, "recover")
            ca_key, ca_cert = self._ca()
            effective = self._publish_list(inventory, ca_key, ca_cert)
            secret_path = Path(secret_at)
            if self.files.exists(secret_path):
                self.files.remove(secret_path)
            _files.durable_replace(self.files, secret_path, secret.encode("ascii"), mode=0o400,
                                   label="recover-secret")
            if owner_uid is not None and hasattr(os, "chown"):
                os.chown(secret_path, int(owner_uid), int(owner_gid if owner_gid is not None
                                                          else -1))
            return {"entry": name, "revoked": revoked, "effective": effective}

    def publish(self) -> dict:
        """Sign and publish a fresh list now, from the inventory. For root, after a clock was
        corrected or when a list must be replaced before the root run would do it."""
        with self._lock():
            self._begin()
            inventory = self._inventory()
            ca_key, ca_cert = self._ca()
            published = self._publish_list(inventory, ca_key, ca_cert)
            return {"published": published, "number": int(inventory.get("list_number") or 0)}

    # ------------------------------------------------------------------ the root run (stage 5)

    def _end_overlaps(self, inventory: dict) -> list:
        now = _now()
        ended = []
        for entry in inventory["entries"].values():
            for record in entry.get("certificates", []):
                if record["status"] != OVERLAPPING:
                    continue
                until = float(record.get("overlap_until") or record["not_after"])
                if until <= now:
                    record["status"] = SUPERSEDED
                    if record.get("revoked_at") is None:
                        record["revoked_at"] = round(now, 3)
                        record["revocation_reason"] = OVERLAP_ENDED
                    ended.append(record["serial"])
        return ended

    def tick(self, floor_dir=None) -> dict:
        """The root run, in the order decision 5.0 fixes -- also, and above all, after a restart:

            1. resolve what a crash left of the list's publication, then of each floor's
               (fsync first, promote a newer stage, drop an older one);
            2. end overlaps that have run out;
            3. reconcile the list with the inventory, and publish a fresh one when a revoked
               serial is missing from it, when it cannot be read, or when it is due a refresh;
            4. ONLY THEN, and only if no revocation is waiting to be published, write each floor.

        Returns what it did. Never raises for a failed step: it reports it, and the floor --
        which ages -- is what turns a writer that keeps failing into services that stop.
        """
        report: dict = {"list": "", "overlaps_ended": [], "pending_revocation": False,
                        "floors": {}}
        floor_dir = Path(floor_dir) if floor_dir else None
        with self._lock():
            self._begin()
            inventory = self._inventory()
            ca_key, ca_cert = self._ca()

            # 1. What a crash left. The list first, then the floors.
            settle_failed = False
            try:
                report["list"] = "stage " + self._settle_list()
            except OSError as exc:
                settle_failed = True
                report["list"] = "could not resolve a leftover stage: " + str(exc)
            floors = {}
            if floor_dir is not None:
                for role in _floor.ROLES:
                    path = _floor.path_for(floor_dir, role)
                    if not self.files.exists(path) and not self.files.exists(
                            _files.stage_names(path)[0]):
                        continue
                    try:
                        floors[role] = _files.settle_stage(self.files, path, _floor.newer,
                                                           label="settle-floor-" + role)
                    except OSError as exc:
                        report["floors"][role] = "could not resolve a leftover stage: " + str(exc)

            # 2. Overlaps that ran out are revoked -- this is what makes the bound a limit.
            ended = self._end_overlaps(inventory)
            if ended:
                report["overlaps_ended"] = ended
                try:
                    self._commit(inventory, "overlap-ended")
                except Indeterminate as exc:
                    report["list"] += "; ending overlaps not committed: " + str(exc)
                    return report

            # 3. Reconcile. The inventory decides; the list must carry every revoked serial.
            revoked = set(self._revoked(inventory))
            published, why = self._published(ca_cert)
            missing = revoked - set(published.serials) if published else revoked
            due = (published is None or bool(missing)
                   or _now() - published.this_update >= _revocation.REFRESH_AFTER_SECONDS)
            if due and not settle_failed:
                if self._publish_list(inventory, ca_key, ca_cert):
                    report["list"] += "; published number %d" % inventory["list_number"]
                else:
                    report["list"] += "; publication FAILED"
                published, why = self._published(ca_cert)
            elif due:
                report["list"] += "; publication not attempted over an unresolved stage"
            missing = revoked - set(published.serials) if published else revoked
            if missing:
                report["pending_revocation"] = True

            # 4. The floors -- held back while a revocation is not durably published.
            if floor_dir is None:
                return report
            for role, settled in floors.items():
                if report["floors"].get(role):
                    continue
                if report["pending_revocation"]:
                    report["floors"][role] = ("not written: a revocation is not yet durably "
                                              "published")
                    continue
                report["floors"][role] = self._advance_floor(
                    _floor.path_for(floor_dir, role), published)
        return report

    def _advance_floor(self, path: Path, published) -> str:
        try:
            state = _floor.parse(self.files.read(path))
        except (OSError, _floor.FloorUnusable) as exc:
            return "not written: the floor file is missing or unreadable, and only `agentnode " \
                   "pki floor init` may start one (%s)" % type(exc).__name__
        try:
            new = _floor.advance(state, system_now=_now(), monotonic_now=_floor._monotonic(),
                                 boot=_floor._boot(),
                                 list_this_update=published.this_update if published else None)
        except _floor.FloorUnusable as exc:
            return "not written: " + str(exc)
        try:
            _files.durable_publish(self.files, path, new.to_bytes(), mode=0o644,
                                   label="floor-" + state.role)
        except OSError as exc:
            return "not written: " + str(exc)
        return "written, generation %d" % new.generation

    # ------------------------------------------------------------------ the floor's lifecycle

    def floor_init(self, floor_dir, roles=_floor.ROLES, *,
                   tolerance_s: float = _floor.DEFAULT_TOLERANCE_SECONDS,
                   max_age_s: float = _floor.DEFAULT_MAX_AGE_SECONDS,
                   after_loss: bool = False) -> dict:
        """Set up the floor files, root's and read-only to everyone else, in their initial state.

        A floor that exists and parses is never replaced here: that would grant a second
        tolerance. One that is missing or unreadable after it had existed is replaced only with
        `after_loss`, and that is written down in the issuer's log.
        """
        floor_dir = Path(floor_dir)
        floor_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(floor_dir, 0o755)
        except OSError:                                       # pragma: no cover
            pass
        done = {}
        with self._lock():
            self._begin()
            _key, ca_cert = self._ca()
            not_before = getattr(ca_cert, "not_valid_before_utc", None)
            not_before = (not_before.timestamp() if not_before is not None else
                          ca_cert.not_valid_before.replace(tzinfo=_dt.timezone.utc).timestamp())
            for role in roles:
                path = _floor.path_for(floor_dir, role)
                _files.settle_stage(self.files, path, _floor.newer, label="init-floor-" + role)
                if self.files.exists(path):
                    try:
                        _floor.parse(self.files.read(path))
                        raise IssuanceRefused(
                            "the %s floor exists and is readable; setting it up again would "
                            "grant a second tolerance. `agentnode pki floor recover` moves a "
                            "floor that stands too far ahead" % role)
                    except _floor.FloorUnusable:
                        if not after_loss:
                            raise IssuanceRefused(
                                "the %s floor is unreadable. Replacing it starts its counters "
                                "again, which is a root decision: repeat with --after-loss"
                                % role) from None
                        self._note("floor re-initialised after loss", role)
                state = _floor.initial(role, not_before, tolerance_s=tolerance_s,
                                       max_age_s=max_age_s)
                _files.durable_publish(self.files, path, state.to_bytes(), mode=0o644,
                                       label="init-floor-" + role)
                done[role] = str(path)
        return done

    def floor_recover(self, floor_dir, role: str) -> dict:
        """Root's recovery of a floor that stands too far ahead (decision 5.7): set it to the
        later of the CA's notBefore and the valid list's thisUpdate -- both signed -- and change
        nothing else. The lifetime counters stay; the tolerance stays spent."""
        path = _floor.path_for(floor_dir, role)
        with self._lock():
            self._begin()
            _key, ca_cert = self._ca()
            _files.settle_stage(self.files, path, _floor.newer, label="recover-floor-" + role)
            state = _floor.parse(self.files.read(path))
            not_before = getattr(ca_cert, "not_valid_before_utc", None)
            not_before = (not_before.timestamp() if not_before is not None else
                          ca_cert.not_valid_before.replace(tzinfo=_dt.timezone.utc).timestamp())
            published, _why = self._published(ca_cert)
            target = max(not_before, published.this_update if published else not_before)
            new = _floor.recover(state, target)
            _files.durable_publish(self.files, path, new.to_bytes(), mode=0o644,
                                   label="recover-floor-" + role)
            self._note("floor recovered from %.3f to %.3f" % (state.floor, target), role)
            return {"role": role, "from": state.floor, "to": target,
                    "elapsed_total": new.elapsed_total, "granted_total": new.granted_total}

    def _note(self, what: str, subject: str = "") -> None:
        """A root action on the floor, in the issuer's log. Names and numbers only."""
        line = {"at": round(_now(), 3), "action": what, "subject": subject}
        try:
            with open(self.ca_dir / REFUSALS, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:                                       # pragma: no cover
            pass

    # ------------------------------------------------------------------ looking

    def inventory(self) -> dict:
        """The inventory as it is on disk, WITHOUT the certificate bodies or secret digests."""
        with self._lock():
            self._begin()
            raw = self._inventory()
        now = _now()
        shown = {"deployment": raw["deployment"], "list_number": raw.get("list_number", 0),
                 "entries": {}}
        for name, entry in raw["entries"].items():
            certificates = []
            for c in entry["certificates"]:
                one = {k: c[k] for k in ("serial", "status", "not_after", "public_key_sha256")}
                # Decision 5.5: the remaining validity belongs in the view an operator asks for
                # anyway, and so does the day renewal falls due -- the last third of the
                # lifetime, day 60 of 90.
                one["remaining_days"] = round((float(c["not_after"]) - now) / 86400.0, 2)
                one["renewal_due_from"] = round(float(c["not_after"])
                                                - int(entry.get("days") or DAYS) * 86400 / 3.0, 3)
                one["overlap_until"] = c.get("overlap_until")
                one["revoked_at"] = c.get("revoked_at")
                one["revocation_reason"] = c.get("revocation_reason", "")
                certificates.append(one)
            shown["entries"][name] = {
                "uri": entry["uri"], "usage": entry["usage"],
                "claimed": not entry.get("secret_sha256"),
                "renewal_locked": bool(entry.get("renewal_locked")),
                "certificates": certificates}
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


def install_renewal(tls_dir, anchor) -> dict:
    """Run AS THE SERVICE, after root renewed: put the renewed pair where the service reads it.

    Checks before it moves anything: `cert.pem.next` was issued by the anchor, carries exactly the
    identity `cert.pem` carries, and belongs to `key.next.pem`. Then the key moves into place and
    then the certificate, each rename made durable. A running service takes the new pair up on its
    next connection (`worker/tls.py`, `Contexts`) and keeps using the previous pair -- still valid,
    it is overlapping -- for any connection that finds the two halves between the renames, so no
    connection presents a certificate with a key that is not its own, and nothing restarts.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    folder = Path(tls_dir)
    next_cert_path, next_key_path = folder / "cert.pem.next", folder / "key.next.pem"
    ca = x509.load_pem_x509_certificate(Path(anchor).read_bytes())
    renewed = x509.load_pem_x509_certificate(next_cert_path.read_bytes())
    current = x509.load_pem_x509_certificate((folder / "cert.pem").read_bytes())
    renewed.verify_directly_issued_by(ca)

    def uris(certificate):
        return certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(x509.UniformResourceIdentifier)

    if uris(renewed) != uris(current):
        raise IssuanceRefused("the renewed certificate names another identity than the current "
                              "one; it is not installed")
    key = serialization.load_pem_private_key(next_key_path.read_bytes(), None)
    if _public_key_fingerprint(key.public_key()) != _public_key_fingerprint(renewed.public_key()):
        raise IssuanceRefused("the renewed certificate is not for the key made for it; it is not "
                              "installed")
    files = _files.Files()
    files.rename(next_key_path, folder / "key.pem")
    files.fsync_dir(folder)
    files.rename(next_cert_path, folder / "cert.pem")
    files.fsync_dir(folder)
    leftover = folder / "renewal.json"
    if leftover.exists():
        leftover.unlink()
    return {"serial": format(renewed.serial_number, "x"), "not_after": _not_after(renewed)}


__all__ = ["BY_RENEWAL", "BY_ROOT", "COMPROMISE", "CURRENT", "DEFAULT_CA_DIR", "DEFAULT_TRUST_DIR",
           "INVENTORY", "INVENTORY_TMP", "Indeterminate", "IssuanceRefused", "Issuer",
           "OVERLAPPING", "OVERLAP_ENDED", "OVERLAP_SECONDS", "REVOKED", "SUPERSEDED",
           "install_renewal", "make_request"]
