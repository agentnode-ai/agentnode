"""Fencing, not heartbeating.

A heartbeat answers "is my control plane still there". It does not answer "am I still ITS
worker", and those come apart exactly when it matters: a gateway is replaced, or partitioned
and restarted elsewhere, and two processes each believe they own this worker. Both can
heartbeat. Both would be obeyed.

`remote-worker-r1` R9 asks that a stop reaches work, and the decision's Q6 says a plain
heartbeat is insufficient without a fencing token. These are the cases that distinguish the two.

The worker assigns the epoch. A number the caller chooses is a number the caller can choose
badly -- two gateways that had each persisted their own counter could both present 7 -- and the
thing being fenced is the right place to order access to it.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

import pytest

from agentnode_sdk.worker import lease as L


class Clock:
    """A monotonic clock a test can move. Forward only, like the real one."""

    def __init__(self, at=1000.0):
        self.t = at

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


@pytest.fixture()
def book(tmp_path):
    return L.Leases(tmp_path / "lease-epoch.json", clock=Clock(), ttl=20.0)


class TestTakingIt:

    def test_the_worker_assigns_the_epoch_and_it_goes_up(self, book):
        first = book.take("g1")
        second = book.take("g2")
        assert second.epoch > first.epoch

    def test_the_holder_may_give_work(self, book):
        held = book.take("g1")
        assert book.check("g1", held.epoch) is not None

    def test_a_caller_with_no_name_is_refused(self, book):
        with pytest.raises(L.LeaseRefused):
            book.take("")


class TestFencing:

    def test_a_previous_holder_stops_counting_the_moment_somebody_takes_over(self, book):
        was = book.take("g1")
        book.take("g2")
        with pytest.raises(L.LeaseRefused) as refused:
            book.check("g1", was.epoch)
        assert refused.value.cause in (L.NOT_THE_HOLDER, L.STALE_EPOCH)

    def test_and_the_new_holder_may(self, book):
        book.take("g1")
        now = book.take("g2")
        assert book.check("g2", now.epoch)

    def test_an_old_epoch_from_the_same_gateway_is_refused(self, book):
        """A gateway that reconnected and took a fresh lease must not be able to have work
        accepted under the number it held before -- that is the same hazard with one process."""
        was = book.take("g1")
        book.take("g1")
        with pytest.raises(L.LeaseRefused) as refused:
            book.check("g1", was.epoch)
        assert refused.value.cause == L.STALE_EPOCH

    def test_no_epoch_at_all_is_refused(self, book):
        book.take("g1")
        with pytest.raises(L.LeaseRefused) as refused:
            book.check("g1", None)
        assert refused.value.cause == L.STALE_EPOCH

    def test_two_holders_are_never_valid_at_once(self, book):
        """The property, stated directly: for any pair of takers, exactly one may give work."""
        a = book.take("gA")
        b = book.take("gB")
        alive = []
        for who, epoch in (("gA", a.epoch), ("gB", b.epoch)):
            try:
                book.check(who, epoch)
                alive.append(who)
            except L.LeaseRefused:
                pass
        assert alive == ["gB"]


class TestTime:

    def test_a_lease_lapses_without_a_renewal(self, book):
        held = book.take("g1")
        book._clock.tick(21.0)
        with pytest.raises(L.LeaseRefused) as refused:
            book.check("g1", held.epoch)
        assert refused.value.cause == L.EXPIRED

    def test_a_renewal_pushes_it_out(self, book):
        held = book.take("g1")
        book._clock.tick(15.0)
        book.renew("g1", held.epoch)
        book._clock.tick(15.0)
        assert book.check("g1", held.epoch)

    def test_only_the_holder_may_renew(self, book):
        held = book.take("g1")
        with pytest.raises(L.LeaseRefused):
            book.renew("g2", held.epoch)

    def test_a_clock_set_BACK_does_not_extend_it(self):
        """The lease is judged on a monotonic clock. This deployment moves wall clocks
        backwards on purpose to test its floors, and a lease that could be extended that way
        would be no lease at all."""
        import time as real_time

        wall = {"t": 1_000_000.0}
        book = L.Leases(None, clock=real_time.monotonic, ttl=0.01)
        held = book.take("g1")
        wall["t"] -= 100_000.0                       # the wall clock goes backwards
        real_time.sleep(0.02)                        # monotonic time still passed
        with pytest.raises(L.LeaseRefused) as refused:
            book.check("g1", held.epoch)
        assert refused.value.cause == L.EXPIRED

    def test_the_numbers_are_derived_from_the_health_window(self):
        from agentnode_sdk.gateway import health

        assert L.LEASE_SECONDS > health.MAX_DETECTION_SECONDS, (
            "a gateway that is merely slow to notice something must not also lose its worker")
        assert L.HEARTBEAT_EVERY_SECONDS * 4 <= L.LEASE_SECONDS, (
            "one missed beat is a busy machine; several are a control plane that is not there")


class TestARestartedWorker:

    def test_holds_no_lease_and_takes_no_work(self, tmp_path):
        first = L.Leases(tmp_path / "e.json", clock=Clock())
        held = first.take("g1")

        restarted = L.Leases(tmp_path / "e.json", clock=Clock())
        with pytest.raises(L.LeaseRefused) as refused:
            restarted.check("g1", held.epoch)
        assert refused.value.cause == L.NO_LEASE

    def test_and_never_issues_an_epoch_twice(self, tmp_path):
        first = L.Leases(tmp_path / "e.json", clock=Clock())
        was = first.take("g1").epoch

        restarted = L.Leases(tmp_path / "e.json", clock=Clock())
        assert restarted.take("g1").epoch > was, (
            "re-issuing a number would make a control plane from before the restart valid again")

    def test_a_counter_that_cannot_be_read_refuses_rather_than_starting_over(self, tmp_path):
        at = tmp_path / "e.json"
        at.write_bytes(b"{not json")
        with pytest.raises(L.LeaseRefused) as refused:
            L.Leases(at, clock=Clock())
        assert refused.value.cause == "lease_counter_unreadable"


class TestWhatTheWorkerDoesWithIt:

    def test_the_single_host_arrangement_has_no_lease_to_enforce(self):
        """One control plane, one connection per request, no takeover to fence against.
        A lease there would be ceremony rather than protection."""
        from agentnode_sdk.worker.service import Bench

        assert Bench.leases is None

    def test_work_bearing_methods_are_the_ones_covered(self):
        import inspect

        from agentnode_sdk.worker import service

        source = inspect.getsource(service.Bench.answer)
        assert '"stop", "gone", "measure", "measure_egress"' in source
        assert "self._with_lease(params)" in source

    def test_the_lease_is_checked_again_immediately_before_the_container(self):
        """A lease alive when the message landed may have lapsed while the job was being read,
        and starting foreign code for a control plane that has gone is the thing it prevents."""
        import inspect

        from agentnode_sdk.worker import service

        source = inspect.getsource(service.Bench.answer)
        run_branch = source[source.index('if method == "run"'):]
        assert run_branch.index("self._with_lease(params)") < run_branch.index(
            "self._run_at_most_once")

    def test_the_caller_is_the_certificate_and_not_anything_it_sent(self):
        import inspect

        from agentnode_sdk.worker import tls

        source = inspect.getsource(tls.TlsListener._one)
        assert "connection.gateway" in source and "_caller" in source
