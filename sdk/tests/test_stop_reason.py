"""Why a run stopped: nothing until it has, and what stopped it once it has.

`EM3C-E8-RECORD-0001` judged the record of the eighth external run BLOCK on two criteria for one
thing. The gateway's own signed answer for a cancelled run said:

    "state": "cancelled", "termination_reason": "exited", "exit_code": 137

and the same record, one step earlier, showed a RUNNING job already reporting
`termination_reason: exited` -- a reason for stopping, given by a run that had not stopped.

Neither was a lie anybody told. The field defaulted to the name of one of the things it can mean,
so nothing had to set it for it to say something; and when the client cancelled a run, what the
runtime made of the container this gateway had just destroyed -- an ordinary exit, status 137 --
was written down as why the run stopped.

## What is real here and what is not

The gateway is real: a real `GatewayService`, a real HTTP server, real pairing, real signatures,
and the client library's own verification. What is replaced is the SANDBOX, exactly as in
`test_cancel_consistency.py`: a backend that blocks until this file lets it go, so a run is
genuinely running when the cancellation arrives. That replacement cannot hide what this file is
about -- the backend here reports an ORDINARY EXIT for the container that was destroyed, which is
what a real runtime reports, and the question is what the gateway then writes down.
"""
from __future__ import annotations

import dataclasses
import hashlib
import threading
import time

import pytest

from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.protocol import (
    CANCELLED,
    EXITED,
    NOT_STOPPED,
    OUTCOME_FIELDS,
    QUEUED,
    RUNNING,
    TERMINATION_REASONS,
    TIMED_OUT,
    outcome_of,
    what_disagrees,
)
from agentnode_sdk.gateway.server import GatewayService, RunRecord, make_server

from tests.test_em3c_gateway import StandInBackend, _granted, _paired, _store_measurement
from tests.test_evidence import _the_answer_these_tests_use  # noqa: F401

#: The answers the reader's half of this file is judged against are REAL ones, from the real
#: gateway the evidence tests use -- declared the way that file declares it, rather than imported,
#: so the same module is not both a plugin and an import. A record this file wrote for itself
#: would be a second author of the protocol.
pytest_plugins = ("tests.real_answers",)


class ABackendThatWaitsAndThenExits(StandInBackend):
    """A sandbox that blocks until it is let go and then reports an ORDINARY EXIT.

    Which is what a real runtime reports for a container somebody destroyed underneath it: the
    process is gone, there is a number, and nothing in it says the container was removed rather
    than having finished. `native_platform` names whose number that is, the way the container
    backend does.
    """

    native_platform = "a-stand-in-runtime"

    def __init__(self, status: int = 137) -> None:
        super().__init__()
        self.status = status
        self.started = threading.Event()
        self.let_go = threading.Event()

    def run_process(self, spec, input_text=None, timeout=120.0):
        self.specs.append(spec)
        self.started.set()
        self.let_go.wait(timeout=30)
        return self.status, "", ""


class ABackendThatCannotSayWhoseNumberItIs(ABackendThatWaitsAndThenExits):
    """The same, from something that does not name its own numbers."""

    native_platform = ""


class ABackendThatRefusesToBeUsed(StandInBackend):
    """A sandbox that fails the test if anything asks it to run something."""

    native_platform = "a-stand-in-runtime"

    def run_process(self, spec, input_text=None, timeout=120.0):
        raise AssertionError("the sandbox was asked to run a job that had already been cancelled")


def _gateway(tmp_path, backend):
    import tempfile

    td = tempfile.mkdtemp(dir=str(tmp_path))
    state = GatewayState(td, version="test")
    service = GatewayService(state, backend=backend)
    _store_measurement(service)
    service.CONTAINER_APPEAR_SECONDS = 0.5
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_address[1]}", state, service, server, thread


@pytest.fixture()
def exiting_gateway(tmp_path):
    """A real gateway whose sandbox blocks and then reports an ordinary exit."""
    backend = ABackendThatWaitsAndThenExits()
    base, state, service, server, thread = _gateway(tmp_path, backend)
    try:
        yield base, state, service, backend
    finally:
        backend.let_go.set()
        server.shutdown()
        thread.join(timeout=10)


@pytest.fixture()
def anonymous_gateway(tmp_path):
    """The same, from a backend that cannot say whose numbers its statuses are."""
    backend = ABackendThatCannotSayWhoseNumberItIs()
    base, state, service, server, thread = _gateway(tmp_path, backend)
    try:
        yield base, state, service, backend
    finally:
        backend.let_go.set()
        server.shutdown()
        thread.join(timeout=10)


def a_cancelled_run(base, state, service, backend, run_id="cancel-me"):
    """Submit, wait until it is really running, cancel it, and return the signed record.

    The order matters and is the order a person's does: the run is going, the cancel arrives, and
    only THEN does the sandbox come back -- with whatever the runtime made of a container that was
    destroyed underneath it. Letting the sandbox go first would be watching a run finish.
    """
    conn = _paired(base, state)
    gc.submit(conn, b"print('x')", granted=_granted(service), run_id=run_id)
    assert backend.started.wait(timeout=10), "the job never reached the sandbox"

    asked = threading.Thread(target=lambda: gc.cancel(conn, run_id), daemon=True)
    asked.start()
    for _ in range(400):
        if service.runs[run_id].cancel_requested.is_set():
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the gateway never recorded that a cancellation was asked for")
    backend.let_go.set()
    asked.join(timeout=60)

    record = {}
    for _ in range(400):
        record = gc.status_of(conn, run_id)
        if record.get("state") == "cancelled":
            break
        time.sleep(0.05)
    return conn, record


# ------------------------------------------------- a run that has not stopped gives no reason


class TestARunThatHasNotStoppedGivesNoReason:

    def test_the_field_does_not_default_to_a_reason(self):
        """Not a convention. The default names none of the things the field can mean, so nothing
        has to set it for what it says to be true."""
        field = {f.name: f for f in dataclasses.fields(RunRecord)}["termination_reason"]
        assert field.default == NOT_STOPPED
        assert field.default not in TERMINATION_REASONS

    def test_a_fresh_record_says_nothing_about_why_it_stopped(self):
        record = RunRecord(run_id="r" * 32, job_id="j")
        assert record.public()["termination_reason"] == NOT_STOPPED
        assert record.state == "accepted"

    def test_a_running_job_says_nothing_about_why_it_stopped(self, exiting_gateway):
        """`EM3C-E8-RECORD-0001` found `running` beside `exited` in a real record."""
        base, state, service, backend = exiting_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="still-going")
        assert backend.started.wait(timeout=10)
        record = gc.status_of(conn, "still-going")
        assert record["state"] in (QUEUED, RUNNING)
        assert record["termination_reason"] == NOT_STOPPED
        assert record["exit_code"] is None

    def test_and_nothing_in_the_answer_disagrees_with_that(self, exiting_gateway):
        base, state, service, backend = exiting_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="agrees")
        assert backend.started.wait(timeout=10)
        record = gc.status_of(conn, "agrees")
        assert what_disagrees(record["state"], record["termination_reason"],
                              record["exit_code"], record["native_status"],
                              record["native_platform"]) == ""


# --------------------------------------------------- a cancelled run says it was cancelled


class TestACancelledRunSaysItWasCancelled:

    def test_a_run_cancelled_while_it_was_running(self, exiting_gateway):
        """The case the eighth external run recorded. The backend reports an ordinary exit for
        the container that was destroyed, and the run still says why it really stopped."""
        base, state, service, backend = exiting_gateway
        _conn, record = a_cancelled_run(base, state, service, backend)
        assert record["state"] == "cancelled"
        assert record["termination_reason"] == CANCELLED

    def test_a_run_cancelled_before_it_started(self, tmp_path):
        """The other path: the cancellation is already there when the worker looks, so the
        sandbox is never asked to run anything and nothing ever reports an exit of any kind.

        The worker is the real one, with a real request, a real grant and a real record. There is
        no call to the sandbox between building the specification and looking at the
        cancellation, so this is where the branch can be entered at all; the backend here fails
        the test if it is used."""
        from agentnode_sdk.gateway.protocol import policy_digest
        from agentnode_sdk.gateway.protocol import JobRequest

        backend = ABackendThatRefusesToBeUsed()
        base, state, service, server, thread = _gateway(tmp_path, backend)
        try:
            artifact = b"print('x')"
            granted = _granted(service)
            request = JobRequest(job_id="j" * 32, run_id="never-started",
                                 artifact_sha256=hashlib.sha256(artifact).hexdigest(),
                                 policy_sha256=policy_digest(granted))
            record = RunRecord(run_id=request.run_id, job_id=request.job_id,
                               artifact_sha256=request.artifact_sha256)
            record.cancel_requested.set()

            service._run(request, artifact, granted, record)

            assert record.state == "cancelled"
            assert record.termination_reason == CANCELLED
            assert record.exit_code is None
            assert record.native_status is None
            assert "cancelled by the client" in record.refusal
        finally:
            server.shutdown()
            thread.join(timeout=10)

    def test_the_state_and_the_reason_cannot_disagree(self, exiting_gateway):
        base, state, service, backend = exiting_gateway
        _conn, record = a_cancelled_run(base, state, service, backend, run_id="agreeing")
        assert what_disagrees(record["state"], record["termination_reason"],
                              record["exit_code"], record["native_status"],
                              record["native_platform"]) == ""

    def test_the_outcome_follows(self, exiting_gateway):
        base, state, service, backend = exiting_gateway
        _conn, record = a_cancelled_run(base, state, service, backend, run_id="outcome")
        assert outcome_of(record["state"], record["termination_reason"]) == "cancelled"

    def test_a_cancelled_run_whose_reason_is_the_runtimes_is_refused_by_the_rule(self):
        """What the record used to carry, held against the rule that now judges it."""
        why = what_disagrees("cancelled", EXITED, None, None, "")
        assert "stopped because it was cancelled" in why


# ------------------------------------------- what was stopped did not choose a status


class TestWhatWasStoppedDidNotChooseAStatus:

    def test_a_cancelled_run_claims_no_exit_status(self, exiting_gateway):
        base, state, service, backend = exiting_gateway
        _conn, record = a_cancelled_run(base, state, service, backend, run_id="no-status")
        assert record["exit_code"] is None

    def test_the_runtimes_own_number_is_kept_beside_the_reason(self, exiting_gateway):
        base, state, service, backend = exiting_gateway
        _conn, record = a_cancelled_run(base, state, service, backend, run_id="kept")
        assert record["native_status"] == backend.status
        assert record["native_platform"] == ABackendThatWaitsAndThenExits.native_platform

    def test_a_number_nobody_can_attribute_is_not_recorded(self, anonymous_gateway):
        """Because a number that does not say whose it is, is one a reader guesses about."""
        base, state, service, backend = anonymous_gateway
        _conn, record = a_cancelled_run(base, state, service, backend, run_id="anonymous")
        assert record["termination_reason"] == CANCELLED
        assert record["exit_code"] is None
        assert record["native_status"] is None
        assert record["native_platform"] == ""
        assert what_disagrees(record["state"], record["termination_reason"],
                              record["exit_code"], record["native_status"],
                              record["native_platform"]) == ""

    def test_the_container_backend_names_its_own_numbers(self):
        from agentnode_sdk.sandbox.backend import SandboxBackend
        from agentnode_sdk.sandbox.container_backend import (
            CONTAINER_PLATFORM,
            ContainerBackend,
        )

        assert ContainerBackend.native_platform == CONTAINER_PLATFORM
        assert CONTAINER_PLATFORM
        # And a backend that has not said stays silent rather than being given a name here.
        assert SandboxBackend.native_platform == ""

    def test_a_run_ended_by_its_limit_is_treated_the_same_way(self):
        """The shape `EM3C-E4-CLASSIFY-0001` established, now the shape both stopped runs use."""
        assert what_disagrees("finished", TIMED_OUT, None, 143, "linux-container") == ""
        why = what_disagrees("finished", TIMED_OUT, 143, None, "")
        assert "Nothing that was stopped chose a status" in why


# ------------------------------------------------------------- the rule, on its own


class TestTheRuleItself:

    def test_a_run_that_has_not_stopped_carrying_a_reason(self):
        for state in (QUEUED, RUNNING):
            why = what_disagrees(state, EXITED, None, None, "")
            assert "has no reason for having stopped" in why

    def test_a_run_that_has_not_stopped_carrying_a_status(self):
        why = what_disagrees(RUNNING, NOT_STOPPED, 0, None, "")
        assert "Nothing that is still running has exited" in why

    def test_a_reason_this_build_does_not_know(self):
        why = what_disagrees("finished", "vanished", None, None, "")
        assert "cannot be read" in why

    def test_a_terminal_run_carrying_no_reason(self):
        why = what_disagrees("finished", NOT_STOPPED, None, None, "")
        assert "cannot be read" in why

    def test_a_native_status_with_nobody_to_attribute_it_to(self):
        why = what_disagrees("finished", EXITED, 0, 137, "")
        assert "which platform produced it" in why

    def test_an_ordinary_exit_with_a_status_is_fine(self):
        assert what_disagrees("finished", EXITED, 0, None, "") == ""

    def test_a_state_this_build_cannot_place_is_left_to_whoever_reads_states(self):
        """Not silently: states are refused where states are read, and saying it twice here
        would make this rule about something other than what it is about."""
        assert what_disagrees("almost-done", EXITED, 0, None, "") == ""


# ------------------------------------------------------------- the reader enforces it


class TestTheReaderEnforcesIt:
    """Through the recorder and the file, which is the only route a record is judged by."""

    def _found(self, tmp_path, **record_changes):
        from tests.test_evidence import (
            OK_RECORD,
            RUN,
            accepted,
            container_for,
            good_step,
            messages,
            resigned,
        )

        answer = resigned(**record_changes)
        found = _recorded_over(tmp_path, [good_step(
            gateway_record=answer, run_id=RUN(), container=container_for(RUN()),
            answer=accepted(over=answer))])
        assert OK_RECORD
        return messages(found)

    def test_a_run_that_has_not_stopped_carrying_a_reason(self, tmp_path):
        said = self._found(tmp_path, state="running", termination_reason=EXITED, exit_code=None)
        assert "has no reason for having stopped" in said

    def test_a_terminal_run_carrying_none(self, tmp_path):
        said = self._found(tmp_path, state="finished", termination_reason=NOT_STOPPED)
        assert "cannot be read" in said

    def test_a_cancelled_run_that_says_it_exited(self, tmp_path):
        """What the eighth external run recorded, held against the reader that judges one."""
        said = self._found(tmp_path, state="cancelled", termination_reason=EXITED, exit_code=137)
        assert "stopped because it was cancelled" in said

    def test_a_reason_beside_an_exit_status(self, tmp_path):
        said = self._found(tmp_path, state="finished", termination_reason=TIMED_OUT, exit_code=3)
        assert "Nothing that was stopped chose a status" in said

    def test_a_number_with_no_platform(self, tmp_path):
        said = self._found(tmp_path, state="finished", termination_reason=EXITED,
                           exit_code=0, native_status=137, native_platform="")
        assert "which platform produced it" in said

    def test_the_reader_supplies_nothing_of_its_own(self, tmp_path):
        """A record that did not carry the field did not say the run exited."""
        import inspect

        from agentnode_sdk.tools import evidence

        source = inspect.getsource(evidence)
        assert 'record.get("termination_reason", production["exited"])' not in source
        assert 'record.get("termination_reason", "")' in source


def _recorded_over(tmp_path, steps):
    from tests.test_evidence import _recorded

    return _recorded(tmp_path, steps)


# ------------------------------------------------------- the client shows what the answer says


class TestTheClientShowsWhatTheAnswerSays:

    def test_it_does_not_supply_a_reason_of_its_own(self):
        import inspect

        from agentnode_sdk.cli import remote_commands

        source = inspect.getsource(remote_commands)
        for supplied in ('or EXITED', 'or "exited"', "or 'exited'"):
            assert supplied not in source, supplied

    def test_what_it_prints_for_a_cancelled_run(self, exiting_gateway, capsys):
        """Every word out of the verified answer. The connection is the one this test paired,
        handed to the command directly rather than through a saved connection store."""
        import types

        from agentnode_sdk.cli import remote_commands

        base, state, service, backend = exiting_gateway
        conn, record = a_cancelled_run(base, state, service, backend, run_id="printed")
        assert record["termination_reason"] == CANCELLED

        saved = types.SimpleNamespace(name="e3")
        was = remote_commands._connection
        remote_commands._connection = lambda args: (saved, conn)
        capsys.readouterr()
        try:
            code = remote_commands.cmd_cancel(types.SimpleNamespace(run="printed"))
        finally:
            remote_commands._connection = was
        printed = capsys.readouterr().out
        assert code == 0, printed
        assert "cancelled (cancelled)" in printed


# ------------------------------------------------------------------- nothing else moved


class TestNothingElseMoved:

    def test_the_signed_outcome_still_covers_the_same_fields(self):
        assert OUTCOME_FIELDS == ("state", "exit_code", "termination_reason", "native_status",
                                  "native_platform", "cleanup_verified", "refusal", "stdout",
                                  "stderr", "policy_deltas", "started_at", "finished_at")

    def test_the_reasons_a_stopped_run_may_give_are_unchanged(self):
        assert TERMINATION_REASONS == (EXITED, TIMED_OUT, CANCELLED)

    def test_and_the_thing_that_says_nothing_is_not_one_of_them(self):
        assert NOT_STOPPED not in TERMINATION_REASONS
