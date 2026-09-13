"""Stopping a server must never wait on the thread that is serving it.

`shutdown()` waits for the serve loop to acknowledge. Called ON that loop, it waits for ever.

This is not hypothetical. Writing the lifecycle test hit it: a stand-in `serve_forever` that never
entered the loop left `shutdown()` waiting on an event only that loop ever sets, and the suite
hung until it was killed. The same shape would hang a real gateway -- a request handler deciding
to stop its own server, or the stop-file watcher calling `shutdown()` inline instead of from a
thread of its own.

So all four ways a gateway really ends are exercised here, each with a deadline, and the one
structural rule behind them is checked where it could be broken.
"""
from __future__ import annotations

import gc
import socket
import threading

import pytest

from tests.reliability import open_descriptors


def descriptors() -> int:
    n = open_descriptors()
    if n < 0:
        pytest.skip("this platform cannot be asked how many descriptors are open")
    return n


def settle() -> None:
    gc.collect()


def quiet_again(down_to: int, seconds: float = 8.0) -> int:
    """Wait, with a deadline, for threads to end. A leaked thread never ends, so this still
    catches one; what it removes is the false positive from a watcher between looks."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and threading.active_count() > down_to:
        time.sleep(0.05)
    return threading.active_count()


def a_free_port() -> int:
    finder = socket.socket()
    finder.bind(("127.0.0.1", 0))
    port = finder.getsockname()[1]
    finder.close()
    return port


def an_initialised_gateway(tmp_path):
    """A real gateway directory, made by the real command, on a port nothing else holds."""
    from agentnode_sdk.cli import gateway_commands

    port = a_free_port()

    class Args:
        dir = str(tmp_path / "served")
        tls_self_signed = True
        tls_cert = tls_key = None
        advertise = "127.0.0.1"
        host = "127.0.0.1"
        measure = False

    Args.port = port
    assert gateway_commands.cmd_init(Args()) == 0
    return Args, gateway_commands


class TestTheWaysAGatewayReallyEnds:

    def test_ctrl_c_ends_it(self, tmp_path, monkeypatch):
        """The Ctrl-C branch, raised from inside the real loop.

        An earlier version sent a real SIGINT from a timer thread. It passed alone and HUNG in the
        full suite, because whether a raised SIGINT becomes a KeyboardInterrupt depends on the
        signal disposition whatever ran before it left behind -- so the command never returned and
        the run stopped there. A test whose result depends on the order of the suite is not
        measuring the product.

        `service_actions` runs inside the real `serve_forever` loop, so raising there exercises
        the real `except KeyboardInterrupt` branch and the real `finally`, and serve_forever's own
        finally still sets the shutdown event. That is the difference from a stand-in loop, which
        is what deadlocks.
        """
        from agentnode_sdk.gateway import server as srv

        Args, gateway_commands = an_initialised_gateway(tmp_path)
        settle()
        before_fds = descriptors()
        before_threads = threading.active_count()

        def somebody_pressed_it(self):
            raise KeyboardInterrupt

        monkeypatch.setattr(srv._ServerThatStopsItsWatchers, "service_actions", somebody_pressed_it)

        assert gateway_commands.cmd_start(Args()) == 0, "Ctrl-C did not end it cleanly"

        left = quiet_again(before_threads)
        settle()
        assert descriptors() <= before_fds, "Ctrl-C kept descriptors"
        assert left <= before_threads, "Ctrl-C left threads running"

    def test_an_error_while_serving_still_gives_everything_back(self, tmp_path, monkeypatch):
        """The unhappy path: something nobody expected is raised inside the loop.

        The command does not catch it -- only KeyboardInterrupt -- so it comes out. What must
        still happen is the `finally`, and it must not hang on the way. `service_actions` runs
        inside the REAL loop, so serve_forever's own finally still sets the shutdown event, which
        is exactly the difference between an error and the deadlock a stand-in loop caused.
        """
        from agentnode_sdk.gateway import server as srv

        Args, gateway_commands = an_initialised_gateway(tmp_path)
        settle()
        before_fds = descriptors()
        before_threads = threading.active_count()

        def fall_over(self):
            raise RuntimeError("the floor gave way")

        monkeypatch.setattr(srv._ServerThatStopsItsWatchers, "service_actions", fall_over)

        with pytest.raises(RuntimeError):
            gateway_commands.cmd_start(Args())

        left = quiet_again(before_threads)
        settle()
        assert descriptors() <= before_fds, "an error while serving kept descriptors"
        assert left <= before_threads, "an error while serving left threads running"

    def test_a_systemd_style_stop_ends_the_process(self, tmp_path):
        """What `systemctl stop` does: SIGTERM, and a bounded wait for the process to go.

        Run out of process, because that is the only honest way to ask it -- and the answer has
        to be that it ENDS within a deadline, not that the machine eventually reaps it.
        """
        import signal
        import subprocess
        import sys
        import textwrap
        import time

        if not hasattr(signal, "SIGTERM"):                    # pragma: no cover - not posix
            pytest.skip("no SIGTERM on this platform")

        script = textwrap.dedent(
            """
            import socket, sys
            from agentnode_sdk.cli import gateway_commands
            f = socket.socket(); f.bind(("127.0.0.1", 0))
            chosen = f.getsockname()[1]; f.close()
            class Args:
                dir = sys.argv[1]
                tls_self_signed = True
                tls_cert = tls_key = None
                advertise = "127.0.0.1"
                host = "127.0.0.1"
                port = chosen
                measure = False
            gateway_commands.cmd_init(Args())
            print("serving", flush=True)
            gateway_commands.cmd_start(Args())
            """
        )
        child = subprocess.Popen([sys.executable, "-c", script, str(tmp_path / "svc")],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.time() + 30
            started = False
            while time.time() < deadline:
                line = child.stdout.readline()
                if not line:
                    break
                if "serving" in line:
                    started = True
                    break
            assert started, "the child never started serving"
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:                     # pragma: no cover
            child.kill()
            pytest.fail("a systemd-style stop did not end the process within fifteen seconds")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)


class TestTheRuleBehindThem:

    def test_nothing_stops_a_server_from_the_thread_serving_it(self):
        """Every shutdown() the gateway calls on its own server is on some OTHER thread.

        The watcher already does this deliberately -- it starts a thread to make the call -- and
        this is what stops that being quietly simplified into a direct call one day.
        """
        import inspect
        import re

        from agentnode_sdk.gateway import server as srv

        source = inspect.getsource(srv)
        # Every mention, not only calls: the safe form passes `shutdown` to a Thread as a
        # TARGET, which never appears as a call and would be invisible to a call-site scan.
        calls = list(re.finditer(r"\.shutdown\b", source))
        assert calls, "no mention of shutdown at all, so this test is watching nothing"
        checked = 0
        for match in calls:
            line_start = source.rfind(chr(10), 0, match.start()) + 1
            line = source[line_start:match.end()].strip()
            # `super().shutdown()` is the override handing on to the base class, not anybody
            # deciding to stop a server. The rule is about who CALLS it.
            if line.startswith("super()."):
                continue
            checked += 1
            window = source[max(0, line_start - 400):match.end()]
            assert "threading.Thread" in window, (
                "this shutdown is not handed to a thread of its own, so it would wait on the "
                "loop it is stopping:" + chr(10) + "  " + line)
        assert checked, "every mention was a super() call, so nothing was actually checked"

    def test_and_the_worker_can_be_stopped_while_it_is_accepting(self, tmp_path):
        """The same fault in the other server this product runs.

        Closing a listening socket does NOT wake a thread already blocked in accept(): the close
        succeeds and the thread stays blocked until somebody connects. Six workers were found
        still accepting after an entire test session had ended. The listener carries a timeout
        now, so the loop comes up for air and sees that it has been told to stop.
        """
        import os

        from agentnode_sdk.worker.service import Bench, LOOK_UP_EVERY_SECONDS
        from tests.test_socket_worker import KEY, AWorkerThatAnswers, needs_unix

        if not hasattr(os, "getuid"):                         # pragma: no cover - not posix
            pytest.skip("unix sockets only")
        assert LOOK_UP_EVERY_SECONDS > 0, "a loop that never looks up cannot be told to stop"

        bench = Bench(AWorkerThatAnswers(), "unix://" + str(tmp_path / "s" / "w.sock"), KEY,
                      only_uid=os.getuid())
        bench.open()
        thread = threading.Thread(target=bench.serve_forever, daemon=True)
        thread.start()

        bench.stop_serving()
        thread.join(timeout=10)
        assert not thread.is_alive(), (
            "the worker was told to stop and is still accepting")
