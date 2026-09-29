"""The lock was doing its job. The call it guards could not give up in time.

"another change to this gateway's policy is already running" was reported for half an hour
after a transport loss, with no lock file on disk and one process running -- which looked
like a lock held in memory. It was not. `ActivationLock` is a file lock and `_transact` holds
it in a `with`, so it is released on every path including `BaseException`.

What actually happened: `measure` waited 1800 seconds on a connection whose peer had gone
without sending a reset, and `ActivationLock.stale_after` was ALSO 1800 seconds. So the
holder was genuinely alive, `_still_running` said so, and the staleness backstop could not
fire before the call it backstops had given up. Every later measurement was refused
correctly, which is what made it hard to see.

Two things follow, and both are tested here: the guarded call must be bounded well inside
the backstop, and a peer that vanishes silently must be noticed long before either.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

import inspect
import socket

from agentnode_sdk.gateway import activation as A
from agentnode_sdk.worker import remote as R


class TestTheBackstopCanActuallyFire:

    def test_the_guarded_call_is_bounded_inside_the_backstop(self):
        """The inequality, not the numbers: moving one without the other goes red."""
        assert R.MEASURE_SECONDS < A.ActivationLock.STALE_AFTER_SECONDS

    def test_with_room_to_spare_rather_than_by_a_second(self):
        margin = A.ActivationLock.STALE_AFTER_SECONDS - R.MEASURE_SECONDS
        assert margin >= 120.0, (
            "a backstop that fires just after the call gives up leaves no room for the "
            "call to finish tidying")

    def test_a_lock_made_the_ordinary_way_uses_it(self, tmp_path):
        assert A.ActivationLock(tmp_path).stale_after == A.ActivationLock.STALE_AFTER_SECONDS

    def test_the_measurement_no_longer_waits_half_an_hour(self):
        source = inspect.getsource(R.SocketWorker.measure)
        assert "1800" not in source
        assert "MEASURE_SECONDS" in source

    def test_nor_does_the_egress_measurement(self):
        source = inspect.getsource(R.SocketWorker.measure_egress)
        assert "1800" not in source
        assert "MEASURE_SECONDS" in source


class TestTheLockWasNeverTheProblem:
    """Guards for the thing the first diagnosis got wrong, so a later reader does not
    'fix' a lock that works."""

    def test_it_is_released_on_every_path(self):
        source = inspect.getsource(A.ActivationLock.__exit__)
        assert "_quiet_unlink" in source

    def test_and_the_transaction_holds_it_in_a_with(self):
        from agentnode_sdk.gateway import server

        source = inspect.getsource(server.GatewayService._transact)
        assert "with ActivationLock(" in source

    def test_a_refused_acquisition_does_not_delete_somebody_elses_lock(self, tmp_path):
        first = A.ActivationLock(tmp_path)
        first.__enter__()
        try:
            second = A.ActivationLock(tmp_path)
            try:
                second.__enter__()
            except A.ActivationError:
                pass
            else:                                             # pragma: no cover
                raise AssertionError("two holders at once")
            assert first.path.exists(), "the refused caller removed the live lock"
        finally:
            first.__exit__(None, None, None)
        assert not first.path.exists()


class TestASilentlyDeadPeerIsNoticed:
    """A bound alone is not enough: with no reset, `recv` learns nothing until it expires."""

    def test_keepalive_is_asked_for_on_the_connection_the_gateway_uses(self):
        source = inspect.getsource(R.TlsWorker._open)
        assert "_ask_the_kernel_to_notice_a_dead_peer" in source

    def test_the_three_settings_only_mean_something_together(self):
        assert R.KEEPALIVE_IDLE_SECONDS > 0
        assert R.KEEPALIVE_INTERVAL_SECONDS > 0
        assert R.KEEPALIVE_FAILURES > 0

    def test_and_they_notice_well_inside_the_measurement_budget(self):
        worst = (R.KEEPALIVE_IDLE_SECONDS
                 + R.KEEPALIVE_INTERVAL_SECONDS * R.KEEPALIVE_FAILURES)
        assert worst < R.MEASURE_SECONDS / 2, (
            "keepalive that fires near the timeout is not an improvement on the timeout")

    def test_it_is_actually_applied_to_a_real_socket(self):
        left, right = socket.socketpair()
        try:
            R._ask_the_kernel_to_notice_a_dead_peer(left)
            assert left.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
        finally:
            left.close()
            right.close()

    def test_and_a_platform_without_the_options_is_not_a_failure_to_connect(self):
        """Best effort on purpose: a kernel missing TCP_KEEPIDLE must not stop a deployment."""

        class Awkward:
            def setsockopt(self, *_a, **_k):
                raise OSError("not here")

        R._ask_the_kernel_to_notice_a_dead_peer(Awkward())
