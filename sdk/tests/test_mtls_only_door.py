"""A worker whose only door is mutual TLS.

The TLS door used to be able to stand only BESIDE the unix socket: `serve()` opened the socket
first and the CLI refused to start without `--socket`. A deployment that had moved to mutual TLS
therefore had to keep a second way in that nothing checked against the same rules -- no
certificate, no revocation list, no time floor, just the file's permissions and whoever is in the
group.

These tests are about the other arrangement: the socket is not opened at all, no socket file
exists, and a worker asked for no door at all is refused rather than started as something that
holds a container runtime and answers nobody.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from agentnode_sdk.worker.service import serve


class AStandInWorker:
    """Enough of a worker for `serve()` to get as far as opening its doors."""

    def __init__(self):
        self.ran = []

    def prove_its_ceilings(self):
        class Held:
            held = True
            reason = ""
            evidence = {}

        return Held()

    def run(self, job):                                        # pragma: no cover - not reached
        self.ran.append(job)
        raise AssertionError("no job should reach this stand-in")


def _a_uid() -> int:
    """The account the worker serves. Windows has no uids; the check under test only asks that
    one was named, so any number does here and the socket door is what needs a real one."""
    return getattr(os, "getuid", lambda: 1000)()


def _a_key(tmp_path):
    """The shared key file, in the shape `read_key` expects."""
    path = tmp_path / "worker.key"
    path.write_text("6d" * 32, encoding="ascii")
    return str(path)


class TestAWorkerNeedsADoor:

    def test_neither_a_socket_nor_tls_is_refused(self, tmp_path):
        """A worker nobody can reach is not a worker, and starting one would look like success."""
        with pytest.raises(ValueError) as refused:
            serve("", _a_key(tmp_path), _a_uid(), worker=AStandInWorker())
        assert "door" in str(refused.value)

    def test_tls_settings_without_an_address_are_still_refused(self, tmp_path):
        with pytest.raises(ValueError):
            serve("", _a_key(tmp_path), _a_uid(), worker=AStandInWorker(),
                  tls_address="", tls=object())

    def test_an_address_without_settings_is_still_refused(self, tmp_path):
        with pytest.raises(ValueError):
            serve("unix:///nowhere.sock", _a_key(tmp_path), _a_uid(),
                  worker=AStandInWorker(), tls_address="tcps://127.0.0.1:8443", tls=None)


class TestTheTlsDoorCanStandAlone:

    def test_no_socket_file_is_created_when_only_tls_is_asked_for(self, tmp_path, monkeypatch):
        """The point of the whole change: nothing to connect to except the TLS door.

        The worker is started in a thread and stopped again; what is asserted is what exists on
        the filesystem while it runs. A socket file that is never created cannot be reached by a
        process in the right group, which is what the old arrangement left open.
        """
        from agentnode_sdk.pki import floor as floors
        from tests.test_mtls_transport import TEST_BOOT, World          # the PKI builders

        monkeypatch.setattr(floors, "_boot", lambda: TEST_BOOT)
        world = World(tmp_path / "pki")
        settings = world.settings(world.service("worker", "w1"), {"g1"})
        where = tmp_path / "run"
        where.mkdir()
        monkeypatch.chdir(tmp_path)

        stub = AStandInWorker()
        started = threading.Event()
        failed = []

        def run_it():
            try:
                started.set()
                serve("", _a_key(tmp_path), _a_uid(), worker=stub,
                      tls_address="tcps://127.0.0.1:0", tls=settings)
            except BaseException as exc:                        # noqa: BLE001 - reported below
                failed.append(exc)

        thread = threading.Thread(target=run_it, daemon=True)
        thread.start()
        assert started.wait(timeout=10)
        time.sleep(1.5)
        assert not failed, "the worker did not start with the TLS door alone: %r" % (failed[:1],)
        assert list(where.iterdir()) == [], "a socket file was created after all"
        assert not any(p.name.endswith(".sock") for p in tmp_path.rglob("*")), \
            "something created a socket file somewhere under the working directory"
