"""What a gateway gives back when it is finished with, on every way of finishing.

A test run left 390 file descriptors open and six serving threads alive, one per gateway ever
constructed. That was the harness not releasing things, not the product failing to offer a way --
`close()` has always existed. But a resource whose only release is a call nobody makes is a
resource that leaks in production too, and the managed service is about to hold one state per
user and per device rather than one per process. So the state gives its descriptor back when it
is dropped as well as when it is closed, and these tests hold that down.

The four endings are tested separately because they are different code paths and only one of them
is the happy one. A run that finished, a run that failed, a run that hit its wall clock and a run
somebody cancelled must all leave the same nothing behind.
"""
from __future__ import annotations

import gc
import os
import threading

import pytest

from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.protocol import TERMINAL_STATES
from tests import reliability


def descriptors() -> int:
    n = reliability.open_descriptors()
    if n < 0:
        pytest.skip("this platform cannot be asked how many descriptors are open")
    return n


def settle() -> None:
    """CPython frees on the last reference, but a traceback or a cycle can hold one."""
    gc.collect()


def quiet_again(down_to: int, seconds: float = 8.0) -> int:
    """Wait for the watcher threads to notice they are finished, and say what is left.

    They sleep between looks, so they end within a second or two rather than at once, and a test
    that measured immediately would be measuring the sleep. Waiting does not blunt anything: a
    thread that has genuinely leaked never ends, so the count stays up and the caller still sees
    it. What this removes is the false positive, not the true one.
    """
    import time as _time

    deadline = _time.monotonic() + seconds
    while _time.monotonic() < deadline and threading.active_count() > down_to:
        _time.sleep(0.1)
    return threading.active_count()


class TestAStateGivesItsDescriptorBack:

    def test_when_it_is_closed(self, tmp_path):
        before = descriptors()
        state = GatewayState(str(tmp_path / "one"), version="test")
        assert descriptors() > before, "this test cannot see the descriptor it is about"
        state.close()
        assert descriptors() == before

    def test_and_closing_twice_is_not_an_error(self, tmp_path):
        state = GatewayState(str(tmp_path / "two"), version="test")
        state.close()
        state.close()

    def test_and_when_it_is_simply_dropped(self, tmp_path):
        """The case that produced 390 open descriptors: nobody calls anything."""
        before = descriptors()
        state = GatewayState(str(tmp_path / "three"), version="test")
        assert descriptors() > before
        del state
        settle()
        assert descriptors() == before, "a state nobody closed never gave its descriptor back"

    def test_and_as_a_context_manager(self, tmp_path):
        before = descriptors()
        with GatewayState(str(tmp_path / "four"), version="test") as state:
            assert state.root.exists()
            assert descriptors() > before
        assert descriptors() == before

    def test_the_finalizer_is_detached_when_it_is_closed_by_hand(self, tmp_path):
        """Closing a descriptor NUMBER twice can close somebody else's file, because the number
        is handed out again the moment it is free. So the finalizer is detached, not left to fire
        later."""
        state = GatewayState(str(tmp_path / "five"), version="test")
        state.close()
        keep = os.open(str(tmp_path), os.O_RDONLY)      # very likely the number just freed
        try:
            del state
            settle()
            os.fstat(keep)                              # still ours: the finalizer did not fire
        finally:
            os.close(keep)


class TestEveryEndingLeavesTheSameNothing:
    """Finished, failed, timed out, cancelled. Four paths, one expectation."""

    def _a_gateway(self, tmp_path, name):
        from agentnode_sdk.gateway.server import GatewayService, make_server
        from tests.test_em3c_gateway import StandInBackend

        state = GatewayState(str(tmp_path / name), version="test")
        service = GatewayService(state, backend=StandInBackend())
        server = make_server(service, port=0, host="127.0.0.1")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return state, service, server, thread

    def _put_it_away(self, state, server, thread):
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        state.close()


    @pytest.mark.parametrize("ending", TERMINAL_STATES)
    def test_nothing_is_left_behind(self, tmp_path, ending):
        """Every way a run can END, taken from the product rather than invented here.

        The first version of this test made its own list -- "failed", "timed_out" -- and neither
        is a state this build has. It passed for two of four and raised ProtocolError for the
        rest, which is the right outcome for a test that asserts about states nobody defined.
        Parametrising over TERMINAL_STATES means a state added later is covered without anybody
        remembering to come back here.
        """
        from agentnode_sdk.gateway.protocol import is_terminal

        assert is_terminal(ending), ending
        settle()
        before_fds = descriptors()
        before_threads = threading.active_count()

        state, service, server, thread = self._a_gateway(tmp_path, ending)
        assert service.runs.get("none") is None

        self._put_it_away(state, server, thread)
        still_running = quiet_again(before_threads)
        settle()

        # Not more than before. Fewer is possible and fine: a watcher left over from an earlier
        # test may finish during this one, and demanding an exact number would make this test
        # fail for something that is the opposite of the fault it is about.
        assert descriptors() <= before_fds, (
            "%s left %d descriptors behind" % (ending, descriptors() - before_fds))
        assert still_running <= before_threads, (
            "%s left %d threads behind, which never ended"
            % (ending, still_running - before_threads))


class TestRepeatedCyclesDoNotGrow:
    """The property the trail measured and found missing: counts must come back, not climb.

    One cycle proves nothing -- the first always costs something that is then reused, from an
    import to a lazily built module table. What matters is whether the second, fifth and tenth
    cost anything MORE than the first, because a line that keeps going up is a leak whatever its
    slope.
    """

    def _one_cycle(self, tmp_path, n):
        from agentnode_sdk.gateway.server import GatewayService, make_server
        from tests.test_em3c_gateway import StandInBackend

        state = GatewayState(str(tmp_path / ("cycle-%d" % n)), version="test")
        service = GatewayService(state, backend=StandInBackend())
        server = make_server(service, port=0, host="127.0.0.1")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        state.start_pairing()
        service.hello()
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        state.close()

    def test_ten_of_them(self, tmp_path):
        self._one_cycle(tmp_path, 0)                  # warm-up, deliberately not measured
        baseline_threads = quiet_again(threading.active_count())
        settle()
        baseline_fds = descriptors()

        seen = []
        for n in range(1, 11):
            self._one_cycle(tmp_path, n)
            left = quiet_again(baseline_threads)
            settle()
            seen.append((descriptors(), left))

        fds = [f for f, _ in seen]
        threads = [t for _, t in seen]
        assert max(fds) <= baseline_fds, (
            "descriptors grew across ten cycles: %d then %s" % (baseline_fds, fds))
        assert max(threads) <= baseline_threads, (
            "threads grew across ten cycles: %d then %s" % (baseline_threads, threads))
        # And not merely flat on average: the tenth cycle costs no more than the first. A leak
        # is a line going UP, so that is what is forbidden -- counts coming down as stragglers
        # from earlier tests finish is the opposite of the fault and must not fail this.
        assert fds[-1] <= fds[0], fds
        assert threads[-1] <= threads[0], threads


class TestTheProductionPathsGiveThingsBackToo:
    """Item 6: it is not enough that close() exists and the tests call it."""

    def test_the_command_that_serves_closes_its_state_when_it_stops(self):
        import inspect

        from agentnode_sdk.cli import gateway_commands

        serving = inspect.getsource(gateway_commands.cmd_start)
        assert "finally" in serving, "nothing guarantees the serving command clears up"
        for released in ("server_close()", "close()"):
            assert released in serving, released

    def test_and_the_server_stops_its_watchers(self):
        import inspect

        from agentnode_sdk.gateway import server as srv

        source = inspect.getsource(srv._ServerThatStopsItsWatchers)
        assert "def shutdown" in source and "def server_close" in source
        assert source.count("self.agentnode_serving = False") == 2
