"""Cancelling asks for a stop. It does not hold the caller until the stop has happened.

Tearing a sandbox down and CONFIRMING it is gone is what makes a terminal state worth anything,
and it takes as long as it takes -- up to the gateway's settle window. Doing that inline meant the
caller, and the person watching them, waited that long with nothing to look at.

The proof here is not a stopwatch. A test that timed the call and asserted "under a second" would
be measuring a machine that happened to be idle, and would pass against the old code on a fast day.
Instead the worker's stop is HELD OPEN on an event this test owns: while the stop is provably still
in progress, the caller's answer must already be back. That is the same claim, established by
construction rather than by duration, and it fails against a synchronous cancel every time.
"""
from __future__ import annotations

import base64
import threading

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
    """

    def __init__(self):
        super().__init__()
        self.may_finish_run = threading.Event()

    def run_process(self, spec, input_text=None, timeout=120.0):
        self.specs.append(spec)
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


@pytest.fixture()
def sandbox(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    backend = ABackendThatKeepsRunning()
    service = GatewayService(state, backend=backend)
    _store_measurement(service)
    held = AWorkerHeldOpen(service.worker)
    held.run_may_finish = backend.may_finish_run
    service._worker = held
    token = state.redeem_pairing(state.start_pairing(), client_name="a laptop")
    try:
        yield service, dispatch.identify(service, token), held
    finally:
        # Both, and in this order: a run still inside the backend would otherwise sit out its
        # whole wait while the fixture tried to tear the state down around it.
        held.run_may_finish.set()
        held.may_finish.set()
        state.close()


def a_running_job(service, who):
    told = dispatch.dispatch("prepare", {
        "command": ["python", "-c", "import time; time.sleep(30)"],
        "artifact_sha256": "a" * 64, "artifact_bytes": 40, "wall_clock_s": 60}, who,
        service=service)
    started = dispatch.dispatch("submit", {
        "run_id": "s" * 32,
        "artifact": base64.b64encode(b"import time; time.sleep(30)").decode("ascii"),
        "command": ["python", "-c", "import time; time.sleep(30)"], "wall_clock_s": 60,
        "accepted_disclosure": told["accepted_disclosure"]}, who, service=service)
    return started["run_id"]


class TestTheCallerIsNotHeld:

    def test_the_answer_comes_back_while_the_stop_is_still_in_progress(self, sandbox):
        """The whole claim, without a stopwatch."""
        service, who, held = sandbox
        run_id = a_running_job(service, who)

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
        run_id = a_running_job(service, who)
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
        run_id = a_running_job(service, who)

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
        run_id = a_running_job(service, who)
        dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        settled = _poll_until_finished(service, who, run_id)

        again = dispatch.dispatch("cancel", {"run_id": run_id}, who, service=service)
        assert again["accepted"] is False
        assert again["state"] == settled["state"]


class TestCleanupIsStillRequired:

    def test_the_terminal_state_still_waits_for_the_sandbox_to_be_gone(self, sandbox):
        """Making the caller wait less must not make the answer mean less.

        Nothing here shortens the confirmation; it moves who waits for it. So a run is not
        terminal until the gateway says so, and `stopping` is never one of the finished states.
        """
        service, who, held = sandbox
        assert "stopping" not in contract.FINISHED_STATES
        assert "stopping" in contract.RUNNING_STATES

        run_id = a_running_job(service, who)
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
        run_id = a_running_job(service, who)
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


def _poll_until_finished(service, who, run_id, tries=200):
    import time

    for _ in range(tries):
        where = dispatch.dispatch("status", {"run_id": run_id}, who, service=service)
        if where["state"] in contract.FINISHED_STATES:
            return where
        time.sleep(0.05)
    raise AssertionError("the run never finished: last said %r" % where)
