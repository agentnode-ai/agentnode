"""A heartbeat that asks for nothing.

Two machines found this and one host could not have. `Leases.renew` moves the expiry;
`Leases.check` only agrees that it has not passed yet. The worker's `renew_lease` handler
called `check`. So every beat was answered successfully, `until` never moved, and the lease
died `LEASE_SECONDS` after it was taken however many beats arrived. `Leases.renew` had no
caller anywhere in the product or the tests -- the absence WAS the defect.

What that cost, measured on two real machines: a deployment idle for sixty seconds refused
all further work until its gateway process was restarted, and a 45-second job was stopped
underneath itself at 29.4 seconds (20s lease + 1s watch tick + 10s stop/appear).

And when it lapsed nothing recovered, because the client cached its epoch forever and the
refusal's cause was flattened into a bare `WorkerUnreachable` that nothing could branch on.

NOT A HOST-ISOLATION TEST: one process, one kernel. The two-machine evidence is separate.
"""
from __future__ import annotations

import inspect
import pathlib

import pytest

from agentnode_sdk.worker import lease as L
from agentnode_sdk.worker import remote as R
from agentnode_sdk.worker import protocol as wire
from agentnode_sdk.worker import service as S

KEY = b"k" * 32


class Clock:
    """A monotonic clock a test can move. Forward only, like the real one."""

    def __init__(self, at=1000.0):
        self.t = at

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


def bench_with_leases(tmp_path, clock, ttl=20.0):
    bench = S.Bench(object(), "unix:///nowhere.sock", KEY, only_uid=None)
    bench.leases = L.Leases(tmp_path / "lease-epoch.json", clock=clock, ttl=ttl)
    bench.journal = None
    bench._caller = "g1"
    return bench


class TestRenewingActuallyRenews:
    """The defect, and the only test here that would have caught it."""

    def test_a_renewed_lease_outlives_its_own_lifetime(self, tmp_path):
        clock = Clock()
        bench = bench_with_leases(tmp_path, clock, ttl=20.0)
        took = bench.answer("take_lease", {})
        epoch = took["epoch"]

        # Beat at fifteen seconds, twice, as the client's heartbeat would.
        for _ in range(2):
            clock.tick(15.0)
            bench.answer("renew_lease", {"lease_epoch": epoch})

        # Thirty seconds have passed on a twenty-second lease. Against the unfixed handler
        # this raises `lease_expired`, because `check` never moved `until`.
        assert bench.leases.check("g1", epoch) is not None

    def test_and_an_unrenewed_one_does_not(self, tmp_path):
        """The other half: if this passed too, the test above would prove nothing."""
        clock = Clock()
        bench = bench_with_leases(tmp_path, clock, ttl=20.0)
        epoch = bench.answer("take_lease", {})["epoch"]
        clock.tick(30.0)
        with pytest.raises(L.LeaseRefused) as refused:
            bench.leases.check("g1", epoch)
        assert refused.value.cause == L.EXPIRED

    def test_the_handler_renews_rather_than_checks(self, tmp_path):
        source = inspect.getsource(S.Bench._renew_the_lease)
        assert "self.leases.renew(" in source
        assert "self.leases.check(" not in source

    def test_renewing_a_worker_that_keeps_no_leases_refuses_by_name(self):
        """It used to reach `held.holder` on a None and come out as an internal error."""
        bench = S.Bench(object(), "unix:///nowhere.sock", KEY, only_uid=None)
        bench.leases = None
        with pytest.raises(wire.ProtocolError) as refused:
            bench.answer("renew_lease", {"lease_epoch": 1})
        assert refused.value.code == wire.NO_LEASE

    def test_the_renewal_keeps_the_same_epoch(self, tmp_path):
        """Renewing must not fence the holder off from its own in-flight work."""
        clock = Clock()
        bench = bench_with_leases(tmp_path, clock, ttl=20.0)
        epoch = bench.answer("take_lease", {})["epoch"]
        clock.tick(5.0)
        again = bench.answer("renew_lease", {"lease_epoch": epoch})
        assert again["epoch"] == epoch

    def test_somebody_elses_epoch_is_still_refused(self, tmp_path):
        clock = Clock()
        bench = bench_with_leases(tmp_path, clock, ttl=20.0)
        bench.answer("take_lease", {})
        with pytest.raises(wire.ProtocolError):
            bench.answer("renew_lease", {"lease_epoch": 999})


class TestNoDeadLeaseCode:
    """`Leases.renew` and `lease_from_the_worker` were both callerless, and each cost a
    defect that only two machines could find. This is the cheap static guard that would
    have caught either one."""

    def _package_source(self) -> str:
        here = pathlib.Path(R.__file__).resolve().parent.parent
        out = []
        for path in sorted(here.rglob("*.py")):
            out.append(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(out)

    def test_leases_renew_has_a_caller_in_the_product(self):
        # `.renew(` alone would match `pki_commands.py`'s certificate renewal and pass against
        # the unfixed code -- a test that cannot fail. It has to name THIS renew.
        assert "leases.renew(" in self._package_source(), (
            "Leases.renew is unreachable again: the heartbeat would extend nothing")

    def test_the_callerless_lease_helper_is_gone(self):
        assert not hasattr(R.SocketWorker, "lease_from_the_worker")


class TestARefusalSaysWhichKind:
    """A lapsed lease is recoverable; an unauthenticated caller is not. Telling them apart
    needs the code as a value, not folded into a sentence."""

    def worker(self):
        return R.SocketWorker("unix:///nowhere.sock", KEY)

    def test_the_cause_survives(self):
        with pytest.raises(R.WorkerUnreachable) as refused:
            self.worker()._interpret(
                {"ok": False, "error": wire.NO_LEASE, "detail": "this worker holds no lease"})
        assert refused.value.cause == wire.NO_LEASE

    def test_and_so_does_the_detail(self):
        with pytest.raises(R.WorkerUnreachable) as refused:
            self.worker()._interpret(
                {"ok": False, "error": wire.UNAUTHENTICATED, "detail": "who are you"})
        assert refused.value.cause == wire.UNAUTHENTICATED
        assert refused.value.detail == "who are you"


class TestWhatIsWorthRetaking:
    """Narrow on purpose. Retrying a refusal a lease cannot answer is a loop."""

    def worker(self, *, leasing=True):
        w = R.SocketWorker("unix:///nowhere.sock", KEY)
        w._leasing = leasing
        return w

    def refusal(self, cause):
        exc = R.WorkerUnreachable("refused")
        exc.cause = cause
        return exc

    def test_a_lapsed_lease_on_a_job_bearing_call_is(self):
        w = self.worker()
        assert w._worth_retaking_the_lease("run", self.refusal(wire.NO_LEASE), already=False)

    def test_but_only_once(self):
        w = self.worker()
        assert not w._worth_retaking_the_lease("run", self.refusal(wire.NO_LEASE), already=True)

    def test_not_a_method_that_carries_no_work(self):
        w = self.worker()
        assert not w._worth_retaking_the_lease(
            "result", self.refusal(wire.NO_LEASE), already=False)

    def test_not_on_a_worker_that_does_not_lease(self):
        w = self.worker(leasing=False)
        assert not w._worth_retaking_the_lease(
            "run", self.refusal(wire.NO_LEASE), already=False)

    @pytest.mark.parametrize("cause", [wire.UNAUTHENTICATED, wire.MALFORMED, wire.INTERNAL])
    def test_and_not_for_a_refusal_a_lease_would_not_answer(self, cause):
        w = self.worker()
        assert not w._worth_retaking_the_lease("run", self.refusal(cause), already=False)


class TestForgettingADeadLease:
    """`_lease_epoch` was assigned in exactly two places -- None at construction, the epoch at
    acquisition -- and nothing ever put it back. That is why only a process restart helped."""

    def test_the_epoch_is_dropped(self):
        w = R.SocketWorker("unix:///nowhere.sock", KEY)
        w._lease_epoch = 7
        w._forget_the_lease()
        assert w._lease_epoch is None

    def test_and_the_heartbeat_with_it(self):
        w = R.SocketWorker("unix:///nowhere.sock", KEY)
        w._lease_epoch = 7
        w._renew_within = 0.01
        w._start_the_heartbeat()
        assert w._heartbeat is not None
        w._forget_the_lease()
        assert w._heartbeat is None

    def test_so_a_later_acquisition_is_not_short_circuited(self):
        """`_hold_a_lease` returns the cached epoch when one is set. If forgetting did not
        clear it, the next job would present the dead number again -- the whole symptom."""
        w = R.SocketWorker("unix:///nowhere.sock", KEY)
        w._lease_epoch = 7
        w._forget_the_lease()
        asked = []

        def fake_ask(method, params, **kw):
            asked.append(method)
            return {"epoch": 8, "renew_within": 5.0}

        w._ask = fake_ask
        assert w._hold_a_lease() == 8
        assert asked == ["take_lease"]


class TestTheRetryIsWiredIn:
    """The predicate above is only worth having if `_ask` consults it."""

    def test_ask_retakes_and_retries(self):
        source = inspect.getsource(R.SocketWorker._ask)
        assert "_worth_retaking_the_lease" in source
        assert "_forget_the_lease()" in source
        assert "_lease_was_retaken=True" in source

    def test_and_does_not_extend_the_callers_budget(self):
        """A retry inside a call that was allowed `wait` seconds must fit inside it."""
        source = inspect.getsource(R.SocketWorker._ask)
        assert "wait - (time.time() - started_asking)" in source
