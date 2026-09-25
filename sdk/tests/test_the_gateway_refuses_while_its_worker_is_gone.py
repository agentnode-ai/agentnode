"""The same three states, seen from outside the health module -- through the gateway itself.

`test_what_the_gateway_says_about_its_worker.py` drives the state machine. This drives the
GATEWAY: it makes a measured, ready gateway, takes its worker away, and asks the things an
operator and a client actually ask. Every assertion here is about behaviour that changed, and
every one of them holds the other way round on a gateway whose worker is answering, which the
`_and_comes_back` tests establish rather than assume.
"""
from __future__ import annotations

import json

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
    assert "not taking work" in verdict.reason


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
    assert now_says["because"]
    # It is still SERVING. The owner's decision was that the gateway must not die for this.
    assert now_says["serving"] is True
    # WHICH of the three it is does not go on this answer, because `/v1/health` is reached
    # without a credential. It goes to the surfaces an operator uses, which are not doors.
    seen = obs.observe(gateway, obs.NowhereSink())
    assert seen["counts"]["worker"] == H.UNAVAILABLE
    assert seen["counts"]["worker_because"] == H.WORKER_UNREACHABLE


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
        assert "cannot reach what runs code" in verdict.reason
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


# ------------------------------------------------------------------ what a door may say


def test_no_anonymous_door_learns_where_this_gateway_s_worker_is(gateway):
    """`/v1/health` and `/v1/hello` are reached before anybody is anybody.

    The first version of this change put the worker's address and the errno on both, by setting
    the reason from the probe's exception. Two tests that were already there caught it
    (`TestHealthGivesNothingAway`, `test_what_is_reachable_without_a_credential_gives_nothing_
    away`) -- this one says the property in the words of this arc so that it is not re-learnt.

    The detail is not lost. It is in `gateway status`, in `gateway watch`, in the events file and
    in the statement in the gateway's own 0700 directory, and none of those is a door.
    """
    import json as _json

    gateway.worker.transport = "mtls"
    _the_worker_is_gone(gateway)
    where = "tcps://127.0.0.1:8443"
    assert where in gateway.health_now().reason, "the control: the operator's copy has it"

    for door in (_json.dumps(obs.health(gateway)), _json.dumps(gateway.hello()),
                 _json.dumps(gateway.readiness_now().as_dict())):
        assert where not in door
        assert "Errno" not in door
        assert "8443" not in door

    # And the shape of the anonymous health answer is not widened either.
    assert set(obs.health(gateway)) == {"serving", "measured", "taking_work", "because"}


def test_the_health_answer_does_not_contradict_itself(gateway):
    """`taking_work` used to mean only "nobody has pressed stop".

    So during a worker outage the same answer said `taking_work: true` beside a `because` that
    said the sandbox was not taking work. `MTLS-DEFAULT-R2-0001` found it on H1: a surface that
    contradicts itself in two adjacent fields is not one an operator can rely on, and it is the
    same kind of untruth as the `Protected` this arc is about.
    """
    assert obs.health(gateway)["taking_work"] is True          # the control

    gateway.worker.transport = "mtls"
    _the_worker_is_gone(gateway)

    said = obs.health(gateway)
    assert said["measured"] is False
    assert said["taking_work"] is False
    assert said["because"]


def test_the_operator_stop_still_decides_taking_work_on_its_own(gateway):
    """The counter-check: folding health in must not have replaced what the field meant."""
    from agentnode_sdk.gateway.allowance import stop_everything

    assert obs.health(gateway)["taking_work"] is True
    stop_everything(gateway.state.root, "upgrading the image")

    assert obs.health(gateway)["taking_work"] is False


def test_a_measurement_that_failed_is_told_apart_from_the_other_two(gateway):
    """H4 asks for three situations distinguished, and this is the third.

    `MTLS-DEFAULT-R2-0001` found that only two of them had been exercised. This drives the state
    machine into the third and asks what a caller and an operator actually get.
    """
    from agentnode_sdk.access.dispatch import Refused

    gateway.health._measure = lambda: False                    # a measurement that establishes nothing
    _the_worker_is_gone(gateway)
    _the_worker_answers(gateway)
    gateway.health.remeasure_if_needed()

    assert gateway.health_now().state == H.UNAVAILABLE
    assert gateway.health_now().code == H.MEASUREMENT_FAILED

    someone = _a_customer(gateway, "a customer")
    with pytest.raises(Refused) as refused:
        _a_run_by(gateway, someone)

    # WHICH of the three, without reading the English -- and a different word from both others.
    assert refused.value.cause == H.MEASUREMENT_FAILED
    assert refused.value.cause not in (H.WORKER_UNREACHABLE, H.MEASUREMENT_RUNNING)
    # And a step that fits THIS one. Waiting is the right answer for the other two and useless
    # here: the worker is answering, and what failed was the measurement.
    assert "Wait" not in refused.value.what_to_do
    assert "doctor --measure" in refused.value.what_to_do

    # And the operator is told something an operator can act on, which is not the same sentence.
    assert "did not establish" in gateway.health_now().reason


def test_the_operator_watch_names_the_cause_it_actually_found(gateway):
    """`MTLS-DEFAULT-R2-0004` refused H4 on this, and it was right.

    A caller had three distinct answers by then. An operator did not: `gateway watch` fired one
    rule, headed "the worker is not there", saying "Nothing can run until it is back... Look at
    the worker" -- for `measurement_failed` as well, directly above a reason on the same screen
    saying the worker was ANSWERING. Being told to wait for something that is not away is worse
    than being told nothing: it sends the person looking in the one place that is fine.

    So the assertion is not that some alert exists. It is that the heading and the step differ
    between the two causes, and that neither carries the other's advice.
    """
    sink = obs.NowhereSink()

    gateway.worker.transport = "mtls"
    _the_worker_is_gone(gateway)
    gone = [a for a in obs.observe(gateway, sink)["alerts"] if a["severity"] == obs.CRITICAL]

    gateway.health._measure = lambda: False
    _the_worker_answers(gateway)
    gateway.health.remeasure_if_needed()
    assert gateway.health_now().code == H.MEASUREMENT_FAILED
    failed = [a for a in obs.observe(gateway, sink)["alerts"] if a["severity"] == obs.CRITICAL]

    assert gone and failed, "both causes stop the machine, so both must ask for attention"
    assert gone[0]["rule"] == "the worker is not there"
    assert failed[0]["rule"] != gone[0]["rule"], "two causes, two headings"
    assert "worker is not there" not in failed[0]["rule"], (
        "the worker is answering; saying it is absent sends somebody to the wrong place")

    # And the step. Waiting is the whole answer to one of them and useless for the other.
    assert "Wait for the sandbox worker to come back" in gone[0]["what_it_means"]
    assert "come back" not in failed[0]["what_it_means"]
    assert "doctor --measure" in failed[0]["what_it_means"], "somebody has to look, and here"


def test_the_operator_and_the_caller_are_told_the_same_thing_to_do(gateway):
    """One table, because two tables drift -- which is how the defect above got in.

    THE FIRST VERSION OF THIS TEST WAS WORTHLESS, and its own counter-check said so. It asserted
    `_server._what_to_do_about(code) == H.what_to_do_about(code)`, which are two names for one
    function: both sides move together, so no amount of drift could ever make it fail. It was
    green under a mutation that gave `measurement_failed` a different answer entirely.

    What the property is actually about is two SURFACES. So this drives a real gateway into each
    cause and compares what the operator's alert tells them to do with what the caller is refused
    with -- which is the pair that disagreed, and the only comparison that can notice it again.
    """
    from agentnode_sdk.access.dispatch import Refused

    sink = obs.NowhereSink()
    someone = _a_customer(gateway, "a customer")
    gateway.worker.transport = "mtls"

    def what_each_is_told():
        alerts = [a for a in obs.observe(gateway, sink)["alerts"] if a["severity"] == obs.CRITICAL]
        with pytest.raises(Refused) as refused:
            _a_run_by(gateway, someone)
        return alerts[0], refused.value

    _the_worker_is_gone(gateway)
    alert, told = what_each_is_told()
    assert told.cause == H.WORKER_UNREACHABLE
    assert told.what_to_do in alert["what_it_means"], (
        "the operator and the caller are looking at one machine in one state")

    gateway.health._measure = lambda: False
    _the_worker_answers(gateway)
    gateway.health.remeasure_if_needed()
    alert, told = what_each_is_told()
    assert told.cause == H.MEASUREMENT_FAILED
    assert told.what_to_do in alert["what_it_means"], (
        "this is the pair that disagreed: the caller was sent to the doctor and the operator was "
        "told to wait for a worker that was answering")


def test_every_cause_gets_its_own_step_and_none_of_them_is_empty():
    """`Refused` will not be built without something to do, so an empty one is a crash waiting
    for the situation that produces it. And three causes with one sentence between them is what
    H4 exists to prevent."""
    from agentnode_sdk.gateway import server as _server

    steps = {code: _server._what_to_do_about(code) for code in
             (H.WORKER_UNREACHABLE, H.NOT_YET_PROBED, H.MEASUREMENT_RUNNING,
              H.MEASUREMENT_FAILED, H.STALE, H.NO_STATEMENT)}
    assert all(steps.values()), "a refusal with nothing to do about it leaves somebody stuck"
    assert len(set(steps.values())) == len(steps), "each cause needs its own, not a shared one"
    # Even one nobody has thought of yet gets something rather than nothing.
    assert _server._what_to_do_about("a code from a later build")


def test_a_gateway_whose_worker_is_down_still_starts_and_keeps_serving(tmp_path):
    """It must not die for this, and `make_server` is where it used to.

    `cmd_start` asked the worker whether it could isolate, one line after binding the socket. A
    gateway coming up while its worker was down therefore raised out of the start command, the
    CLI printed `That did not work: ... could not be reached`, the process exited 1, and systemd
    restarted it -- measured on the isolated pair on 2026-09-25, the restart counter climbing
    while the state machine underneath published `unavailable` correctly to nobody.

    HEALTH-HONESTY-0001 forbids exactly that: a control plane stays up and refuses in a
    structured way rather than dying because its worker is away.
    """
    import urllib.request

    from agentnode_sdk.gateway.server import make_server
    from agentnode_sdk.worker.local import LocalWorker

    class ItIsNotThere(LocalWorker):
        transport = "mtls"

        def can_it_isolate(self):
            raise WorkerUnreachable("the sandbox worker at tcps://127.0.0.1:8443 could not "
                                    "be reached: [Errno 111] Connection refused")

        def instance_label(self):
            raise WorkerUnreachable("the same, from the other call that used to be made early")

        def confirm_reachable(self, budget=None):
            raise WorkerUnreachable("still not there")

    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, worker=ItIsNotThere(StandInBackend()))
    server = None
    try:
        # BINDS AND SERVES. This is the line the defect was on.
        server = make_server(service, host="127.0.0.1", port=0)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        import threading as _t

        serving = _t.Thread(target=server.serve_forever, daemon=True)
        serving.start()

        with urllib.request.urlopen(base + "/v1/health", timeout=30) as answer:
            said = json.loads(answer.read().decode("utf-8"))

        # Still up, still answering, and honest about what it cannot do.
        assert said["serving"] is True
        assert said["measured"] is False
        assert said["taking_work"] is False
        assert said["because"]
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        service.close()
        state.close()


def test_asking_what_would_happen_answers_instead_of_dropping_the_connection(gateway):
    """Composing the answer names the worker, and the name comes from the worker.

    With the worker away that raised out of the request handler and the connection closed with
    NO RESPONSE: `Remote end closed connection without response` is what a client saw, which is
    indistinguishable from a broken gateway. Measured on the isolated pair on 2026-09-25, on a
    gateway that had been started while its worker was down -- so the defect was reachable only
    in the case the first review pointed at.

    What this door does is say what WOULD happen; whether it will is `submit`'s to decide. A
    first fix refused here instead, which moved that decision and broke nine tests in
    `test_em3c_gateway` that are about exactly this separation. So the fix is that it answers,
    and nothing else changed.
    """
    from agentnode_sdk.access import dispatch

    someone = _a_customer(gateway, "a customer")
    asking = {"artifact_sha256": "0" * 64, "artifact_bytes": 4, "wall_clock_s": 30}
    assert dispatch.dispatch("prepare", asking, someone, service=gateway)  # the control

    gateway.worker.transport = "mtls"
    _the_worker_is_gone(gateway)

    said = dispatch.dispatch("prepare", asking, someone, service=gateway)

    assert said["accepted_disclosure"], "it answers"
    # What it says about the worker when it genuinely cannot name one is the next test; this
    # worker can still name itself, and the point here is that the request got an answer.

    # And the refusal still happens, where it is supposed to: at the submission.
    from agentnode_sdk.access.dispatch import Refused

    with pytest.raises(Refused) as refused:
        _a_run_by(gateway, someone)
    assert refused.value.cause == H.WORKER_UNREACHABLE


def test_and_the_disclosure_itself_survives_a_worker_it_cannot_name(gateway):
    """The second line, for any path that reaches the disclosure with the worker away."""
    from agentnode_sdk.access import dispatch

    def it_cannot_be_asked():
        raise WorkerUnreachable("the sandbox worker could not be reached")

    gateway.worker.instance_label = it_cannot_be_asked
    someone = _a_customer(gateway, "a customer")

    said = dispatch._what_would_happen(
        gateway, someone,
        {"artifact_sha256": "0" * 64, "artifact_bytes": 4, "wall_clock_s": 30})

    assert "could not reach its worker" in said["runs_at"]
