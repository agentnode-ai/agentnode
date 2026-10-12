"""O6 follow-up R: a reader of the token file is not refused for a transient sharing state on Windows.

On Windows, opening tokens.json while another holder replaces it can fail with PermissionError(EACCES) --
`winerror` None, because CPython builds the error from the C runtime's errno. Since O6 any failure to read the
file is `TokensUnreadable`, a 503 for whoever asked. Measured on main, Windows: 37 of 3000 reads refused beside
a writing thread, 612 of 34991 beside a writing process (bundle `o6-followups`, E0054).

The read is now retried for exactly that error, briefly and with a bound (DECISION-R, from consultations Q0001
and Q0007), and nothing else is retried. A persistent error still ends in `TokensUnreadable` with that error as
its cause -- never in "nobody is paired".
"""
from __future__ import annotations

import errno
import os
import threading
import time

import pytest

from agentnode_sdk.gateway.identity import GatewayState, TokensUnreadable

#: More opens than any bounded retry may make. Reaching it means the retry has no bound.
WATCHDOG = 500


class _Runaway(BaseException):
    """The retry went past any bound it could have. BaseException, so nothing in the product catches it."""


@pytest.fixture()
def state(tmp_path):
    s = GatewayState(str(tmp_path / "state"), version="test")
    s._issue_token(client_name="seed")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def retrying(monkeypatch):
    """The retry switched on, so the deterministic tests run on every platform."""
    monkeypatch.setattr(GatewayState, "RETRY_A_SHARING_VIOLATION", True, raising=False)


def _opens_fail(monkeypatch, first_n, error):
    """The first `first_n` opens of tokens.json raise `error` (a callable making it); later ones read."""
    real = GatewayState._read_private
    opens = []

    def opening(self, name):
        if name != "tokens.json":
            return real(self, name)
        opens.append(time.monotonic())
        if len(opens) > WATCHDOG:
            raise _Runaway("more than %d opens" % WATCHDOG)
        if len(opens) <= first_n:
            raise error()
        return real(self, name)

    monkeypatch.setattr(GatewayState, "_read_private", opening)
    return opens


def _sharing():
    return PermissionError(errno.EACCES, "Permission denied")


def test_b_a_transient_denial_is_waited_out(state, retrying, monkeypatch):
    opens = _opens_fail(monkeypatch, 3, _sharing)
    tokens = state._read_tokens()
    assert [r.get("client_name") for r in tokens.values()] == ["seed"], (
        "after three transient denials the read answered %r" % (tokens,))
    assert len(opens) == 4, "the read opened the file %d times for three transient denials" % len(opens)


def test_c_a_denial_that_persists_is_refused_within_the_bound(state, retrying, monkeypatch):
    opens = _opens_fail(monkeypatch, WATCHDOG + 1, _sharing)
    started = time.monotonic()
    try:
        state._read_tokens()
    except TokensUnreadable as exc:
        raised = exc
    except _Runaway:
        pytest.fail("retried without a bound: %d opens" % len(opens))
    else:
        pytest.fail("a file that could not be opened was read as %r" % (state._read_tokens,))
    took = time.monotonic() - started
    assert isinstance(raised.__cause__, PermissionError) and raised.__cause__.errno == errno.EACCES, (
        "the refusal does not carry the last denial as its cause: %r" % (raised.__cause__,))
    assert 2 <= len(opens) <= 101, "the read opened the file %d times for a persistent denial" % len(opens)
    assert took < 2.0 + 0.75, "a persistent denial held the read for %.2fs" % took


@pytest.mark.parametrize("error", [
    lambda: FileNotFoundError(errno.ENOENT, "No such file"),
    lambda: IsADirectoryError(errno.EISDIR, "Is a directory"),
    lambda: OSError(errno.EIO, "Input/output error"),
    lambda: PermissionError(errno.EPERM, "Operation not permitted"),
    lambda: UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
], ids=["not-found", "a-directory", "an-io-error", "eperm-not-eacces", "not-utf8"])
def test_d_what_is_not_a_sharing_state_is_refused_at_once(state, retrying, monkeypatch, error):
    opens = _opens_fail(monkeypatch, WATCHDOG + 1, error)
    try:
        state._read_tokens()
    except TokensUnreadable:
        pass
    except _Runaway:
        pytest.fail("retried an error that is not a sharing state, without a bound")
    else:
        pytest.fail("an unreadable file was read")
    assert len(opens) == 1, "an error that is not a sharing state was retried: %d opens" % len(opens)


def test_d_content_that_is_not_json_is_refused_at_once(state, retrying, monkeypatch):
    real = GatewayState._read_private
    opens = []

    def opening(self, name):
        if name == "tokens.json":
            opens.append(1)
            return "{this is not json"
        return real(self, name)

    monkeypatch.setattr(GatewayState, "_read_private", opening)
    with pytest.raises(TokensUnreadable):
        state._read_tokens()
    assert len(opens) == 1, "content that is not JSON was read %d times" % len(opens)


def test_e_the_retry_is_on_only_where_the_transient_exists():
    assert getattr(GatewayState, "RETRY_A_SHARING_VIOLATION", None) is (os.name == "nt"), (
        "the retry is %r on a platform whose os.name is %r"
        % (getattr(GatewayState, "RETRY_A_SHARING_VIOLATION", None), os.name))


@pytest.mark.skipif(os.name != "nt", reason="the measured transient is a Windows file-sharing state")
def test_a_a_reader_beside_a_writer_is_never_refused(tmp_path):
    root = str(tmp_path / "state")
    seed = GatewayState(root, version="test")
    seed._issue_token(client_name="seed")
    base = seed._read_tokens()
    seed.close()
    reader, writer = GatewayState(root, version="test"), GatewayState(root, version="test")
    refused = []

    def write():
        for i in range(1500):
            body = dict(base)
            body["k%d" % (i % 7)] = {"client_name": "w%d" % i, "issued_at": i, "client_id": "c",
                                      "account_id": ""}
            writer._write_tokens(body)

    def read():
        for _ in range(1500):
            try:
                reader._read_tokens()
            except TokensUnreadable as exc:
                refused.append(repr(exc.__cause__))

    try:
        threads = [threading.Thread(target=write), threading.Thread(target=read)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
    finally:
        reader.close()
        writer.close()
    assert refused == [], "%d of 1500 reads beside a writer were refused: %s" % (len(refused), refused[:3])
