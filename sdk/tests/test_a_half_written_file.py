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

    @pytest.mark.skipif(not securedir.SUPPORTED,
                        reason="the descriptor-relative writer is POSIX-only")
    def test_and_neither_does_the_descriptor_relative_one(self, tmp_path):
        source = inspect.getsource(securedir.write_secret)
        assert '"." + name + ".new"' not in source, (
            "write_secret is back to one fixed temp name per file, which two writers share")
        assert "secrets.token_hex" in source, (
            "the temp name has to be unique to THIS write")
        assert "os.unlink(tmp" not in source, (
            "the pre-unlink was the window that made the fixed name lose data; it went with it")


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
