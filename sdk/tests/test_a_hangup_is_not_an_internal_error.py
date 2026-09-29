"""Hundreds of lines saying "internal error" about something entirely ordinary.

The worker's log on the two test machines filled with

    internal error while answering: BrokenPipeError(32, 'Broken pipe')
    internal error while answering: ConnectionResetError(104, 'Connection reset by peer')

every time a peer hung up untidily -- which, during a network-interruption test, is
constantly. None of them was an internal error. The category that exists to surface this
process being wrong about something was burying that signal under transport noise.

A CLEAN hangup never came here: `read_frame` turns an early end into a MALFORMED
ProtocolError and the worker answers it with silence. These are the untidy ones -- a reset,
a read that timed out, a TLS teardown -- and every one of them is an `OSError`.

The fix that would be worse than the defect is widening the quiet arm until real bugs fall
into it, so half of this file is about a `KeyError` still being reported as what it is.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

import inspect
import socket
import ssl

import pytest

from agentnode_sdk.worker import protocol as wire
from agentnode_sdk.worker.service import Bench

KEY = b"k" * 32


class Deaf:
    """A connection that fails the way a vanished peer does."""

    def __init__(self, blow_up_with):
        self.blow_up_with = blow_up_with
        self.sent = []
        self.closed = False

    def settimeout(self, _seconds):
        pass

    def makefile(self, _mode):
        raise self.blow_up_with

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


def converse_with(exc, capsys):
    bench = Bench(object(), "unix:///nowhere.sock", KEY, only_uid=None)
    connection = Deaf(exc)
    bench.converse(connection)
    return connection, capsys.readouterr().out


class TestThePeerGoingAwayIsSaidPlainly:

    @pytest.mark.parametrize("exc", [
        BrokenPipeError(32, "Broken pipe"),
        ConnectionResetError(104, "Connection reset by peer"),
        ConnectionAbortedError(103, "Software caused connection abort"),
        TimeoutError("timed out"),
        socket.timeout("timed out"),
        ssl.SSLEOFError("EOF occurred in violation of protocol"),
        ssl.SSLZeroReturnError("TLS/SSL connection has been closed"),
    ])
    def test_it_is_not_called_an_internal_error(self, exc, capsys):
        _connection, out = converse_with(exc, capsys)
        assert "internal error" not in out
        assert "went away" in out

    def test_and_the_kind_is_named_so_a_log_is_still_useful(self, capsys):
        _connection, out = converse_with(ConnectionResetError(104, "reset"), capsys)
        assert "ConnectionResetError" in out

    def test_nothing_is_sent_down_a_connection_that_is_gone(self, capsys):
        connection, _out = converse_with(BrokenPipeError(32, "Broken pipe"), capsys)
        assert connection.sent == []

    def test_the_connection_is_still_closed(self, capsys):
        connection, _out = converse_with(BrokenPipeError(32, "Broken pipe"), capsys)
        assert connection.closed


class TestARealBugIsStillOne:
    """The failure mode of this repair. If the quiet arm swallowed these, the log would be
    tidy and useless."""

    @pytest.mark.parametrize("exc", [
        KeyError("method"),
        AttributeError("nope"),
        ValueError("no"),
        TypeError("no"),
    ])
    def test_it_is_still_reported_as_an_internal_error(self, exc, capsys):
        _connection, out = converse_with(exc, capsys)
        assert "internal error while answering" in out

    def test_the_sentence_a_frozen_test_asserts_on_is_still_produced(self):
        source = inspect.getsource(Bench.converse)
        assert "internal error while answering" in source

    def test_the_quiet_arm_is_oserror_and_not_exception(self):
        source = inspect.getsource(Bench.converse)
        assert "except OSError as exc:" in source

    def test_and_it_comes_before_the_general_one(self):
        source = inspect.getsource(Bench.converse)
        assert source.index("except OSError as exc:") < source.index(
            "except Exception as exc:")

    def test_a_genuine_bug_still_answers_the_caller_when_it_can(self, capsys):
        """The worker tells the gateway rather than letting it wait out its deadline."""

        class Rude(Deaf):
            def makefile(self, _mode):
                raise KeyError("boom")

        bench = Bench(object(), "unix:///nowhere.sock", KEY, only_uid=None)
        connection = Rude(None)
        bench.converse(connection)
        out = capsys.readouterr().out
        assert "internal error while answering" in out
