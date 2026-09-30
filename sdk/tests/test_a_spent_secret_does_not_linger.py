"""The issuer kept the plaintext of every enrolment secret it ever consumed.

`_deliver` called `forget_the_enrolment`, whose own comment says the removal is "the difference
between a procedure somebody may follow and a property the product has". That function returns
early unless `cert.pem` AND `key.pem` are both in the directory. On the SERVICE's host that guard
is right: the secret is what buys a usable pair and removing it early strands the service. On the
ISSUER's host a `key.pem` never appears, because the private key stays with whoever requested the
certificate. So on a two-host deployment the guard could never pass, the call removed nothing
every single time, and `/var/lib/agentnode/enrolment/<instance>/secret` survived indefinitely --
while `install.sh` told the operator, in words, to delete every copy on both machines.

Found by enumerating both disks in the acceptance run of 2026-09-30, criterion X6-F. Measured
severity at the time: the secret was SPENT -- re-presenting it was refused with "this enrollment
secret has already been used", exit 1, no certificate issued. A kept-contract defect, not a live
exposure, which is why the fix is narrow.

Judged against the frozen profile `spent-enrolment-secret-r1`, criteria S1-S10.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentnode_sdk.pki import enrolment as _enrolment


# --------------------------------------------------------------------------- the unit itself

class TestTheIssuerSideCleanupExistsAndIsSeparate:
    """S1 and the reason there are two functions rather than one relaxed one."""

    def test_the_service_side_guard_is_untouched(self, tmp_path):
        """forget_the_enrolment must STILL refuse without a usable pair. Relaxing it would have
        been the easy fix and would strand a service that has a secret and no certificate."""
        (tmp_path / "secret").write_text("s3cr3t", encoding="ascii")
        (tmp_path / "request.json").write_text("{}", encoding="utf-8")
        assert _enrolment.forget_the_enrolment(tmp_path) == []
        assert (tmp_path / "secret").is_file(), "the service's secret was taken before its pair"

    def test_and_still_acts_once_the_pair_is_there(self, tmp_path):
        (tmp_path / "secret").write_text("s3cr3t", encoding="ascii")
        (tmp_path / "request.json").write_text("{}", encoding="utf-8")
        (tmp_path / "cert.pem").write_text("c", encoding="ascii")
        (tmp_path / "key.pem").write_text("k", encoding="ascii")
        assert sorted(_enrolment.forget_the_enrolment(tmp_path)) == ["request.json", "secret"]

    def test_the_issuer_side_does_not_wait_for_a_key_that_never_comes(self, tmp_path):
        """S1. The issuer's directory has cert.pem and never a key.pem."""
        (tmp_path / "secret").write_text("s3cr3t", encoding="ascii")
        (tmp_path / "request.json").write_text("{}", encoding="utf-8")
        (tmp_path / "cert.pem").write_text("c", encoding="ascii")
        assert sorted(_enrolment.forget_a_spent_secret(tmp_path)) == ["request.json", "secret"]
        assert not (tmp_path / "secret").exists()
        assert not (tmp_path / "request.json").exists()
        assert (tmp_path / "cert.pem").is_file(), "it removed something it was not asked to"

    def test_it_removes_only_the_two_names(self, tmp_path):
        for name in ("secret", "request.json", "cert.pem", "key.pem", "inventory.json", "ca.key"):
            (tmp_path / name).write_text("x", encoding="ascii")
        _enrolment.forget_a_spent_secret(tmp_path)
        left = sorted(p.name for p in tmp_path.iterdir())
        assert left == ["ca.key", "cert.pem", "inventory.json", "key.pem"]

    def test_a_read_only_secret_is_still_removed(self, tmp_path):
        """The secret is written 0400 and a read-only file cannot be unlinked on Windows."""
        s = tmp_path / "secret"
        s.write_text("s3cr3t", encoding="ascii")
        os.chmod(s, 0o400)
        assert _enrolment.forget_a_spent_secret(tmp_path) == ["secret"]
        assert not s.exists()


class TestRepeatedCleanupIsSafe:
    """S5. Called again, on an empty directory, on one that never existed."""

    def test_again_on_the_same_directory(self, tmp_path):
        (tmp_path / "secret").write_text("s", encoding="ascii")
        assert _enrolment.forget_a_spent_secret(tmp_path) == ["secret"]
        assert _enrolment.forget_a_spent_secret(tmp_path) == []
        assert _enrolment.forget_a_spent_secret(tmp_path) == []

    def test_on_a_directory_that_does_not_exist(self, tmp_path):
        assert _enrolment.forget_a_spent_secret(tmp_path / "never-existed") == []

    def test_on_a_path_that_is_a_file(self, tmp_path):
        f = tmp_path / "a-file"
        f.write_text("x", encoding="ascii")
        assert _enrolment.forget_a_spent_secret(f) == []

    def test_it_never_raises_even_when_the_name_is_a_directory(self, tmp_path):
        (tmp_path / "secret").mkdir()
        assert _enrolment.forget_a_spent_secret(tmp_path) == []
        assert (tmp_path / "secret").is_dir(), "it deleted a directory it should have ignored"

    def test_and_a_removal_that_genuinely_fails_is_swallowed(self, tmp_path):
        """The `except OSError` branch, exercised rather than assumed.

        The first version of this class never reached that branch at all: every case it tried --
        a missing file, a missing directory, a name that is a directory -- fails the `is_file()`
        guard and returns without raising. A counter-check that made the branch re-raise stayed
        green, which is how the gap was found. This holds the file open, which on Windows makes
        `unlink` raise PermissionError, and on POSIX still exercises the path when the directory
        is not writable.
        """
        secret = tmp_path / "secret"
        secret.write_text("s3cr3t", encoding="ascii")
        handle = open(secret, "rb")
        try:
            if os.name != "nt":
                os.chmod(tmp_path, 0o500)          # cannot unlink from a non-writable directory
            try:
                gone = _enrolment.forget_a_spent_secret(tmp_path)
            finally:
                if os.name != "nt":
                    os.chmod(tmp_path, 0o700)
        finally:
            handle.close()
        # It must not raise. Whether the file went is the platform's business, not this
        # function's contract: what is promised is that a residue it cannot remove costs a line
        # in a log and not an exception.
        assert isinstance(gone, list)


# --------------------------------------------------------------------------- wiring and order

SOURCE = Path(__file__).resolve().parent.parent / "agentnode_sdk" / "pki" / "issuer.py"
ISSUER = SOURCE.read_text(encoding="utf-8")


class TestTheIssuerCallsItAndInTheRightOrder:
    """S4. Commit first, forget second. The other order loses the record of a live secret."""

    def test_enroll_forgets_the_spent_plaintext(self):
        assert "_forget_a_spent_secret" in ISSUER

    def test_it_is_called_on_the_success_path_after_the_commit(self):
        body = ISSUER[ISSUER.index("def enroll("):ISSUER.index("def renew(")]
        commit = body.index('self._commit(inventory, "issue")')
        forget = body.index("self._forget_a_spent_secret(entry)", commit)
        assert commit < forget, "the plaintext is removed before the consumption is durable"

    def test_and_on_the_replay_path_too(self):
        """A run that crashed between its commit and its removal leaves a copy; the next
        legitimate replay is where it gets cleaned up."""
        body = ISSUER[ISSUER.index("def enroll("):ISSUER.index("def renew(")]
        assert body.count("self._forget_a_spent_secret(entry)") == 2

    def test_renewal_does_not_call_it(self):
        """A renewal involves no enrolment secret. Cleaning there would be reaching."""
        body = ISSUER[ISSUER.index("def renew("):]
        assert "_forget_a_spent_secret" not in body

    def test_the_order_is_argued_in_the_source(self):
        assert "COMMIT FIRST, THEN FORGET" in ISSUER


# --------------------------------------------------------------------------- end to end

def _issuer(tmp_path):
    from agentnode_sdk.pki.issuer import Issuer
    iss = Issuer(tmp_path / "ca", tmp_path / "trust")
    iss.initialise("test-deployment")
    return iss


def _a_request(folder):
    """Build a real request the way `pki request` does, from the secret in `folder`.

    `make_request` writes request.json and returns its path, so this reads it back -- the same
    round trip the operator makes by hand between the two machines.
    """
    from agentnode_sdk.pki.issuer import make_request
    secret = (folder / "secret").read_text(encoding="ascii").strip()
    return json.loads(make_request(folder, secret).read_text(encoding="utf-8"))


@pytest.fixture()
def stand(tmp_path):
    iss = _issuer(tmp_path)
    folder = tmp_path / "enrolment" / "w1"
    folder.mkdir(parents=True)
    iss.add("worker", "w1", secret_at=folder / "secret", deliver_to=folder / "cert.pem")
    return iss, folder


class TestSuccessRemovesThePlaintext:
    """S1, measured through the real issuer rather than the helper."""

    def test_after_a_successful_issuance_no_secret_remains(self, stand):
        iss, folder = stand
        assert (folder / "secret").is_file(), "the fixture did not write a secret"
        body = _a_request(folder)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert (folder / "cert.pem").is_file(), "no certificate was delivered"
        assert not (folder / "secret").exists(), "the spent plaintext secret is still there"
        assert not (folder / "request.json").exists()

    def test_and_the_inventory_never_held_the_secret_itself(self, stand):
        iss, folder = stand
        secret = (folder / "secret").read_text(encoding="ascii").strip()
        body = _a_request(folder)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        everything = "".join(p.read_text(encoding="utf-8", errors="replace")
                             for p in Path(iss.ca_dir).rglob("*") if p.is_file())
        assert secret not in everything, "the secret itself is in the issuer's own files"


class TestReplayIsStillRefused:
    """S2. And it must not depend on the plaintext still existing."""

    def test_a_different_key_is_refused_after_the_plaintext_is_gone(self, stand):
        from agentnode_sdk.pki.issuer import IssuanceRefused
        iss, folder = stand
        secret = (folder / "secret").read_text(encoding="ascii").strip()
        first = _a_request(folder)
        iss.enroll(first["csr"].encode("ascii"), first["secret"])
        assert not (folder / "secret").exists()

        other = folder.parent / "impostor"
        other.mkdir()
        (other / "secret").write_text(secret, encoding="ascii")
        second = _a_request(other)
        with pytest.raises(IssuanceRefused) as refused:
            iss.enroll(second["csr"].encode("ascii"), second["secret"])
        assert "already been used" in str(refused.value)

    def test_the_refusal_survives_a_fresh_issuer_object(self, stand):
        """S2's restart half: a new Issuer reading the same files must refuse just the same."""
        from agentnode_sdk.pki.issuer import Issuer, IssuanceRefused
        iss, folder = stand
        secret = (folder / "secret").read_text(encoding="ascii").strip()
        first = _a_request(folder)
        iss.enroll(first["csr"].encode("ascii"), first["secret"])

        again = Issuer(iss.ca_dir, iss.trust_dir)
        other = folder.parent / "impostor2"
        other.mkdir()
        (other / "secret").write_text(secret, encoding="ascii")
        second = _a_request(other)
        with pytest.raises(IssuanceRefused):
            again.enroll(second["csr"].encode("ascii"), second["secret"])

    def test_the_consumption_record_holds_a_digest_and_not_the_secret(self, stand):
        iss, folder = stand
        secret = (folder / "secret").read_text(encoding="ascii").strip()
        body = _a_request(folder)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        inventory = json.loads((Path(iss.ca_dir) / "inventory.json").read_text(encoding="utf-8"))
        consumed = inventory["entries"]["worker/w1"]["consumed"]
        assert consumed, "nothing was recorded as consumed"
        assert secret not in json.dumps(consumed)
        assert len(consumed[0]["secret_sha256"]) == 64


class TestALostAnswerStillGetsItsCertificate:
    """S6. Removing the issuer's copy must not break the requester's retry."""

    def test_the_same_key_gets_the_same_certificate_back(self, stand):
        iss, folder = stand
        body = _a_request(folder)
        first = iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert not (folder / "secret").exists()
        again = iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert again == first, "the retry got a different certificate"

    def test_and_the_retry_re_delivers_it(self, stand):
        iss, folder = stand
        body = _a_request(folder)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        (folder / "cert.pem").unlink()
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert (folder / "cert.pem").is_file(), "the lost delivery was not repeated"


class TestAFailedAttemptKeepsAValidSecret:
    """S3. The cases that must NOT consume or delete anything."""

    def test_an_unknown_secret_leaves_the_real_one_alone(self, stand):
        from agentnode_sdk.pki.issuer import IssuanceRefused
        iss, folder = stand
        other = folder.parent / "stranger"
        other.mkdir()
        (other / "secret").write_text("AGENTNODE-ENROL-not-a-real-secret", encoding="ascii")
        body = _a_request(other)
        with pytest.raises(IssuanceRefused):
            iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert (folder / "secret").is_file(), "a stranger's failure took the real secret"

    def test_a_request_that_is_not_a_request_at_all(self, stand):
        """Unparseable input raises from the x509 layer rather than as an IssuanceRefused, which
        is a distinction worth keeping: the first version of this test asserted IssuanceRefused
        and was asserting my own wrong expectation. What the criterion is about is the SECRET,
        and it survives either way."""
        iss, folder = stand
        with pytest.raises(Exception):
            iss.enroll(b"-----BEGIN CERTIFICATE REQUEST-----\nnonsense\n"
                       b"-----END CERTIFICATE REQUEST-----\n", "whatever")
        assert (folder / "secret").is_file()

    def test_a_syntactically_valid_request_with_the_wrong_secret(self, stand):
        """The refusal path proper: a real CSR, correctly signed, presented with a secret that
        belongs to no entry."""
        from agentnode_sdk.pki.issuer import IssuanceRefused
        iss, folder = stand
        body = _a_request(folder)
        with pytest.raises(IssuanceRefused) as refused:
            iss.enroll(body["csr"].encode("ascii"), "AGENTNODE-ENROL-" + "0" * 32)
        assert "no unclaimed entry" in str(refused.value)
        assert (folder / "secret").is_file(), "a wrong secret took the right one with it"

    def test_an_expired_secret_is_refused_and_not_removed(self, stand):
        """It is refused, and the plaintext stays: root may want to see what is there."""
        from agentnode_sdk.pki.issuer import IssuanceRefused
        iss, folder = stand
        path = Path(iss.ca_dir) / "inventory.json"
        inventory = json.loads(path.read_text(encoding="utf-8"))
        inventory["entries"]["worker/w1"]["secret_expires"] = 1.0
        path.write_text(json.dumps(inventory), encoding="utf-8")
        body = _a_request(folder)
        with pytest.raises(IssuanceRefused) as refused:
            iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert "expired" in str(refused.value)
        assert (folder / "secret").is_file(), "an expired secret was silently destroyed"

    def test_and_after_a_refusal_the_entry_is_still_enrollable(self, stand):
        from agentnode_sdk.pki.issuer import IssuanceRefused
        iss, folder = stand
        body = _a_request(folder)
        with pytest.raises(IssuanceRefused):
            iss.enroll(body["csr"].encode("ascii"), "AGENTNODE-ENROL-" + "f" * 32)
        pem = iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert b"BEGIN CERTIFICATE" in pem, "a failed attempt had spoiled the entry"
        assert not (folder / "secret").exists(), "and the successful one still cleaned up"


class TestNoCrashWindowIssuesTwice:
    """S4, as a property of the state rather than of the timing."""

    def test_a_crash_after_the_commit_leaves_a_spent_secret_and_not_a_live_one(self, stand):
        """Simulated by committing through a real enrol and then putting the plaintext back --
        which is exactly what a crash between the commit and the removal would leave."""
        from agentnode_sdk.pki.issuer import IssuanceRefused
        iss, folder = stand
        secret = (folder / "secret").read_text(encoding="ascii").strip()
        body = _a_request(folder)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        (folder / "secret").write_text(secret, encoding="ascii")   # the crash window

        other = folder.parent / "after-the-crash"
        other.mkdir()
        (other / "secret").write_text(secret, encoding="ascii")
        second = _a_request(other)
        with pytest.raises(IssuanceRefused):
            iss.enroll(second["csr"].encode("ascii"), second["secret"])

    def test_and_the_next_legitimate_replay_cleans_that_leftover_up(self, stand):
        iss, folder = stand
        secret = (folder / "secret").read_text(encoding="ascii").strip()
        body = _a_request(folder)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        (folder / "secret").write_text(secret, encoding="ascii")   # the crash window
        iss.enroll(body["csr"].encode("ascii"), body["secret"])    # same key: a replay
        assert not (folder / "secret").exists(), "the leftover survived a replay"


class TestNothingLeaksTheSecret:
    """S7. Not into a refusal, not into the inventory, not into a log this code writes."""

    def test_a_refusal_does_not_quote_the_secret(self, stand):
        from agentnode_sdk.pki.issuer import IssuanceRefused
        iss, folder = stand
        other = folder.parent / "stranger2"
        other.mkdir()
        made_up = "AGENTNODE-ENROL-0123456789abcdef"
        (other / "secret").write_text(made_up, encoding="ascii")
        body = _a_request(other)
        with pytest.raises(IssuanceRefused) as refused:
            iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert made_up not in str(refused.value)

    def test_nor_does_anything_the_issuer_writes(self, stand):
        iss, folder = stand
        secret = (folder / "secret").read_text(encoding="ascii").strip()
        body = _a_request(folder)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        for root in (iss.ca_dir, iss.trust_dir):
            for p in Path(root).rglob("*"):
                if p.is_file():
                    assert secret not in p.read_text(encoding="utf-8", errors="replace"), p
