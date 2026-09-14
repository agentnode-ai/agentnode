"""Cancellation as a mechanism a service can be run on, not as a demonstration that it works.

The first version moved the waiting off the caller by starting a thread per request. That fixed
the visible problem and introduced a quieter one: anybody who could ask for a cancellation could
ask for a thread, and nothing counted how many. These are the properties that stop that being
true -- bounded, one per run, owned, durable, and rate limited -- each established by a test that
fails without the mechanism rather than by the arrangement looking sensible.

`carry_out` is a stand-in here on purpose. What it does is the gateway's business and has its own
tests; what is under test here is everything AROUND it, and a real teardown would make these
measure Docker.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from agentnode_sdk.access import stopping as pool


class ASandboxThatTakesItsTime:
    """Stands in for the teardown. The test decides when it finishes and how it ends."""

    def __init__(self, settles=True, blows_up=False):
        self.asked = []
        self.may_finish = threading.Event()
        self.started = threading.Event()
        self.settles = settles
        self.blows_up = blows_up
        self._lock = threading.Lock()
        self.at_once = 0
        self.most_at_once = 0

    def __call__(self, run_id):
        with self._lock:
            self.asked.append(run_id)
            self.at_once += 1
            self.most_at_once = max(self.most_at_once, self.at_once)
        self.started.set()
        try:
            self.may_finish.wait(timeout=20)
            if self.blows_up:
                raise OSError("the worker went away")
            return self.settles
        finally:
            with self._lock:
                self.at_once -= 1


@pytest.fixture()
def sandbox(tmp_path):
    made = []

    def build(carry_out=None, **kw):
        it = pool.Stopping(str(tmp_path), carry_out or (lambda run_id: True), **kw)
        made.append(it)
        return it

    yield build
    for it in made:
        it.close()


# --------------------------------------------------------------------- bounded and idempotent

class TestOnePerRun:

    def test_a_storm_against_one_run_produces_exactly_one_cancellation(self, sandbox):
        """The obvious attack, and the ordinary case: a client polling by asking again."""
        teardown = ASandboxThatTakesItsTime()
        stopping = sandbox(teardown)

        first = stopping.ask("run-1", by="a device")
        assert teardown.started.wait(timeout=5)
        answers = [stopping.ask("run-1", by="a device") for _ in range(200)]

        assert all(a is first for a in answers), "a repeat started a second cancellation"
        teardown.may_finish.set()
        _until(lambda: first.state == pool.SETTLED)
        assert teardown.asked == ["run-1"], teardown.asked
        assert first.attempts == 1

    def test_and_repeating_it_costs_no_budget(self, sandbox):
        """It must not: repeating is how a client watches its own cancellation."""
        teardown = ASandboxThatTakesItsTime()
        stopping = sandbox(teardown)
        stopping.ask("run-1", by="a device")
        assert teardown.started.wait(timeout=5)

        for _ in range(pool.Stopping.PER_DEVICE * 5):
            stopping.ask("run-1", by="a device")          # would refuse if it were counted

        teardown.may_finish.set()

    def test_many_threads_asking_at_once_still_get_one(self, sandbox):
        teardown = ASandboxThatTakesItsTime()
        stopping = sandbox(teardown)
        seen, ready, go = [], threading.Barrier(12), threading.Event()

        def asker():
            ready.wait(timeout=10)
            go.wait(timeout=10)
            try:
                seen.append(stopping.ask("run-1", by="a device"))
            except pool.TooManyStops as refused:
                seen.append(refused)

        askers = [threading.Thread(target=asker, daemon=True) for _ in range(12)]
        for a in askers:
            a.start()
        go.set()
        for a in askers:
            a.join(timeout=10)

        stops = [s for s in seen if isinstance(s, pool.Stop)]
        assert len(stops) == 12, seen
        assert len({id(s) for s in stops}) == 1, "concurrent askers got different cancellations"
        teardown.may_finish.set()
        _until(lambda: stops[0].state == pool.SETTLED)
        assert teardown.asked == ["run-1"]


class TestNothingGrowsWithoutLimit:

    def test_the_hands_are_a_fixed_number_however_many_runs_are_stopping(self, sandbox):
        """Measured on THIS pool, not on the process.

        The first version counted every thread whose name began with "agentnode-stopping" and
        required exactly two. That passes alone and fails in a full run, because other tests have
        pools of their own that are legitimately alive -- so it was measuring the suite rather
        than the property, and a review was right to refuse it as evidence of anything.

        What the property actually says is that ONE pool has a fixed number of hands however many
        runs it is asked to stop. That is what this measures: the pool's own hands, and how many
        teardowns it ever had in flight at once.
        """
        teardown = ASandboxThatTakesItsTime()
        stopping = sandbox(teardown, hands=2)
        before = _hands()
        for n in range(40):
            stopping.ask("run-%d" % n, by="device-%d" % n)

        assert teardown.started.wait(timeout=5)
        mine = [t for t in stopping._hands if t.is_alive()]
        assert len(mine) == 2, [t.name for t in mine]
        # ... and it started no others: the process gained exactly this pool's two.
        assert _hands() - before == 2
        assert teardown.most_at_once <= 2, teardown.most_at_once
        teardown.may_finish.set()

    def test_and_two_pools_have_two_pairs_of_hands_rather_than_four_pools_worth(self, sandbox):
        """The global statement, made honestly: N pools cost N times the fixed number, not more.

        Worth its own test because the per-pool check above cannot see a pool that starts hands
        belonging to nobody.
        """
        first, second = ASandboxThatTakesItsTime(), ASandboxThatTakesItsTime()
        before = _hands()
        one, two = sandbox(first, hands=2), sandbox(second, hands=2)
        for n in range(20):
            one.ask("a-%d" % n, by="a")
            two.ask("b-%d" % n, by="b")
        assert first.started.wait(timeout=5) and second.started.wait(timeout=5)
        assert _hands() - before == 4, _hands() - before
        first.may_finish.set()
        second.may_finish.set()

    def test_a_device_asking_for_too_many_new_stops_is_refused(self, sandbox):
        stopping = sandbox(lambda run_id: True)
        for n in range(pool.Stopping.PER_DEVICE):
            stopping.ask("run-%d" % n, by="one device")
        with pytest.raises(pool.TooManyStops):
            stopping.ask("one-too-many", by="one device")

    def test_but_another_device_is_not_punished_for_it(self, sandbox):
        stopping = sandbox(lambda run_id: True)
        for n in range(pool.Stopping.PER_DEVICE):
            stopping.ask("run-%d" % n, by="the noisy one")
        assert stopping.ask("mine", by="somebody else") is not None

    def test_a_full_queue_is_refused_rather_than_absorbed(self, sandbox):
        teardown = ASandboxThatTakesItsTime()
        stopping = sandbox(teardown, hands=1, room=4)
        stopping.PER_DEVICE = 10_000                     # the queue is what is under test here
        with pytest.raises(pool.TooManyStops) as refused:
            for n in range(200):
                stopping.ask("run-%d" % n, by="a device")
        assert "as many runs as it can at once" in str(refused.value)
        teardown.may_finish.set()


# --------------------------------------------------------------------------------- durability

class TestARestartDoesNotForget:

    def test_what_was_being_stopped_is_written_down_before_it_is_attempted(self, sandbox):
        teardown = ASandboxThatTakesItsTime()
        stopping = sandbox(teardown)
        stopping.ask("run-1", by="a device")
        assert teardown.started.wait(timeout=5)
        assert stopping.unfinished() == ["run-1"]
        teardown.may_finish.set()

    def test_and_forgotten_only_once_the_sandbox_is_confirmed_gone(self, sandbox):
        teardown = ASandboxThatTakesItsTime(settles=True)
        stopping = sandbox(teardown)
        stop = stopping.ask("run-1", by="a device")
        teardown.may_finish.set()
        _until(lambda: stop.state == pool.SETTLED)
        assert stopping.unfinished() == []

    def test_a_teardown_that_could_not_confirm_stays_written_down(self, sandbox):
        """The whole point. An unconfirmed teardown that was forgotten is a container left
        running with nothing anywhere that refers to it."""
        teardown = ASandboxThatTakesItsTime(settles=False)
        stopping = sandbox(teardown)
        stop = stopping.ask("run-1", by="a device")
        teardown.may_finish.set()
        _until(lambda: stop.state == pool.GAVE_UP)
        assert stopping.unfinished() == ["run-1"], "an unconfirmed teardown was forgotten"

    def test_a_gateway_killed_mid_cancellation_picks_it_up_again(self, sandbox):
        """A restart, as far as this object is concerned: the first one is abandoned without
        being closed, and a second one is built over the same directory."""
        abandoned = ASandboxThatTakesItsTime()
        first = sandbox(abandoned)
        first.ask("run-1", by="a device")
        assert abandoned.started.wait(timeout=5)
        assert first.unfinished() == ["run-1"]
        abandoned.may_finish.set()

        after = ASandboxThatTakesItsTime()
        second = sandbox(after)
        assert second.pick_up_where_it_left_off() == ["run-1"]
        assert after.started.wait(timeout=5), "the restarted gateway never tried again"
        after.may_finish.set()
        _until(lambda: second.about("run-1").state == pool.SETTLED)
        assert second.unfinished() == []

    def test_an_unreadable_journal_is_not_read_as_nothing_to_do(self, sandbox, tmp_path):
        """This test was named for the right property and asserted the opposite one.

        It required `unfinished()` to answer the empty list, which IS reading an unreadable
        journal as nothing to do -- the exact thing the name forbids. A review caught the code;
        the test had been agreeing with it. It now requires the refusal, and keeps the half it
        always had right: the unreadable file is left alone so a person can still look at it.
        """
        (tmp_path / "stopping.json").write_text("{not json", encoding="utf-8")
        stopping = sandbox(lambda run_id: True)
        with pytest.raises(pool.JournalUnavailable):
            stopping.unfinished()
        assert (tmp_path / "stopping.json").read_text(encoding="utf-8") == "{not json"


# ------------------------------------------------------------------------------ when it fails

class TestAFailedStopDoesNotLookLikeOneThatWorked:

    def test_a_teardown_that_raises_is_recorded_as_a_problem_and_not_as_settled(self, sandbox):
        teardown = ASandboxThatTakesItsTime(blows_up=True)
        stopping = sandbox(teardown)
        stop = stopping.ask("run-1", by="a device")
        teardown.may_finish.set()
        _until(lambda: stop.state == pool.GAVE_UP)

        assert stop.settled is None, "a failed teardown reported a confirmation"
        assert "the worker went away" in stop.problem
        assert stopping.unfinished() == ["run-1"]

    def test_and_asking_again_is_a_retry_rather_than_a_duplicate(self, sandbox):
        teardown = ASandboxThatTakesItsTime(blows_up=True)
        stopping = sandbox(teardown)
        stop = stopping.ask("run-1", by="a device")
        teardown.may_finish.set()
        _until(lambda: stop.state == pool.GAVE_UP)

        again = stopping.ask("run-1", by="a device")
        assert again is not stop
        assert again.attempts == 1, "a retry forgot what had already been tried"
        _until(lambda: again.state == pool.GAVE_UP)
        assert again.attempts == 2

    def test_one_run_failing_does_not_stop_the_others(self, sandbox):
        done = []

        def teardown(run_id):
            if run_id == "bad":
                raise OSError("no")
            done.append(run_id)
            return True

        stopping = sandbox(teardown)
        for name in ("bad", "a", "b", "c"):
            stopping.ask(name, by="a device")
        _until(lambda: sorted(done) == ["a", "b", "c"])


# --------------------------------------------------------------------------------- ownership

class TestNothingIsLeftRunning:

    def test_a_gateway_that_never_cancels_anything_has_no_hands(self, sandbox):
        before = _hands()
        sandbox(lambda run_id: True)
        assert _hands() == before, "threads were started before there was anything to do"

    def test_and_everything_started_is_joined_by_close(self, sandbox, tmp_path):
        before = _hands()
        stopping = pool.Stopping(str(tmp_path), lambda run_id: True)
        stopping.ask("run-1", by="a device")
        _until(lambda: _hands() > before)
        stopping.close()
        assert _hands() == before, "a hand outlived the object that started it"

    def test_closing_twice_is_not_an_error(self, tmp_path):
        stopping = pool.Stopping(str(tmp_path), lambda run_id: True)
        stopping.close()
        stopping.close()

    def test_and_it_does_not_start_new_ones_while_shutting_down(self, tmp_path):
        stopping = pool.Stopping(str(tmp_path), lambda run_id: True)
        stopping.close()
        with pytest.raises(pool.TooManyStops):
            stopping.ask("run-1", by="a device")

    def test_used_as_a_context_manager_it_gives_everything_back(self, tmp_path):
        before = _hands()
        with pool.Stopping(str(tmp_path), lambda run_id: True) as stopping:
            stopping.ask("run-1", by="a device")
            _until(lambda: _hands() > before)
        assert _hands() == before


def _hands() -> int:
    return len([t for t in threading.enumerate()
                if t.name.startswith("agentnode-stopping") and t.is_alive()])


def _until(it_is_true, seconds: float = 15.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if it_is_true():
            return
        time.sleep(0.02)
    raise AssertionError("it never became true within %gs" % seconds)


class TestTheServiceOwnsWhatItStarts:
    """Daemon status is not lifecycle ownership.

    A daemon thread means the interpreter will not wait for it at exit, which is a different
    question from whether the thing that created it knows it exists. A review pointed out that
    run threads were started and never held, so one could outlive the service -- and the same
    standard that applies to the cancellation pool applies to them.
    """

    def a_service(self, tmp_path, held=None):
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService
        from tests.test_em3c_gateway import StandInBackend, _store_measurement

        state = GatewayState(str(tmp_path / "state"), version="test")
        service = GatewayService(state, backend=held or StandInBackend())
        _store_measurement(service)
        return state, service

    def test_a_run_thread_is_held_while_it_runs(self, tmp_path):
        import base64
        import hashlib

        from agentnode_sdk.access import dispatch
        from tests.test_cancel_is_not_a_wait import ABackendThatKeepsRunning

        backend = ABackendThatKeepsRunning()
        state, service = self.a_service(tmp_path, held=backend)
        try:
            token = state.redeem_pairing(state.start_pairing(), client_name="a laptop")
            who = dispatch.identify(service, token)
            code = b"print('x')"
            told = dispatch.dispatch("prepare", {
                "command": ["python", "-c", "print('x')"],
                "artifact_sha256": hashlib.sha256(code).hexdigest(),
                "artifact_bytes": len(code), "wall_clock_s": 30}, who, service=service)
            dispatch.dispatch("submit", {
                "run_id": "r" * 32, "artifact": base64.b64encode(code).decode("ascii"),
                "command": ["python", "-c", "print('x')"], "wall_clock_s": 30,
                "accepted_disclosure": told["accepted_disclosure"]}, who, service=service)

            _until(lambda: len(service._running) == 1)
            assert [t.name for t in service._running][0].startswith("agentnode-run-")

            backend.may_finish_run.set()
            # ... and it takes itself out again when it is done, so what is held is what is
            # actually running rather than everything ever started.
            _until(lambda: not service._running)
        finally:
            backend.may_finish_run.set()
            service.close()
            state.close()

    def test_close_waits_for_them_and_says_what_it_could_not_get_back(self, tmp_path):
        state, service = self.a_service(tmp_path)
        try:
            assert service.close() == [], "close reported a thread it had not started"
            # Idempotent, like the pool's.
            assert service.close() == []
        finally:
            state.close()

    def test_and_a_thread_that_will_not_end_is_reported_rather_than_abandoned(self, tmp_path):
        """The honest half. close() is bounded, so it cannot promise every thread ended -- what
        it must not do is fall silent about one it left behind."""
        state, service = self.a_service(tmp_path)
        forever = threading.Event()
        stuck = threading.Thread(target=lambda: forever.wait(timeout=30), daemon=True,
                                 name="agentnode-run-stuck")
        try:
            service.CLOSE_SECONDS = 0.2
            with service._running_lock:
                service._running.add(stuck)
            stuck.start()
            assert service.close() == ["agentnode-run-stuck"]
        finally:
            forever.set()
            stuck.join(timeout=5)
            state.close()

    def test_close_actually_waits_rather_than_only_reporting(self, tmp_path):
        """The test above states what close() SAYS; this one states that it waits.

        They are different properties and the first does not imply the second: a close that
        joined nothing would still report a stuck thread correctly, because a thread that will
        not end is alive whether or not anybody waited for it. What distinguishes waiting is a
        thread that ends SHORTLY AFTER close is called -- waiting turns it into nothing left
        behind, not waiting reports it as abandoned when it was about to finish on its own.
        """
        state, service = self.a_service(tmp_path)
        nearly_done = threading.Event()
        soon = threading.Thread(target=lambda: nearly_done.wait(timeout=30), daemon=True,
                                name="agentnode-run-soon")
        try:
            with service._running_lock:
                service._running.add(soon)
            soon.start()
            threading.Timer(0.4, nearly_done.set).start()
            assert service.close() == [], "close returned before the thread had ended"
            assert not soon.is_alive()
        finally:
            nearly_done.set()
            soon.join(timeout=5)
            state.close()


class TestAJournalThatCannotBeKeptIsSaidOutLoud:
    """Unreadable is not empty, and a failed write is not a success.

    A review found both directions fail open: a write error was swallowed with a comment
    defending it, and `unfinished()` turned an unreadable journal into "nothing was being
    stopped". Either one lets a gateway come up clean while containers it can no longer account
    for are still running.
    """

    def test_a_journal_that_cannot_be_read_is_not_an_empty_one(self, tmp_path):
        from agentnode_sdk.access import stopping as pool

        (tmp_path / "stopping.json").write_text("{not json at all", encoding="utf-8")
        it = pool.Stopping(str(tmp_path), lambda run_id: True)
        try:
            with pytest.raises(pool.JournalUnavailable):
                it.unfinished()
        finally:
            it.close()

    def test_and_a_gateway_that_cannot_read_it_stops_taking_work(self, tmp_path):
        """Fail closed, the same way an unreadable stop file means stopped."""
        from agentnode_sdk.access import stopping as pool
        from agentnode_sdk.gateway.allowance import why_it_is_stopped

        (tmp_path / "stopping.json").write_text("{not json at all", encoding="utf-8")
        it = pool.Stopping(str(tmp_path), lambda run_id: True)
        try:
            assert it.pick_up_where_it_left_off() == []
            assert why_it_is_stopped(str(tmp_path)), "it came up taking work"
            assert "read the cancellation journal" in it.journal_problem
        finally:
            it.close()

    def test_an_absent_journal_is_simply_nothing_to_pick_up(self, tmp_path):
        """The other half, and the reason the first one is not just paranoia: absent must stay
        harmless, or every first start would halt itself."""
        from agentnode_sdk.access import stopping as pool
        from agentnode_sdk.gateway.allowance import why_it_is_stopped

        it = pool.Stopping(str(tmp_path), lambda run_id: True)
        try:
            assert it.unfinished() == []
            assert it.pick_up_where_it_left_off() == []
            assert why_it_is_stopped(str(tmp_path)) == ""
            assert it.journal_problem == ""
        finally:
            it.close()

    def test_a_stop_that_could_not_be_written_down_says_so(self, tmp_path):
        """The cancellation still happens -- a run the operator asked to stop is better stopped
        without a record than left running with one -- but nobody is told it was durable."""
        from agentnode_sdk.access import stopping as pool
        from agentnode_sdk.gateway.allowance import why_it_is_stopped

        it = pool.Stopping(str(tmp_path), lambda run_id: True)
        try:
            def refuses(kept):
                raise OSError(30, "read-only file system")

            it._write_journal = refuses
            stop = it.ask("run-a", by="somebody")
            assert stop is not None, "the cancellation was dropped"
            assert stop.durable is False
            assert "write down a cancellation" in it.journal_problem
            # ... and the gateway stops admitting new work, because it can no longer promise
            # what it promises.
            assert why_it_is_stopped(str(tmp_path))
        finally:
            it.close()


class TestClosingSaysWhatItCouldNotGetBack:
    """A bounded wait cannot promise every thread ended. It can promise never to lose one."""

    def test_the_pool_returns_the_hands_that_would_not_finish(self, tmp_path):
        from agentnode_sdk.access import stopping as pool

        holding = threading.Event()
        it = pool.Stopping(str(tmp_path), lambda run_id: holding.wait(timeout=30) or True)
        try:
            it.ask("run-a", by="somebody")
            _until(lambda: any(h.is_alive() for h in it._hands))
            left = it.close(seconds=0.2)
            assert left, "a hand that was still working was not reported"
            assert all(name.startswith("agentnode-stopping") for name in left)
            # Idempotent, and it says the same thing the second time rather than "none".
            assert it.close(seconds=0.2) == left
        finally:
            holding.set()

    def test_and_the_service_reports_them_as_its_own(self, tmp_path):
        """The service's close() used to throw the pool's answer away, so a cancellation worker
        could outlive the service while close() reported nothing left running."""
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService
        from tests.test_em3c_gateway import StandInBackend, _store_measurement

        holding = threading.Event()
        state = GatewayState(str(tmp_path / "state"), version="test")
        service = GatewayService(state, backend=StandInBackend())
        _store_measurement(service)
        try:
            service.stopping._carry_out = lambda run_id: holding.wait(timeout=30) or True
            service.stopping.ask("run-a", by="somebody")
            _until(lambda: any(h.is_alive() for h in service.stopping._hands))
            service.stopping.GOODBYE_SECONDS = 0.2
            left = service.close()
            assert any(name.startswith("agentnode-stopping") for name in left), left
            assert service.left_running == left
        finally:
            holding.set()
            state.close()


class TestTheServerOwnsItsWatchers:
    """Two threads watch a running gateway. Clearing a flag asks them to stop; it does not
    establish that they did, and a review was right that the difference is the whole property."""

    def a_server(self, tmp_path):
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService, make_server
        from tests.test_em3c_gateway import StandInBackend, _store_measurement

        state = GatewayState(str(tmp_path / "state"), version="test")
        service = GatewayService(state, backend=StandInBackend())
        _store_measurement(service)
        return state, service, make_server(service, port=0)

    def test_they_are_held_by_name_and_joined_when_the_server_closes(self, tmp_path):
        state, service, server = self.a_server(tmp_path)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        try:
            held = list(server.agentnode_watchers)
            assert sorted(w.name for w in held) == ["agentnode-watch-permissions",
                                                    "agentnode-watch-the-stop"]
            _until(lambda: all(w.is_alive() for w in held))

            server.shutdown()
            serving.join(timeout=10)
            # shutdown() already waited for them, so there is nothing left to report and --
            # the part that matters -- nothing left running either.
            assert server.agentnode_left_watching == []
            assert not any(w.is_alive() for w in held), "a watcher outlived the server"
        finally:
            server.server_close()
            service.close()
            state.close()

    def test_and_one_that_will_not_stop_is_reported_rather_than_assumed_gone(self, tmp_path):
        state, service, server = self.a_server(tmp_path)
        forever = threading.Event()
        stuck = threading.Thread(target=lambda: forever.wait(timeout=30), daemon=True,
                                 name="agentnode-watch-stuck")
        try:
            server.WATCHERS_SECONDS = 0.2
            server.agentnode_watchers = list(server.agentnode_watchers) + [stuck]
            stuck.start()
            server.agentnode_serving = False
            left = server.let_the_watchers_go()
            # The two real watchers wake on a one or two second tick and this wait is shorter
            # than that, so they are named here too -- which is the property, not a nuisance:
            # what has not been seen to end is reported, never assumed gone.
            assert "agentnode-watch-stuck" in left
            assert server.agentnode_left_watching == left
        finally:
            forever.set()
            stuck.join(timeout=5)
            server.server_close()
            service.close()
            state.close()


class TestTwoWritersDoNotCorruptTheJournal:
    """Found in a full run, not in this file: a restart leaves the abandoned pool's hand still
    working over the same directory as the new one, and both write the journal.

    The replace was always atomic. The SCRATCH FILE was not: it had one fixed name, so each
    `open(..., "w")` truncated what the other had not yet flushed and what landed was one
    writer's bytes with the tail of the other's after them -- valid JSON followed by rubbish.

    It only surfaced now because it used to be swallowed. `unfinished()` answered the empty list
    for an unreadable journal, so a corrupted one looked exactly like a clean start: the
    fail-open was hiding the defect underneath it.
    """

    def test_a_second_writer_does_not_leave_the_first_one_s_tail_behind(self, tmp_path):
        from agentnode_sdk.access import stopping as pool

        one = pool.Stopping(str(tmp_path), lambda run_id: True)
        two = pool.Stopping(str(tmp_path), lambda run_id: True)
        try:
            # A long document and a short one, alternating, from two pools over one directory --
            # the shape that corrupts, because the short write is what leaves a tail.
            long_one = {("run-%03d" % i): {"asked_at": 1.0, "asked_by": "x" * 40}
                        for i in range(40)}
            stop = threading.Event()
            trouble = []

            def keep_writing(it, what):
                while not stop.is_set():
                    try:
                        it._write_journal(what)
                        it._read_journal()
                    except Exception as problem:            # noqa: BLE001
                        trouble.append(problem)
                        return

            hands = [threading.Thread(target=keep_writing, args=(one, long_one), daemon=True),
                     threading.Thread(target=keep_writing, args=(two, {}), daemon=True)]
            for hand in hands:
                hand.start()
            time.sleep(2.0)
            stop.set()
            for hand in hands:
                hand.join(timeout=10)

            assert not trouble, "the journal was corrupted: %r" % (trouble[0],)
            # And nothing was left lying around in a directory an operator reads.
            assert not [f for f in os.listdir(tmp_path) if f.endswith(".new")]
        finally:
            one.close()
            two.close()
