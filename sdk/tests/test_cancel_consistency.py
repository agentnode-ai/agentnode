"""What state a run is in, who may say so, and when a cancellation is true.

`EM3C-E6-RECORD-0001`, criterion V7: the sixth external run cancelled a job, the gateway removed
its container, and the client then told the operator the run was `running`. Two things were wrong
at once and neither would have been enough on its own.

The gateway answered too early. `cancel` removed the container and returned while the worker had
not yet published the terminal state -- and the worker publishes it LAST, after cleanup, on
purpose. So the record really did say `running` at that instant. It was true of the record and
false about the world.

And the client did not check. `status_of` verified the answer it got; `cancel`, three lines below
it, returned the body unverified. The one state a person was shown after asking to stop something
was the only state in that client nothing had looked at.

Underneath both: nothing anywhere said a state may not go backwards. Every place that set one just
assigned the field.

## What is real here and what is not

The gateway is real: a real `GatewayService`, a real HTTP server, a real pairing, real signatures,
and the client library's own verification. What is replaced is the SANDBOX -- a backend that
blocks until this file lets it go, so that a run is genuinely running when the cancellation
arrives and the window between "the container is gone" and "the worker has published the terminal
state" is a place a test can stand rather than a race it has to win.

That replacement cannot hide the failure this file exists to catch. The failure lives entirely
between the cancel endpoint and the worker's last line; a backend that blocks makes that window
wide and deterministic instead of narrow and lucky. `TestARealContainer` runs the same case
against a real runtime, and the counter-checks put each defect back and watch the fast tests go
red for it.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway.protocol import (
    CANCELLED,
    EXITED,
    OUTCOMES,
    QUEUED,
    RUNNING,
    STATES,
    TERMINAL_STATES,
    TIMED_OUT,
    ProtocolError,
    is_terminal,
    may_move,
    outcome_of,
    refuse_move,
    stage_of,
)
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, RunRecord, make_server

from tests.test_em3c_gateway import StandInBackend, _granted, _paired, _store_measurement


class ABackendThatWaits(StandInBackend):
    """A sandbox that does not return until it is let go, so a run is really running."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.let_go = threading.Event()
        self.slept = 0.0

    def run_process(self, spec, input_text=None, timeout=120.0):
        self.specs.append(spec)
        self.started.set()
        began = time.monotonic()
        self.let_go.wait(timeout=30)
        self.slept = time.monotonic() - began
        return 0, "RAN", ""


@pytest.fixture()
def waiting_gateway(tmp_path):
    """A real gateway whose sandbox blocks. Everything except the sandbox is the real thing."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        state = GatewayState(td, version="test")
        backend = ABackendThatWaits()
        service = GatewayService(state, backend=backend)
        _store_measurement(service)
        # This gateway has no container runtime to wait for, so it does not wait the length of
        # one. Only the window is shortened; nothing about the order is changed.
        service.CONTAINER_APPEAR_SECONDS = 0.5
        server = make_server(service, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            yield base, state, service, backend
        finally:
            backend.let_go.set()
            server.shutdown()
            thread.join(timeout=10)
            # And then wait for the RUNS. Several of these tests leave one deliberately unsettled
            # -- that is what they are about -- and the worker publishes the terminal state last,
            # after it has written the ledger. Letting the temporary directory go while that is
            # still happening is the test deleting a file the gateway is holding, which on
            # Windows is an error rather than a shrug. Nothing about the gateway is waited on
            # here that a real operator would not also wait for when stopping one.
            from agentnode_sdk.gateway.protocol import is_terminal as _is_terminal

            for _ in range(300):
                if all(_is_terminal(r.state) for r in list(service.runs.values())):
                    break
                time.sleep(0.05)


def a_running_job(base, state, service, backend, run_id="run-under-test"):
    """Submit a job and wait until it is really running. Returns the connection."""
    conn = _paired(base, state)
    gc.submit(conn, b"print('x')", granted=_granted(service), run_id=run_id)
    assert backend.started.wait(timeout=10), "the job never reached the sandbox"
    return conn


# ---------------------------------------------------------------- the state machine itself


class TestAStateGoesOneWay:

    def test_every_state_can_be_placed(self):
        assert set(STATES) == {QUEUED, RUNNING} | set(TERMINAL_STATES)
        for state in STATES:
            assert stage_of(state) in (0, 1, 2)

    def test_a_state_nobody_declared_is_refused_rather_than_ranked_lowest(self):
        """Ranking an unknown state lowest would let one arrive and be treated as the start."""
        with pytest.raises(ProtocolError) as caught:
            stage_of("almost-done")
        assert "not a state this build knows" in str(caught.value)

    def test_forward_is_allowed(self):
        assert may_move(QUEUED, RUNNING)
        for terminal in TERMINAL_STATES:
            assert may_move(QUEUED, terminal)
            assert may_move(RUNNING, terminal)

    def test_standing_still_is_not_a_move(self):
        for state in STATES:
            assert may_move(state, state)

    def test_backwards_is_refused(self):
        assert not may_move(RUNNING, QUEUED)
        for terminal in TERMINAL_STATES:
            assert not may_move(terminal, QUEUED)
            assert not may_move(terminal, RUNNING)

    def test_a_terminal_state_is_where_it_stops(self):
        for terminal in TERMINAL_STATES:
            for other in TERMINAL_STATES:
                assert may_move(terminal, other) is (terminal == other)

    def test_the_refusal_names_both_states(self):
        with pytest.raises(ProtocolError) as caught:
            refuse_move("cancelled", RUNNING)
        assert "'cancelled'" in str(caught.value) and "'running'" in str(caught.value)

    def test_is_terminal_agrees_with_the_list(self):
        for state in STATES:
            assert is_terminal(state) == (state in TERMINAL_STATES)


class TestWhatAnEndAmountsTo:
    """The four the record has to be able to say: it succeeded, it was cancelled, it ran out of
    time, it failed."""

    def test_a_run_that_has_not_ended_has_no_outcome(self):
        assert outcome_of(QUEUED) == ""
        assert outcome_of(RUNNING) == ""

    def test_every_terminal_state_has_exactly_one(self):
        for state in TERMINAL_STATES:
            got = outcome_of(state)
            assert got in OUTCOMES, (state, got)

    def test_the_four_are_reachable_and_distinct(self):
        assert outcome_of("finished", EXITED) == "succeeded"
        assert outcome_of("cancelled", CANCELLED) == "cancelled"
        assert outcome_of("finished", TIMED_OUT) == "timed_out"
        assert outcome_of("refused", EXITED) == "failed"
        assert len({outcome_of("finished", EXITED), outcome_of("cancelled", CANCELLED),
                    outcome_of("finished", TIMED_OUT), outcome_of("refused", EXITED)}) == 4

    def test_a_run_stopped_by_its_own_limit_is_not_a_success(self):
        """The distinction the fourth external run lost when a timeout was an integer."""
        assert outcome_of("finished", TIMED_OUT) != outcome_of("finished", EXITED)

    def test_the_ones_that_are_neither_cancelled_nor_timed_out_nor_finished_failed(self):
        for state in ("refused", "unverified", "interrupted"):
            assert outcome_of(state, EXITED) == "failed"


class TestTheRecordWillNotGoBackwards:

    def test_a_record_moves_forward(self):
        record = RunRecord(run_id="r", job_id="j")
        assert record.state == QUEUED
        record.move_to(RUNNING)
        record.move_to("cancelled")
        assert record.state == "cancelled"

    def test_and_refuses_to_go_back(self):
        record = RunRecord(run_id="r", job_id="j")
        record.move_to(RUNNING)
        record.move_to("finished")
        with pytest.raises(ProtocolError):
            record.move_to(RUNNING)
        assert record.state == "finished", "the refused move must not have half-happened"

    def test_the_outcome_comes_off_the_record(self):
        record = RunRecord(run_id="r", job_id="j")
        record.move_to(RUNNING)
        record.termination_reason = TIMED_OUT
        record.move_to("finished")
        assert record.outcome == "timed_out"

    def test_there_is_one_place_a_state_is_set(self):
        """A guard everything routes around is not a guard, and an exception written into the
        test that checks for exceptions is the same thing with extra steps. There are none: a
        record rebuilt from the ledger is CONSTRUCTED in the state it is in."""
        import inspect

        from agentnode_sdk.gateway import server

        # A RUN's state, which is what this is about. `GatewayService.__init__` also writes
        # `self.state`, and that one is a `GatewayState` -- the directory the gateway keeps its
        # things in. Two different words spelled the same way, and a check that cannot tell them
        # apart would be reporting the name rather than the property.
        written = [line.strip() for line in inspect.getsource(server).splitlines()
                   if ".state = " in line and "self.state = state" not in line]
        assert written == ["self.state = new_state"], written

    def test_and_the_one_place_is_the_guarded_one(self):
        import inspect

        from agentnode_sdk.gateway.server import RunRecord

        assert "self.state = new_state" in inspect.getsource(RunRecord.move_to)
        assert "refuse_move(self.state, new_state)" in inspect.getsource(RunRecord.move_to)

    def test_a_record_rebuilt_from_the_ledger_begins_where_it_is(self):
        """Constructing a record in a terminal state is not a move out of one -- there was no
        earlier state to move from. What must not happen is constructing it at the start and
        then writing over that."""
        record = RunRecord(run_id="r", job_id="j", state="interrupted")
        assert record.state == "interrupted"
        with pytest.raises(ProtocolError):
            record.move_to(RUNNING)


# ---------------------------------------------------------------- a real gateway


class TestACancellationAnswersWhenItIsDone:

    def test_it_waits_for_the_run_to_actually_stop(self, waiting_gateway):
        base, state, service, backend = waiting_gateway
        conn = a_running_job(base, state, service, backend)

        released = threading.Timer(0.6, backend.let_go.set)
        released.start()
        try:
            record, settled = gc.cancel(conn, "run-under-test")
        finally:
            released.cancel()
            backend.let_go.set()

        assert settled is True
        assert is_terminal(record["state"]), record["state"]
        assert record["state"] == "cancelled"

    def test_and_the_cleanup_is_done_by_then(self, waiting_gateway):
        """The worker publishes the terminal state last, after cleanup. A terminal answer is
        therefore a complete one -- which is the whole reason for waiting rather than replying."""
        base, state, service, backend = waiting_gateway
        conn = a_running_job(base, state, service, backend)
        threading.Timer(0.4, backend.let_go.set).start()
        record, settled = gc.cancel(conn, "run-under-test")
        assert settled is True
        assert record["cleanup_verified"] is not None
        assert record["finished_at"] is not None

    def test_what_comes_back_is_signed_and_verified(self, waiting_gateway):
        base, state, service, backend = waiting_gateway
        conn = a_running_job(base, state, service, backend)
        threading.Timer(0.4, backend.let_go.set).start()
        record, _ = gc.cancel(conn, "run-under-test")
        # It came back at all, which means the client's own verification did not refuse it, and
        # it carries what that verification recomputes over.
        from agentnode_sdk.gateway.protocol import SIGNATURE_FIELDS, STAMP_FIELDS

        for field in SIGNATURE_FIELDS + STAMP_FIELDS:
            assert record.get(field), field

    def test_an_answer_that_was_tampered_with_is_refused(self, waiting_gateway, monkeypatch):
        """Verification is what the cancellation path did not do. This is that path."""
        base, state, service, backend = waiting_gateway
        conn = a_running_job(base, state, service, backend)
        threading.Timer(0.3, backend.let_go.set).start()

        real_post = gc._post

        def meddling(url, body, **kw):
            status, answer = real_post(url, body, **kw)
            if isinstance(answer, dict) and answer.get("state"):
                answer["state"] = RUNNING           # the exact lie E6 displayed
            return status, answer

        monkeypatch.setattr(gc, "_post", meddling)
        with pytest.raises(gc.GatewayClientError):
            gc.cancel(conn, "run-under-test")

    def test_cancelling_twice_says_the_same_thing(self, waiting_gateway):
        base, state, service, backend = waiting_gateway
        conn = a_running_job(base, state, service, backend)
        threading.Timer(0.4, backend.let_go.set).start()
        first, first_settled = gc.cancel(conn, "run-under-test")
        second, second_settled = gc.cancel(conn, "run-under-test")
        assert first_settled is True and second_settled is True
        assert first["state"] == second["state"]
        assert first["run_id"] == second["run_id"]
        assert first["finished_at"] == second["finished_at"]

    def test_a_cancellation_that_has_not_settled_says_so(self, waiting_gateway):
        """And does not call itself terminal, which is the failure in the other direction."""
        base, state, service, backend = waiting_gateway
        service.CANCEL_SETTLE_SECONDS = 0.4
        conn = a_running_job(base, state, service, backend)
        record, settled = gc.cancel(conn, "run-under-test")
        assert settled is False
        assert not is_terminal(record["state"])
        assert record["state"] == RUNNING
        backend.let_go.set()

    def test_a_run_that_was_never_started_is_not_invented(self, waiting_gateway):
        base, state, service, backend = waiting_gateway
        conn = _paired(base, state)
        with pytest.raises(gc.GatewayClientError):
            gc.cancel(conn, "0" * 32)


class TestAPollAtTheSameTimeSeesNothingImpossible:

    def test_a_watcher_never_sees_the_run_go_backwards(self, waiting_gateway):
        """The concurrent case, and it is not a timing test: the poller records every state it is
        given and the sequence is checked afterwards, so a single backwards answer fails it."""
        base, state, service, backend = waiting_gateway
        # The watcher is this run's own client: a run is readable by its owner and by nobody
        # else, so a second pairing would be refused for the right reason and prove nothing.
        # Two threads, one client, which is what a person polling in another window has.
        conn = a_running_job(base, state, service, backend)
        watcher = conn

        seen: list = []
        trouble: list = []
        stop = threading.Event()

        def poll():
            while not stop.is_set():
                try:
                    seen.append(gc.status_of(watcher, "run-under-test")["state"])
                except Exception as exc:                      # noqa: BLE001
                    trouble.append(exc)
                    return
                time.sleep(0.01)

        eye = threading.Thread(target=poll, daemon=True)
        eye.start()
        threading.Timer(0.5, backend.let_go.set).start()
        record, settled = gc.cancel(conn, "run-under-test")
        time.sleep(0.2)
        stop.set()
        eye.join(timeout=5)

        assert settled is True
        assert not trouble, trouble
        assert seen, "the watcher never got an answer"
        for before, after in zip(seen, seen[1:]):
            assert may_move(before, after), (before, after, seen)
        assert seen[-1] == record["state"]

    def test_a_watcher_that_saw_the_end_is_never_told_it_started_again(self, waiting_gateway):
        base, state, service, backend = waiting_gateway
        conn = a_running_job(base, state, service, backend)
        threading.Timer(0.3, backend.let_go.set).start()
        record, _ = gc.cancel(conn, "run-under-test")
        assert is_terminal(record["state"])
        again = gc.status_of(conn, "run-under-test")
        assert again["state"] == record["state"]


# ---------------------------------------------------------------- the client's own refusal


class TestAnAnswerThatMovesARunBackwardsIsRefused:

    def a_connection(self, gateway_id="g-1"):
        return gc.GatewayConnection(base_url="http://127.0.0.1:1", token="t",
                                    gateway_id=gateway_id, fingerprint="f")

    def test_a_late_answer_is_refused(self):
        conn = self.a_connection("late")
        gc.not_backwards(conn, {"run_id": "a", "state": "cancelled"})
        with pytest.raises(gc.GatewayClientError) as caught:
            gc.not_backwards(conn, {"run_id": "a", "state": RUNNING})
        assert "already seen" in str(caught.value)

    def test_two_answers_in_the_wrong_order(self):
        conn = self.a_connection("order")
        gc.not_backwards(conn, {"run_id": "b", "state": "finished"})
        with pytest.raises(gc.GatewayClientError):
            gc.not_backwards(conn, {"run_id": "b", "state": QUEUED})

    def test_a_terminal_state_may_arrive_twice(self):
        conn = self.a_connection("twice")
        gc.not_backwards(conn, {"run_id": "c", "state": "cancelled"})
        again = gc.not_backwards(conn, {"run_id": "c", "state": "cancelled"})
        assert again["state"] == "cancelled"

    def test_it_never_supplies_a_state_of_its_own(self):
        """It refuses or it passes the answer through. What it must never do is substitute the
        value it remembers -- that would be displaying a cached state, which is the thing."""
        conn = self.a_connection("supply")
        gc.not_backwards(conn, {"run_id": "d", "state": "cancelled"})
        passed = gc.not_backwards(conn, {"run_id": "d", "state": "cancelled",
                                         "stdout": "the second answer"})
        assert passed["stdout"] == "the second answer"

    def test_two_gateways_do_not_share_a_memory(self):
        one, other = self.a_connection("one"), self.a_connection("other")
        gc.not_backwards(one, {"run_id": "shared", "state": "cancelled"})
        assert gc.not_backwards(other, {"run_id": "shared", "state": RUNNING})["state"] == RUNNING

    def test_an_answer_that_names_no_run_is_left_alone(self):
        conn = self.a_connection("nameless")
        assert gc.not_backwards(conn, {"error": "no such endpoint"}) == {"error": "no such endpoint"}


# ---------------------------------------------------------------- what a person is shown


class TestWhatTheCommandLineShows:

    def a_saved_gateway(self, tmp_path, base, state, monkeypatch):
        from agentnode_sdk.gateway.connections import ConnectionStore, SavedGateway

        monkeypatch.setenv("AGENTNODE_HOME", str(tmp_path / "home"))
        conn = _paired(base, state)
        ConnectionStore().save(SavedGateway(name="g", url=base, token=conn.token,
                                            gateway_id=conn.gateway_id,
                                            fingerprint=conn.fingerprint))
        return conn

    def test_it_prints_the_state_the_gateway_signed(self, waiting_gateway, tmp_path,
                                                    monkeypatch, capsys):
        from agentnode_sdk.cli import remote_commands

        base, state, service, backend = waiting_gateway
        conn = self.a_saved_gateway(tmp_path, base, state, monkeypatch)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="shown")
        assert backend.started.wait(timeout=10)
        threading.Timer(0.4, backend.let_go.set).start()

        code = remote_commands.cmd_cancel(
            type("A", (), {"run": "shown", "name": "g"})())
        out = capsys.readouterr().out
        assert code == 0, out
        assert "It stopped" in out and "cancelled" in out
        assert "running" not in out, out

    def test_and_says_so_when_it_did_not_stop(self, waiting_gateway, tmp_path,
                                              monkeypatch, capsys):
        from agentnode_sdk.cli import remote_commands

        base, state, service, backend = waiting_gateway
        service.CANCEL_SETTLE_SECONDS = 0.4
        conn = self.a_saved_gateway(tmp_path, base, state, monkeypatch)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="unsettled")
        assert backend.started.wait(timeout=10)

        code = remote_commands.cmd_cancel(
            type("A", (), {"run": "unsettled", "name": "g"})())
        out = capsys.readouterr().out
        backend.let_go.set()
        assert code == 1, out
        assert "has not stopped yet" in out
        assert "It stopped" not in out

    def test_there_is_no_way_to_ask_for_an_unverified_answer(self):
        """`EM3C-CANCEL-0005`: showing that no caller used the flag is not the same as its not
        being callable. There is no flag."""
        import inspect
        import pathlib

        import agentnode_sdk

        assert list(inspect.signature(gc.status_of).parameters) == ["connection", "run_id"]
        root = pathlib.Path(agentnode_sdk.__file__).parent
        naming = [p.name for p in root.rglob("*.py")
                  if "verify=False" in p.read_text(encoding="utf-8")]
        assert naming == [], naming

    def test_reading_deciding_and_writing_are_one_thing(self):
        """`EM3C-CANCEL-0005`: they were three, so two answers arriving at once could both be
        judged against the same older value and then written in the wrong order. A client polling
        in one thread while cancelling in another is the ordinary case.

        Hammered rather than argued: many pairs, each a terminal answer and an earlier one, and
        afterwards the memory must never hold the earlier of the two."""
        conn = gc.GatewayConnection(base_url="http://127.0.0.1:1", token="t",
                                    gateway_id="racing", fingerprint="f")
        refused: list = []
        for round_number in range(200):
            run = "run-%d" % round_number
            done = threading.Barrier(2)

            def one(state, run=run, done=done):
                done.wait(timeout=5)
                try:
                    gc.not_backwards(conn, {"run_id": run, "state": state})
                except gc.GatewayClientError:
                    refused.append(state)

            # Both orders, so the case where the earlier answer LOSES the race really happens.
            # Whichever way round they go, the memory must end terminal and the loser refused.
            pair = [threading.Thread(target=one, args=("cancelled",)),
                    threading.Thread(target=one, args=(RUNNING,))]
            threads = pair if round_number % 2 else list(reversed(pair))
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            assert gc._FURTHEST[("racing", run)] == "cancelled", round_number
        assert refused, "no interleaving ever put the two in the order that has to be refused"

    def test_nothing_unverified_reaches_it(self):
        """A source check, because the defect was a missing call rather than a wrong value."""
        import inspect

        from agentnode_sdk.gateway import client as module

        source = inspect.getsource(module.cancel)
        assert "verify_answer(connection, answer)" in source
        assert "not_backwards(" in source
        assert "return answer" not in source


# ---------------------------------------------------------------- a real container


@pytest.mark.skipif(os.environ.get("AGENTNODE_SANDBOX_E2E") != "1",
                    reason="set AGENTNODE_SANDBOX_E2E=1 (needs a container runtime) to run")
class TestARealContainer:
    """The same case with nothing replaced. Slower and less deterministic, which is why the fast
    tests above exist -- but this is the one where the container is real and really removed."""

    def test_cancelling_a_real_run_answers_only_once_it_is_gone(self, tmp_path):
        import tempfile

        from agentnode_sdk.sandbox.container_backend import ContainerBackend

        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            service = GatewayService(state, backend=ContainerBackend())
            _store_measurement(service)
            server = make_server(service, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                conn = _paired(base, state)
                payload = b"import time\nprint('up', flush=True)\ntime.sleep(120)\n"
                gc.submit(conn, payload,
                          granted=_granted(service, wall_clock_s=120), run_id="real-cancel")
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if gc.status_of(conn, "real-cancel")["state"] == RUNNING:
                        break
                    time.sleep(0.5)
                # A poll running at the same time, against a REAL container. `EM3C-CANCEL-0001`
                # found the concurrent case and the container case were two different tests, so
                # a container-specific ordering defect had nowhere to show. They are one test.
                seen: list = []
                trouble: list = []
                stop = threading.Event()

                def poll():
                    while not stop.is_set():
                        try:
                            seen.append(gc.status_of(conn, "real-cancel")["state"])
                        except Exception as exc:              # noqa: BLE001
                            trouble.append(exc)
                            return
                        time.sleep(0.05)

                eye = threading.Thread(target=poll, daemon=True)
                eye.start()
                try:
                    record, settled = gc.cancel(conn, "real-cancel")
                    time.sleep(0.5)
                finally:
                    stop.set()
                    eye.join(timeout=10)

                assert not trouble, trouble
                assert seen, "the watcher never got an answer"
                for before, after in zip(seen, seen[1:]):
                    assert may_move(before, after), (before, after, seen)
                assert seen[-1] == record["state"]
                assert settled is True, record
                assert record["state"] == "cancelled"
                assert record["cleanup_verified"] is True
                # The container's name is the gateway's own business and is not in what a
                # client may see, so it is asked of the gateway rather than recomputed here --
                # a formula copied into a test is a second definition waiting to disagree.
                named = service.runs["real-cancel"].container_name
                assert named, "the gateway never gave this run a container name"
                answered, names = service._containers_named(named)
                assert answered and names == [], names
            finally:
                server.shutdown()
                thread.join(timeout=10)
