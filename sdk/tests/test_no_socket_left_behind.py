"""A worker that has stopped leaves no door lying around, and never takes somebody else's.

Closing a unix socket does not remove its file. So a worker that served a socket, was stopped,
and was started again with a TLS door and no `--socket` left the old path on disk -- and a
machine that is supposed to have exactly one door showed two, one of them dead. Found on the
closed alpha while switching it to mutual TLS and then back and forward again: `ls` showed
/run/agentnode/worker.sock with nothing serving it, and telling that apart from a real door
needed somebody to connect to it by hand.

The banner had the same shape of problem in words: a worker with only a TLS door announced
itself as "also listening with mutual TLS", which says a socket is open beside it.

Removing a file at shutdown is the easy half. The hard half is being sure it is still yours, and
three answers to that were wrong before this one. The last of them -- "while I am bound, the path
is mine by construction" -- was found by an independent review and is the reason a lock exists:
between `bind()` and `listen()` a socket file is present and refuses connections, which is
exactly what a dead worker's leftover looks like.
"""
from __future__ import annotations

import os
import signal
import socket
import threading
import time

import pytest

from agentnode_sdk.worker import service


# Windows has an AF_UNIX of sorts, and it is not the one the worker's door is built on -- no
# SO_PEERCRED, so no "which account is connecting", which is the whole point of that door.
a_posix_door = pytest.mark.skipif(os.name != "posix" or not hasattr(socket, "AF_UNIX"),
                                  reason="the worker's unix door is a posix one")


def a_bench(tmp_path, name: str = "worker.sock") -> tuple:
    path = os.path.join(str(tmp_path), name)
    bench = service.Bench(worker=None, address="unix://" + path, key=b"k" * 32,
                          only_uid=os.getuid(), remembers_at=str(tmp_path / "floor.json"))
    return bench, path


@a_posix_door
class TestTheFileGoesWhenTheWorkerDoes:

    def test_stopping_takes_the_socket_file_away(self, tmp_path):
        bench, path = a_bench(tmp_path)
        bench.open()
        assert os.path.exists(path), "the worker did not open the door it was asked for"
        bench.stop_serving()
        assert not os.path.exists(path), \
            "the worker stopped and left its socket file behind, which reads as a door"

    def test_a_worker_that_never_opened_can_still_be_stopped(self, tmp_path):
        bench, path = a_bench(tmp_path)
        bench.stop_serving()
        assert not os.path.exists(path)

    def test_stopping_twice_is_not_an_error(self, tmp_path):
        bench, _ = a_bench(tmp_path)
        bench.open()
        bench.stop_serving()
        bench.stop_serving()

    def test_a_stopped_worker_leaves_nothing_a_later_one_has_to_clear(self, tmp_path):
        """The sequence the alpha hit: socket worker, stopped, then a TLS-only worker."""
        bench, path = a_bench(tmp_path)
        bench.open()
        bench.stop_serving()
        # Nothing opens the socket path now -- this is the TLS-only worker's life.
        assert not os.path.exists(path)

    def test_serving_and_then_being_stopped_also_clears_it(self, tmp_path):
        bench, path = a_bench(tmp_path)
        bench.open()
        serving = threading.Thread(target=bench.serve_forever, daemon=True)
        serving.start()
        bench.stop_serving()
        serving.join(timeout=30)
        assert not serving.is_alive(), "the worker was told to stop and did not"
        assert not os.path.exists(path)


@a_posix_door
class TestItNeverTakesAPathnameThatIsNotItsOwn:
    """The half that is hard, and that three earlier mechanisms got wrong."""

    def test_a_second_worker_cannot_take_a_pathname_the_first_still_holds(self, tmp_path):
        """Which is what makes removing it safe: it was never anyone else's to remove."""
        first, path = a_bench(tmp_path)
        first.open()
        second, _ = a_bench(tmp_path)
        with pytest.raises(OSError) as refused:
            second.open()
        assert "already listening" in str(refused.value)
        assert os.path.exists(path), "the refused worker took the live one's door with it"
        first.stop_serving()
        assert not os.path.exists(path)

    def test_a_second_worker_cannot_slip_in_between_bind_and_listen(self, tmp_path):
        """The interleaving an independent review found, driven deterministically.

        In that window the socket file exists and refuses connections -- indistinguishable from
        a dead worker's leftover. So the second worker unlinked the first one's brand-new socket
        and bound its own; and the first, whose pathname had not changed, later removed the
        second one's LIVE door on the way out. No check on the name could tell them apart,
        because the name was the same. The lock is what closes this window.
        """
        first, path = a_bench(tmp_path)
        in_the_window = threading.Event()
        may_continue = threading.Event()
        real_listen = socket.socket.listen

        def pause_between_bind_and_listen(self, *rest):
            if self.family == socket.AF_UNIX and not in_the_window.is_set():
                in_the_window.set()
                may_continue.wait(timeout=60)
            return real_listen(self, *rest)

        what_the_second_one_did = {}

        def the_second_worker():
            if not in_the_window.wait(timeout=60):            # pragma: no cover - never paused
                return
            second, _ = a_bench(tmp_path)
            try:
                second.open()
                what_the_second_one_did["outcome"] = "it took the pathname"
                second.stop_serving()
            except OSError as refused:
                what_the_second_one_did["outcome"] = "refused: %s" % refused
            may_continue.set()

        racer = threading.Thread(target=the_second_worker, daemon=True)
        racer.start()
        socket.socket.listen = pause_between_bind_and_listen
        try:
            first.open()
        finally:
            socket.socket.listen = real_listen
            may_continue.set()
            racer.join(timeout=60)

        assert what_the_second_one_did.get("outcome", "").startswith("refused"), (
            "a second worker got the pathname while the first was between bind and listen: %s"
            % what_the_second_one_did.get("outcome", "it never ran"))
        assert os.path.exists(path), "the first worker's door was taken before it ever served"
        first.stop_serving()
        assert not os.path.exists(path)

    def test_a_pathname_a_dead_worker_left_is_cleared_by_the_next_one(self, tmp_path):
        """SIGKILL cannot be caught, so this is what covers it. The lock dies with the process."""
        gone, path = a_bench(tmp_path)
        gone.open()
        # What is left when a process ceases to exist: the file, and no lock held by anybody.
        gone._socket.close()
        gone._let_the_lock_go()
        gone._path = None
        assert os.path.exists(path), "the setup did not leave the stale file it is about"

        next_one, _ = a_bench(tmp_path)
        next_one.open()
        assert os.path.exists(path)
        next_one.stop_serving()
        assert not os.path.exists(path)

    def test_a_worker_that_could_not_open_holds_no_pathname(self, tmp_path):
        """A lock kept by a worker with no door would make the pathname unusable for everyone."""
        first, path = a_bench(tmp_path)
        first.open()
        second, _ = a_bench(tmp_path)
        with pytest.raises(OSError):
            second.open()
        first.stop_serving()
        # The refused one must not still be holding the name it never got.
        third, _ = a_bench(tmp_path)
        third.open()
        assert os.path.exists(path)
        third.stop_serving()


@a_posix_door
class TestTheWayItIsActuallyStopped:
    """systemd sends a signal. Everything above calls `stop_serving()` by hand.

    That difference hid the whole defect once already: the unlink was in the code, every unit
    test was green, and a real worker on the alpha sent a SIGTERM still left its socket file
    behind -- because Python's default for the terminating signals ends the process where it
    stands, so no `finally` ran and `stop_serving` was never reached. A test that only calls the
    method cannot see that. These spawn a real worker and signal it.
    """

    def a_worker_serving(self, tmp_path):
        import subprocess
        import sys

        path = str(tmp_path / "worker.sock")
        key = tmp_path / "worker.key"
        key.write_text("ab" * 32, encoding="ascii")
        environment = dict(os.environ)
        environment["HOME"] = str(tmp_path)
        environment["AGENTNODE_ALLOW_UNPINNED"] = "1"
        started = subprocess.Popen(
            [sys.executable, "-m", "agentnode_sdk.cli", "worker", "serve",
             "--socket", "unix://" + path, "--key", str(key), "--for-user", str(os.getuid())],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment)
        for _ in range(240):
            if os.path.exists(path):
                return started, path
            if started.poll() is not None:
                said = (started.stdout.read() or b"").decode("utf-8", "replace")
                raise AssertionError("the worker exited before it opened a door:\n" + said)
            time.sleep(0.25)
        started.kill()
        raise AssertionError("the worker never opened its socket")

    def stop_it(self, started, how):
        started.send_signal(how)
        try:
            return started.wait(timeout=60)
        finally:
            if started.poll() is None:                        # pragma: no cover - it hung
                started.kill()
                started.wait(timeout=30)

    @pytest.mark.parametrize("how", ["SIGTERM", "SIGHUP", "SIGQUIT"])
    def test_a_stopping_signal_takes_the_socket_file_with_it(self, tmp_path, how):
        """All three end a process by default, and systemd may send any of them."""
        number = getattr(signal, how, None)
        if number is None:                                    # pragma: no cover - not posix
            pytest.skip("no %s here" % how)
        started, path = self.a_worker_serving(tmp_path)
        self.stop_it(started, number)
        assert not os.path.exists(path), \
            "a worker stopped with %s left its socket file behind" % how

    def test_and_it_stops_rather_than_being_killed(self, tmp_path):
        """If SIGTERM were still the default, the exit code would say the signal killed it."""
        started, _ = self.a_worker_serving(tmp_path)
        code = self.stop_it(started, signal.SIGTERM)
        assert code != -signal.SIGTERM, \
            "the process was ended by the signal rather than stopping on it"

    def test_the_stop_is_caught_before_any_door_is_open(self, tmp_path, monkeypatch):
        """Otherwise there is a window in which a stop is still fatal and still leaves a file.

        The handler used to go on after the doors were open. A review found the gap; this holds
        the ordering, because the gap is invisible unless a signal happens to land inside it.
        """
        from agentnode_sdk.worker import service as under_test

        caught_when_opening = []
        real_open = under_test.Bench.open

        def note_what_is_installed(self):
            caught_when_opening.append(
                signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, None))
            raise KeyboardInterrupt                           # far enough; stop the worker here

        monkeypatch.setattr(under_test.Bench, "open", note_what_is_installed)
        key = tmp_path / "worker.key"
        key.write_text("cd" * 32, encoding="ascii")
        try:
            under_test.serve("unix://" + str(tmp_path / "worker.sock"), str(key), os.getuid(),
                             worker=_a_stand_in_worker())
        except (KeyboardInterrupt, Exception):                # noqa: BLE001 - only the order matters
            pass
        finally:
            under_test.Bench.open = real_open

        assert caught_when_opening == [True], \
            "the door was opened before anything was listening for a stop"


def _a_stand_in_worker():
    """Enough of a worker for `serve()` to reach the point where it opens a door."""

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


class TestTheWorkerSaysWhatDoorsItHas:
    """What it PRINTS, not what its source contains.

    These used to grep the source files. A review said so, and was right: a source check can
    break on a harmless rename and can pass without the branch it is about ever running. These
    start a real worker with a real issued certificate and read what it said.
    """

    def a_worker_that_announced_itself(self, tmp_path, monkeypatch, capsys, with_a_socket):
        from agentnode_sdk.pki import floor as floors
        from agentnode_sdk.worker import service as under_test
        from agentnode_sdk.worker import tls as tls_module
        from tests.test_mtls_transport import TEST_BOOT, World

        monkeypatch.setattr(floors, "_boot", lambda: TEST_BOOT)
        world = World(tmp_path / "pki")
        settings = world.settings(world.service("worker", "w1"), {"g1"})
        monkeypatch.chdir(tmp_path)

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
                                 worker=_a_stand_in_worker(),
                                 tls_address="tcps://127.0.0.1:0", tls=settings)
            except BaseException as exc:                      # noqa: BLE001 - reported below
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

    @a_posix_door
    def test_a_worker_with_only_a_tls_door_does_not_claim_a_socket(self, tmp_path, monkeypatch,
                                                                   capsys):
        said = self.a_worker_that_announced_itself(tmp_path, monkeypatch, capsys,
                                                   with_a_socket=False)
        assert "listening with mutual TLS" in said, "it never said it had a TLS door"
        assert "also listening with mutual TLS" not in said, \
            "a worker with one door announced itself as though it had two"
        assert "there is NO unix socket" in said

    @a_posix_door
    def test_a_worker_with_both_doors_does_say_also(self, tmp_path, monkeypatch, capsys):
        said = self.a_worker_that_announced_itself(tmp_path, monkeypatch, capsys,
                                                   with_a_socket=True)
        assert "also listening with mutual TLS" in said, \
            "a worker with two doors did not say the TLS one was a second"
        assert "listening at " in said, "it did not say where its socket was"

    def test_the_banner_does_not_promise_a_socket_to_every_worker(self, capsys):
        """The line that told even a TLS-only worker it was about to open a socket."""
        from agentnode_sdk.cli import worker_commands

        class AskedForNoDoorAtAll:
            key = ""
            for_user = ""
            socket = ""
            listen = ""

        # It refuses this worker -- no key, no account, no door -- but it prints the banner on
        # the way, which is the sentence under test.
        worker_commands.cmd_serve(AskedForNoDoorAtAll())
        said = capsys.readouterr().out
        assert "Before it opens the socket" not in said, \
            "the banner tells every worker it is about to open a socket"
