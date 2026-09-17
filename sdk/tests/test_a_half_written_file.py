"""No file this gateway keeps is ever half-written, and no two writers share a temp name.

Every one of these comes from one drill: two deletions of the same account, at once. It failed on
Linux with `FileNotFoundError` and locally with "the record of this gateway's accounts is not
readable as JSON", and both were the same shape three times over:

  * `securedir.write_secret` renamed over -- correct -- from the FIXED name `.<file>.new`. Two
    writers share it: one unlinks what the other just created, one renames a path the other has
    already renamed away, and in the case that raises nothing, one writer's whole file silently
    replaces the other's.
  * `_write_private_by_path`, the fallback, truncated in place. A reader arriving mid-write sees
    an empty or partial file -- and `accounts.py` refuses to run anything on a record of
    customers it cannot parse, correctly. So one concurrent write could stop the gateway.
  * `activation.py` renamed over with a unique name and no RETRY, and on Windows a rename fails
    while any reader holds the target. The measurement that decides whether this gateway may take
    work at all could therefore simply not be written, and the caller saw `PermissionError`.

A concurrency test can only ever say "it did not happen this time". These say what has to be true
every time, deterministically: the write is a rename, the rename is retried, the temp name is
nobody else's, and a write that fails leaves what was there before.
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from agentnode_sdk.gateway import accounts as accounts_module
from agentnode_sdk.gateway import activation, filelock, securedir
from agentnode_sdk.gateway.identity import GatewayState


@pytest.fixture()
def state(tmp_path):
    held = GatewayState(str(tmp_path / "state"), version="test")
    try:
        yield held
    finally:
        held.close()


# ------------------------------------------------------------------ it is a rename


class TestAPrivateFileIsReplacedRatherThanTruncated:

    def test_the_fallback_writer_goes_through_the_atomic_one(self):
        """Structural, and deliberately so: the property is about HOW it writes, and a timing
        test can only ever report that it did not go wrong on this run."""
        source = inspect.getsource(GatewayState._write_private_by_path)
        assert "atomically(" in source, (
            "the private writer no longer renames over. Truncating in place means a reader "
            "arriving mid-write gets an empty file, and this gateway refuses to run anything "
            "on a customer record it cannot parse.")
        assert ".write_text(" not in source

    def test_a_write_that_fails_leaves_what_was_there_before(self, state, monkeypatch):
        """The whole point of renaming over, asserted without a race."""
        state._write_private("accounts.json", json.dumps({"acct-" + "a" * 16: {}}))
        before = (state.root / "accounts.json").read_text(encoding="utf-8")

        def explode(*_args, **_kwargs):
            raise OSError("the disk went away")

        monkeypatch.setattr(filelock, "replace_with_retry", explode)
        with pytest.raises(OSError):
            state._write_private_by_path("accounts.json", "{}")

        assert (state.root / "accounts.json").read_text(encoding="utf-8") == before, (
            "a failed write left the previous content damaged")

    def test_and_leaves_no_temp_file_behind(self, state, monkeypatch):
        def explode(*_args, **_kwargs):
            raise OSError("the disk went away")

        monkeypatch.setattr(filelock, "replace_with_retry", explode)
        with pytest.raises(OSError):
            state._write_private_by_path("accounts.json", "{}")

        leftovers = [p.name for p in state.root.iterdir() if p.name.startswith(".accounts")]
        assert not leftovers, leftovers


# ------------------------------------------------------------------ nobody shares a temp name


class TestNoTwoWritersShareATempName:

    def test_the_atomic_writer_takes_a_name_nobody_else_has(self, tmp_path):
        """Two writes in a row must not reuse one name; `mkstemp` is what makes that true."""
        seen = []
        real = os.replace

        def watch(tmp, path):
            seen.append(str(tmp))
            return real(tmp, path)

        import agentnode_sdk.gateway.filelock as under_test

        original = under_test.os.replace
        under_test.os.replace = watch
        try:
            for _ in range(5):
                filelock.atomically(tmp_path / "thing.json", "{}")
        finally:
            under_test.os.replace = original

        assert len(set(seen)) == len(seen), (
            "two writes used the same temp name: %s. One name shared by every writer of a file "
            "is how two of them at once lose one of the writes." % seen)

    def test_and_neither_does_the_descriptor_relative_one(self):
        """POSIX-only CODE, read as text, so this runs everywhere.

        The first version of this skipped itself off POSIX, and the skip is where it went wrong:
        it asserted that `os.unlink(tmp` appears nowhere, which also forbids the CLEANUP unlink
        that stops a failed write leaking a temp file -- a thing the writer should do. It was
        green on the machine it was written on, because it never ran there, and red the first
        time Linux saw it. A property about source text does not need the platform it describes.
        """
        source = inspect.getsource(securedir.write_secret)
        assert '"." + name + ".new"' not in source, (
            "write_secret is back to one fixed temp name per file, which two writers share")
        assert "secrets.token_hex" in source, (
            "the temp name has to be unique to THIS write")

        # What was actually wrong: the unlink came BEFORE the create, to make room for a name
        # somebody else might be using. Between the two, the other writer's temp file was gone.
        # An unlink that cleans up AFTER a failure is a different thing and is wanted.
        # The CALL, not the word: the docstring above it explains the pre-unlink that went, and
        # a test that matched prose would forbid saying why.
        before_it = source[:source.index("os.open(")]
        assert "os.unlink(" not in before_it, (
            "write_secret unlinks the temp name before creating it, which is the window that "
            "made the shared name lose data")


# ------------------------------------------------------------------ the rename is retried


class TestEveryRenameOverIsRetried:
    """On Windows a rename fails while a reader holds the target. Once, for everybody."""

    def test_the_shared_helper_retries_and_then_gives_up(self, tmp_path, monkeypatch):
        tries = []
        real = os.replace

        def stubborn(tmp, path):
            tries.append(1)
            if len(tries) < 3:
                raise PermissionError(13, "in use")
            return real(tmp, path)

        monkeypatch.setattr(filelock.os, "replace", stubborn)
        near = tmp_path / "near"
        near.write_text("x", encoding="utf-8")
        filelock.replace_with_retry(str(near), str(tmp_path / "thing"))

        assert len(tries) == 3
        assert (tmp_path / "thing").read_text(encoding="utf-8") == "x"

    def test_and_a_target_held_for_ever_still_raises(self, tmp_path, monkeypatch):
        """Retrying is not swallowing. A rename that never lands is still a failure."""
        monkeypatch.setattr(filelock, "REPLACE_SECONDS", 0.05)

        def never(*_args):
            raise PermissionError(13, "in use")

        monkeypatch.setattr(filelock.os, "replace", never)
        with pytest.raises(PermissionError):
            filelock.replace_with_retry(str(tmp_path / "a"), str(tmp_path / "b"))

    def test_and_the_snapshot_that_decides_uses_it_too(self):
        """`activation.py` writes the measurement the readiness gate reads. It had its own
        rename with no retry, so the one file that decides whether this gateway takes work at
        all was the one that could silently fail to be written."""
        source = inspect.getsource(activation)
        assert "replace_with_retry(" in source
        assert source.count("os.replace(") == 0, (
            "one of the activation writers renames without the retry")


# ------------------------------------------------------------------ a read-modify-write


class TestChangingAnAccountHoldsTheFile:
    """An atomic write does not make a read-modify-write safe.

    Two `forget()` calls both load, each removes its own account, and whichever stores second
    puts the other one back. The lock has to be on the FILE: `Accounts` is built fresh by several
    callers -- deleting a customer builds its own -- so a lock belonging to the object guards
    nothing between them.
    """

    def test_the_gateway_gives_its_accounts_a_lock_on_the_accounts_file(self, state):
        held = state.accounts._guard()
        assert isinstance(held, filelock.ProcessLock), type(held)
        # `ProcessLock` holds a lock file BESIDE the thing it guards, so what is asserted is
        # which file it belongs to rather than that it is that file.
        assert Path(getattr(held, "path", "")).name.startswith(accounts_module.ACCOUNTS_NAME), (
            "the lock is not on the file the accounts are in: %s" % getattr(held, "path", ""))

    def test_and_every_change_to_one_is_made_inside_it(self):
        for name in ("create", "suspend", "restore", "forget"):
            source = inspect.getsource(getattr(accounts_module.Accounts, name))
            assert "with self._guard():" in source, (
                "Accounts.%s loads and stores without holding the file, so two of them at once "
                "lose one of the changes" % name)

    def test_and_it_is_really_taken_when_one_is_changed(self, state, monkeypatch):
        """Not "the code says so": the lock is entered when the change is made."""
        taken = []
        real = filelock.ProcessLock.__enter__

        def watch(self):
            taken.append(str(self.path))
            return real(self)

        monkeypatch.setattr(filelock.ProcessLock, "__enter__", watch)
        state.accounts.create(name="alice")
        assert any(Path(p).name.startswith(accounts_module.ACCOUNTS_NAME) for p in taken), taken

    def test_and_an_accounts_store_with_no_lock_still_works(self, tmp_path):
        """The guard is optional, because a caller with a dict for storage has no file to lock
        -- and a default that quietly did nothing on the real path is what this exists to stop,
        so the real path is asserted above rather than assumed."""
        kept = {}
        loose = accounts_module.Accounts(
            read=lambda name: kept.get(name),
            write=lambda name, text: kept.__setitem__(name, text))
        made = loose.create(name="somebody")
        assert loose.get(made.account_id).name == "somebody"


class TestAFileThatLostItsNameWhileItWasBeingRead:
    """Zero names is not a second door, and the message that said so was false whenever it fired.

    Found on Linux CI under the concurrent-deletion drill: `tokens.json has 0 names. A second hard
    link is a second door into the same bytes` — for a file with NO names, which cannot have a
    second one. What had actually happened is the ordinary shape of an atomic write seen from the
    reading side: the writer renamed a new file over this one between our `open` and our `fstat`.

    Nothing is loosened by separating the two. A file with no names cannot be opened afresh by
    anybody, so "no second door" holds MORE strongly there than in the one-name case. What is
    left is the risk of reading superseded bytes, and the answer to that is to read again.
    """

    def _only_on_posix(self):
        import pytest

        from agentnode_sdk.gateway import securedir

        try:
            securedir._require_supported()
        except Exception as exc:                                  # noqa: BLE001
            pytest.skip("the secure-directory checks are POSIX-only here: %s" % exc)

    def test_zero_names_is_reported_as_a_replacement_not_as_an_intrusion(self):
        import os

        from agentnode_sdk.gateway import securedir

        self._only_on_posix()

        class NoNames:
            st_mode = 0o100600
            st_uid = os.getuid() if hasattr(os, "getuid") else 0
            st_nlink = 0

        with __import__("pytest").raises(securedir.BeingReplaced) as told:
            securedir._judge(NoNames(), "tokens.json", expect_dir=False)
        assert "replaced" in str(told.value)
        assert "hard link" not in str(told.value), (
            "the explanation is false whenever it fires: a file with no names has no second one")

    def test_and_two_names_is_still_refused(self):
        """The half that must NOT move. A second hard link is the thing this check exists for."""
        import os

        from agentnode_sdk.gateway import securedir

        self._only_on_posix()

        class TwoNames:
            st_mode = 0o100600
            st_uid = os.getuid() if hasattr(os, "getuid") else 0
            st_nlink = 2

        with __import__("pytest").raises(securedir.InsecureState):
            securedir._judge(TwoNames(), "tokens.json", expect_dir=False)

    def test_a_read_that_keeps_being_replaced_fails_closed_rather_than_spinning(self):
        """Retrying is the answer to a replacement; retrying for ever is not an answer at all."""
        import os

        from agentnode_sdk.gateway import securedir

        self._only_on_posix()

        def always_replaced(info, what, expect_dir=False):
            raise securedir.BeingReplaced("%s was replaced while it was being read" % what)

        was, securedir._judge = securedir._judge, always_replaced
        try:
            import tempfile

            root = tempfile.mkdtemp()
            os.chmod(root, 0o700)
            with open(os.path.join(root, "tokens.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
            os.chmod(os.path.join(root, "tokens.json"), 0o600)
            fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with __import__("pytest").raises(securedir.UnverifiableState) as told:
                    securedir.read_secret(fd, "tokens.json")
                assert "every attempt" in str(told.value)
            finally:
                os.close(fd)
        finally:
            securedir._judge = was
