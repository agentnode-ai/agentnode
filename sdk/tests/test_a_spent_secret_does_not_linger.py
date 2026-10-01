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

SPENT = "agentnode-enrol-1." + "a" * 64          # a value this test treats as already consumed
LIVE = "agentnode-enrol-1." + "b" * 64           # and one that is still somebody's to use


def _spent(*values):
    """The digests of values the inventory would record as consumed."""
    return {_enrolment.digest_of_a_secret(v) for v in values}


class TestTheDecisionIsAboutContentNotAboutThePath:
    """The fourth design of this check, and the first that cannot destroy a live secret.

    Three earlier versions asked "is this the right DIRECTORY?" and then removed by pathname. Each
    could delete a secret somebody was about to use: by two entries sharing a directory, by a
    junction giving one directory two names, and by a directory appearing between the comparison and
    the unlink. Two of the three were rated CRITICAL by independent review.

    A removal decided by the file's CONTENT has no such states to enumerate. These tests are the
    property itself rather than a list of the ways round the old one.
    """

    def test_a_spent_secret_goes(self, tmp_path):
        (tmp_path / "secret").write_text(SPENT, encoding="ascii")
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == ["secret"]

    def test_a_LIVE_secret_in_the_very_same_directory_stays(self, tmp_path):
        """The whole point. Same directory, same filename, same call -- different bytes."""
        (tmp_path / "secret").write_text(LIVE, encoding="ascii")
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == []
        assert (tmp_path / "secret").read_text(encoding="ascii") == LIVE

    def test_so_no_path_trick_can_reach_a_live_secret(self, tmp_path):
        """Every state the three earlier designs fell to, all at once: the directory is reached
        through an alias, it did not exist when the entry was created, and it is shared. None of it
        matters, because the file does not contain a spent secret."""
        real = tmp_path / "real"
        real.mkdir()
        alias = tmp_path / "alias"
        _a_directory_alias(real, alias)
        (real / "secret").write_text(LIVE, encoding="ascii")
        assert _enrolment.forget_a_spent_secret(alias, _spent(SPENT)) == []
        assert (real / "secret").read_text(encoding="ascii") == LIVE
        # and the same call through the same alias DOES remove the spent one
        (real / "secret").write_text(SPENT, encoding="ascii")
        assert _enrolment.forget_a_spent_secret(alias, _spent(SPENT)) == ["secret"]

    def test_another_entry_s_spent_secret_is_not_this_entry_s_to_remove(self, tmp_path):
        """Conservative on purpose: the digests passed in are one entry's own consumptions."""
        (tmp_path / "secret").write_text(SPENT, encoding="ascii")
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(LIVE)) == []

    def test_no_digests_means_nothing_is_known_to_be_spent_so_nothing_goes(self, tmp_path):
        (tmp_path / "secret").write_text(SPENT, encoding="ascii")
        assert _enrolment.forget_a_spent_secret(tmp_path, set()) == []
        assert _enrolment.forget_a_spent_secret(tmp_path, None) == []
        assert (tmp_path / "secret").is_file()

    def test_a_request_carrying_the_spent_secret_goes_and_one_carrying_a_live_one_stays(self, tmp_path):
        """`request.json` holds the secret in a field, so the value has to be read out of it."""
        (tmp_path / "request.json").write_text(json.dumps({"csr": "...", "secret": LIVE}),
                                               encoding="ascii")
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == []
        (tmp_path / "request.json").write_text(json.dumps({"csr": "...", "secret": SPENT}),
                                               encoding="ascii")
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == ["request.json"]

    def test_an_unreadable_or_unparseable_request_is_left_alone(self, tmp_path):
        (tmp_path / "request.json").write_text("{not json", encoding="ascii")
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == []
        assert (tmp_path / "request.json").is_file()

    def test_the_digest_is_the_same_one_the_inventory_records(self):
        """If these two drifted apart the comparison would be meaningless, and it would LOOK fine:
        nothing would ever be removed and the only symptom would be leftovers."""
        from agentnode_sdk.pki.issuer import _sha256
        value = "  " + SPENT + "\n"
        assert _enrolment.digest_of_a_secret(value) == \
            _sha256(str(value).strip().encode("ascii", "replace"))

    def test_whitespace_around_the_value_on_disk_does_not_hide_it(self, tmp_path):
        (tmp_path / "secret").write_text(SPENT + "\n", encoding="ascii")
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == ["secret"]

    def test_the_file_HASHED_must_be_the_file_UNLINKED(self, tmp_path, monkeypatch):
        """The fifth review's CRITICAL finding, built as a synchronised swap.

        Deciding on content fixed every static state and left one gap: the content was read by
        pathname and the unlink used that pathname again, so between the two the name could be made
        to point at a different, still-usable secret. The reviewer asked for exactly this
        counter-check -- "a synchronized second-thread/process swap between those operations".

        No second thread is needed and none should be used: a thread would make the test racy, which
        is the opposite of what is wanted. `digest_of_a_secret` is called at precisely the moment
        between reading the handle and the removal, so wrapping it does the swap deterministically,
        every run, in exactly the window under test. The production code has no hook in it.
        """
        victim = tmp_path / "secret"
        victim.write_text(SPENT, encoding="ascii")
        elsewhere = tmp_path / "somebody-elses-live-secret"
        elsewhere.write_text(LIVE, encoding="ascii")
        # COMPUTED BEFORE THE PATCH IS INSTALLED. The first version of this test passed
        # `_spent(SPENT)` as an argument to the call, so it was evaluated AFTER the patch: the
        # helper's own digest call tripped the swap before the function under test had opened
        # anything, the file was already LIVE when it was read, and the test passed for a reason
        # that had nothing to do with the identity check. A counter-check that deletes that check
        # stayed green, which is how this was found.
        digests = _spent(SPENT)
        real = _enrolment.digest_of_a_secret
        swapped = []

        def swap_the_file_underneath(value):
            answer = real(value)                       # the honest digest of what WAS read
            if not swapped:
                swapped.append(True)
                victim.unlink()                        # the name now refers to nothing...
                elsewhere.replace(victim)              # ...and now to a LIVE secret
            return answer

        monkeypatch.setattr(_enrolment, "digest_of_a_secret", swap_the_file_underneath)
        gone = _enrolment.forget_a_spent_secret(tmp_path, digests)
        monkeypatch.undo()

        assert swapped, "the swap never happened, so this test proved nothing"
        assert gone == [], "a live secret was deleted because the name was re-pointed after the check"
        assert victim.is_file(), "the swapped-in file is gone"
        assert victim.read_text(encoding="ascii") == LIVE, \
            "the file that was unlinked was not the file that was hashed"

    def test_a_residue_larger_than_a_residue_can_be_is_NOT_READ_AT_ALL(self, tmp_path, monkeypatch):
        """S5, and the first version of this test could not fail.

        It wrote an oversized file and asserted nothing was removed — which is true however much of
        the file gets read, because a megabyte of `x` does not hash to a spent secret. The property
        the review raised is about the READ, not the outcome: "a device, FIFO or very large file can
        block cleanup or raise an uncaught resource exception while the issuer lock is held". So this
        watches the read itself.
        """
        big = tmp_path / "secret"
        big.write_text("x" * (_enrolment.MOST_A_RESIDUE_CAN_BE * 2), encoding="ascii")
        real_read = os.read
        asked = []

        def watched(fd, size):
            asked.append(size)
            return real_read(fd, size)

        monkeypatch.setattr(_enrolment.os, "read", watched)
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == []
        monkeypatch.undo()

        # Either it refused the file before reading it, or it read it with a bound. Not unbounded,
        # and not the file's own size.
        assert all(n <= _enrolment.MOST_A_RESIDUE_CAN_BE for n in asked), \
            f"the cleanup read without a bound: {asked}"
        assert big.is_file()

    def test_a_residue_that_is_not_a_regular_file_is_not_removed(self, tmp_path):
        """A directory stands in for the device or FIFO the review named.

        Honest about what this does and does not establish: on Windows `os.open` refuses a directory
        outright, so the `S_ISREG` check is not what makes this pass here — the open does. On POSIX
        the open succeeds and `S_ISREG` is the deciding guard. The outcome asserted is the same on
        both, which is why the test is worth having, but a counter-check that removes `S_ISREG`
        cannot go red on this platform and is named as such in the counter-check log.
        """
        (tmp_path / "secret").mkdir()
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == []
        assert (tmp_path / "secret").is_dir()

    def test_a_request_whose_secret_field_is_not_a_string_is_left_alone(self, tmp_path):
        for body in ('{"secret": 7}', '{"secret": null}', '{"secret": {"a": 1}}', '[]', '"x"'):
            (tmp_path / "request.json").write_text(body, encoding="ascii")
            assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == [], body
            assert (tmp_path / "request.json").is_file(), body


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
        (tmp_path / "secret").write_text(SPENT, encoding="ascii")
        (tmp_path / "request.json").write_text(json.dumps({"secret": SPENT}), encoding="ascii")
        (tmp_path / "cert.pem").write_text("c", encoding="ascii")
        assert sorted(_enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT))) \
            == ["request.json", "secret"]
        assert not (tmp_path / "secret").exists()
        assert not (tmp_path / "request.json").exists()
        assert (tmp_path / "cert.pem").is_file(), "it removed something it was not asked to"

    def test_it_removes_only_the_two_names(self, tmp_path):
        # Every one of them holds the spent value, so only the NAMES can save the others.
        for name in ("cert.pem", "key.pem", "inventory.json", "ca.key", "secret"):
            (tmp_path / name).write_text(SPENT, encoding="ascii")
        (tmp_path / "request.json").write_text(json.dumps({"secret": SPENT}), encoding="ascii")
        _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT))
        left = sorted(p.name for p in tmp_path.iterdir())
        assert left == ["ca.key", "cert.pem", "inventory.json", "key.pem"]

    def test_a_read_only_secret_is_still_removed(self, tmp_path):
        """The secret is written 0400 and a read-only file cannot be unlinked on Windows."""
        s = tmp_path / "secret"
        s.write_text(SPENT, encoding="ascii")
        os.chmod(s, 0o400)
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == ["secret"]
        assert not s.exists()


class TestRepeatedCleanupIsSafe:
    """S5. Called again, on an empty directory, on one that never existed."""

    def test_again_on_the_same_directory(self, tmp_path):
        (tmp_path / "secret").write_text(SPENT, encoding="ascii")
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == ["secret"]
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == []
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == []

    def test_on_a_directory_that_does_not_exist(self, tmp_path):
        assert _enrolment.forget_a_spent_secret(tmp_path / "never-existed", _spent(SPENT)) == []

    def test_on_a_path_that_is_a_file(self, tmp_path):
        f = tmp_path / "a-file"
        f.write_text("x", encoding="ascii")
        assert _enrolment.forget_a_spent_secret(f, _spent(SPENT)) == []

    def test_it_never_raises_even_when_the_name_is_a_directory(self, tmp_path):
        (tmp_path / "secret").mkdir()
        assert _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT)) == []
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
        secret.write_text(SPENT, encoding="ascii")
        handle = open(secret, "rb")
        try:
            if os.name != "nt":
                os.chmod(tmp_path, 0o500)          # cannot unlink from a non-writable directory
            try:
                gone = _enrolment.forget_a_spent_secret(tmp_path, _spent(SPENT))
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
        """The first version searched for the removal STARTING AT the commit, so a call inserted
        BEFORE the commit was invisible to it: the counter-check that reverses the order left
        this test green, and the only thing that went red was an unrelated occurrence count.

        It now reads the success path as a whole and requires exactly one removal in it, after
        the commit. "There exists a commit somewhere before a removal" was never the property.
        """
        body = ISSUER[ISSUER.index("def enroll("):ISSUER.index("def renew(")]
        success = body[body.index("matches = ["):]
        assert success.count("self._forget_a_spent_secret(entry)") == 1, \
            "more than one removal on the success path; which runs first is then not stated"
        commit = success.index('self._commit(inventory, "issue")')
        forget = success.index("self._forget_a_spent_secret(entry)")
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
    """Build a real request the way the REQUESTING side does, in a directory of its own.

    This matters far more than it looks, and the first version of this file got it wrong.

    `make_request` generates the private key in whatever directory it is handed, and the issuer
    delivers the certificate into ITS directory. On a two-host deployment those are different
    machines, so the issuer's directory never holds a `key.pem` -- which is precisely why
    `forget_the_enrolment`'s cert+key guard could never fire there, and therefore why the
    plaintext secret survived every single issuance. The defect IS the separation.

    The first version built the request inside the issuer's own directory. `key.pem` and
    `cert.pem` then both landed there, the old guard PASSED, and the old code removed the secret
    too -- so every S1 test here would have been green against the unfixed product. That is a
    test which cannot fail for the reason it names, and it was found by the counter-check that
    deletes the fix: the suite stayed green on the very test meant to catch it.

    So the secret is CARRIED to a directory belonging to the requesting side, the way the
    operator carries it between the machines, and the request is built there. A sibling
    directory is not a second host; the two-host evidence is the live run. What it does
    reproduce exactly is the one condition the defect needs.
    """
    from agentnode_sdk.pki.issuer import make_request
    folder = Path(folder)
    secret = (folder / "secret").read_text(encoding="ascii").strip()
    requester = folder.parent / (folder.name + "-requester")
    requester.mkdir(parents=True, exist_ok=True)
    (requester / "secret").write_text(secret, encoding="ascii")
    return json.loads(make_request(requester, secret).read_text(encoding="utf-8"))


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
        # S1 says to judge by enumerating the issuer's own directory, so enumerate it. The live
        # run of 2026-10-01 showed exactly this: `secret` before, only `cert.pem` after.
        assert sorted(p.name for p in folder.iterdir()) == ["cert.pem"], \
            "the issuer's own directory holds more than the certificate it delivered"

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


def _and_the_entry_still_enrols(iss, folder, what_failed):
    """The second half of S3, which the first version of this file did exactly once.

    The criterion reads "after each, the untouched entry's secret must still enrol
    successfully". Proving that once, after one of the five refusals, leaves the other four
    asserting only that a file is still on disk -- and a secret can survive as bytes while
    being unusable, because `secret_sha256` was cleared or a consumption was recorded against
    it. So every case ends here, with a real certificate out of the real issuer.
    """
    body = _a_request(folder)
    pem = iss.enroll(body["csr"].encode("ascii"), body["secret"])
    assert b"BEGIN CERTIFICATE" in pem, f"{what_failed} left the entry unable to enrol"
    assert not (folder / "secret").exists(), \
        f"after {what_failed} a successful issuance no longer cleans up"
    return pem


def _with_a_broken_signature(csr_pem: str) -> bytes:
    """A CSR that parses and whose signature does not check out.

    The last byte of the DER lies inside the signature itself, so flipping it leaves every
    ASN.1 length exactly as it was: the request still loads, and only the proof that its sender
    holds the key is destroyed. A request that fails to PARSE is a different case, tested
    separately -- collapsing the two would leave the issuer's dedicated refusal for this one
    unexercised.
    """
    import base64

    body = "".join(ln for ln in csr_pem.strip().splitlines() if "-----" not in ln)
    der = bytearray(base64.b64decode(body))
    der[-1] ^= 0xFF
    out = base64.b64encode(bytes(der)).decode("ascii")
    wrapped = "\n".join(out[i:i + 64] for i in range(0, len(out), 64))
    return ("-----BEGIN CERTIFICATE REQUEST-----\n" + wrapped
            + "\n-----END CERTIFICATE REQUEST-----\n").encode("ascii")


class TestAFailedAttemptKeepsAValidSecret:
    """S3. The five cases the profile enumerates, each ending in a proven enrolment.

    The profile names them: a malformed request, a request whose signature does not verify, a
    secret that belongs to no entry, an expired secret, and a crash between presenting and
    committing. The first submission covered three, and proved the "still enrols" half once, at
    the end, for one of them. That is the reviewer's finding F-S3-INCOMPLETE-FAILURE-MATRIX and
    both halves of it are closed here.
    """

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
        _and_the_entry_still_enrols(iss, folder, "a secret belonging to no entry")

    def test_a_request_whose_signature_does_not_verify(self, stand):
        """The issuer has a dedicated refusal for this: "the request's own signature does not
        verify, so it does not show that its sender holds the key". It fires before anything
        looks the secret up, so this case proves the earliest exit leaves the secret alone.
        """
        from agentnode_sdk.pki.issuer import IssuanceRefused
        iss, folder = stand
        body = _a_request(folder)
        with pytest.raises(IssuanceRefused) as refused:
            iss.enroll(_with_a_broken_signature(body["csr"]), body["secret"])
        assert "signature does not verify" in str(refused.value)
        assert (folder / "secret").is_file(), "a forged request took the valid secret with it"
        _and_the_entry_still_enrols(iss, folder, "a request whose signature does not verify")

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
        _and_the_entry_still_enrols(iss, folder, "a malformed request")

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
        _and_the_entry_still_enrols(iss, folder, "a wrong secret with a real request")

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
        # The "still enrols" half needs the expiry I injected lifted again, because otherwise the
        # second attempt is refused for the same reason and proves nothing about consumption.
        # Lifting it is NOT undoing a consumption: `secret_sha256` is asserted intact first, so
        # what is shown is that the refusal cost the secret nothing but its clock.
        inventory = json.loads(path.read_text(encoding="utf-8"))
        assert inventory["entries"]["worker/w1"]["secret_sha256"], \
            "an expired secret was also consumed"
        assert inventory["entries"]["worker/w1"].get("consumed", []) == []
        inventory["entries"]["worker/w1"]["secret_expires"] = 2.0e9
        path.write_text(json.dumps(inventory), encoding="utf-8")
        _and_the_entry_still_enrols(iss, folder, "an expired secret")

    def test_a_crash_between_presenting_and_committing(self, stand, monkeypatch):
        """The fifth case, and the one the first submission filed under S4 instead.

        The secret has been presented and accepted, the certificate has been built, and the
        process dies before the consumption is durable. Nothing durable may have changed: the
        plaintext is there, `secret_sha256` is intact, no consumption is recorded -- so the
        secret is still LIVE and still enrols. That is the other side of S4's argument for the
        order: this window has to be the survivable one, which it only is because the commit
        comes first and the removal second.
        """
        from agentnode_sdk.pki.issuer import Issuer
        iss, folder = stand

        def the_power_goes_out(self, inventory, what):
            raise RuntimeError("crashed between presenting and committing")

        monkeypatch.setattr(Issuer, "_commit", the_power_goes_out)
        body = _a_request(folder)
        with pytest.raises(RuntimeError):
            iss.enroll(body["csr"].encode("ascii"), body["secret"])
        monkeypatch.undo()

        assert (folder / "secret").is_file(), "the crash took the live secret with it"
        assert not (folder / "cert.pem").exists(), \
            "a certificate was delivered although the consumption never committed"
        entry = json.loads((Path(iss.ca_dir) / "inventory.json").read_text(
            encoding="utf-8"))["entries"]["worker/w1"]
        assert entry["secret_sha256"], "an attempt that never committed consumed the secret"
        assert entry.get("consumed", []) == [], "a consumption is recorded without a commit"
        _and_the_entry_still_enrols(iss, folder, "a crash before the commit")

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


def _a_directory_alias(target: Path, link: Path) -> None:
    """Two spellings of one directory, by whatever means this platform allows.

    On Windows `os.symlink` for a directory needs a privilege this account does not have, but a
    junction via `mklink /J` does not. Measured rather than assumed: the junction and its target
    report identical `st_dev` and `st_ino` here.

    If no alias can be made this **fails** rather than skipping. A skip would quietly retire the
    only test covering a defect an independent review rated CRITICAL, and a skip is not a red test.
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name == "nt":
        import subprocess
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True)
        if link.exists():
            return
        raise AssertionError("no directory alias could be made, so this check did not run: %s"
                             % (done.stderr or done.stdout).strip())
    raise AssertionError("this platform offers no directory alias, so this check did not run")


class TestItAlsoClearsWhatOlderCodeLeftBehind:
    """S1 retrospectively, which the first submission did not do and was marked WARN for.

    The review's `F-S1-PROSPECTIVE-ONLY` said the fix "does not remove plaintext produced by
    older code", so the frozen no-plaintext property stayed incomplete until somebody deleted the
    leftovers by hand. I had argued against closing it, on the grounds that a sweep would make
    one operation reach into directories it was not asked about. **That argument was wrong.**
    `deliver_to` is not a guess: the product wrote it into the inventory itself when the entry was
    created.

    What follows is the guard that makes the sweep safe, tested from both sides: it takes what the
    inventory records as spent, and it leaves everything else alone.
    """

    def _a_second_entry_with_a_leftover(self, iss, folder, instance="w9"):
        """An entry enrolled and then given back a plaintext secret, which is exactly the state
        the OLD code left behind: consumption committed, `secret_sha256` cleared, and the
        cleartext still sitting in the issuer's directory."""
        other = folder.parent / instance
        other.mkdir()
        iss.add("worker", instance, secret_at=other / "secret", deliver_to=other / "cert.pem")
        leftover = (other / "secret").read_text(encoding="ascii").strip()
        body = _a_request(other)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert not (other / "secret").exists()
        (other / "secret").write_text(leftover, encoding="ascii")      # as the old code left it
        return other

    def test_a_leftover_from_an_earlier_issuance_goes_on_the_next_one(self, stand):
        iss, folder = stand
        stale = self._a_second_entry_with_a_leftover(iss, folder)
        assert (stale / "secret").is_file(), "the test did not set up the leftover"
        body = _a_request(folder)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert not (stale / "secret").exists(), \
            "a plaintext from an earlier issuance survived a later successful one"

    def test_but_a_live_secret_belonging_to_another_entry_is_left_alone(self, stand):
        """The entry has never been enrolled, so whatever is in its directory is still live and
        taking it would strand whoever is about to use it."""
        iss, folder = stand
        waiting = folder.parent / "w8"
        waiting.mkdir()
        iss.add("worker", "w8", secret_at=waiting / "secret", deliver_to=waiting / "cert.pem")
        body = _a_request(folder)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        assert (waiting / "secret").is_file(), \
            "an unrelated entry's LIVE secret was deleted by someone else's issuance"

    def test_and_an_entry_given_a_fresh_secret_after_recovery_is_not_touched(self, stand):
        """The dangerous case, through the real recovery API rather than a hand-edited field.

        After `recover_entry` the entry has a consumption on record AND a new live secret. A
        sweep keyed on "has consumed anything" alone would delete it; the guard also requires
        `secret_sha256` to be empty, and recovery sets it.
        """
        iss, folder = stand
        recovered = folder.parent / "w7"
        recovered.mkdir()
        iss.add("worker", "w7", secret_at=recovered / "secret", deliver_to=recovered / "cert.pem")
        body = _a_request(recovered)
        iss.enroll(body["csr"].encode("ascii"), body["secret"])
        iss.recover_entry("worker", "w7", secret_at=recovered / "secret")
        assert (recovered / "secret").is_file(), "recovery did not write a new secret"
        fresh = (recovered / "secret").read_text(encoding="ascii").strip()

        mine = _a_request(folder)
        iss.enroll(mine["csr"].encode("ascii"), mine["secret"])

        assert (recovered / "secret").is_file(), "a freshly recovered live secret was swept away"
        assert (recovered / "secret").read_text(encoding="ascii").strip() == fresh

    def test_a_directory_shared_with_a_live_entry_is_left_alone(self, stand):
        """The worst thing the sweep could do, and it could do it until this test existed.

        Two identities pointed at ONE directory. Nothing stops an operator doing that: `add` takes
        `secret_at` and `deliver_to` from the caller. Entry `a1` is enrolled, so it is spent and
        the sweep is willing to clean its directory. Entry `a2` is then created with a LIVE secret
        in the same place. Sweeping on `a1`'s behalf would delete `a2`'s live secret -- destroying
        a secret somebody is about to use, on behalf of an enrolment that had nothing to do with
        it, and leaving them no way to enrol at all.

        I wrote this case into the reviewer's brief as something to attack, then checked it myself
        before submitting, and it was real. The guard compares directories rather than assuming
        they differ.
        """
        iss, folder = stand
        shared = folder.parent / "shared"
        shared.mkdir()
        iss.add("worker", "a1", secret_at=shared / "secret", deliver_to=shared / "cert.pem")
        spent = _a_request(shared)
        iss.enroll(spent["csr"].encode("ascii"), spent["secret"])
        assert not (shared / "secret").exists(), "a1's own cleanup did not run"

        iss.add("worker", "a2", secret_at=shared / "secret", deliver_to=shared / "cert.pem")
        live = (shared / "secret").read_text(encoding="ascii").strip()

        mine = _a_request(folder)
        iss.enroll(mine["csr"].encode("ascii"), mine["secret"])

        assert (shared / "secret").is_file(), \
            "the sweep deleted a LIVE secret belonging to another entry in the same directory"
        assert (shared / "secret").read_text(encoding="ascii").strip() == live
        # And a2 can still do what the secret is for.
        theirs = _a_request(shared)
        assert b"BEGIN CERTIFICATE" in iss.enroll(theirs["csr"].encode("ascii"), theirs["secret"])

    def test_a_live_secret_reached_through_a_directory_ALIAS_is_left_alone(self, stand):
        """The third review's CRITICAL finding, end to end, and still the right test after the
        fourth review made the guard unnecessary rather than stronger.

        Two identities, one physical directory, reached by two names. The first three designs of
        this cleanup compared paths and deleted by name, so this state destroyed a live secret. The
        current one decides on the file's contents, so the alias is simply not interesting -- but
        the test stays, because what must be true is about the outcome and not about the mechanism.
        """
        iss, folder = stand
        real = folder.parent / "real"
        real.mkdir()
        alias = folder.parent / "alias"
        _a_directory_alias(real, alias)

        # the spent entry names the directory through the ALIAS
        iss.add("worker", "b1", secret_at=alias / "secret", deliver_to=alias / "cert.pem")
        spent = _a_request(alias)
        iss.enroll(spent["csr"].encode("ascii"), spent["secret"])

        # the live entry names THE SAME directory by its real path
        iss.add("worker", "b2", secret_at=real / "secret", deliver_to=real / "cert.pem")
        live = (real / "secret").read_text(encoding="ascii").strip()

        mine = _a_request(folder)
        iss.enroll(mine["csr"].encode("ascii"), mine["secret"])

        assert (real / "secret").is_file(), \
            "the sweep deleted a LIVE secret through a directory alias"
        assert (real / "secret").read_text(encoding="ascii").strip() == live
        theirs = _a_request(real)
        assert b"BEGIN CERTIFICATE" in iss.enroll(theirs["csr"].encode("ascii"), theirs["secret"])

    def test_a_live_entry_whose_directory_appeared_after_the_spent_one(self, stand):
        """The state behind the fourth review's CRITICAL finding, as far as a test can build it.

        That finding was not really a race. The guard it replaced sampled filesystem identities into
        a set: a directory absent at that moment contributed a *string* key while the same directory
        present later produced a *(st_dev, st_ino)* tuple, so it could not match itself and a live
        secret in it was deleted.

        **What this test cannot do is construct the intra-call window**, where the directory appears
        between the sampling and the unlink inside one `enroll`. That needs another thread, and a
        test that pretends otherwise would be theatre. What makes the window harmless is that there
        is no sampling step left at all -- the decision is the file's content -- and that is tested
        directly in `TestTheDecisionIsAboutContentNotAboutThePath`, which hands the function a live
        secret in the directory it is cleaning and requires it to survive.

        This covers the ordering a test CAN establish: the spent entry existed and was consumed
        before the live entry's directory existed at all.
        """
        iss, folder = stand
        spent_dir = folder.parent / "spent"
        spent_dir.mkdir()
        iss.add("worker", "c1", secret_at=spent_dir / "secret", deliver_to=spent_dir / "cert.pem")
        used = _a_request(spent_dir)
        iss.enroll(used["csr"].encode("ascii"), used["secret"])

        late = folder.parent / "late"
        assert not late.exists(), "the directory was supposed to appear later"
        late.mkdir()
        iss.add("worker", "c2", secret_at=late / "secret", deliver_to=late / "cert.pem")
        theirs = (late / "secret").read_text(encoding="ascii").strip()

        mine = _a_request(folder)
        iss.enroll(mine["csr"].encode("ascii"), mine["secret"])

        assert (late / "secret").is_file(), \
            "a live secret in a late-appearing directory was swept away"
        assert (late / "secret").read_text(encoding="ascii").strip() == theirs

    def test_the_sweep_runs_after_the_commit_like_the_other_one(self):
        body = ISSUER[ISSUER.index("def enroll("):ISSUER.index("def renew(")]
        success = body[body.index("matches = ["):]
        assert success.index('self._commit(inventory, "issue")') \
            < success.index("self._forget_every_spent_secret(inventory)")

    def test_it_reports_what_it_removed_and_names_the_entry(self, tmp_path):
        """A sweep that returns nothing cannot be logged, and one that returns bare filenames
        cannot say whose they were."""
        iss = _issuer(tmp_path)
        one = tmp_path / "e1"
        one.mkdir()
        (one / "secret").write_text(SPENT, encoding="ascii")
        inventory = {"entries": {"worker/w1": {
            "consumed": [{"transaction": "x",
                          "secret_sha256": _enrolment.digest_of_a_secret(SPENT)}],
            "secret_sha256": "",
            "deliver_to": str(one / "cert.pem")}}}
        assert iss._forget_every_spent_secret(inventory) == ["worker/w1/secret"]
        assert iss._forget_every_spent_secret(inventory) == [], "it is not idempotent"

    def test_and_an_entry_with_no_recorded_consumption_is_not_swept(self, tmp_path):
        """`consumed` empty means nothing is known to be spent, so there is no digest that could
        authorise a removal and nothing may be removed."""
        iss = _issuer(tmp_path)
        one = tmp_path / "e2"
        one.mkdir()
        (one / "secret").write_text(SPENT, encoding="ascii")
        inventory = {"entries": {"worker/w2": {"consumed": [], "secret_sha256": "",
                                              "deliver_to": str(one / "cert.pem")}}}
        assert iss._forget_every_spent_secret(inventory) == []
        assert (one / "secret").is_file()


# --------------------------------------------------------------------------- the operator's text

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "separate-worker-host"


class TestTheOperatorTextDescribesWhatIsImplemented:
    """S8, as something that can break rather than something I read once.

    Until now this criterion rested entirely on reading the two install scripts, which means no
    counter-check could make it go red and the claim "the documents describe what is
    implemented" had nothing holding it. The drift it guards against is not hypothetical: the
    control-plane script had been asserting the OPPOSITE of the truth -- "the product does not
    remove them" about a directory the product does clean -- and that sentence survived until
    this arc went looking for it.
    """

    def test_the_control_plane_script_says_who_clears_the_issuer_side_copy(self):
        text = (DEPLOY / "install-control-plane.sh").read_text(encoding="utf-8")
        assert "the issuer now clears it after committing the consumption" in text, \
            "the script no longer describes who removes the issuer's own plaintext copy"

    def test_the_worker_script_says_the_product_removes_its_own_copies(self):
        text = (DEPLOY / "install-worker-host.sh").read_text(encoding="utf-8")
        assert "The product removes its own copies" in text

    def test_and_still_names_what_the_operator_is_left_responsible_for(self):
        """The other failure mode of S8: a script that claims a deletion the product performs
        and goes quiet about the copies only the operator knows it made."""
        text = (DEPLOY / "install-worker-host.sh").read_text(encoding="utf-8")
        assert "Delete those." in text, \
            "the operator is no longer told about the copies the product cannot see"

    def test_no_script_states_as_fact_that_the_plaintext_is_left_behind(self):
        """The exact words that were wrong. They may still appear as a QUOTATION inside the
        correction that replaced them, so this requires the quoting frame wherever they occur.

        The comments are hard-wrapped, so the frame and the words it quotes sit on different
        lines; unwrapping first is the difference between testing the claim and testing where
        somebody happened to break the line.
        """
        for name in ("install-control-plane.sh", "install-worker-host.sh"):
            text = (DEPLOY / name).read_text(encoding="utf-8")
            flat = " ".join(" ".join(
                ln.lstrip().lstrip("#").strip() for ln in text.splitlines()).split())
            if "product does not remove them" in flat:
                assert 'earlier comment here said "the product does not remove them"' in flat, \
                    f"{name} states as fact what is only true of the other directory"
