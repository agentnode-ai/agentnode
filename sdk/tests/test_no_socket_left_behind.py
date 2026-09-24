"""A worker that has stopped leaves no door lying around.

Closing a unix socket does not remove its file. So a worker that served a socket, was stopped,
and was started again with a TLS door and no `--socket` left the old path on disk -- and a
machine that is supposed to have exactly one door showed two, one of them dead. Found on the
closed alpha while switching it to mutual TLS and then back and forward again: `ls` showed
/run/agentnode/worker.sock with nothing serving it, and telling that apart from a real door
needed somebody to connect to it by hand.

The banner had the same shape of problem in words: a worker with only a TLS door announced
itself as "also listening with mutual TLS", which says a socket is open beside it.
"""
from __future__ import annotations

import os
import socket
import threading

import pytest

from agentnode_sdk.worker import service


# Windows has an AF_UNIX of sorts, and it is not the one the worker's door is built on -- no
# SO_PEERCRED, so no "which account is connecting", which is the whole point of that door. The
# skip is on the class that opens one, not on the module: what the worker SAYS about its doors
# is the same question everywhere and is checked everywhere.
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

    def test_it_does_not_take_away_a_door_somebody_else_now_holds(self, tmp_path):
        """A worker whose listener was closed behind its back cannot claim the path.

        While a worker's listener is bound, the path is its own by construction: another
        worker's `open()` would connect, find somebody listening, and refuse. So the only way
        the path can belong to somebody else is if this worker's socket was closed without
        going through `stop_serving`. Then it may not remove what it finds there.

        Two earlier versions of the check got this wrong in ways only a machine showed: a
        connect probe reached the worker's own draining accept loop, and an inode recorded at
        bind time matched a second worker's socket because the filesystem reused the number.
        """
        first, path = a_bench(tmp_path)
        first.open()
        first._socket.close()                      # closed behind the Bench's back
        second, _ = a_bench(tmp_path)
        second.open()                              # second clears it and binds its own
        assert os.path.exists(path)
        first.stop_serving()                       # must not remove the live one's door
        assert os.path.exists(path), "a worker removed a door another worker was serving"
        second.stop_serving()
        assert not os.path.exists(path)

    def test_the_file_goes_while_the_socket_is_still_bound(self, tmp_path):
        """The order is the guarantee, so it is the thing to hold onto.

        Unlinking before the close is what makes the ownership question answerable at all. If
        this ever went back to close-then-unlink, the two checks above could both pass on one
        machine and the file would still be left behind on another.
        """
        bench, path = a_bench(tmp_path)
        bench.open()
        was_bound_when_unlinked = []
        real = bench._unlink_the_path

        def watch():
            was_bound_when_unlinked.append(bench._socket.fileno() != -1)
            real()

        bench._unlink_the_path = watch
        bench.stop_serving()
        assert was_bound_when_unlinked == [True],             "the socket was closed before its file was taken away"
        assert not os.path.exists(path)

    def test_a_stopped_worker_leaves_nothing_a_later_one_has_to_clear(self, tmp_path):
        """The sequence the alpha hit: socket worker, stopped, then a TLS-only worker."""
        bench, path = a_bench(tmp_path)
        bench.open()
        bench.stop_serving()
        # Nothing opens the socket path now -- this is the TLS-only worker's life.
        assert not os.path.exists(path)
        assert not os.path.exists(os.path.dirname(path) + "/worker.sock")

    def test_serving_and_then_being_stopped_also_clears_it(self, tmp_path):
        bench, path = a_bench(tmp_path)
        bench.open()
        serving = threading.Thread(target=bench.serve_forever, daemon=True)
        serving.start()
        bench.stop_serving()
        serving.join(timeout=30)
        assert not serving.is_alive(), "the worker was told to stop and did not"
        assert not os.path.exists(path)


class TestTheWorkerSaysWhatDoorsItHas:

    def test_a_tls_only_worker_does_not_say_also(self):
        """`also` claims a second door. Read against the source, which is where the word is."""
        source = service.__file__
        with open(source, encoding="utf-8") as fh:
            text = fh.read()
        assert '"also listening with mutual TLS' not in text, \
            "a worker with one door announces itself as though it had two"
        assert '"also " if address else ""' in text, \
            "the word 'also' is not conditional on there being a socket as well"

    def test_the_banner_does_not_promise_a_socket(self):
        from agentnode_sdk.cli import worker_commands

        with open(worker_commands.__file__, encoding="utf-8") as fh:
            text = fh.read()
        assert "Before it opens the socket" not in text, \
            "the banner tells every worker it is about to open a socket"
        assert "Before any door opens" in text
