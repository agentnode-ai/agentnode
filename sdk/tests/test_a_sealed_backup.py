"""A backup of a gateway is its private keys, so it is encrypted or it is not taken.

The frozen data-operations criterion `P1-NO-SECRET-REACHES-A-LOG-AN-AUDIT-OR-A-REFUSAL` failed
on exactly this: the archive put the metering signing key and the TLS private key into an
unencrypted tar. The archive cannot stop containing them -- a restore without them produces a
gateway that cannot verify its own record of use, cannot be reached over the certificate its
clients pinned, and refuses every job because it cannot read its own policy. So the archive is
sealed, and every way that can go wrong ends before a byte of plaintext exists.

What is asserted here, in the order it matters:

* it is an ESTABLISHED construction, not an invented one;
* the key is not in the archive, and cannot be, because the archive is a directory the key is
  not in and this is checked rather than promised;
* the header is AUTHENTICATED, so an archive cannot be relabelled, re-pointed at another
  gateway, or have its manifest digest swapped;
* wrong key, tampered bytes, a truncated file and an archive belonging to something else each
  end in the same refusal, before anything is written;
* nothing anywhere -- header, output, error text -- carries the key.
"""
from __future__ import annotations

import inspect
import json

import pytest

from agentnode_sdk.gateway import archive


@pytest.fixture()
def key():
    return archive.new_key()


@pytest.fixture()
def sealed(key):
    plain = b"this stands for a tar of a gateway's whole state, keys and all\n" * 64
    return plain, archive.seal(plain, key, about={"gateway": "g-under-test",
                                                  "manifest_sha256": "a" * 64})


# ------------------------------------------------------------------ nothing invented


class TestItUsesAnEstablishedConstruction:

    def test_the_cipher_comes_from_the_library_this_project_already_depends_on(self):
        source = inspect.getsource(archive)
        assert "from cryptography.hazmat.primitives.ciphers.aead import AESGCM" in source
        assert archive.ALGORITHM == "AES-256-GCM"

    def test_and_nothing_here_rolls_its_own(self):
        """A test that would fail the day somebody reaches for xor, a home-made MAC, or a
        key schedule. Not a proof of correctness -- a tripwire on the shape of the mistake."""
        source = inspect.getsource(archive).lower()
        for never in ("def _encrypt", "def _xor", "hmac.new(", "^ key", "def _kdf"):
            assert never not in source, never

    def test_the_key_is_the_length_the_cipher_wants_and_the_nonce_is_fresh(self, key):
        assert len(key) == 32
        one = archive.seal(b"x", key, about={})
        other = archive.seal(b"x", key, about={})
        assert one != other, (
            "two archives of the same bytes under the same key are identical, which means the "
            "nonce is not fresh -- and a repeated nonce is how this construction fails badly")


# ------------------------------------------------------------------ the key is elsewhere


class TestTheKeyIsNotInTheArchive:

    def test_the_sealed_bytes_do_not_contain_it(self, key, sealed):
        _plain, box = sealed
        assert key not in box
        assert key.hex().encode("ascii") not in box

    def test_nor_does_the_header_anybody_can_read(self, key, sealed):
        _plain, box = sealed
        said = json.dumps(archive.header_of(box))
        assert key.hex() not in said
        assert archive.key_id(key) in said, (
            "the header must say WHICH key it wants, or a person holding three archives and two "
            "keys has to guess")

    def test_and_the_key_id_is_not_the_key(self, key):
        named = archive.key_id(key)
        assert named not in key.hex()
        assert len(named) < len(key.hex())

    def test_a_key_file_is_owner_only_and_refuses_to_overwrite(self, tmp_path, key):
        import os
        import stat

        where = tmp_path / "backup.key"
        archive.write_key(where, key)
        if os.name == "posix":
            assert stat.S_IMODE(where.stat().st_mode) == 0o600
        assert archive.read_key(where) == key
        with pytest.raises(OSError):
            archive.write_key(where, archive.new_key())

    def test_and_a_missing_key_is_its_own_answer(self, tmp_path):
        """Distinct from a damaged archive: "look in your password manager" is the remedy for
        one of those and not for the other."""
        with pytest.raises(archive.NoKey):
            archive.read_key(tmp_path / "not-here")


# ------------------------------------------------------------------ what fails closed


class TestEveryWayThisGoesWrongEndsBeforeAnythingIsRestored:

    def test_the_right_key_opens_it_and_gives_back_exactly_what_went_in(self, key, sealed):
        plain, box = sealed
        assert archive.open_sealed(box, key) == plain

    def test_a_WRONG_key_does_not(self, sealed):
        _plain, box = sealed
        with pytest.raises(archive.CannotOpen):
            archive.open_sealed(box, archive.new_key())

    def test_a_TAMPERED_archive_does_not(self, key, sealed):
        _plain, box = sealed
        middle = len(box) // 2
        broken = box[:middle] + bytes([box[middle] ^ 0x01]) + box[middle + 1:]
        with pytest.raises(archive.CannotOpen):
            archive.open_sealed(broken, key)

    def test_a_TRUNCATED_archive_does_not(self, key, sealed):
        _plain, box = sealed
        for cut in (1, 16, len(box) // 3, len(box) - 1):
            with pytest.raises(archive.CannotOpen):
                archive.open_sealed(box[:-cut], key)

    def test_a_RELABELLED_header_does_not(self, key, sealed):
        """The header is not encrypted -- a restore has to know which key it needs. It IS
        authenticated, so changing one byte of it breaks the whole archive."""
        _plain, box = sealed
        magic, line, rest = box.split(b"\n", 2)
        said = json.loads(line)
        said["gateway"] = "some-other-gateway"
        re_labelled = magic + b"\n" + json.dumps(said, sort_keys=True).encode("utf-8") + b"\n" + rest
        with pytest.raises(archive.CannotOpen):
            archive.open_sealed(re_labelled, key)

    def test_an_archive_belonging_to_SOMETHING_ELSE_does_not(self, key):
        """Perfectly valid, correctly sealed, and not the one that was asked for."""
        other = archive.seal(b"another gateway's state", key,
                             about={"gateway": "not-this-one", "manifest_sha256": "b" * 64})
        with pytest.raises(archive.CannotOpen):
            archive.open_sealed(other, key, expect={"gateway": "g-under-test"})
        with pytest.raises(archive.CannotOpen):
            archive.open_sealed(other, key, expect={"manifest_sha256": "a" * 64})

    def test_and_something_that_is_not_an_archive_at_all_does_not(self, key):
        for rubbish in (b"", b"not ours", b"AGENTNODE-SEALED-1", b"AGENTNODE-SEALED-1\n{}\n"):
            with pytest.raises(archive.CannotOpen):
                archive.open_sealed(rubbish, key)

    def test_and_every_one_of_those_says_the_same_thing(self, key, sealed):
        """Telling them apart tells whoever holds the archive which it is, and none of those
        answers helps the person who is entitled to it."""
        _plain, box = sealed
        middle = len(box) // 2
        reasons = set()
        for broken in (box[:middle] + bytes([box[middle] ^ 0x01]) + box[middle + 1:],
                       box[:-8]):
            try:
                archive.open_sealed(broken, key)
            except archive.CannotOpen as shut:
                reasons.add(str(shut))
        assert len(reasons) == 1, reasons


# ------------------------------------------------------------------ nothing leaks


class TestNothingSaysTheKeyOutLoud:

    def test_not_in_a_refusal(self, key, sealed):
        _plain, box = sealed
        wrong = archive.new_key()
        try:
            archive.open_sealed(box, wrong)
        except archive.CannotOpen as shut:
            said = str(shut)
        assert key.hex() not in said and wrong.hex() not in said
        assert key.hex()[:16] not in said and wrong.hex()[:16] not in said

    def test_not_in_what_the_command_prints(self, tmp_path, capsys, key):
        where, plain, box = tmp_path / "k", tmp_path / "plain", tmp_path / "box"
        archive.write_key(where, key)
        plain.write_bytes(b"some state")
        assert archive.main(["seal", "--in", str(plain), "--out", str(box),
                             "--key", str(where), "--gateway", "g"]) == 0
        assert archive.main(["open", "--in", str(box), "--out", str(tmp_path / "back"),
                             "--key", str(where)]) == 0
        said = capsys.readouterr().out
        assert key.hex() not in said
        assert (tmp_path / "back").read_bytes() == b"some state"

    def test_and_the_command_exits_non_zero_when_it_cannot_open_one(self, tmp_path, capsys, key):
        where, box = tmp_path / "k", tmp_path / "box"
        archive.write_key(where, archive.new_key())
        box.write_bytes(archive.seal(b"state", key, about={}))
        assert archive.main(["open", "--in", str(box), "--out", str(tmp_path / "back"),
                             "--key", str(where)]) == 1
        assert not (tmp_path / "back").exists(), (
            "something was written despite the archive not opening")
        assert "PROBLEM" in capsys.readouterr().out

    def test_and_a_missing_key_file_exits_differently_from_a_broken_archive(self, tmp_path,
                                                                           capsys, key):
        box = tmp_path / "box"
        box.write_bytes(archive.seal(b"state", key, about={}))
        assert archive.main(["open", "--in", str(box), "--out", str(tmp_path / "back"),
                             "--key", str(tmp_path / "nowhere")]) == 2
        assert "PROBLEM" in capsys.readouterr().out


class TestCryptoShreddingIsTheDeletionMechanismForBackups:
    """`ALPHA-R2-DATAOPS-0009` P4: "prior backups and handed-out exports survive".

    True, and the two halves have different answers. This class is the first half; the second is
    in `docs/what-is-kept.md` and is a limitation rather than a mechanism, because a file on
    somebody else's laptop is outside the boundary and no amount of design reaches it.

    What IS reachable: every archive sealed under a key is unreadable once that key is gone. The
    property is already there -- it is why sealing exists -- and what was missing is that nothing
    named it as the deletion mechanism, so nothing tested it as one.
    """

    def test_destroying_the_key_makes_the_archive_unreadable(self, tmp_path):
        import os

        from agentnode_sdk.gateway import archive

        key = archive.new_key()
        secret = b"acct-deadbeef the customer who asked to be deleted"
        sealed = archive.seal(secret, key, about={"gateway": "g", "manifest_sha256": "0" * 64})
        assert archive.open_sealed(sealed, key) == secret        # the control: it opened

        key_file = tmp_path / "backup.key"
        key_file.write_bytes(key)
        os.unlink(key_file)                                      # destroyed
        del key

        with pytest.raises(archive.CannotOpen):
            archive.open_sealed(sealed, archive.new_key())

    def test_and_it_shreds_EVERY_archive_under_that_key_not_one_account(self, tmp_path):
        """The cost, asserted rather than only written down. An operator reading the documented
        sequence -- new key, fresh backup, verify, then destroy the old one -- needs this to be
        the reason for it, not a caveat at the bottom."""
        from agentnode_sdk.gateway import archive

        key = archive.new_key()
        archives = [archive.seal(("account %d" % n).encode(), key,
                                 about={"gateway": "g", "manifest_sha256": "0" * 64})
                    for n in range(3)]
        assert all(archive.open_sealed(a, key) for a in archives)

        other = archive.new_key()
        for a in archives:
            with pytest.raises(archive.CannotOpen):
                archive.open_sealed(a, other)
