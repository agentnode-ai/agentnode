"""mtls-loopback-identity-r1, decision stage 1: the issuer, its inventory, and its transaction.

What an issued certificate says comes from the inventory and from nowhere else; who may claim an
entry is decided by a single-use secret or, for a renewal, by the current key; and the commit is
durable before anything is delivered.

The crash cases run under the decision's fault model (`agentnode_sdk.pki.files`): every crash is
forced into BOTH permitted outcomes -- the directory operations since the last directory fsync
LOST, or all of them SURVIVED -- and the invariant is checked in each. A real crash would pick one
outcome by chance, and a test that let it pick would pass a wrong implementation whenever the
lucky one came up.

The invariant checked, as the decision states it:

* per issuance transaction, at most one certificate committed and at most one delivered, and no
  uncommitted certificate surviving the next lock;
* per entry, every committed certificate recorded with its status, and no valid certificate of
  that entry that is not in the inventory.

Who can READ the issuing key is a property of accounts and file modes on the real host; it is
exercised on the alpha as each service account, not here.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from agentnode_sdk.pki import files as F
from agentnode_sdk.pki import identity as ids
from agentnode_sdk.pki.issuer import (CURRENT, INVENTORY, INVENTORY_TMP, OVERLAPPING, REFUSALS,
                                      SUPERSEDED, Indeterminate, IssuanceRefused, Issuer,
                                      make_request)


def _x509():
    from cryptography import x509

    return x509


class Place:
    """An issuer on disk, one worker entry, and a request made by that worker."""

    def __init__(self, root: Path, files=None) -> None:
        self.root = Path(root)
        self.ca, self.trust = self.root / "ca", self.root / "trust"
        Issuer(self.ca, self.trust).initialise("alpha")
        self.folder = self.root / "worker-w1"
        self.folder.mkdir()
        Issuer(self.ca, self.trust).add("worker", "w1", secret_at=self.folder / "secret",
                                        deliver_to=self.folder / "cert.pem")
        self.secret = (self.folder / "secret").read_text()
        self.request = json.loads(make_request(self.folder, self.secret).read_text())
        self.files = files or F.Files()

    def issuer(self, files=None) -> Issuer:
        return Issuer(self.ca, self.trust, files=files or self.files)

    def enroll(self, files=None) -> bytes:
        return self.issuer(files).enroll(self.request["csr"].encode(), self.request["secret"])

    def inventory(self) -> dict:
        return json.loads((self.ca / INVENTORY).read_text())

    def committed(self) -> list[dict]:
        return self.inventory()["entries"]["worker/w1"]["certificates"]

    def delivered(self):
        path = self.folder / "cert.pem"
        return _x509().load_pem_x509_certificate(path.read_bytes()) if path.exists() else None


@pytest.fixture()
def place(tmp_path):
    return Place(tmp_path)


def _serials(records) -> list:
    """What an assertion may print about committed certificates: serial and status, never a body.
    A failure message is evidence too, and the profile allows no certificate body in evidence."""
    return [(r.get("serial"), r.get("status")) for r in records]


def _said(results: dict) -> dict:
    """How each claim ended, without the certificate a granted one returned."""
    return {k: ("certificate, %d bytes" % len(v)) if isinstance(v, bytes) else repr(v)
            for k, v in results.items()}


def serial(certificate) -> str:
    return format(certificate.serial_number, "x")


# ====================================================================== (c) what a certificate says

class TestTheCertificateSaysExactlyTheEntry:

    def test_one_uri_one_usage_no_ca(self, place):
        x509 = _x509()
        certificate = x509.load_pem_x509_certificate(place.enroll())
        sans = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(x509.UniformResourceIdentifier)
        usages = [o.dotted_string for o in certificate.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage).value]
        assert sans == ["agentnode://alpha/worker/w1"]
        assert usages == [ids.SERVER_AUTH]
        assert certificate.extensions.get_extension_for_class(
            x509.BasicConstraints).value.ca is False

    @pytest.mark.parametrize("bad", [
        "agentnode://Alpha/worker/w1", "agentnode://alpha/worker/w%31",
        "agentnode://alpha/worker", "agentnode://alpha/worker/w1/extra",
        "agentnode://alpha//w1", "agentnode://alpha/admin/w1", "agentnode://alpha/worker/w1\n",
        "https://alpha/worker/w1", "agentnode://alpha/worker/" + "a" * 65,
    ])
    def test_a_name_outside_the_grammar_is_refused_not_repaired(self, bad):
        with pytest.raises(ids.NotAnIdentity):
            ids.parse(bad)

    def test_an_entry_whose_label_does_not_fit_is_refused_at_creation(self, place):
        with pytest.raises(ids.NotAnIdentity):
            place.issuer().add("worker", "ContainerBackend", secret_at=place.root / "x",
                               deliver_to=place.root / "y")


# ====================================================================== (d) the request contributes a key

class TestTheRequestContributesOnlyItsKey:

    def test_a_request_asking_for_more_gets_exactly_the_entry(self, place, monkeypatch):
        """A request naming another role, another instance, both usages and a CA -- signed by
        the right key and carrying the right secret. What comes back is the entry, unchanged."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

        from agentnode_sdk.pki import floor as floors
        from agentnode_sdk.pki.trust import TrustView

        # What the peer check judges by since stage 5: this issuer's list, and a floor its root
        # run wrote in this (one) boot. Both valid, so only the certificate's fields decide.
        monkeypatch.setattr(floors, "_boot", lambda: "test-boot")
        place.issuer().floor_init(place.root / "floor")
        place.issuer().tick(place.root / "floor")
        trust = TrustView.read(anchor=place.trust / "ca.pem",
                               revocation_list=place.trust / "revoked.crl",
                               floor=floors.path_for(place.root / "floor", "worker"),
                               role="worker")

        key = serialization.load_pem_private_key((place.folder / "key.pem").read_bytes(), None)
        greedy = (x509.CertificateSigningRequestBuilder()
                  .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "root")]))
                  .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(
                      "agentnode://alpha/gateway/admin")]), critical=False)
                  .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH,
                                                        ExtendedKeyUsageOID.CLIENT_AUTH]),
                                 critical=False)
                  .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                                 critical=True)
                  .sign(key, hashes.SHA256()))
        pem = place.issuer().enroll(greedy.public_bytes(serialization.Encoding.PEM),
                                    place.secret)
        certificate = x509.load_pem_x509_certificate(pem)
        der = certificate.public_bytes(serialization.Encoding.DER)
        who = ids.check_peer(der, deployment="alpha", expected_role="worker",
                             accept_instances={"w1"}, trust=trust)
        assert who.uri() == "agentnode://alpha/worker/w1"
        assert certificate.extensions.get_extension_for_class(
            x509.BasicConstraints).value.ca is False
        # Whichever the installed cryptography has: the *_utc pair exists from 42, the SDK allows 41.
        after = getattr(certificate, "not_valid_after_utc", None) or certificate.not_valid_after
        before = getattr(certificate, "not_valid_before_utc", None) or certificate.not_valid_before
        assert (after - before).days <= 91


# ====================================================================== (e) the secret

class TestTheSecret:

    def test_no_secret_no_certificate(self, place):
        with pytest.raises(IssuanceRefused):
            place.issuer().enroll(place.request["csr"].encode(), "")
        assert _serials(place.committed()) == []

    def test_a_wrong_secret_no_certificate(self, place):
        with pytest.raises(IssuanceRefused):
            place.issuer().enroll(place.request["csr"].encode(), "0" * 64)
        assert _serials(place.committed()) == []

    def test_a_used_secret_cannot_be_used_for_another_key(self, place, tmp_path):
        place.enroll()
        other = tmp_path / "thief"
        other.mkdir()
        stolen = json.loads(make_request(other, place.secret).read_text())
        with pytest.raises(IssuanceRefused, match="already been used"):
            place.issuer().enroll(stolen["csr"].encode(), place.secret)
        assert len(place.committed()) == 1

    def test_an_expired_secret_no_certificate(self, place, monkeypatch):
        from agentnode_sdk.pki import issuer as module

        later = module._now() + 25 * 3600
        monkeypatch.setattr(module, "_now", lambda: later)
        with pytest.raises(IssuanceRefused, match="expired"):
            place.enroll()

    def test_a_refusal_is_written_down_without_the_secret(self, place):
        with pytest.raises(IssuanceRefused):
            place.issuer().enroll(place.request["csr"].encode(), "0" * 64)
        text = (place.ca / REFUSALS).read_text()
        assert "no unclaimed entry" in text
        assert place.secret not in text and "BEGIN" not in text
        assert json.loads(text.splitlines()[-1])["public_key_sha256"]

    def test_the_inventory_keeps_only_a_digest_of_the_secret(self, place):
        assert place.secret not in (place.ca / INVENTORY).read_text()


# ====================================================================== (f) renewal

class TestRenewal:

    def test_a_renewal_signed_by_the_current_key_is_granted(self, place):
        place.enroll()
        renewal = json.loads(make_request(place.folder, renew=True).read_text())
        place.issuer().renew(renewal["csr"].encode(), renewal["current"].encode(),
                             bytes.fromhex(renewal["signature"]))
        assert [c["status"] for c in place.committed()] == [OVERLAPPING, CURRENT]

    def test_a_renewal_not_signed_by_the_current_key_is_refused(self, place, tmp_path):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        place.enroll()
        renewal = json.loads(make_request(place.folder, renew=True).read_text())
        stranger = ec.generate_private_key(ec.SECP256R1())
        forged = stranger.sign(renewal["csr"].encode(), ec.ECDSA(hashes.SHA256()))
        with pytest.raises(IssuanceRefused, match="not signed by the current key"):
            place.issuer().renew(renewal["csr"].encode(), renewal["current"].encode(), forged)
        assert [c["status"] for c in place.committed()] == [CURRENT]


# ====================================================================== (g) two at once

class _FirstHoldsTheDoor(F.Files):
    """A's files: A stops just before its commit and waits for B to read the inventory -- or for
    the lock to make that impossible. The wait is how long B gets to try, not what is measured:
    what is measured is how many certificates end up committed."""

    def __init__(self, b_has_read: threading.Event) -> None:
        self.b_has_read = b_has_read

    def point(self, name: str) -> None:
        if name == "issue:written":
            self.b_has_read.wait(timeout=3.0)


class _SecondSaysWhenItReads(F.Files):
    def __init__(self, b_has_read: threading.Event) -> None:
        self.b_has_read = b_has_read

    def read(self, path) -> bytes:
        data = super().read(path)
        if Path(path).name == INVENTORY:
            self.b_has_read.set()
        return data


class TestTwoClaimsAtOnce:

    def test_two_simultaneous_claims_yield_one_certificate(self, place, tmp_path):
        """A is inside its transaction, built and about to commit. B arrives with the same
        secret and another key. Under the lock B cannot even read the inventory until A is done,
        and then finds the entry claimed. Without the lock B reads the unclaimed entry while A
        is paused -- and both commit."""
        other = tmp_path / "twin"
        other.mkdir()
        twin = json.loads(make_request(other, place.secret).read_text())
        b_has_read = threading.Event()
        a_inside = threading.Event()
        results: dict = {}

        class _A(_FirstHoldsTheDoor):
            def read(self, path):
                if Path(path).name == INVENTORY:
                    a_inside.set()
                return super().read(path)

        def claim(name, files, request):
            try:
                results[name] = place.issuer(files).enroll(request["csr"].encode(), place.secret)
            except BaseException as ended:                     # noqa: BLE001 - recorded, judged below
                results[name] = ended

        a = threading.Thread(target=claim, args=("a", _A(b_has_read), place.request))
        a.start()
        assert a_inside.wait(timeout=30)
        b = threading.Thread(target=claim, args=("b", _SecondSaysWhenItReads(b_has_read), twin))
        b.start()
        a.join(timeout=60)
        b.join(timeout=60)
        granted = [r for r in results.values() if isinstance(r, bytes)]
        refused = [r for r in results.values() if isinstance(r, IssuanceRefused)]
        assert len(granted) == 1 and len(refused) == 1, "the two claims interfered: %r" % _said(results)
        assert len(place.committed()) == 1


# ====================================================================== (h) crashes

ISSUE_POINTS = ("issue:written", "issue:before-rename", "issue:after-rename",
                "issue:after-dirsync")


def _certificates_anywhere(place) -> set[str]:
    """Every serial of this entry that exists anywhere on disk: inventory, temporary inventory,
    delivered file, delivery leftovers."""
    x509 = _x509()
    found = set()
    for path in [*place.ca.iterdir(), *place.folder.iterdir()]:
        if path.suffix not in (".pem", ".json", ".tmp") or path.name in ("key.pem",):
            continue
        text = path.read_text(errors="ignore")
        for block in text.split("-----BEGIN CERTIFICATE-----")[1:]:
            pem = ("-----BEGIN CERTIFICATE-----" + block.split("-----END CERTIFICATE-----")[0]
                   + "-----END CERTIFICATE-----\n").replace("\\n", "\n")
            try:
                found.add(serial(x509.load_pem_x509_certificate(pem.encode())))
            except Exception:                                  # noqa: BLE001 - torn by design
                pass
    return found


def _crash(place, point: str, outcome: str):
    recording = F.RecordingFiles()
    recording.crash_at(point)
    with pytest.raises(F.SimulatedCrash):
        place.enroll(recording)
    recording.settle(outcome)
    return recording


class TestTheTransactionUnderBothOutcomes:

    @pytest.mark.parametrize("outcome", F.OUTCOMES)
    @pytest.mark.parametrize("point", ISSUE_POINTS)
    def test_after_a_crash_and_a_retry_exactly_one_committed_one_delivered(self, place,
                                                                           point, outcome):
        _crash(place, point, outcome)
        # Nothing was delivered before the commit returned, in any outcome.
        assert place.delivered() is None
        place.enroll(F.Files())
        committed = place.committed()
        assert len(committed) == 1, _serials(committed)
        assert serial(place.delivered()) == committed[0]["serial"]
        assert not (place.ca / INVENTORY_TMP).exists(), "an uncommitted inventory survived"
        # Every valid certificate of the entry is the committed one.
        assert _certificates_anywhere(place) == {committed[0]["serial"]}

    def test_h1_before_the_rename_the_entry_is_still_unclaimed(self, place):
        for outcome in F.OUTCOMES:
            _crash(place, "issue:before-rename", outcome)
            entry = place.inventory()["entries"]["worker/w1"]
            assert entry["secret_sha256"] and entry["certificates"] == []

    def test_h2_between_rename_and_dirsync_either_outcome_is_reconciled(self, place):
        """LOST: the old inventory; the retry issues. SURVIVED: the new one; the retry hands over
        the certificate that was committed -- the same serial, not a second one."""
        _crash(place, "issue:after-rename", F.LOST)
        assert _serials(place.committed()) == []
        _crash(place, "issue:after-rename", F.SURVIVED)
        survived = place.committed()
        assert len(survived) == 1
        place.enroll(F.Files())
        assert [c["serial"] for c in place.committed()] == [survived[0]["serial"]]
        assert serial(place.delivered()) == survived[0]["serial"]

    def test_h3_after_the_commit_delivery_is_repeated_not_reissued(self, place):
        _crash(place, "issue:after-dirsync", F.LOST)
        committed = place.committed()
        assert len(committed) == 1 and place.delivered() is None
        place.enroll(F.Files())
        assert [c["serial"] for c in place.committed()] == [committed[0]["serial"]]
        assert serial(place.delivered()) == committed[0]["serial"]

    def test_h4_a_failed_directory_fsync_is_indeterminate_and_delivers_nothing(self, place):
        recording = F.RecordingFiles()
        recording.fail_after("issue:after-rename", "fsync_dir", OSError(5, "I/O error"))
        with pytest.raises(Indeterminate):
            place.enroll(recording)
        assert place.delivered() is None
        place.enroll(F.Files())
        assert len(place.committed()) == 1
        assert serial(place.delivered()) == place.committed()[0]["serial"]

    def test_h4_and_the_next_call_delivers_only_after_its_own_fsync(self, place):
        recording = F.RecordingFiles()
        recording.fail_after("issue:after-rename", "fsync_dir", OSError(5, "I/O error"))
        with pytest.raises(Indeterminate):
            place.enroll(recording)
        again = F.RecordingFiles()
        again.fail_at("fsync_dir", OSError(5, "still failing"))
        try:
            place.enroll(again)
            ended = None
        except BaseException as caught:                        # noqa: BLE001 - judged below
            ended = caught
        # What matters first is what it DID, then how it said so.
        assert place.delivered() is None, "it delivered on a state it could not make durable"
        assert isinstance(ended, Indeterminate), ended

    def test_h5_a_leftover_temporary_inventory_is_removed_unread(self, place):
        """SURVIVED after the file fsync, before the rename: the temporary inventory is on disk,
        with a certificate in it. The next call removes it without reading it, and the one that
        ends up committed and delivered is the retry's."""
        _crash(place, "issue:before-rename", F.SURVIVED)
        leftover = place.ca / INVENTORY_TMP
        assert leftover.exists()
        abandoned = _certificates_anywhere(place)
        assert len(abandoned) == 1
        place.enroll(F.Files())
        assert not leftover.exists()
        committed = {c["serial"] for c in place.committed()}
        assert len(committed) == 1 and committed.isdisjoint(abandoned)
        assert _certificates_anywhere(place) == committed

    def test_h6_renewal_keeps_one_current_and_at_most_one_overlapping(self, place):
        place.enroll()
        for _ in range(2):
            renewal = json.loads(make_request(place.folder, renew=True).read_text())
            place.issuer().renew(renewal["csr"].encode(), renewal["current"].encode(),
                                 bytes.fromhex(renewal["signature"]))
            (place.folder / "cert.pem").write_bytes((place.folder / "cert.pem.next").read_bytes())
            (place.folder / "key.pem").write_bytes((place.folder / "key.next.pem").read_bytes())
            (place.folder / "key.next.pem").unlink()
        assert [c["status"] for c in place.committed()] == [SUPERSEDED, OVERLAPPING, CURRENT]


class TestTheFaultModelItself:
    """The model is what judges the crash cases, so it gets its own controls."""

    def test_lost_undoes_a_rename_and_survived_keeps_it(self, tmp_path):
        for outcome, expected in ((F.LOST, b"old"), (F.SURVIVED, b"new")):
            folder = tmp_path / outcome
            folder.mkdir()
            (folder / "f").write_bytes(b"old")
            recording = F.RecordingFiles()
            recording.crash_at("t:after-rename")
            with pytest.raises(F.SimulatedCrash):
                F.durable_replace(recording, folder / "f", b"new", label="t")
            recording.settle(outcome)
            assert (folder / "f").read_bytes() == expected
            assert not (folder / ".f.tmp").exists() or outcome == F.SURVIVED

    def test_after_the_directory_fsync_both_outcomes_keep_the_write(self, tmp_path):
        for outcome in F.OUTCOMES:
            folder = tmp_path / ("d-" + outcome)
            folder.mkdir()
            (folder / "f").write_bytes(b"old")
            recording = F.RecordingFiles()
            recording.crash_at("t:after-dirsync")
            with pytest.raises(F.SimulatedCrash):
                F.durable_replace(recording, folder / "f", b"new", label="t")
            recording.settle(outcome)
            assert (folder / "f").read_bytes() == b"new"

    def test_an_unsynced_overwrite_is_torn_when_lost(self, tmp_path):
        (tmp_path / "f").write_bytes(b"0123456789")
        recording = F.RecordingFiles()
        recording.overwrite(tmp_path / "f", b"abcdefghij")
        recording.settle(F.LOST)
        assert (tmp_path / "f").read_bytes() == b"abcde"
