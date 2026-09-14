"""Where a program on somebody's own machine keeps its device token.

A file with tight permissions is not a bad answer, and it is not the one that survives the
ordinary accidents: a backup that copies dotfiles, a support request that says "paste your
config", a synced home directory, a screen share over a terminal. A token in a file is a token in
every copy of that file.

So the platform's keyring is used when there is one. What these tests are mostly about is the
other case -- because the tempting thing to do when there is no keyring is to write the file
anyway and say nothing, and that is a fallback nobody decided on.
"""
from __future__ import annotations

import pytest

from agentnode_sdk.access import credentials
from agentnode_sdk.gateway.connections import ConnectionStore, SavedGateway


class AKeyring:
    """Stands in for the platform's. Holds things; can be asked what it holds."""

    def __init__(self):
        self.kept = {}

    def set_password(self, service, name, token):
        self.kept[(service, name)] = token

    def get_password(self, service, name):
        return self.kept.get((service, name))

    def delete_password(self, service, name):
        del self.kept[(service, name)]


@pytest.fixture()
def with_a_keyring(monkeypatch):
    ring = AKeyring()
    monkeypatch.setattr(credentials, "_keyring", lambda: ring)
    return ring


@pytest.fixture()
def without_one(monkeypatch):
    monkeypatch.setattr(credentials, "_keyring", lambda: None)
    monkeypatch.delenv(credentials.SAY_SO, raising=False)


def a_connection(name="lab"):
    return SavedGateway(name=name, url="https://example.invalid", token="the-secret",
                        gateway_id="g", fingerprint="f", certificate_sha256="")


class TestWhenThereIsAKeyring:

    def test_the_token_goes_into_it(self, with_a_keyring, tmp_path):
        ConnectionStore(str(tmp_path / "conns.json")).save(a_connection())
        assert with_a_keyring.kept[(credentials.SERVICE, "lab")] == "the-secret"

    def test_and_not_into_the_file(self, with_a_keyring, tmp_path):
        where = tmp_path / "conns.json"
        ConnectionStore(str(where)).save(a_connection())
        written = where.read_text(encoding="utf-8")
        assert "the-secret" not in written, (
            "the file still holds the credential, so a backup of it still holds the credential")

    def test_but_reading_it_back_gives_the_token(self, with_a_keyring, tmp_path):
        store = ConnectionStore(str(tmp_path / "conns.json"))
        store.save(a_connection())
        assert store.get("lab").token == "the-secret"

    def test_and_forgetting_a_connection_forgets_the_token(self, with_a_keyring, tmp_path):
        store = ConnectionStore(str(tmp_path / "conns.json"))
        store.save(a_connection())
        store.forget("lab")
        assert with_a_keyring.kept == {}

    def test_a_locked_keyring_does_not_crash_a_command_that_may_not_need_it(
            self, monkeypatch, tmp_path):
        class Locked(AKeyring):
            def get_password(self, service, name):
                raise RuntimeError("the keyring is locked")

        monkeypatch.setattr(credentials, "_keyring", lambda: Locked())
        assert credentials.fetch("lab") == ""


class TestWhenThereIsNot:

    def test_nothing_is_written_and_the_reason_is_given(self, without_one, tmp_path):
        """Not a silent fallback. A fallback that happens quietly is a fallback nobody chose,
        and the whole point of moving the token was that nobody had chosen where it was."""
        where = tmp_path / "conns.json"
        with pytest.raises(credentials.NoSafePlace) as refused:
            ConnectionStore(str(where)).save(a_connection())
        assert not where.exists(), "a credential was written after refusing to write one"
        said = str(refused.value)
        assert "no keyring" in said
        assert credentials.SAY_SO in said, "the way forward is not named"
        assert "pip install keyring" in said

    def test_but_saying_so_out_loud_is_allowed(self, without_one, monkeypatch, tmp_path):
        monkeypatch.setenv(credentials.SAY_SO, "file")
        where = tmp_path / "conns.json"
        store = ConnectionStore(str(where))
        store.save(a_connection())
        assert store.get("lab").token == "the-secret"
        assert "the-secret" in where.read_text(encoding="utf-8")

    def test_and_that_choice_has_to_be_the_exact_word(self, without_one, monkeypatch, tmp_path):
        for vague in ("1", "true", "yes", "please"):
            monkeypatch.setenv(credentials.SAY_SO, vague)
            with pytest.raises(credentials.NoSafePlace):
                ConnectionStore(str(tmp_path / ("c%s.json" % vague))).save(a_connection())


class TestConnectionsSavedBeforeThisExisted:

    def test_keep_working(self, without_one, monkeypatch, tmp_path):
        """Their token is in the file. Refusing to read it would lock people out of their own
        gateway to make a point."""
        monkeypatch.setenv(credentials.SAY_SO, "file")
        store = ConnectionStore(str(tmp_path / "conns.json"))
        store.save(a_connection())

        monkeypatch.delenv(credentials.SAY_SO)
        monkeypatch.setattr(credentials, "_keyring", lambda: None)
        assert store.get("lab").token == "the-secret"

    def test_and_the_keyring_wins_when_both_have_one(self, with_a_keyring, monkeypatch, tmp_path):
        monkeypatch.setenv(credentials.SAY_SO, "file")
        store = ConnectionStore(str(tmp_path / "conns.json"))
        store.save(a_connection())
        with_a_keyring.kept[(credentials.SERVICE, "lab")] = "the-newer-one"
        assert store.get("lab").token == "the-newer-one"


class TestWhatItReportsAboutItself:

    def test_it_says_where_a_token_would_go(self, with_a_keyring):
        assert credentials.where_it_would_go() == "keyring"

    def test_and_says_nowhere_when_there_is_nowhere(self, without_one):
        assert credentials.where_it_would_go() == ""

    def test_a_backend_that_is_only_a_placeholder_is_not_a_keyring(self, monkeypatch):
        """keyring returns a "fail" backend when it found nothing usable. Treating that as a
        keyring means every read and write raising from somewhere much less helpful."""
        class Fail:
            pass

        Fail.__module__ = "keyring.backends.fail"
        fake = type("K", (), {"get_keyring": staticmethod(lambda: Fail())})
        monkeypatch.setitem(__import__("sys").modules, "keyring", fake)
        monkeypatch.setitem(__import__("sys").modules, "keyring.errors",
                            type("E", (), {"NoKeyringError": RuntimeError}))
        assert credentials._keyring() is None
