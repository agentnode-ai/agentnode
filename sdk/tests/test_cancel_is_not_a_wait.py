"""Cancelling asks for a stop. It does not hold the caller until the stop has happened.

Tearing a sandbox down and CONFIRMING it is gone is what makes a terminal state worth anything,
and it takes as long as it takes -- up to the gateway's settle window. Doing that inline meant the
caller, and the person watching them, waited that long with nothing to look at.

The proof here is not a stopwatch. A test that timed the call and asserted "under a second" would
be measuring a machine that happened to be idle, and would pass against the old code on a fast day.
Instead the worker's stop is HELD OPEN on an event this test owns: while the stop is provably still
in progress, the caller's answer must already be back. That is the same claim, established by
construction rather than by duration, and it fails against a synchronous cancel every time.

WHY THIS FILE FAILED EIGHT TIMES IN CI, AND WHAT WAS WRONG WITH IT
------------------------------------------------------------------
Every one of those failures was the same assertion: the worker was never asked to stop. Measured
on a two-core Linux box under load, with the gateway's own state printed at the moment it
happened, the run was:

    state 'cancelled'   terminal? True   cleanup_verified True
    refusal 'cancelled by the client before it started'
    stop.state 'settled'   stop.problem ''   asked_to_stop False

The run was already over, and it had never executed. A cancellation that arrives in the window
between the slot being held and the container being asked for is answered by the run's own thread,
which raises before the job reaches the worker; nothing was created, so nothing is asked to stop,
and the record says exactly that. The product was right; these tests were reading a correct
refusal as a missing stop, because they cancelled whatever stage the run happened to be at. On an
idle machine the run always won that race, so the case never showed.

The repair is an ordering the test controls, not a longer wait: `a_running_job` now waits for the
backend's own `has_started` before returning, so a test that means to cancel a RUNNING job cancels
one. **No timeout in this file was raised** -- the ten-second waits are left exactly as they were,
because they are deadlock guards now rather than the thing being relied on.
"""
from __future__ import annotations

import base64
import hashlib
import os
import pathlib
import sys
import threading
import time

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement


class ABackendThatKeepsRunning(StandInBackend):
    """A stand-in whose run does not end until this test says so.

    The first version of these tests used the plain stand-in, which returns the moment it is
    asked -- so on a fast machine the job was already FINISHED before the cancellation could be
    observed, and `status` rightly reported the terminal state rather than `stopping`. That was
    the test racing itself, not the gateway misbehaving, and it only showed up on Linux. A run
    that ends when this test lets it end removes the race by construction.

    The SECOND race was at the other end, and it is the one that made this file fail eight times
    in CI. A run that has not reached the worker is cancelled by a different and deliberate path:
    the run's own thread raises before the container is asked for, and the record ends as
    `cancelled by the client before it started` with nothing to stop. On a machine with a spare
    core the run always reached the backend first and the tests never saw that path; on a loaded
    two-core runner the cancellation won, and the tests read a correct refusal as a missing stop.
    `has_started` closes that: a test that means to cancel a RUNNING run now waits until the run
    really is one. The other case is not left untested -- it has a test of its own, below, which
    reaches it by holding the only slot instead of by hoping.
    """

    def __init__(self):
        super().__init__()
        self.may_finish_run = threading.Event()
        #: Set the moment the run is really executing, before it is held open. This is the
        #: synchronisation point the cancelling tests wait on; nothing here measures duration.
        self.has_started = threading.Event()

    def run_process(self, spec, input_text=None, timeout=120.0):
        self.specs.append(spec)
        self.has_started.set()
        self.may_finish_run.wait(timeout=30)
        return 0, "RAN", ""


class AWorkerHeldOpen:
    """Wraps the real worker so `stop` blocks until this test lets it go."""

    def __init__(self, underneath):
        self._underneath = underneath
        self.asked_to_stop = threading.Event()
        self.may_finish = threading.Event()
        #: What happened, in the order it happened. The event alone was not enough: the held stop
        #: gives up after its own timeout, so a SYNCHRONOUS cancel would eventually return with
        #: the event still unset and the test would pass on a technicality. Which came first is
        #: the actual claim, and it cannot be satisfied by waiting longer.
        self.in_order = []

    def __getattr__(self, name):
        return getattr(self._underneath, name)

    def stop(self, run_id, container_name, appear_seconds):
        self.asked_to_stop.set()
        # Deliberately long: if the caller were waiting for this, it would still be waiting.
        self.may_finish.wait(timeout=30)
        self.in_order.append("the stop finished")
        return self._underneath.stop(run_id, container_name, appear_seconds)


def _descriptors_still_open_on(where):
    """Whatever this process still holds open under `where`, or `None` if it cannot be asked.

    The question is about the files THIS TEST created, not about the process's handle count. A
    whole-process count was tried first and is not usable: on Windows it counts events, threads
    and mappings as well, so it wanders upward during a run and a strict comparison fails on a
    process that has leaked nothing. Counting the wrong thing strictly is not strictness.

    So each platform is asked the question it can answer, and both answers are strict:

    * POSIX lists the open descriptors, and the ones pointing into `where` are named outright;
    * Windows has no such list without a package this suite does not depend on, so the
      filesystem is asked instead -- a directory with an open file under it cannot be renamed,
      so a rename that succeeds IS the answer and one that fails names the file by failing.

    A platform that can do neither returns `None`, and the caller fails rather than passing on a
    check that measured nothing.
    """
    where = pathlib.Path(where)
    if sys.platform != "win32":
        try:
            fds = os.listdir("/proc/self/fd")
        except OSError:
            return None
        held = []
        for fd in fds:
            try:
                target = os.readlink(os.path.join("/proc/self/fd", fd))
            except OSError:            # it was closed while this was being read
                continue
            if target.startswith(str(where)):
                held.append(target)
        return held
    moved = where.with_name(where.name + "-asked-if-anything-is-open")
    try:
        os.rename(where, moved)
    except OSError as why:
        return ["%s could not be moved, so something under it is open: %s" % (where, why)]
    os.rename(moved, where)
    return []


def _how_far_along(reported):
    """Where a REPORTED state sits, in the vocabulary a client is actually written against.

    `protocol.stage_of` ranks the states a RECORD can hold, and `stopping` is deliberately not
    one of them: no record is ever stored as stopping -- it is derived for the answer while the
    teardown is in flight. The two lists say different things, so a watcher's states are placed
    with the list a watcher is shown: `contract.RUNNING_STATES`, then everything finished. A
    state in neither raises rather than being ranked lowest, which is the same rule `stage_of`
    keeps for its own vocabulary.
    """
    if reported in contract.FINISHED_STATES:
        return len(contract.RUNNING_STATES)
    return contract.RUNNING_STATES.index(reported)


def _gateway_threads():
    """The threads this gateway owns, by the names it gives them."""
    return sorted(t.name for t in threading.enumerate()
                  if t.is_alive() and t.name.startswith("agentnode-"))


@pytest.fixture(autouse=True)
def nothing_is_left_behind(tmp_path):
    """Every test in this file gives back what it took, and that is asserted rather than assumed.

    Autouse and declared before anything else, so it is set up first and therefore torn down
    LAST -- after the gateway has been closed. What it checks is what a cancellation can leak:
    the pool's hands and the run threads, the descriptors the state directory is held open with,
    and the sandboxes. A leak here would not fail a single assertion in any test below, which is
    exactly why it is checked separately.

    The thread check allows the daemons a bounded moment to finish after `close()` and then
    asserts equality; the limit is a deadlock guard and the assertion is on the final state, not
    on how quickly it arrived.
    """
    before_threads = _gateway_threads()
    assert _descriptors_still_open_on(tmp_path) is not None, (
        "this platform (%s) can be asked neither way whether anything is left open, so this "
        "check would establish nothing" % sys.platform)
    yield
    assert _eventually(lambda: _gateway_threads() == before_threads), (
        "the gateway's threads did not go back to what they were: %r, was %r"
        % (_gateway_threads(), before_threads))
    still_open = _descriptors_still_open_on(tmp_path)
    assert still_open == [], (
        "the test's own directory is still held open: %r" % (still_open,))


@pytest.fixture()
def sandbox(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    backend = ABackendThatKeepsRunning()
    service = GatewayService(state, backend=backend)
    _store_measurement(service)
    # Confirming a container is gone waits for it to appear first, and with a stand-in backend
    # it never does -- so the real appear window would be spent in full on every teardown here.
    # Shortened rather than mocked away: the same code path runs, it just does not spend twenty
    # seconds establishing that nothing arrived.
    service.CONTAINER_APPEAR_SECONDS = 0.2
    held = AWorkerHeldOpen(service.worker)
    held.run_may_finish = backend.may_finish_run
    held.run_has_started = backend.has_started
    service._worker = held
    token = state.redeem_pairing(state.start_pairing(), client_name="a laptop")
    try:
        yield service, dispatch.identify(service, token), held
    finally:
        # Both, and in this order: a run still inside the backend would otherwise sit out its
        # whole wait while the fixture tried to tear the state down around it.
        held.run_may_finish.set()
        held.may_finish.set()
        # `close()` says what it could NOT get back, so an empty list is the gateway stating that
        # every thread and server it owned has been released -- asserted here rather than taken
        # on trust, because a cancellation that leaves a run thread behind would leave it here.
        could_not_get_back = service.close()
        state.close()
        assert could_not_get_back == [], (
            "the gateway could not get these back: %r" % (could_not_get_back,))
        # And the sandboxes. This backend really does know the answer -- it never started one --
        # so this asks it rather than assuming from the absence of a container name.
        answered, still_named = backend.containers_named("agentnode-")
        assert answered and still_named == [], (
            "the backend still knows of these sandboxes: %r" % (still_named,))


#: A deadlock guard, and nothing else. Every wait in this file is either an ordering gate that is
#: already satisfied by the time it is reached, or one of these: a limit that ends a broken test
#: instead of hanging the suite. No claim here rests on how long anything took.
A_DEADLOCK_GUARD = 30.0


def a_running_job(service, who, really_started=None, run_id="s" * 32):
    """Submit a job and, when asked, do not come back until it is genuinely running.

    `really_started` is the backend's own signal, set from inside the run. Waiting for it is what
    separates "cancel a running job" from "cancel a job that has not started", which the gateway
    answers differently on purpose -- and which is the race that made this file fail in CI. A
    caller that does not pass it is asking for the submitted job whatever stage it is at.
    """
    # The disclosure is bound to the job it described, so what is prepared and what is
    # submitted have to be the same job. They were not: this prepared for a made-up
    # digest and then submitted real code, which is exactly the substitution the gate
    # now refuses.
    code = "import time; time.sleep(30)"
    body = code.encode("utf-8")
    told = dispatch.dispatch("prepare", {
        "command": ["python", "-c", code],
        "artifact_sha256": hashlib.sha256(body).hexdigest(),
        "artifact_bytes": len(body), "wall_clock_s": 60}, who,
        service=service)
    started = dispatch.dispatch("submit", {
        "run_id": run_id,
        "artifact": base64.b64encode(body).decode("ascii"),
        "command": ["python", "-c", code], "wall_clock_s": 60,
        "accepted_disclosure": told["accepted_disclosure"]}, who, service=service)
    if really_started is not None:
        assert really_started.wait(timeout=A_DEADLOCK_GUARD), (
            "the run never reached the backend, so there is no running job to cancel and this "
            "test would be asking about the wrong case")
    return started["run_id"]


class TestTheCallerIsNotHeld:

    def test_the_answer_comes_back_while_the_stop_is_still_in_progress(self, sandbox):
        """The whole claim, without a stopwatch."""
        service, who, held = sandbox
        run_id = a_running_job(service, who, held.run_has_started)

        assert held.asked_to_stop.wait(timeout=0) is False, "the stop began before it was asked for"
        answer = dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        held.in_order.append("the answer came back")

        assert held.asked_to_stop.wait(timeout=10), "the stop never started"
        assert not held.may_finish.is_set(), "this test released the stop too early to prove anything"
        # The claim, stated as an order rather than as a duration: the caller had its answer
        # before the stop was finished with. A cancel that waited would have these the other way
        # round, and no amount of patience would reverse them.
        assert held.in_order == ["the answer came back"], held.in_order
        assert answer["state"] == "stopping"
        assert answer["accepted"] is True

        held.may_finish.set()
        held.run_may_finish.set()
        assert _eventually(lambda: held.in_order[:2] == ["the answer came back",
                                                        "the stop finished"]), held.in_order

    def test_and_the_run_says_stopping_until_it_is_really_over(self, sandbox):
        service, who, held = sandbox
        run_id = a_running_job(service, who, held.run_has_started)
        dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        assert held.asked_to_stop.wait(timeout=10)

        where = dispatch.dispatch("status", {"run_id": run_id}, who, service=service)
        assert where["state"] == "stopping", where
        assert where["state"] in contract.STATES

        held.run_may_finish.set()
        held.may_finish.set()
        settled = _poll_until_finished(service, who, run_id)
        assert settled["state"] in contract.FINISHED_STATES, settled

    def test_asking_twice_does_not_start_a_second_stop(self, sandbox):
        """Idempotent: the second request joins the first."""
        service, who, held = sandbox
        run_id = a_running_job(service, who, held.run_has_started)

        first = dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        assert held.asked_to_stop.wait(timeout=10)
        second = dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)

        assert first["accepted"] is True
        assert second["accepted"] is False, "a second request started another stop"
        assert second["state"] == "stopping"
        held.may_finish.set()

    def test_and_cancelling_something_already_finished_says_so(self, sandbox):
        service, who, held = sandbox
        held.may_finish.set()
        held.run_may_finish.set()
        run_id = a_running_job(service, who, held.run_has_started)
        dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        settled = _poll_until_finished(service, who, run_id)

        again = dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        assert again["accepted"] is False
        assert again["state"] == settled["state"]


class TestAJobThatHasNotStartedIsADifferentCase:
    """The case this whole repair is about, reached on purpose instead of by accident.

    Every test above cancels a run that is genuinely executing, because that is what they mean to
    ask about. A run that has not got that far is ended another way -- it never reaches the
    worker, so no sandbox is ever created and none is asked to stop -- and that is not a lesser
    case: it is the one that turned up eight times in CI while the tests were asking about the
    other one. Gating those tests on a real start would have left this path untested, so it is
    reached here deliberately, by holding the only slot: which stage the run is at is decided by
    this test rather than by how busy the machine happens to be.
    """

    def test_a_job_cancelled_while_it_waits_for_a_slot_never_gets_a_sandbox(self, sandbox):
        import json as _json

        service, who, held = sandbox
        (service.state.root / "allowance.json").write_text(
            _json.dumps({"machine_concurrent_runs": 1, "queue_depth": 2}), encoding="utf-8")
        assert service.allowance().machine_concurrent_runs == 1, (
            "the ceiling was not picked up, so nothing here would have had to wait")

        holding_the_slot = a_running_job(service, who, held.run_has_started)
        waiting = a_running_job(service, who, run_id="w" * 32)
        assert _eventually(lambda: service.slots.waiting() == 1), (
            "the second job was not made to wait, so this test would be about the other case")

        answer = dispatch.dispatch("cancel", {"run_id": waiting}, who, service=service)
        assert answer["accepted"] is True
        assert answer["state"] == "stopping"
        held.may_finish.set()

        ended = _poll_until_finished(service, who, waiting)
        assert ended["state"] == "cancelled", ended
        record = service.runs[waiting]
        assert record.refusal == "cancelled by the client while it was waiting for a slot", (
            record.refusal)
        # The three things that say no sandbox was ever involved. Asserted separately, because
        # each of them is a different way of getting this case wrong: charging for a job that
        # never ran, naming a container that was never created, and claiming a cleanup nobody did.
        assert not record.started_at, "a job that never ran was given a start time"
        assert record.container_name == "", "a sandbox was named for a job that never started"
        assert record.cleanup_verified is True, (
            "a job that created nothing still has to say nothing was left behind")

        # And the run that was holding the slot was not disturbed by any of it.
        where = dispatch.dispatch("status", {"run_id": holding_the_slot}, who, service=service)
        assert where["state"] == "running", where
        held.run_may_finish.set()


class TestAWatcherNeverSeesTheRunGoBackwards:

    def test_the_states_a_watcher_is_shown_only_move_forward(self, sandbox):
        """A second client watching the run while it is cancelled, and what it is allowed to see.

        The states are collected by an actual watcher rather than reasoned about: `status` is
        asked over and over through the whole cancellation, and what comes back has to be
        non-decreasing. Which states appear depends on how the two threads interleave; that they
        never go backwards does not, which is why the assertion is on the order and not on the
        sequence.
        """
        from agentnode_sdk.gateway.protocol import ProtocolError, may_move

        service, who, held = sandbox
        run_id = a_running_job(service, who, held.run_has_started)
        seen = []
        enough = threading.Event()

        def watch():
            while not enough.is_set():
                where = dispatch.dispatch("status", {"run_id": run_id}, who, service=service)
                if not seen or seen[-1] != where["state"]:
                    seen.append(where["state"])
                if where["state"] in contract.FINISHED_STATES:
                    return
                time.sleep(0.005)

        watcher = threading.Thread(target=watch, name="a-second-client-watching")
        watcher.start()
        try:
            dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
            assert held.asked_to_stop.wait(timeout=10)
            held.may_finish.set()
            held.run_may_finish.set()
            _poll_until_finished(service, who, run_id)
        finally:
            enough.set()
            watcher.join(timeout=A_DEADLOCK_GUARD)
        assert not watcher.is_alive(), "the watcher never came back"

        assert seen, "the watcher saw nothing at all, so it establishes nothing"
        for before, after in zip(seen, seen[1:]):
            assert _how_far_along(after) >= _how_far_along(before), (
                "a watcher was shown %r after %r: %r" % (after, before, seen))
        assert "stopping" in seen, (
            "the teardown was never observable to a watcher: %r" % (seen,))
        assert seen[-1] in contract.FINISHED_STATES, seen

        # And the rule underneath it, asked of the record itself: a run that has ended cannot be
        # made to run again, whoever asks.
        assert may_move("cancelled", "running") is False
        with pytest.raises(ProtocolError):
            service.runs[run_id].move_to("running")


class TestCleanupIsStillRequired:

    def test_the_terminal_state_still_waits_for_the_sandbox_to_be_gone(self, sandbox):
        """Making the caller wait less must not make the answer mean less.

        Nothing here shortens the confirmation; it moves who waits for it. So a run is not
        terminal until the gateway says so, and `stopping` is never one of the finished states.
        """
        service, who, held = sandbox
        assert "stopping" not in contract.FINISHED_STATES
        assert "stopping" in contract.RUNNING_STATES

        run_id = a_running_job(service, who, held.run_has_started)
        dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        assert held.asked_to_stop.wait(timeout=10)
        where = dispatch.dispatch("status", {"run_id": run_id}, who, service=service)
        assert where["state"] not in contract.FINISHED_STATES, (
            "a run was called finished while its sandbox was still being torn down")
        held.may_finish.set()

    def test_a_stop_that_fails_does_not_look_like_one_that_worked(self, sandbox):
        service, who, held = sandbox

        def it_goes_wrong(run_id, container_name, appear_seconds):
            held.asked_to_stop.set()
            raise OSError("the runtime went away")

        held.stop = it_goes_wrong
        run_id = a_running_job(service, who, held.run_has_started)
        dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        assert held.asked_to_stop.wait(timeout=10)

        where = dispatch.dispatch("status", {"run_id": run_id}, who, service=service)
        assert where["state"] != "cancelled", (
            "a cancellation that failed was reported as one that worked")


def _eventually(it_is_true, tries=200):
    import time

    for _ in range(tries):
        if it_is_true():
            return True
        time.sleep(0.05)
    return False


def _poll_until_finished(service, who, run_id, tries=600):
    import time

    for _ in range(tries):
        where = dispatch.dispatch("status", {"run_id": run_id}, who, service=service)
        if where["state"] in contract.FINISHED_STATES:
            return where
        time.sleep(0.05)
    raise AssertionError("the run never finished: last said %r" % where)


class TestCancellingIsGovernedLikeEverythingElse:
    """A cancellation is a request to this gateway, and is subject to what governs requests.

    It would be easy to argue the other way -- stopping work is not starting work -- but an
    operation exempt from the stop and from the limits is an operation an exhausted or halted
    gateway still has to carry out, and that is the state in which it can least afford to.
    """

    def test_the_operators_stop_refuses_a_cancellation_as_well(self, sandbox):
        service, who, held = sandbox
        run_id = a_running_job(service, who, held.run_has_started)
        import json
        import os

        with open(os.path.join(str(service.state.root), "stopped.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"reason": "Wartung"}, fh)

        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        assert refused.value.refusal == "gateway_stopped"
        assert "Wartung" in refused.value.because

    def test_a_storm_across_many_runs_is_refused_before_it_becomes_work(self, sandbox):
        """Asking about ONE cancellation repeatedly is free; starting hundreds is not."""
        service, who, held = sandbox
        from agentnode_sdk.access import stopping as poolmod

        service.stopping.PER_DEVICE = 3
        run_id = a_running_job(service, who, held.run_has_started)
        first = dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        assert first["accepted"] is True
        for _ in range(50):                       # the same run, over and over: never refused
            dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)

        refused = None
        for n in range(10):                       # distinct runs: bounded
            try:
                service.stopping.ask("made-up-run-%d" % n, by=who.client_id)
            except poolmod.TooManyStops as too_many:
                refused = too_many
                break
        assert refused is not None, "a device could start unlimited cancellations"
        held.may_finish.set()


class TestARestartDoesNotLoseACancellation:

    def test_a_gateway_built_over_the_same_directory_picks_up_what_was_being_stopped(
            self, tmp_path):
        """What a restart really is, from the state directory's point of view."""
        state = GatewayState(str(tmp_path / "state"), version="test")
        backend = ABackendThatKeepsRunning()
        first = GatewayService(state, backend=backend)
        _store_measurement(first)
        first.CONTAINER_APPEAR_SECONDS = 0.2
        held = AWorkerHeldOpen(first.worker)
        held.run_may_finish = backend.may_finish_run
        first._worker = held
        token = state.redeem_pairing(state.start_pairing(), client_name="a laptop")
        who = dispatch.identify(first, token)
        run_id = a_running_job(first, who, backend.has_started)
        dispatch.dispatch("cancel", {"run_id": run_id}, who, service=first)
        assert held.asked_to_stop.wait(timeout=10)
        assert first.stopping.unfinished() == [run_id], "it was not written down before trying"

        # The process dies here: not closed, not cleaned up, nothing given back.
        try:
            second = GatewayService(state, backend=ABackendThatKeepsRunning())
            _store_measurement(second)
            second.CONTAINER_APPEAR_SECONDS = 0.2
            # Picked up on the way up, by __init__, not by anybody remembering to ask.
            assert second.stopping.about(run_id) is not None, (
                "a gateway came back up having forgotten it was tearing a sandbox down")
        finally:
            held.run_may_finish.set()
            held.may_finish.set()
            try:
                second.close()
            except NameError:
                pass
            first.close()
            state.close()
