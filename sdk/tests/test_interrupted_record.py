"""Exactly one signed closing line per accepted run, whatever interrupted it.

`interrupted-audit-record-r1`. The observation: a job the gateway had accepted could end without
any line in the signed log at all. Nothing was billed for it, which sounds like the customer's
good luck -- but the signed, chained log is the thing this service would hand somebody as proof
of what it did, and a job it does not contain is a job this service cannot account for.

The queue made the case ordinary rather than rare. A job waiting for a slot when a restart
happens is now a normal event, not an unlucky one.

Three things had to become true, and only the first of them existed:

1. a line is WRITTEN for an interrupted run;
2. it is written EXACTLY once -- enforced, not merely observed so far;
3. it SAYS something: which interruption, whether the job ever started, what became of its
   sandbox.

Most of what follows drives the code rather than reading it. That is a lesson from the profile
before this one: four mechanisms there turned out to have nothing but a check on their own source
text as a witness, and one had no witness at all. A check that reads `inspect.getsource` is the
right instrument for "the reasoning is written down beside the number" and the wrong one for
"this comes out".
"""
from __future__ import annotations

import ast
import inspect
import json
import pathlib
import textwrap
import time

import pytest

from agentnode_sdk.gateway import meter
from agentnode_sdk.gateway.protocol import (
    GATEWAY_CRASHED,
    GATEWAY_KILLED,
    GATEWAY_LOST,
    GATEWAY_STOPPED,
    NOTHING_WAS_ESTABLISHED,
    SANDBOX_CONFIRMED_GONE,
    SANDBOX_DISPOSITIONS,
    SANDBOX_NEVER_CREATED,
    SANDBOX_NOT_ESTABLISHED,
    SANDBOX_STILL_THERE,
    TERMINATION_REASONS,
    UNVERIFIED_OUTCOME,
    outcome_of,
)

ADMITTED = {"cpu": 1.0, "memory_mb": 512, "wall_clock_s": 60,
            "allowance_sha256": "a" * 64, "operator_policy_sha256": "p" * 64,
            "operator_policy_version": 1, "worker_topology": "single-host-development"}


def lines_in(root) -> list:
    where = pathlib.Path(root) / meter.METER_NAME
    if not where.is_file():
        return []
    return [json.loads(x) for x in where.read_text(encoding="utf-8").splitlines() if x.strip()]


def lines_for(root, run_id: str) -> list:
    """EVERY line for this run, not the first. A test about `exactly one` cannot use a helper
    that stops looking after it finds one."""
    return [x for x in lines_in(root) if x.get("run_id") == run_id]


def the_one_line_for(root, run_id: str) -> dict:
    got = lines_for(root, run_id)
    assert len(got) == 1, "expected exactly one line for %s, found %d" % (run_id, len(got))
    return got[0]


def a_line(root, run_id: str, **changes):
    """One metered line with everything named, for tests about the meter itself."""
    said = dict(run_id=run_id, client_id="dev-1", account_id="acct-" + "0" * 16,
                queued_at=1000.0, started_at=1010.0, finished_at=1021.0,
                cpu=1.0, memory_mb=512, wall_clock_s=60,
                state="finished", outcome="succeeded", termination_reason="exited",
                bytes_out=0, worker_topology="single-host-development", worker_id="w",
                allowance_sha256="a" * 64, operator_policy_sha256="p" * 64,
                operator_policy_version=1)
    said.update(changes)
    return meter.record(root, **said)


@pytest.fixture(autouse=True)
def a_boot_identity(monkeypatch):
    """Every platform gets one for the duration of a test, because the LOGIC is portable.

    Only Linux publishes a boot identity, so a test that used the real one would SKIP
    everywhere else -- and a skipped test is not a witness to anything. What is platform-bound
    is whether the value can be had; what this file is about is what the code does with it, and
    that is the same code on every machine.

    `test_this_machine_really_publishes_a_boot_identity` covers the other half where it exists.
    """
    from agentnode_sdk.gateway import lifecycle

    monkeypatch.setattr(lifecycle, "this_boot", lambda: "the-boot-this-test-runs-under")


@pytest.fixture()
def gateway(tmp_path):
    """A real gateway whose sandbox is a stand-in, on a directory of its own."""
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService
    from tests.test_em3c_gateway import StandInBackend, _store_measurement

    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        try:
            service.close()
        except Exception:                                      # noqa: BLE001
            pass
        try:
            state.close()
        except Exception:                                      # noqa: BLE001
            pass


def claim(service, run_id: str, *, when: float, started: float | None = None) -> None:
    """A run the ledger has accepted, and optionally one it saw start."""
    assert service.ledger.claim(run_id, "nonce-" + run_id, "s" * 64, "dev",
                                now=when, owner_account_id="acct-" + "1" * 16,
                                admitted=ADMITTED)
    if started is not None:
        service.ledger.note_state(run_id, "running", at=started)


def lifecycle_module():
    from agentnode_sdk.gateway import lifecycle

    return lifecycle


def restart(service, *, cleanly: bool = False, went: str = "killed"):
    """A SECOND gateway on the same directory, which is what a restart is.

    `cleanly` decides what the one before it left behind: a gateway that began to stop, or one
    that went without saying anything. That file is the only thing telling the two apart, and
    these tests write it the way the two code paths write it rather than inventing a third.
    """
    from agentnode_sdk.gateway import lifecycle
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService
    from tests.test_em3c_gateway import StandInBackend

    root = str(pathlib.Path(service.state.root))
    if cleanly:
        lifecycle.say_it_is_stopping(root)
    elif went == "crashed":
        lifecycle.say_it_crashed(root, "RuntimeError")
    elif went == "the host restarted":
        # The marker stays as it was and the boot identity moves, which is exactly what a
        # machine that rebooted leaves behind -- and the only thing that tells it from a
        # process ended while the machine kept running.
        where = pathlib.Path(root) / lifecycle.SERVING_NAME
        said = json.loads(where.read_text(encoding="utf-8"))
        said["boot"] = "a-different-boot-entirely"
        where.write_text(json.dumps(said), encoding="utf-8")
    service.close()
    state = GatewayState(root, version="test")
    return GatewayService(state, backend=StandInBackend()), state


# ------------------------------------------------------------------ I1


class TestExactlyOneClosingLinePerAcceptedRun:
    """I1. Not at least one and not at most one, and enforced rather than observed."""

    def test_a_second_line_for_a_run_is_refused(self, tmp_path):
        a_line(tmp_path, "run-once")
        with pytest.raises(meter.AlreadyRecorded) as caught:
            a_line(tmp_path, "run-once", finished_at=9999.0)
        assert caught.value.run_id == "run-once"
        assert caught.value.seq == 1
        assert len(lines_for(tmp_path, "run-once")) == 1

    def test_the_refusal_is_decided_by_the_file_and_not_by_the_caller(self, tmp_path):
        """A caller that is not there any more cannot remember anything.

        The whole difficulty is that the process which wrote the first line may be gone. So the
        second attempt here shares nothing with the first except the directory -- no object, no
        cached state -- which is the situation a restart is in.
        """
        a_line(tmp_path, "run-shared")
        import importlib

        fresh = importlib.reload(meter)
        try:
            with pytest.raises(fresh.AlreadyRecorded):
                fresh.record(tmp_path, run_id="run-shared", client_id="dev-1",
                             account_id="acct-" + "0" * 16, queued_at=1.0, started_at=2.0,
                             finished_at=3.0, cpu=1.0, memory_mb=512, wall_clock_s=60,
                             state="finished", outcome="succeeded", termination_reason="exited",
                             bytes_out=0, worker_topology="x", worker_id="w",
                             allowance_sha256="a" * 64, operator_policy_sha256="p" * 64,
                             operator_policy_version=1)
        finally:
            importlib.reload(meter)
        assert len(lines_for(tmp_path, "run-shared")) == 1

    def test_a_death_between_the_line_and_the_ledger_does_not_produce_two(self, gateway):
        """The window the old ordering left open, driven.

        Closing a run wrote its line and THEN moved the ledger entry, so a process that died
        between the two left a run with a line that was still selectable. The next start would
        write a second. Here the first half happens and the second does not, and then the
        directory is taken over.
        """
        claim(gateway, "run-half-closed", when=time.time() - 30.0,
              started=time.time() - 20.0)
        record = gateway.runs.get("run-half-closed")
        from agentnode_sdk.gateway.server import RunRecord

        record = RunRecord(run_id="run-half-closed", job_id="j", state="interrupted")
        record.finished_at = time.time()
        # The line, and deliberately NOT `note_state`.
        gateway._close_an_interrupted_run(
            record, gateway.ledger.run_entry("run-half-closed") or {}, reason=GATEWAY_LOST)
        assert len(lines_for(gateway.state.root, "run-half-closed")) == 1

        again, state = restart(gateway)
        try:
            assert len(lines_for(state.root, "run-half-closed")) == 1, (
                "the run was closed a second time by the gateway that took over")
        finally:
            again.close()
            state.close()

    def test_a_run_whose_line_failed_is_left_where_it_will_be_found(self, gateway, monkeypatch):
        """The other half of `exactly one`: nothing may make the ABSENCE of a line permanent.

        Moving the ledger entry is what takes a run out of the set a later start selects from.
        It used to happen whether or not the line had been written, so a line that failed was
        not written later -- it was never written, and the run was gone from the only place
        anything would have looked.

        A check on the ORDER of the two calls passes on that code, because the order was never
        what was wrong. This drives it instead.
        """
        now = time.time()
        claim(gateway, "line-failed", when=now - 30.0, started=now - 20.0)

        def refuse(*a, **kw):
            raise OSError("the disk said no")

        monkeypatch.setattr(meter, "record", refuse)
        again, state = restart(gateway)
        try:
            assert not lines_for(state.root, "line-failed")
            assert "line-failed" in again.ledger.unfinished_runs(), (
                "a run whose line could not be written was taken out of the set a later start "
                "selects from, so nothing will ever write it")
        finally:
            again.close()
            state.close()

    def test_and_a_run_that_did_get_its_line_is_taken_out_of_that_set(self, gateway):
        """The counterpart, so the test above cannot pass by nothing ever being moved."""
        now = time.time()
        claim(gateway, "line-written", when=now - 30.0, started=now - 20.0)
        again, state = restart(gateway)
        try:
            assert the_one_line_for(state.root, "line-written")
            assert "line-written" not in again.ledger.unfinished_runs()
        finally:
            again.close()
            state.close()

    def test_a_line_that_failed_is_tried_again_by_the_gateway_that_owes_it(self, gateway,
                                                                           monkeypatch):
        """`INTERRUPTED-AUDIT-RECORD-0001`, F1: leaving the run in the ledger means a LATER
        START can write the line, and nothing requires a later start. That is a possibility and
        not a bound.

        So the gateway that could not write it keeps the debt and tries again while it lives.
        """
        now = time.time()
        claim(gateway, "owed-a-line", when=now - 30.0, started=now - 20.0)
        broken = {"until": 1}
        real = meter.record

        def sometimes(*a, **kw):
            if broken["until"] > 0:
                broken["until"] -= 1
                raise OSError("the disk said no")
            return real(*a, **kw)

        monkeypatch.setattr(meter, "record", sometimes)
        again, state = restart(gateway)
        try:
            assert not lines_for(state.root, "owed-a-line")
            assert again.what_is_still_owed() == ["owed-a-line"], (
                "the gateway does not know it owes this line, so only a later start could "
                "ever write it")
            # The retry, driven rather than waited for: the same call its thread makes.
            assert again.pay_what_is_owed() == ["owed-a-line"]
            assert the_one_line_for(state.root, "owed-a-line")
            assert again.what_is_still_owed() == []
        finally:
            again.close()
            state.close()

    def test_and_the_debt_is_paid_on_the_way_out_as_well(self, gateway, monkeypatch):
        """A gateway going away is the last one holding these in memory."""
        now = time.time()
        claim(gateway, "owed-at-the-end", when=now - 30.0, started=now - 20.0)
        broken = {"until": 1}
        real = meter.record

        def sometimes(*a, **kw):
            if broken["until"] > 0:
                broken["until"] -= 1
                raise OSError("the disk said no")
            return real(*a, **kw)

        monkeypatch.setattr(meter, "record", sometimes)
        again, state = restart(gateway)
        try:
            assert not lines_for(state.root, "owed-at-the-end")
            again.close()
            assert the_one_line_for(state.root, "owed-at-the-end")
        finally:
            state.close()

    def test_the_retry_happens_in_this_process_and_not_at_a_later_start(self):
        """The bound the criterion asks for is one that does not need anybody to start
        anything."""
        from agentnode_sdk.gateway.server import GatewayService

        assert isinstance(GatewayService.OWED_RETRY_SECONDS, float)
        assert 0 < GatewayService.OWED_RETRY_SECONDS <= 60
        assert "OWED_RETRY_SECONDS" in inspect.getsource(
            GatewayService._keep_trying_to_pay_what_is_owed)

    def test_the_enforcement_is_inside_the_lock_that_serialises_appends(self):
        """Not in a caller, and not before the lock: two processes reaching it together must not
        both see an empty file."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(meter.record)))
        withs = [node for node in ast.walk(tree) if isinstance(node, ast.With)
                 and "_writing" in ast.unparse(node.items[0].context_expr)]
        assert withs, "the append is not under a lock any more"
        inside = ast.unparse(withs[0])
        assert "AlreadyRecorded" in inside, (
            "the check for an existing line is outside the lock that serialises appends, so two "
            "writers can both pass it")


# ------------------------------------------------------------------ I2


class TestARestartClosesWhatItInterrupted:
    """I2. Both kinds of job, once each, without anybody asking."""

    def test_a_running_job_and_a_waiting_job_both_get_their_line(self, gateway):
        now = time.time()
        claim(gateway, "was-running", when=now - 100.0, started=now - 60.0)
        claim(gateway, "only-waiting", when=now - 90.0)
        again, state = restart(gateway)
        try:
            ran = the_one_line_for(state.root, "was-running")
            waited = the_one_line_for(state.root, "only-waiting")
        finally:
            again.close()
            state.close()
        assert ran["state"] == "interrupted" and waited["state"] == "interrupted"
        assert ran["ever_started"] is True
        assert waited["ever_started"] is False

    def test_the_lines_are_there_before_anybody_asks(self, gateway):
        """A record written only when a client happens to poll is not a record of what happened.

        Nothing is asked here between the restart and the reading: no status call, no client.
        """
        now = time.time()
        claim(gateway, "nobody-asked", when=now - 50.0, started=now - 40.0)
        again, state = restart(gateway)
        try:
            assert lines_for(state.root, "nobody-asked"), (
                "the line appears only when somebody asks for the run")
        finally:
            again.close()
            state.close()

    def test_a_restart_tells_a_slot_without_a_container_from_one_with_one(self, gateway):
        """Two runs, both interrupted, both having held a slot, and only one of which ever had
        a container. A restart has to record them differently, because they are different.

        Here for I2 rather than for I7: what this is about is what a RESTART writes down, and a
        restart that cannot tell the two apart writes the same sentence about both.
        """
        now = time.time()
        claim(gateway, "slot-only", when=now - 30.0, started=now - 20.0)
        claim(gateway, "slot-and-container", when=now - 30.0, started=now - 20.0)
        gateway.ledger.note_a_sandbox_was_asked_for("slot-and-container")
        again, state = restart(gateway)
        try:
            without = the_one_line_for(state.root, "slot-only")
            with_one = the_one_line_for(state.root, "slot-and-container")
        finally:
            again.close()
            state.close()
        assert without["ever_started"] is True and with_one["ever_started"] is True
        assert without["sandbox"] != with_one["sandbox"], (
            "a restart records the same thing about a run that had a container and one that "
            "never asked for one: both say %r" % without["sandbox"])

    def test_and_the_run_is_not_started_again(self, gateway):
        """Closing it answers the client. It must not also re-run the job."""
        now = time.time()
        claim(gateway, "not-again", when=now - 20.0, started=now - 10.0)
        again, state = restart(gateway)
        try:
            record = again.runs.get("not-again")
            assert record is not None and record.state == "interrupted"
            assert "not been started again" in str(record.refusal)
        finally:
            again.close()
            state.close()


# ------------------------------------------------------------------ I3


class TestEveryWayOfBeingInterruptedClosesTheSameWay:
    """I3. A stop, a gateway that vanished, and a worker that could not be reached."""

    def test_a_gateway_that_was_stopped_says_so(self, gateway):
        now = time.time()
        claim(gateway, "stopped-me", when=now - 30.0, started=now - 20.0)
        again, state = restart(gateway, cleanly=True)
        try:
            line = the_one_line_for(state.root, "stopped-me")
        finally:
            again.close()
            state.close()
        assert line["termination_reason"] == GATEWAY_STOPPED

    def test_a_gateway_that_left_nothing_to_read_is_recorded_as_lost(self, gateway):
        """The case that stays unestablished, and the word that claims nothing further.

        This used to be every gateway that did not stop cleanly. It is now the narrower and
        truer set: the ones where there is nothing to read at all -- a directory served before
        this marker existed -- or where the boot identity says the machine restarted, which
        says the host went and not why.
        """
        now = time.time()
        claim(gateway, "nothing-to-read", when=now - 30.0, started=now - 20.0)
        (pathlib.Path(gateway.state.root) / lifecycle_module().SERVING_NAME).unlink(
            missing_ok=True)
        again, state = restart(gateway, cleanly=False)
        try:
            line = the_one_line_for(state.root, "nothing-to-read")
        finally:
            again.close()
            state.close()
        assert line["termination_reason"] == GATEWAY_LOST

    def test_a_gateway_that_was_killed_says_that_rather_than_only_lost(self, gateway):
        """`INTERRUPTED-AUDIT-RECORD-0001`, F2: the profile asks for the process-killed case to
        be tellable from the line alone, and the first version collapsed it into "lost".

        What makes it tellable is the boot identity. No shutdown, no recorded failure, and the
        SAME boot means the machine kept running while the process did not -- so something
        outside the process ended it.
        """
        now = time.time()
        claim(gateway, "killed-me", when=now - 30.0, started=now - 20.0)
        again, state = restart(gateway, went="killed")
        try:
            line = the_one_line_for(state.root, "killed-me")
        finally:
            again.close()
            state.close()
        assert line["termination_reason"] == GATEWAY_KILLED

    def test_a_gateway_that_failed_says_that_instead(self, gateway):
        """A crash runs code, which is the whole difference: there is a moment, however short,
        in which the process can say that it is ending badly."""
        now = time.time()
        claim(gateway, "crashed-me", when=now - 30.0, started=now - 20.0)
        again, state = restart(gateway, went="crashed")
        try:
            line = the_one_line_for(state.root, "crashed-me")
        finally:
            again.close()
            state.close()
        assert line["termination_reason"] == GATEWAY_CRASHED

    def test_and_a_machine_that_restarted_is_not_called_killed(self, gateway):
        """The one that stays unestablished, and it must not borrow a word from the others."""
        now = time.time()
        claim(gateway, "host-went", when=now - 30.0, started=now - 20.0)
        again, state = restart(gateway, went="the host restarted")
        try:
            line = the_one_line_for(state.root, "host-went")
        finally:
            again.close()
            state.close()
        assert line["termination_reason"] == GATEWAY_LOST

    def test_the_four_gateway_side_reasons_are_distinct(self):
        assert len({GATEWAY_STOPPED, GATEWAY_CRASHED, GATEWAY_KILLED, GATEWAY_LOST}) == 4
        for reason in (GATEWAY_STOPPED, GATEWAY_CRASHED, GATEWAY_KILLED, GATEWAY_LOST):
            assert reason in TERMINATION_REASONS
            assert reason in NOTHING_WAS_ESTABLISHED

    def test_a_stop_writes_the_lines_itself_rather_than_leaving_them(self, gateway):
        """The bound on `later`, where there is one.

        A gateway being stopped is the one interruption where it is still there to say what
        happened, so it says it then. Everything else has to wait for the next start, and that
        is a bound only in the sense that somebody restarts it.
        """
        now = time.time()
        claim(gateway, "closed-on-the-way-out", when=now - 10.0, started=now - 5.0)
        from agentnode_sdk.gateway.server import RunRecord

        record = RunRecord(run_id="closed-on-the-way-out", job_id="j", state="running")
        gateway.runs["closed-on-the-way-out"] = record
        closed = gateway.close_what_is_still_in_flight()
        assert closed == ["closed-on-the-way-out"]
        line = the_one_line_for(gateway.state.root, "closed-on-the-way-out")
        assert line["termination_reason"] == GATEWAY_STOPPED
        assert line["state"] == "interrupted"

    def test_and_a_stop_is_the_same_event_as_ctrl_c(self):
        """A service manager stops things with SIGTERM, whose default ends the process without
        running a single `finally` -- so the whole shutdown path did not run on `systemctl stop`.
        """
        from agentnode_sdk.cli import gateway_commands

        source = inspect.getsource(gateway_commands.cmd_start)
        assert "SIGTERM" in source, "nothing handles the signal a service manager actually sends"
        assert "close_what_is_still_in_flight" in source, (
            "a stop does not close the runs it is interrupting")

    def test_a_run_whose_line_could_not_be_written_is_written_at_the_next_start(self, gateway,
                                                                               monkeypatch):
        """`Later` in the cases that have no choice, driven rather than described."""
        now = time.time()
        claim(gateway, "written-later", when=now - 40.0, started=now - 30.0)

        broken = {"tried": 0}
        real = meter.record

        def refuse(*a, **kw):
            broken["tried"] += 1
            raise OSError("the disk said no")

        monkeypatch.setattr(meter, "record", refuse)
        again, state = restart(gateway)
        monkeypatch.setattr(meter, "record", real)
        try:
            assert broken["tried"] >= 1
            assert not lines_for(state.root, "written-later")
            once_more, state2 = restart(again)
            try:
                assert the_one_line_for(state2.root, "written-later")
            finally:
                once_more.close()
                state2.close()
        finally:
            state.close()


# ------------------------------------------------------------------ I4


class TestAJobThatNeverHeldASlotBillsZero:
    """I4. Zero, and zero because there is nothing to subtract from."""

    def test_a_job_that_never_started_is_billed_nothing(self, gateway):
        now = time.time()
        claim(gateway, "never-ran", when=now - 25.0)
        again, state = restart(gateway)
        try:
            line = the_one_line_for(state.root, "never-ran")
        finally:
            again.close()
            state.close()
        assert line["seconds"] == 0.0
        assert line["ever_started"] is False

    def test_and_it_is_zero_because_there_is_no_start_time_to_subtract_from(self):
        """The two ways of getting zero are not equally safe.

        A rule that sets the figure to zero afterwards can be got wrong by a later change that
        does not know about it. An absence of anything to subtract from cannot: the arithmetic
        has nowhere to get a number.
        """
        tree = ast.parse(textwrap.dedent(inspect.getsource(meter.record)))
        billed = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "seconds":
                        billed = ast.unparse(value)
        assert billed is not None, "the billed figure is not built where the line is built"
        assert "if float(started_at)" in billed and "else 0.0" in billed, (
            "the zero no longer comes from there being no start time: %s" % billed)

    def test_a_job_that_did_start_is_charged_for_the_slot_it_held(self, gateway):
        now = time.time()
        claim(gateway, "held-a-slot", when=now - 100.0, started=now - 40.0)
        again, state = restart(gateway)
        try:
            line = the_one_line_for(state.root, "held-a-slot")
        finally:
            again.close()
            state.close()
        assert 35.0 <= line["seconds"] <= 45.0, (
            "billed %s; it should be the slot it held" % line["seconds"])
        assert 55.0 <= line["waited_s"] <= 65.0, (
            "waited %s; it should be arrival to slot" % line["waited_s"])

    def test_and_both_figures_are_in_the_line_rather_than_inferred_from_it(self, gateway):
        now = time.time()
        claim(gateway, "both-figures", when=now - 30.0, started=now - 10.0)
        again, state = restart(gateway)
        try:
            line = the_one_line_for(state.root, "both-figures")
        finally:
            again.close()
            state.close()
        for field in ("seconds", "waited_s", "started_at", "queued_at", "finished_at",
                      "ever_started"):
            assert field in line, field


# ------------------------------------------------------------------ I5


class TestTheLineSaysWhy:
    """I5. As a value a machine branches on, and a sentence that agrees with it."""

    def test_the_interruptions_have_their_own_names(self):
        assert GATEWAY_STOPPED in TERMINATION_REASONS
        assert GATEWAY_LOST in TERMINATION_REASONS
        assert GATEWAY_STOPPED != GATEWAY_LOST

    def test_a_reader_of_one_line_can_tell_which(self, gateway):
        now = time.time()
        claim(gateway, "which-one", when=now - 20.0, started=now - 10.0)
        again, state = restart(gateway, cleanly=True)
        try:
            line = the_one_line_for(state.root, "which-one")
        finally:
            again.close()
            state.close()
        assert line["termination_reason"] in TERMINATION_REASONS
        assert line["outcome"] == UNVERIFIED_OUTCOME

    def test_all_four_gateway_side_reasons_come_out_of_real_restarts(self, gateway):
        """Four runs, four ways for a gateway to go, four different words in four lines.

        Here rather than beside each individual case because what I5 asks is whether a READER
        can tell which -- and that is a question about the set, not about any one of them. A
        vocabulary in which two of the four always come out the same answers this test and no
        other.
        """
        now = time.time()
        said = {}
        for run_id, went, cleanly in (("by-a-stop", "killed", True),
                                      ("by-a-failure", "crashed", False),
                                      ("from-outside", "killed", False),
                                      ("by-something-unknown", "the host restarted", False)):
            claim(gateway, run_id, when=now - 30.0, started=now - 20.0)
            again, state = restart(gateway, cleanly=cleanly, went=went)
            try:
                said[run_id] = the_one_line_for(state.root, run_id)["termination_reason"]
            finally:
                gateway = again
        try:
            assert len(set(said.values())) == 4, (
                "two of the four ways a gateway can go produce the same word, so a reader of "
                "one line cannot tell them apart: %r" % said)
            assert said["by-a-stop"] == GATEWAY_STOPPED
            assert said["by-a-failure"] == GATEWAY_CRASHED
            assert said["from-outside"] == GATEWAY_KILLED
            assert said["by-something-unknown"] == GATEWAY_LOST
        finally:
            gateway.close()

    @pytest.mark.skipif(not __import__("sys").platform.startswith("linux"),
                        reason="only Linux publishes a boot identity")
    def test_this_machine_really_publishes_a_boot_identity(self, monkeypatch):
        """The other half, where it can be had: the value the logic above depends on is real.

        `monkeypatch.undo` first, because the fixture that makes the logic portable is exactly
        what this test must not use.
        """
        monkeypatch.undo()
        from agentnode_sdk.gateway import lifecycle

        first = lifecycle.this_boot()
        assert first, "this machine publishes no boot identity, so a kill cannot be told apart"
        assert first == lifecycle.this_boot(), "it changed between two reads of the same boot"

    def test_none_of_them_is_read_as_the_job_having_failed(self):
        """A job the gateway lost may have done all of its work. Saying it failed is a claim
        about the customer's code that nothing supports."""
        for reason in (GATEWAY_STOPPED, GATEWAY_LOST):
            assert reason in NOTHING_WAS_ESTABLISHED
            assert outcome_of("interrupted", reason) == UNVERIFIED_OUTCOME

    @pytest.mark.parametrize("reason,ever_ran,expected", [
        (GATEWAY_STOPPED, True, "was stopped"),
        (GATEWAY_STOPPED, False, "was stopped"),
        (GATEWAY_CRASHED, True, "failed and ended"),
        (GATEWAY_CRASHED, False, "failed and ended"),
        (GATEWAY_KILLED, True, "ended from outside"),
        (GATEWAY_KILLED, False, "ended from outside"),
        (GATEWAY_LOST, True, "not known"),
        (GATEWAY_LOST, False, "not known"),
    ])
    def test_the_sentence_says_what_the_value_says(self, reason, ever_ran, expected):
        from agentnode_sdk.gateway.server import GatewayService

        said = GatewayService.what_to_tell_them_about_an_interruption(reason, ever_ran)
        assert expected in said
        assert "did not finish" in said
        if not ever_ran:
            assert "never started" in said and "nothing was charged" in said

    def test_and_the_sentence_no_longer_claims_a_restart_that_did_not_happen(self):
        """Every interrupted run used to be told "the gateway restarted", which for a job cut
        short by a planned stop was a specific claim and a wrong one."""
        from agentnode_sdk.gateway.server import GatewayService

        said = GatewayService.what_to_tell_them_about_an_interruption(GATEWAY_STOPPED, True)
        assert "restarted" not in said


# ------------------------------------------------------------------ I6


class TestTheLineCarriesTheWaitAndWhetherItStarted:
    """I6. Separate, unambiguous, and present in the case where nothing else is."""

    def test_never_started_is_not_the_same_as_a_missing_start_time(self, tmp_path):
        a_line(tmp_path, "did-start", started_at=1010.0)
        a_line(tmp_path, "did-not", started_at=0.0, queued_at=1000.0, finished_at=1010.0)
        assert the_one_line_for(tmp_path, "did-start")["ever_started"] is True
        assert the_one_line_for(tmp_path, "did-not")["ever_started"] is False

    def test_the_two_values_cannot_disagree(self, tmp_path):
        """Both are derived from the same number, so a line that bills something and claims not
        to have started is not one this meter can be asked to write."""
        a_line(tmp_path, "agreeing", started_at=0.0, queued_at=1000.0, finished_at=1010.0)
        line = the_one_line_for(tmp_path, "agreeing")
        assert line["ever_started"] is False and line["seconds"] == 0.0

    def test_the_wait_is_recorded_even_when_the_job_never_ran(self, gateway):
        """Exactly the case where the customer has nothing else to look at."""
        now = time.time()
        claim(gateway, "waited-only", when=now - 45.0)
        again, state = restart(gateway)
        try:
            line = the_one_line_for(state.root, "waited-only")
        finally:
            again.close()
            state.close()
        assert line["waited_s"] >= 40.0, line["waited_s"]
        assert line["seconds"] == 0.0

    def test_ever_started_is_a_boolean_and_not_a_number_to_interpret(self, tmp_path):
        a_line(tmp_path, "a-boolean")
        assert the_one_line_for(tmp_path, "a-boolean")["ever_started"] is True


# ------------------------------------------------------------------ I7


class TestTheLineCarriesWhatBecameOfTheSandbox:
    """I7. Four answers, and the third and fourth are not the first."""

    def test_there_are_four_and_they_are_distinct(self):
        assert len(set(SANDBOX_DISPOSITIONS)) == 4
        assert SANDBOX_NOT_ESTABLISHED != SANDBOX_CONFIRMED_GONE
        assert SANDBOX_NEVER_CREATED != SANDBOX_CONFIRMED_GONE

    @pytest.mark.parametrize("asked,verified,answered,expected", [
        (True, True, True, SANDBOX_CONFIRMED_GONE),
        (False, True, True, SANDBOX_NEVER_CREATED),
        (True, False, True, SANDBOX_STILL_THERE),
        (True, None, True, SANDBOX_NOT_ESTABLISHED),
        (True, True, False, SANDBOX_NOT_ESTABLISHED),
    ])
    def test_what_each_case_is_recorded_as(self, asked, verified, answered, expected):
        from agentnode_sdk.gateway.server import GatewayService

        assert GatewayService.what_became_of_the_sandbox(
            asked_for_a_sandbox=asked, cleanup_verified=verified,
            the_worker_answered=answered) == expected

    def test_a_run_that_held_a_slot_but_never_asked_for_a_container_is_not_a_cleanup(self,
                                                                                    gateway):
        """Interruption point 2, and the reason the answer is not keyed on `ever_started`.

        A slot is taken and the ledger says `running` BEFORE the worker is asked for anything.
        A run interrupted in that window held a slot and never had a container, and recording
        it as a confirmed cleanup would claim one had existed.
        """
        now = time.time()
        claim(gateway, "slot-but-no-container", when=now - 20.0, started=now - 10.0)
        again, state = restart(gateway)
        try:
            line = the_one_line_for(state.root, "slot-but-no-container")
        finally:
            again.close()
            state.close()
        assert line["ever_started"] is True, "it did hold a slot"
        assert line["sandbox"] == SANDBOX_NEVER_CREATED, (
            "a run that never asked for a container is recorded as %r" % line["sandbox"])

    def test_and_one_that_did_ask_is_recorded_as_a_cleanup(self, gateway):
        """The counterpart, so the test above cannot pass by nothing ever being a cleanup."""
        now = time.time()
        claim(gateway, "asked-for-one", when=now - 20.0, started=now - 10.0)
        gateway.ledger.note_a_sandbox_was_asked_for("asked-for-one")
        again, state = restart(gateway)
        try:
            line = the_one_line_for(state.root, "asked-for-one")
        finally:
            again.close()
            state.close()
        assert line["sandbox"] == SANDBOX_CONFIRMED_GONE, line["sandbox"]

    def test_a_gateway_that_could_not_ask_does_not_record_a_cleanup(self, gateway, monkeypatch):
        """The case a restart is most likely to be in: up before its worker."""
        now = time.time()
        claim(gateway, "could-not-ask", when=now - 20.0, started=now - 10.0)

        def cannot(*a, **kw):
            raise OSError("no worker here")

        again, state = restart(gateway)
        monkeypatch.setattr(type(again.worker), "stop", cannot, raising=False)
        try:
            line = the_one_line_for(state.root, "could-not-ask")
        finally:
            again.close()
            state.close()
        assert line["sandbox"] in SANDBOX_DISPOSITIONS

    def test_the_line_of_a_job_that_never_created_one_says_so(self, gateway):
        now = time.time()
        claim(gateway, "no-sandbox", when=now - 15.0)
        again, state = restart(gateway)
        try:
            line = the_one_line_for(state.root, "no-sandbox")
        finally:
            again.close()
            state.close()
        assert line["sandbox"] in (SANDBOX_NEVER_CREATED, SANDBOX_NOT_ESTABLISHED)
        assert line["sandbox"] != SANDBOX_CONFIRMED_GONE, (
            "a job that never held a slot is recorded as having had a sandbox tidied up")


# ------------------------------------------------------------------ I8


class TestNothingIsClosedTwice:
    """I8. Across several restarts, and across two gateways on one directory."""

    def test_a_second_restart_does_not_close_it_again(self, gateway):
        now = time.time()
        claim(gateway, "twice-over", when=now - 60.0, started=now - 30.0)
        again, state = restart(gateway)
        first = lines_for(state.root, "twice-over")
        once_more, state2 = restart(again)
        try:
            assert len(first) == 1
            assert len(lines_for(state2.root, "twice-over")) == 1
        finally:
            once_more.close()
            state2.close()
            state.close()

    def test_and_a_third(self, gateway):
        now = time.time()
        claim(gateway, "three-times", when=now - 60.0, started=now - 30.0)
        a, sa = restart(gateway)
        b, sb = restart(a)
        c, sc = restart(b)
        try:
            assert len(lines_for(sc.root, "three-times")) == 1
        finally:
            c.close()
            sa.close()
            sb.close()
            sc.close()

    def test_two_gateways_on_one_directory_cannot_both_close_a_run(self, gateway, tmp_path):
        """Both would select it. Only one line comes out, and the other is told why."""
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService, RunRecord
        from tests.test_em3c_gateway import StandInBackend

        now = time.time()
        claim(gateway, "shared-directory", when=now - 30.0, started=now - 20.0)
        root = str(pathlib.Path(gateway.state.root))
        entry = gateway.ledger.run_entry("shared-directory") or {}

        one = RunRecord(run_id="shared-directory", job_id="j", state="interrupted")
        one.finished_at = time.time()
        gateway._close_an_interrupted_run(one, entry, reason=GATEWAY_LOST)

        other_state = GatewayState(root, version="test")
        other = GatewayService(other_state, backend=StandInBackend(), recover=False)
        try:
            two = RunRecord(run_id="shared-directory", job_id="j", state="interrupted")
            two.finished_at = time.time()
            other._close_an_interrupted_run(two, entry, reason=GATEWAY_LOST)
            assert len(lines_for(root, "shared-directory")) == 1
        finally:
            other.close()
            other_state.close()

    def test_and_a_second_gateway_is_stopped_from_serving_at_all(self, tmp_path):
        """Defence in depth, and the one that was missing when it mattered.

        The meter makes the record survive two gateways on one directory. Nothing made the
        arrangement itself impossible, and on the closed alpha one had been in place for four
        days without anybody noticing.
        """
        from agentnode_sdk.gateway import lifecycle

        root = tmp_path / "shared"
        root.mkdir()
        holding = lifecycle.OnlyOneGateway(root).take()
        try:
            with pytest.raises(lifecycle.AnotherGatewayHasIt) as caught:
                lifecycle.OnlyOneGateway(root).take()
            assert "already serving" in str(caught.value)
        finally:
            holding.close()
        # And it is given back, so a gateway that stops does not lock the directory for good.
        lifecycle.OnlyOneGateway(root).take().close()

    def test_the_lock_holds_against_another_process(self, tmp_path):
        """A thread lock is not what is needed here.

        The one above runs both attempts in this interpreter, where a per-path lock inside the
        process answers first and the kernel is never consulted. Two gateways on one directory
        are two PROCESSES, so the lock that matters is the one the kernel owns -- and this takes
        it from a python that shares nothing with this one but the filesystem.
        """
        import subprocess
        import sys

        root = tmp_path / "across-processes"
        root.mkdir()
        holding = lifecycle_module().OnlyOneGateway(root).take()
        try:
            child = chr(10).join([
                "import sys",
                "from agentnode_sdk.gateway import lifecycle",
                "try:",
                "    lifecycle.OnlyOneGateway(sys.argv[1]).take()",
                "    print(chr(84)+chr(79)+chr(79)+chr(75))",
                "except lifecycle.AnotherGatewayHasIt:",
                "    print(chr(82)+chr(69)+chr(70)+chr(85)+chr(83)+chr(69)+chr(68))",
            ])
            done = subprocess.run(
                [sys.executable, "-c", child, str(root)],
                capture_output=True, text=True, timeout=120)
        finally:
            holding.close()
        assert "REFUSED" in (done.stdout or ""), (
            "another process took the lock while this one held it: %r / %r"
            % (done.stdout, done.stderr))

    def test_the_lock_is_taken_by_the_one_command_that_takes_over(self):
        """An operator command that could not look at a running gateway would be the defect
        this project already fixed once, from the other direction."""
        from agentnode_sdk.cli import gateway_commands

        assert "OnlyOneGateway" in inspect.getsource(gateway_commands.cmd_start)
        assert "OnlyOneGateway" not in inspect.getsource(gateway_commands._service)


# ------------------------------------------------------------------ I9


class TestNothingIsBilledTwice:
    """I9. Read out of the file, not computed from it."""

    def test_no_run_appears_twice_across_a_sequence_of_restarts(self, gateway):
        now = time.time()
        claim(gateway, "billed-a", when=now - 90.0, started=now - 60.0)
        claim(gateway, "billed-b", when=now - 80.0)
        a, sa = restart(gateway)
        b, sb = restart(a)
        c, sc = restart(b)
        try:
            seen = {}
            for line in lines_in(sc.root):
                seen.setdefault(line["run_id"], []).append(line)
            doubled = {k: len(v) for k, v in seen.items() if len(v) > 1}
            assert not doubled, "these runs have more than one line: %r" % doubled
        finally:
            c.close()
            sa.close()
            sb.close()
            sc.close()

    def test_the_window_quota_is_charged_once(self, gateway):
        """A counter charged for a run starting and never for it ending, or twice for one run,
        is the same defect in a different file."""
        now = time.time()
        claim(gateway, "quota-once", when=now - 50.0, started=now - 20.0)
        again, state = restart(gateway)
        once_more, state2 = restart(again)
        try:
            assert len(lines_for(state2.root, "quota-once")) == 1
        finally:
            once_more.close()
            state.close()
            state2.close()

    def test_the_figures_come_from_the_file(self, gateway):
        """Nothing in this test computes a bill; it reads the numbers the gateway wrote."""
        now = time.time()
        claim(gateway, "from-the-file", when=now - 40.0, started=now - 20.0)
        again, state = restart(gateway)
        try:
            raw = (pathlib.Path(state.root) / meter.METER_NAME).read_text(encoding="utf-8")
        finally:
            again.close()
            state.close()
        mine = [json.loads(x) for x in raw.splitlines()
                if x.strip() and json.loads(x).get("run_id") == "from-the-file"]
        assert len(mine) == 1
        assert mine[0]["seconds"] > 0.0


# ------------------------------------------------------------------ I10


class TestTheChainStillVerifies:
    """I10. With the new lines and the new fields in it."""

    def test_the_log_verifies_end_to_end_after_a_restart(self, gateway):
        now = time.time()
        claim(gateway, "chained-1", when=now - 40.0, started=now - 20.0)
        claim(gateway, "chained-2", when=now - 30.0)
        again, state = restart(gateway)
        try:
            report = meter.verify(state.root)
        finally:
            again.close()
            state.close()
        assert report["ok"], report
        assert report["lines"] == 2 and report["unchecked"] == 0, report

    def test_the_new_fields_are_inside_the_signature(self, tmp_path):
        """Beside it, they could be changed without the signature noticing, which is the same as
        not being in the record at all."""
        assert "ever_started" in meter.FIELDS
        assert "sandbox" in meter.FIELDS
        assert "ever_started" not in meter.SEALED and "sandbox" not in meter.SEALED

        a_line(tmp_path, "signed-fields")
        where = pathlib.Path(tmp_path) / meter.METER_NAME
        line = json.loads(where.read_text(encoding="utf-8").splitlines()[0])
        line["ever_started"] = not line["ever_started"]
        where.write_text(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n",
                         encoding="utf-8")
        assert not meter.verify(tmp_path)["ok"], (
            "a line whose `ever_started` was flipped still verifies, so the field is not signed")

    def test_the_shape_was_changed_where_the_shape_is_declared(self):
        """A field added at a call site is a field the module does not know it has."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(meter)))
        declared = [node for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    and any(getattr(t, "id", "") == "FIELDS" for t in node.targets)]
        assert declared, "FIELDS is no longer declared in this module"
        assert "ever_started" in ast.unparse(declared[0])
        assert "sandbox" in ast.unparse(declared[0])

    def test_a_line_may_not_carry_a_field_nobody_declared(self, tmp_path):
        a_line(tmp_path, "declared-only")
        line = the_one_line_for(tmp_path, "declared-only")
        assert set(line) - set(meter.SEALED) == set(meter.FIELDS)
