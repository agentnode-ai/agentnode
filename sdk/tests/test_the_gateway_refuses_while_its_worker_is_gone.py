"""The same three states, seen from outside the health module -- through the gateway itself.

`test_what_the_gateway_says_about_its_worker.py` drives the state machine. This drives the
GATEWAY: it makes a measured, ready gateway, takes its worker away, and asks the things an
operator and a client actually ask. Every assertion here is about behaviour that changed, and
every one of them holds the other way round on a gateway whose worker is answering, which the
`_and_comes_back` tests establish rather than assume.
"""
from __future__ import annotations

import pytest

from agentnode_sdk.gateway import health as H
from agentnode_sdk.gateway import observability as obs
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from agentnode_sdk.worker import WorkerUnreachable
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        service.close()
        state.close()


def _the_worker_is_gone(service):
    """Publish a real loss, the way the watch does when a probe fails.

    Through `consider` and a real probe rather than by assigning to the snapshot, so what these
    tests see is what the running machine would publish -- including the generation and the
    reason -- and not a state no code path can produce.
    """
    def gone(_budget):
        raise WorkerUnreachable("the sandbox worker at tcps://127.0.0.1:8443 could not be reached")

    service.health._reach = gone
    service.health.consider(service.health.probe_once())


def _the_worker_answers(service):
    service.health._reach = lambda _budget: None
    service.health.consider(service.health.probe_once())


# ------------------------------------------------------------------ admission


def test_a_measured_gateway_is_ready_until_its_worker_stops_answering(gateway):
    assert gateway.readiness_now().ready                      # the control this rests on

    _the_worker_is_gone(gateway)

    verdict = gateway.readiness_now()
    assert not verdict.ready
    assert "not answering" in verdict.reason


def test_the_measurement_itself_is_untouched_by_the_worker_going_away(gateway):
    """A worker that has gone does not make the old measurement wrong about the past.

    It makes it ineligible, which is a different thing, and keeping them apart is what lets
    `protected` come back after one fresh measurement instead of a whole re-activation.
    """
    _the_worker_is_gone(gateway)

    assert not gateway.readiness_now().ready
    assert gateway._what_the_measurement_proves().ready


def test_while_the_worker_is_gone_the_gateway_says_which_kind_of_unavailable(gateway):
    _the_worker_is_gone(gateway)
    assert gateway.health_now().code == H.WORKER_UNREACHABLE

    _the_worker_answers(gateway)
    assert gateway.health_now().state == H.MEASURING
    assert gateway.health_now().code == H.MEASUREMENT_RUNNING
    # Two refusals, told apart WITHOUT reading the English. That is the whole of `H4`.
    assert H.WORKER_UNREACHABLE != H.MEASUREMENT_RUNNING


def test_a_job_submitted_while_the_worker_is_gone_is_refused_with_a_cause(gateway):
    from agentnode_sdk.access.dispatch import Refused

    someone = _a_customer(gateway, "a customer")
    assert _a_run_by(gateway, someone)                        # the control: it works before

    _the_worker_is_gone(gateway)

    with pytest.raises(Refused) as refused:
        _a_run_by(gateway, someone)

    # The contract's own name is unchanged -- one HTTP answer, one thing a client does -- and
    # the cause beside it says WHICH situation, which is what decides whether waiting helps.
    assert refused.value.refusal == "sandbox_unavailable"
    assert refused.value.cause == H.WORKER_UNREACHABLE
    assert refused.value.as_answer()["cause"] == H.WORKER_UNREACHABLE


def test_nothing_ran_and_nothing_was_booked_while_the_worker_was_gone(gateway):
    from agentnode_sdk.access.dispatch import Refused

    someone = _a_customer(gateway, "a customer")
    _the_worker_is_gone(gateway)
    before = len(list(gateway.runs.values()))

    with pytest.raises(Refused):
        _a_run_by(gateway, someone)

    started = [r for r in gateway.runs.values() if getattr(r, "started_at", 0.0)]
    assert started == [], "nothing may begin while the worker is unreachable"
    # The refused submission is remembered so it cannot be replayed into an acceptance, but
    # nothing about it says a job ran.
    for record in list(gateway.runs.values())[before:]:
        assert record.state == "refused"
        assert record.container_name == ""
        assert record.finished_at is not None


# ------------------------------------------------------------------ what an operator reads


def test_the_operator_health_check_stops_reporting_a_measured_machine(gateway, tmp_path):
    service_says = obs.health(gateway)
    assert service_says["measured"] is True                   # the control

    # A gateway whose worker is on the other end of a transport, which is the only case where
    # a worker can be lost without this process going with it.
    gateway.worker.transport = "mtls"
    _the_worker_is_gone(gateway)

    now_says = obs.health(gateway)

    assert now_says["measured"] is False
    assert now_says["worker"] == H.UNAVAILABLE
    assert now_says["worker_because"] == H.WORKER_UNREACHABLE
    # It is still SERVING. The owner's decision was that the gateway must not die for this.
    assert now_says["serving"] is True


def test_what_a_client_is_told_carries_the_cause(gateway):
    from agentnode_sdk.access.dispatch import Refused

    refused = Refused("sandbox_unavailable", "the sandbox worker is not answering",
                      "Wait for it to come back.", H.WORKER_UNREACHABLE)

    assert refused.as_answer()["cause"] == H.WORKER_UNREACHABLE
    # And a refusal with no cause to give still renders, without an empty key to branch on.
    assert "cause" not in Refused("malformed", "no", "Send it again.").as_answer()


def test_hello_says_what_is_measured_and_what_is_live_separately(gateway):
    _the_worker_is_gone(gateway)

    said = gateway.hello()

    assert said["ready"] is False
    assert said["health"]["state"] == H.UNAVAILABLE
    assert said["health"]["code"] == H.WORKER_UNREACHABLE


# ------------------------------------------------------------------ coming back


def test_execution_stays_blocked_between_the_worker_returning_and_a_new_measurement(gateway):
    _the_worker_is_gone(gateway)
    _the_worker_answers(gateway)
    assert gateway.health_now().state == H.MEASURING

    from agentnode_sdk.access.dispatch import Refused

    someone = _a_customer(gateway, "a customer")
    with pytest.raises(Refused) as refused:
        _a_run_by(gateway, someone)

    # A refusal that was actually asked for and actually given -- not an absence of a job.
    assert refused.value.refusal == "sandbox_unavailable"
    assert refused.value.cause == H.MEASUREMENT_RUNNING
    # And something to do that fits. Measured on the isolated pair: a caller in this exact
    # situation was told to "ask whoever runs this sandbox to measure it again", while the
    # sandbox was in the middle of measuring. Being told to ask for what is already happening
    # is worse than being told nothing.
    assert "measure it again" not in refused.value.what_to_do
    assert "Wait" in refused.value.what_to_do


def test_an_in_process_gateway_is_not_reported_as_broken_for_having_nothing_to_probe(gateway):
    """A worker in this process cannot be lost without the gateway going with it.

    Without this the whole unit suite -- and every embedded gateway -- would read its own
    absence of a published statement as a dead worker.
    """
    assert gateway.worker.transport == "in-process"
    assert gateway.published_health().may_admit
    assert gateway.readiness_now().ready


# ------------------------------------------------------------------ built without reaching out


def test_a_service_can_be_built_while_its_worker_is_unreachable(tmp_path):
    """Because three operator commands do nothing but build one, and then report.

    Found by exercise on the closed alpha's isolated pair, not by reading: with the worker
    stopped, `gateway status`, `gateway watch` and `gateway doctor` all ended in a traceback out
    of `GatewayService.__init__`, which asked the worker its name. The handling further down
    those commands never ran, so an operator could not tell a degraded machine from a broken
    command -- which is exactly what this arc is about.
    """
    from agentnode_sdk.worker.local import LocalWorker

    class ItIsNotThere(LocalWorker):
        """A real worker object with a door that is shut, rather than a stand-in for one: the
        thing under test is what the SERVICE does when a reachable-worker call fails."""

        transport = "mtls"

        def instance_label(self):
            raise WorkerUnreachable("the sandbox worker at tcps://127.0.0.1:8443 could not "
                                    "be reached: [Errno 111] Connection refused")

    state = GatewayState(str(tmp_path / "state"), version="test")
    try:
        service = GatewayService(state, worker=ItIsNotThere(StandInBackend()))
        _store_measurement(service)

        # Building it works, and so does asking it the things those commands ask.
        assert service.readiness_now() is not None
        assert obs.health(service)["serving"] is True
        assert service.published_health() is not None

        # And the value itself is not invented. Anything that needs it still fails.
        with pytest.raises(WorkerUnreachable):
            service.instance
    finally:
        state.close()


def test_the_instance_is_the_same_every_time_it_is_asked(gateway):
    """It names a process. Asking twice must not produce two."""
    assert gateway.instance == gateway.instance
    assert gateway.instance.endswith(gateway._this_process)


def test_readiness_answers_rather_than_raising_when_the_worker_cannot_be_reached(tmp_path):
    """`readiness_now` is the one path everything else asks. It must always answer.

    Found by exercise, twice. The binding that says which runtime and image a report is about
    can only come from the worker, so judging a stored report reaches for it -- and with the
    worker stopped, `gateway status` died in a traceback out of `report_binding`. A gateway that
    cannot judge its measurement is not ready; that is an answer, and it is not an exception.
    """
    from agentnode_sdk.worker.local import LocalWorker

    class OneThatCanBeShut(LocalWorker):
        transport = "mtls"
        shut = False

        def can_it_isolate(self):
            if self.shut:
                raise WorkerUnreachable("the sandbox worker at tcps://127.0.0.1:8443 could not "
                                        "be reached: [Errno 111] Connection refused")
            return super().can_it_isolate()

    worker = OneThatCanBeShut(StandInBackend())
    state = GatewayState(str(tmp_path / "state"), version="test")
    try:
        service = GatewayService(state, worker=worker)
        # Measured FIRST, while the door is open, so what is judged below is a real stored
        # report and not the absence of one.
        _store_measurement(service)
        assert service.readiness_now().ready
        worker.shut = True
        # The watch in THIS process has never probed, so nothing short-circuits ahead of the
        # binding -- which is exactly the case a command in its own process is in.
        assert service.health_now().state == H.STARTING

        verdict = service.readiness_now()

        assert verdict.ready is False
        assert "not answering" in verdict.reason
        assert verdict.next_steps                              # something a person can do
    finally:
        state.close()


def test_the_watch_asks_for_attention_when_the_worker_is_gone(gateway, tmp_path):
    """Found on the isolated pair: `gateway watch` printed "Nothing is asking for attention"
    while the machine could not have run a single job.

    Same failure as the measurement unit still saying `Protected`, on a different surface. The
    run counts look identical on a quiet machine and on a broken one, so the absence of alerts
    read as health.
    """
    sink = obs.NowhereSink()

    quiet = obs.observe(gateway, sink)
    assert [a["rule"] for a in quiet["alerts"]] == [], "the control: nothing is wrong yet"

    gateway.worker.transport = "mtls"
    _the_worker_is_gone(gateway)

    said = obs.observe(gateway, sink)

    assert "the worker is not there" in [a["rule"] for a in said["alerts"]]
    assert said["counts"]["worker"] == H.UNAVAILABLE
    assert said["counts"]["worker_because"] == H.WORKER_UNREACHABLE
