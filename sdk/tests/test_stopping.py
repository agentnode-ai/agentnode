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
        teardown = ASandboxThatTakesItsTime()
        stopping = sandbox(teardown, hands=2)
        for n in range(40):
            stopping.ask("run-%d" % n, by="device-%d" % n)

        assert teardown.started.wait(timeout=5)
        alive = [t for t in threading.enumerate() if t.name.startswith("agentnode-stopping")]
        assert len(alive) == 2, [t.name for t in alive]
        assert teardown.most_at_once <= 2, teardown.most_at_once
        teardown.may_finish.set()

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
        (tmp_path / "stopping.json").write_text("{not json", encoding="utf-8")
        stopping = sandbox(lambda run_id: True)
        # It reports nothing rather than inventing runs, and -- this is the part that matters --
        # it does not overwrite the file with an empty one, so a person can still look at it.
        assert stopping.unfinished() == []
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
