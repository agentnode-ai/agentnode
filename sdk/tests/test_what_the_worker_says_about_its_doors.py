"""A worker says how many doors it has, and it has to be right about it.

Found on the closed alpha, which had deliberately been put on mutual TLS with no unix socket at
all. Its worker announced itself as

    also listening with mutual TLS at 127.0.0.1:8443, loopback only, as w1

`also` says there is something else. There was not: the socket had been taken away on purpose,
and the one line an operator reads to see what the machine offers said the opposite of the truth.

The banner above it had the same shape of problem. It told EVERY worker

    Before it opens the socket it hits a memory ceiling, to see whether one binds.

including one that opens no socket -- and that sentence had been written between the two halves
of another one, so both read as nonsense.

Everything here runs the code and reads what came out. Nothing greps the source: a check on the
source text can break on a harmless rename and can pass without the branch it is about ever
running, which is exactly the trap an earlier version of these tests fell into.
"""
from __future__ import annotations

import os
import socket
import threading
import time

import pytest


a_posix_door = pytest.mark.skipif(os.name != "posix" or not hasattr(socket, "AF_UNIX"),
                                  reason="the worker's unix door is a posix one")


def a_worker_that_announced_itself(tmp_path, monkeypatch, capsys, *, with_a_socket: bool) -> str:
    """Start a real worker with a real issued certificate, let it speak, and stop it again.

    The doors are real: a TLS listener built from a certificate this deployment's own issuer
    produced, and where asked for, a unix socket bound on disk. What is borrowed is the worker
    behind them -- a stand-in, because what is under test is one sentence about how many doors
    there are, and running jobs is not part of it.
    """
    from agentnode_sdk.pki import floor as floors
    from agentnode_sdk.worker import service as under_test
    from agentnode_sdk.worker import tls as tls_module
    from tests.test_mtls_transport import TEST_BOOT, World

    monkeypatch.setattr(floors, "_boot", lambda: TEST_BOOT)
    world = World(tmp_path / "pki")
    settings = world.settings(world.service("worker", "w1"), {"g1"})
    monkeypatch.chdir(tmp_path)

    # Held so the test can stop what it started; they are the real classes, not stand-ins.
    opened = []
    for module, name in ((under_test, "Bench"), (tls_module, "TlsListener")):
        real = getattr(module, name)

        def remembering(*rest, _real=real, **kw):
            made = _real(*rest, **kw)
            opened.append(made)
            return made

        monkeypatch.setattr(module, name, remembering)

    where = tmp_path / "run"
    where.mkdir()
    address = "unix://" + str(where / "worker.sock") if with_a_socket else ""
    key = tmp_path / "worker.key"
    key.write_text("6d" * 32, encoding="ascii")

    failed = []

    def run_it():
        try:
            under_test.serve(address, str(key), getattr(os, "getuid", lambda: 1000)(),
                             worker=a_stand_in_worker(),
                             tls_address="tcps://127.0.0.1:0", tls=settings)
        except BaseException as exc:                          # noqa: BLE001 - reported below
            failed.append(exc)

    thread = threading.Thread(target=run_it, daemon=True)
    thread.start()
    for _ in range(120):
        if len(opened) >= 2:
            break
        time.sleep(0.25)
    time.sleep(0.75)
    for door in opened:
        door.stop_serving()
    thread.join(timeout=30)
    assert not thread.is_alive(), "the worker would not stop"
    assert not failed, "the worker did not start: %r" % (failed[:1],)
    return capsys.readouterr().out


def a_stand_in_worker():
    """Enough of a worker for `serve()` to reach the point where it opens its doors."""

    class Held:
        held = True
        reason = ""
        evidence: dict = {}

    class AStandIn:
        def prove_its_ceilings(self):
            return Held()

        def run(self, job):                                   # pragma: no cover - not reached
            raise AssertionError("no job should reach this stand-in")

    return AStandIn()


@a_posix_door
class TestAWorkerWithOnlyATlsDoor:

    def test_it_does_not_claim_a_socket_beside_it(self, tmp_path, monkeypatch, capsys):
        said = a_worker_that_announced_itself(tmp_path, monkeypatch, capsys, with_a_socket=False)
        assert "listening with mutual TLS" in said, "it never said it had a TLS door"
        assert "also listening with mutual TLS" not in said, \
            "a worker with one door announced itself as though it had two"

    def test_and_it_says_outright_that_there_is_no_socket(self, tmp_path, monkeypatch, capsys):
        said = a_worker_that_announced_itself(tmp_path, monkeypatch, capsys, with_a_socket=False)
        assert "there is NO unix socket" in said

    def test_and_no_socket_file_appears_anywhere(self, tmp_path, monkeypatch, capsys):
        """What it says and what it does, checked against each other rather than separately."""
        a_worker_that_announced_itself(tmp_path, monkeypatch, capsys, with_a_socket=False)
        assert list((tmp_path / "run").iterdir()) == [], "it created a socket file after all"
        assert not any(p.name.endswith(".sock") for p in tmp_path.rglob("*")), \
            "something created a socket file somewhere under the working directory"


@a_posix_door
class TestAWorkerWithBothDoors:
    """The configuration deployments have had all along. It must read exactly as it did."""

    def test_it_does_say_also(self, tmp_path, monkeypatch, capsys):
        said = a_worker_that_announced_itself(tmp_path, monkeypatch, capsys, with_a_socket=True)
        assert "also listening with mutual TLS" in said, \
            "a worker with two doors did not say the TLS one was the second"

    def test_and_it_still_says_where_its_socket_is(self, tmp_path, monkeypatch, capsys):
        said = a_worker_that_announced_itself(tmp_path, monkeypatch, capsys, with_a_socket=True)
        assert "listening at " in said and ".sock" in said, \
            "it stopped saying where the socket it opened is"
        assert "for uid " in said, "it stopped saying which account may reach it"

    def test_and_the_socket_is_really_opened(self, tmp_path, monkeypatch, capsys):
        a_worker_that_announced_itself(tmp_path, monkeypatch, capsys, with_a_socket=True)
        made = list((tmp_path / "run").iterdir())
        assert [p.name for p in made] == ["worker.sock"], \
            "the socket door it announced was not the one it opened: %s" % made


@a_posix_door
class TestTheBannerEveryWorkerPrints:
    """Read off a real worker started through the CLI, because that is the only way to see it.

    Three things come before the banner and each stops the run short of it: the runtime-pin
    check, the account check, and the refusal of a worker with no door. Earlier attempts at this
    tripped over the first two in turn and failed against the fix exactly as they failed against
    the parent, which is the one thing a test like this must not do. So this gives the worker
    everything it needs, lets it really start, and reads what it said.
    """

    def what_a_started_worker_printed(self, tmp_path) -> str:
        import subprocess
        import sys

        where = tmp_path / "run"
        where.mkdir()
        path = str(where / "worker.sock")
        key = tmp_path / "worker.key"
        key.write_text("ab" * 32, encoding="ascii")
        environment = dict(os.environ)
        environment["HOME"] = str(tmp_path)
        environment["AGENTNODE_ALLOW_UNPINNED"] = "1"
        # Unbuffered, because this worker is stopped with a signal and a pipe is
        # block-buffered: without it the banner is still sitting in the buffer when the
        # process dies and the test reads an empty string. That is what happened first.
        environment["PYTHONUNBUFFERED"] = "1"
        started = subprocess.Popen(
            [sys.executable, "-m", "agentnode_sdk.cli", "worker", "serve",
             "--socket", "unix://" + path, "--key", str(key), "--for-user", str(os.getuid())],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment, text=True)
        try:
            for _ in range(240):
                if os.path.exists(path):
                    break
                if started.poll() is not None:
                    raise AssertionError("the worker exited before it opened a door:\n"
                                         + (started.stdout.read() or ""))
                time.sleep(0.25)
            else:                                             # pragma: no cover - never opened
                raise AssertionError("the worker never opened its socket")
            time.sleep(0.5)
        finally:
            started.terminate()
            said, _ = started.communicate(timeout=60)
        assert "AgentNode sandbox worker" in said, \
            "the banner was never reached, so this test establishes nothing: " + said[:400]
        return said

    def test_it_does_not_promise_a_socket_to_every_worker(self, tmp_path):
        said = self.what_a_started_worker_printed(tmp_path)
        assert "Before it opens the socket" not in said, \
            "the banner tells every worker it is about to open a socket"
        assert "Before any door opens" in said

    def test_and_the_sentence_it_was_written_into_is_whole_again(self, tmp_path):
        said = self.what_a_started_worker_printed(tmp_path)
        assert "It holds no pairing\n  state, no signing identity and no client's token." in said, \
            "the ceiling sentence is still written through the middle of another one"

    def test_and_a_socket_worker_still_says_what_it_always_said(self, tmp_path):
        """The configuration deployments already run, read end to end through the CLI."""
        said = self.what_a_started_worker_printed(tmp_path)
        assert "listening at " in said and ".sock for uid " in said, \
            "it stopped saying where its socket is and who may reach it"
        assert "also listening with mutual TLS" not in said, \
            "a worker with no TLS door claimed one"
