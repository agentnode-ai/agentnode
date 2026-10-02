"""One run, one story: what the signed log, the ledger, the quota and the client all say.

`state-consistency-r1`. The observation this file exists for was made on two real machines, twice.
A job was running on the worker; the control-plane HOST was rebooted; the gateway's clean shutdown
closed the run. Afterwards, for the same run id:

    the signed use log   state=interrupted, outcome=unverified, termination_reason=gateway_stopped
    the ledger           state=finished
    the quota            44.093 s and 60.237 s, against the signed 33.422 s and 49.617 s

and -- established from the code while deciding the repair, and asserted here for the first time --
a client asking the gateway about that run would have been told `finished`, whose outcome is
`succeeded`. `close_what_is_still_in_flight` writes the signed line and the ledger word but never
moves the in-memory record, so the handler's tail is free to make a legal `running -> finished`
move afterwards.

So the defect is not one wrong word in one file. Four things answer for a run, and three of them
can be made to contradict the one that is signed.

## What is real here and what is not

The gateway is real, the shutdown path is real, the handler is real, the meter and its chain are
real, the ledger is real, and the client is the real client library. What is replaced is the
SANDBOX -- a backend that blocks until this file lets it go, so the run is genuinely running when
the stop arrives and the window between "the stop closed it" and "its own handler returned" is a
place a test can stand rather than a race it has to win. That replacement cannot hide the failure:
the failure lives entirely between those two events.

The pattern, the fixture and `ABackendThatWaits` are taken from `test_cancel_consistency.py`, which
established them for the same kind of window.
"""
from __future__ import annotations

import json
import textwrap
import sys
import os
import pathlib
import threading
import time

import pytest

from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway import meter
from agentnode_sdk.gateway.allowance import USE_NAME
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.protocol import GATEWAY_STOPPED, TERMINAL_STATES, is_terminal, outcome_of
from agentnode_sdk.gateway.server import GatewayService, make_server

from tests import consent
from tests.test_cancel_consistency import ABackendThatWaits
from tests.test_em3c_gateway import _granted, _paired, _store_measurement


# ------------------------------------------------------------------ reading the four records


def lines_for(root, run_id: str) -> list:
    """EVERY signed line for this run, not the first. A test about `exactly one` may not use a
    helper that stops looking once it has found one."""
    where = pathlib.Path(root) / meter.METER_NAME
    if not where.is_file():
        return []
    said = [json.loads(x) for x in where.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [x for x in said if str(x.get("run_id") or "") == run_id
            and not meter.is_a_tombstone(x)]


def the_one_line_for(root, run_id: str) -> dict:
    got = lines_for(root, run_id)
    assert len(got) == 1, "expected exactly one signed line for %s, found %d" % (run_id, len(got))
    return got[0]


def quota_figures_for(root, run_id: str) -> dict:
    """Every scope's seconds for this run, by scope. A figure per scope, because the quota is
    charged to the client AND to the account and either could be the one that is wrong."""
    where = pathlib.Path(root) / USE_NAME
    if not where.is_file():
        return {}
    try:
        body = json.loads(where.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    out = {}
    for scope, entries in (body.items() if isinstance(body, dict) else []):
        if not isinstance(entries, list):
            continue
        for e in entries:
            if isinstance(e, dict) and str(e.get("run_id") or "") == run_id:
                out[str(scope)] = e.get("seconds")
    return out


def what_the_ledger_says_about_the_outcome(entry: dict) -> dict:
    """Every string in the ledger entry that is one of the protocol's terminal words, by key.

    Deliberately NOT "read the field called state". The point of the repair is that the outcome
    may live somewhere else, so a test that names one field would pass or fail for a reason about
    the shape rather than about the contradiction. This asks the only question that matters: does
    anything in this entry claim an ending, and is it the same ending the signed line claims.
    """
    return {k: v for k, v in entry.items()
            if isinstance(v, str) and v in TERMINAL_STATES}



def release_when_both_are_ready(started, barrier, tmp_path, seconds=60.0):
    """Wait until every child says it is ready, then release them all at once.

    A barrier written as soon as the children are SPAWNED releases them before either has finished
    starting, and process startup then serialises them. That is not a contention, and it is how one of
    these tests came to pass with a defect in place: the second child was constructed after the first
    had already written, so its snapshot was fresh and the missing reload cost nothing. Measured, in
    `raw/S19-03`.

    So each child creates `<barrier>.ready.<pid>` once it holds its object, and this waits for one per
    child before writing the barrier itself. If a child dies before announcing, this says so rather
    than timing out silently -- a test that quietly measured one process is the thing being avoided.
    """
    import time

    want = len(started)
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        ready = list(tmp_path.glob(barrier.name + ".ready.*"))
        if len(ready) >= want:
            barrier.write_text("go", encoding="utf-8")
            return [p.name for p in ready]
        dead = [p for p in started if p.poll() is not None]
        if dead:
            out, err = dead[0].communicate()
            raise AssertionError(
                "a child ended before announcing it was ready, so there was never a contention to "
                "measure. exit=%r\nstdout:\n%s\nstderr:\n%s"
                % (dead[0].returncode, out[-800:], err[-800:]))
        time.sleep(0.002)
    raise AssertionError(
        "only %d of %d children announced they were ready within %.0fs, so releasing them would "
        "measure a sequence rather than a contention"
        % (len(list(tmp_path.glob(barrier.name + ".ready.*"))), want, seconds))

# ------------------------------------------------------------------ the stand


@pytest.fixture()
def waiting_gateway(tmp_path):
    """A real gateway whose sandbox blocks. Everything except the sandbox is the real thing."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        state = GatewayState(td, version="test")
        backend = ABackendThatWaits()
        service = GatewayService(state, backend=backend)
        _store_measurement(service)
        service.CONTAINER_APPEAR_SECONDS = 0.5
        server = make_server(service, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:%d" % server.server_address[1]
        try:
            yield base, state, service, backend
        finally:
            backend.let_go.set()
            server.shutdown()
            thread.join(timeout=10)
            for _ in range(300):
                if all(is_terminal(r.state) for r in list(service.runs.values())):
                    break
                time.sleep(0.05)


def a_running_job(base, state, service, backend, run_id):
    """Submit a job and wait until it is really inside the sandbox. Returns the connection."""
    conn = _paired(base, state)
    consent.submit(conn, b"print('x')", granted=_granted(service), run_id=run_id)
    assert backend.started.wait(timeout=10), "the job never reached the sandbox"
    return conn


def until_it_has_ended(service, run_id, seconds=30.0):
    """Wait for the handler's own thread to finish with this run, bounded."""
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        record = service.runs.get(run_id)
        if record is not None and is_terminal(getattr(record, "state", "")):
            return record
        time.sleep(0.02)
    raise AssertionError("the run never reached a terminal state within %.0fs" % seconds)


# ------------------------------------------------------------------ the reproduction


class TestAStopAndTheHandlerDoNotTellTwoStories:
    """The sequence reproduced twice on two real machines, in one process and deterministic.

    Each assertion below is a separate statement about the same run, and each one of them fails on
    the parent commit. They are deliberately not collapsed into one: a single assertion would say
    only that something is wrong, and which of the four records is lying is the whole question.
    """

    def test_the_stop_writes_the_signed_line_while_the_handler_is_still_inside(self,
                                                                              waiting_gateway):
        """The premise: the window is real and the stop is what closes the run in it.

        ## A correction, kept visible

        The first version of this test ended with `assert record.state == "running"`, and on the
        parent commit it passed. It was written to pin down WHY the handler's later move was legal:
        the stop wrote the files and left the in-memory record behind. That is the defect, not the
        premise, and asserting it would have frozen the defect into the suite -- the fix makes the
        stop move the record too, so the old assertion went red for the right reason. The run where
        it did is `raw/S3-01-green-after-the-change.log`.

        What the premise actually needs is that the stop closes the run WHILE the handler is still
        inside the sandbox, which is what makes everything below a race rather than a sequence. That
        is what it asserts now, and the record's state is checked against the log instead of against
        a literal.
        """
        base, state, service, backend = waiting_gateway
        a_running_job(base, state, service, backend, "stopped-then-returned")

        closed = service.close_what_is_still_in_flight()
        assert closed == ["stopped-then-returned"], (
            "the stop did not close the run it was holding, so the race below never happens")
        line = the_one_line_for(state.root, "stopped-then-returned")
        assert line["state"] == "interrupted"
        assert line["termination_reason"] == GATEWAY_STOPPED
        assert line["outcome"] == "unverified"
        # THE HANDLER HAS NOT RETURNED. Nothing has let the sandbox go, so the run's own thread is
        # still inside it and everything the other tests do happens after this point -- which is
        # the definition of the window this file is about.
        assert not backend.let_go.is_set(), (
            "the sandbox was already released, so the handler may have finished before the stop "
            "and these tests would be measuring a sequence rather than a race")
        # And the record already says what the log says, rather than still saying `running`.
        assert service.runs["stopped-then-returned"].state == line["state"], (
            "the stop wrote a signed %r line and left the record at %r. A terminal state that has "
            "not been published is a terminal state the next writer can still move."
            % (line["state"], service.runs["stopped-then-returned"].state))

    def test_the_ledger_does_not_contradict_the_signed_line(self, waiting_gateway):
        base, state, service, backend = waiting_gateway
        a_running_job(base, state, service, backend, "stopped-then-returned")
        assert service.close_what_is_still_in_flight() == ["stopped-then-returned"]
        line = the_one_line_for(state.root, "stopped-then-returned")

        backend.let_go.set()
        until_it_has_ended(service, "stopped-then-returned")

        entry = service.ledger.run_entry("stopped-then-returned") or {}
        said = what_the_ledger_says_about_the_outcome(entry)
        wrong = {k: v for k, v in said.items() if v != line["state"]}
        assert not wrong, (
            "the signed line says %r and the ledger says %r. Two durable records, one run, two "
            "different endings -- and the ledger is the one a later start reads to decide whether "
            "anything still needs recovering." % (line["state"], wrong))

    def test_there_is_still_exactly_one_signed_line(self, waiting_gateway):
        """Green on the parent, and named so that is on the record rather than assumed.

        The meter already refuses a second line for a run it has. This asserts it over the exact
        race, because `exactly one` is the property every other record is reconciled against: if
        it did not hold, there would be no authority to reconcile to.
        """
        base, state, service, backend = waiting_gateway
        a_running_job(base, state, service, backend, "stopped-then-returned")
        assert service.close_what_is_still_in_flight() == ["stopped-then-returned"]
        backend.let_go.set()
        until_it_has_ended(service, "stopped-then-returned")
        assert len(lines_for(state.root, "stopped-then-returned")) == 1

    def test_the_quota_is_charged_what_the_signed_line_says(self, waiting_gateway):
        base, state, service, backend = waiting_gateway
        a_running_job(base, state, service, backend, "stopped-then-returned")
        assert service.close_what_is_still_in_flight() == ["stopped-then-returned"]
        line = the_one_line_for(state.root, "stopped-then-returned")

        backend.let_go.set()
        until_it_has_ended(service, "stopped-then-returned")

        charged = quota_figures_for(state.root, "stopped-then-returned")
        assert charged, (
            "nothing was charged to any scope for a run that held a slot and ran. On the stand the "
            "handler charged it; a run closed only by a restart is charged by nobody at all.")
        for scope, seconds in charged.items():
            assert seconds == pytest.approx(float(line["seconds"]), abs=0.001), (
                "scope %s was charged %r seconds while the signed line this customer would be "
                "handed says %r. The two numbers come from two different clocks read by two "
                "different code paths." % (scope, seconds, line["seconds"]))

    def test_the_client_is_not_told_it_succeeded(self, waiting_gateway):
        """The face of this defect a customer would actually see.

        `close_what_is_still_in_flight` never moves the in-memory record, so after it has written
        a signed `interrupted` line the handler's tail is free to move `running -> finished`, and
        `outcome_of("finished", "exited", 0)` is `succeeded`. Honest files are not enough if the
        one place a person looks says the job worked.
        """
        base, state, service, backend = waiting_gateway
        conn = a_running_job(base, state, service, backend, "stopped-then-returned")
        assert service.close_what_is_still_in_flight() == ["stopped-then-returned"]
        line = the_one_line_for(state.root, "stopped-then-returned")

        backend.let_go.set()
        until_it_has_ended(service, "stopped-then-returned")

        answered = gc.status_of(conn, "stopped-then-returned")
        assert answered["state"] == line["state"], (
            "the signed record says %r and the client is told %r (outcome %r). The run a customer "
            "asks about must be the run the signed log describes."
            % (line["state"], answered["state"],
               outcome_of(str(answered.get("state") or ""),
                          str(answered.get("termination_reason") or ""),
                          answered.get("exit_code"))))


# ------------------------------------------------------------------ a plain gateway, no HTTP


@pytest.fixture()
def gateway(tmp_path):
    """A real gateway whose sandbox is a stand-in, on a directory of its own. No server."""
    from tests.test_em3c_gateway import StandInBackend

    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        for closing in (service.close, state.close):
            try:
                closing()
            except Exception:                                  # noqa: BLE001
                pass


def restarted(service):
    """A SECOND GatewayService on the same directory, which is what a restart is."""
    from tests.test_em3c_gateway import StandInBackend

    root = str(pathlib.Path(service.state.root))
    service.close()
    state = GatewayState(root, version="test")
    return GatewayService(state, backend=StandInBackend()), state


ADMITTED = {"cpu": 1.0, "memory_mb": 512, "wall_clock_s": 60,
            "allowance_sha256": "a" * 64, "operator_policy_sha256": "p" * 64,
            "operator_policy_version": 1, "worker_topology": "single-host-development"}

AN_ACCOUNT = "acct-" + "1" * 16


def claimed(service, run_id, *, when=None, started=None, client="dev-1", account=AN_ACCOUNT):
    """A run the ledger has accepted, and optionally one it saw take a slot."""
    when = time.time() - 10.0 if when is None else when
    assert service.ledger.claim(run_id, "nonce-" + run_id, "s" * 64, client,
                                now=when, owner_account_id=account, admitted=ADMITTED)
    if started is not None:
        service.ledger.note_lifecycle(run_id, "running", at=started)


def a_signed_line(root, run_id, **changes):
    """One real signed, chained line. The authority every other record is reconciled against."""
    said = dict(run_id=run_id, client_id="dev-1", account_id=AN_ACCOUNT,
                queued_at=1000.0, started_at=1010.0, finished_at=1021.0,
                cpu=1.0, memory_mb=512, wall_clock_s=60,
                state="finished", outcome="succeeded", termination_reason="exited",
                bytes_out=0, worker_topology="single-host-development", worker_id="w",
                allowance_sha256="a" * 64, operator_policy_sha256="p" * 64,
                operator_policy_version=1)
    said.update(changes)
    return meter.record(root, **said)


def the_quota(root):
    from agentnode_sdk.gateway.allowance import Use

    return Use(pathlib.Path(root) / USE_NAME)


def the_quota_file(root):
    """The quota document itself, for a test that needs its digest or has to edit it."""
    return pathlib.Path(root) / USE_NAME


def edit_the_line(root, run_id: str, **changes):
    """Change a field of the one signed line on disk.

    Needed because the meter will not WRITE some of the shapes these tests are about: `meter.record`
    does `float(queued_at or started_at)`, so a zero is replaced by the start and an unreadable value
    raises. Those shapes are unreachable through the writer -- and reachable through anything else that
    can edit a file, which is why the reconciliation still has to answer for them. Saying they are
    reachable through the product would be a claim this cannot support, so the tests say where they
    come from instead.
    """
    where = pathlib.Path(root) / meter.METER_NAME
    out = []
    for raw in where.read_text(encoding="utf-8").splitlines():
        if run_id in raw:
            said = json.loads(raw)
            said.update(changes)
            raw = json.dumps(said, sort_keys=True)
        out.append(raw)
    where.write_text(chr(10).join(out) + chr(10), encoding="utf-8")


def digest_of(path) -> str:
    import hashlib

    p = pathlib.Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


# ------------------------------------------------------------- the same race, the other way round


class TestTheHandlerFinishesFirstAndNothingRewritesIt:
    """The half nobody had hit yet.

    `_close_an_interrupted_run` returned `True` when it found a line already there, and its caller
    then wrote the literal word `interrupted` into the ledger -- over an entry whose signed line may
    say `finished`. The same defect, pointing the other way: the durable word coming from the code
    path instead of from the record that owns it.
    """

    def test_a_close_that_finds_a_line_takes_that_lines_word(self, gateway):
        from agentnode_sdk.gateway.protocol import GATEWAY_STOPPED
        from agentnode_sdk.gateway.server import RunRecord

        claimed(gateway, "already-finished", started=time.time() - 5.0)
        a_signed_line(gateway.state.root, "already-finished", state="finished")

        record = RunRecord(run_id="already-finished", job_id="j", state="running")
        entry = gateway.ledger.run_entry("already-finished") or {}
        settled = gateway._close_an_interrupted_run(record, entry, reason=GATEWAY_STOPPED)

        assert settled == "finished", (
            "the close reported %r for a run whose one signed line says 'finished'" % settled)
        assert gateway.ledger.settled_as("already-finished") == "finished"
        assert len(lines_for(gateway.state.root, "already-finished")) == 1

    def test_a_stop_steps_over_a_run_that_has_already_ended(self, waiting_gateway):
        base, state, service, backend = waiting_gateway
        a_running_job(base, state, service, backend, "ended-first")
        backend.let_go.set()
        until_it_has_ended(service, "ended-first")
        line = the_one_line_for(state.root, "ended-first")
        before = service.ledger.settled_as("ended-first")

        assert service.close_what_is_still_in_flight() == []
        assert service.ledger.settled_as("ended-first") == before == line["state"]
        assert len(lines_for(state.root, "ended-first")) == 1


# ------------------------------------------------------------------ dying between two files


class TestACrashBetweenTwoFiles:
    """Two files cannot be written atomically, so the repair is reconciliation and not ordering.

    This is the reviewer's correction to the first design, driven rather than described. Each test
    leaves the state a process that died in one particular gap would have left, and asks what the
    next start makes of it.
    """

    def test_a_line_with_no_ledger_word_is_settled_at_the_next_start(self, gateway):
        """Dead between the signed line and the ledger."""
        claimed(gateway, "line-then-nothing", started=time.time() - 5.0)
        a_signed_line(gateway.state.root, "line-then-nothing", state="interrupted",
                      outcome="unverified", termination_reason="gateway_stopped")
        assert gateway.ledger.settled_as("line-then-nothing") == "", "premise: nothing settled yet"

        again, state = restarted(gateway)
        try:
            assert again.ledger.settled_as("line-then-nothing") == "interrupted"
            assert len(lines_for(state.root, "line-then-nothing")) == 1, (
                "the start wrote a second line instead of reading the one that was there")
        finally:
            again.close()
            state.close()

    def test_a_line_with_a_stale_quota_is_repaired_at_the_next_start(self, gateway):
        """Dead between the ledger and the quota -- or, as it happened on the stand, charged by a
        path that did not write the line."""
        claimed(gateway, "stale-quota", started=time.time() - 5.0)
        a_signed_line(gateway.state.root, "stale-quota", state="interrupted",
                      outcome="unverified", started_at=1010.0, finished_at=1021.0)
        held = the_quota(gateway.state.root)
        keys = ["dev-1", AN_ACCOUNT]
        held.note_every(keys, "stale-quota")
        held.finished_every(keys, "stale-quota", 999.0)
        assert all(v == 999.0 for v in
                   held.what_a_run_was_charged(keys, "stale-quota").values()), "premise"

        again, state = restarted(gateway)
        try:
            line = the_one_line_for(state.root, "stale-quota")
            charged = the_quota(state.root).what_a_run_was_charged(keys, "stale-quota")
            for scope, seconds in charged.items():
                assert seconds == pytest.approx(float(line["seconds"]), abs=0.001), (
                    "scope %s still holds %r against a signed %r"
                    % (scope, seconds, line["seconds"]))
            entry = again.ledger.run_entry("stale-quota") or {}
            assert entry.get("quota_repairs"), (
                "the figure was corrected and nothing says so. A repair nobody can find is a "
                "repair nobody can audit.")
            assert len(lines_for(state.root, "stale-quota")) == 1, (
                "the repair wrote a second usage line")
        finally:
            again.close()
            state.close()

    def test_a_run_with_no_line_at_all_is_still_closed_by_the_next_start(self, gateway):
        """The case reconciliation must NOT swallow: nothing signed, so a line is owed."""
        claimed(gateway, "no-line-yet", started=time.time() - 5.0)
        assert not lines_for(gateway.state.root, "no-line-yet"), "premise"

        again, state = restarted(gateway)
        try:
            line = the_one_line_for(state.root, "no-line-yet")
            assert line["state"] == "interrupted"
            assert again.ledger.settled_as("no-line-yet") == "interrupted"
        finally:
            again.close()
            state.close()


# --------------------------------------------------- what may change, and what may not


class TestWhatIsMonotone:
    """Every persisted fact moves one way, and the decision is made against the file."""

    def test_cleanup_true_is_absorbing(self, gateway):
        claimed(gateway, "confirmed-gone")
        assert gateway.ledger.note_cleanup("confirmed-gone", True) is True
        # What a later start hands in when it could not reach the worker.
        assert gateway.ledger.note_cleanup("confirmed-gone", None) is True, (
            "an established cleanup was replaced by 'nobody could ask'")
        assert gateway.ledger.note_cleanup("confirmed-gone", False) is True
        assert (gateway.ledger.run_entry("confirmed-gone") or {}).get("cleanup") is True, (
            "an established 'confirmed gone' was replaced by 'nobody could ask'")

    def test_unconfirmed_cleanup_may_still_move_between_its_two_unknowns(self, gateway):
        claimed(gateway, "still-unknown")
        assert gateway.ledger.note_cleanup("still-unknown", None) is None
        assert gateway.ledger.note_cleanup("still-unknown", False) is False
        assert gateway.ledger.note_cleanup("still-unknown", None) is None
        assert gateway.ledger.note_cleanup("still-unknown", True) is True

    def test_the_outcome_is_written_once_and_a_second_word_is_kept_not_applied(self, gateway):
        claimed(gateway, "settled-once")
        assert gateway.ledger.note_it_settled("settled-once", "interrupted") == (
            "interrupted", True)
        # The same word again, which is what two paths both reading one line will do.
        assert gateway.ledger.note_it_settled("settled-once", "interrupted") == (
            "interrupted", False), (
            "a second write of the same word was applied again instead of being a no-op")
        # A different word, which means somebody read something else.
        assert gateway.ledger.note_it_settled("settled-once", "finished") == ("interrupted", False)
        entry = gateway.ledger.run_entry("settled-once") or {}
        assert entry.get("settled_as") == "interrupted"
        assert entry.get("settled_conflicts"), (
            "a contradicting word was refused and left no trace, so nobody can find out it "
            "happened")
        assert entry["settled_conflicts"][-1]["offered"] == "finished"

    def test_settling_a_run_also_ends_its_lifecycle(self, gateway):
        claimed(gateway, "closes-too", started=time.time() - 5.0)
        gateway.ledger.note_it_settled("closes-too", "finished")
        assert (gateway.ledger.run_entry("closes-too") or {}).get("state") == "closed"
        # And nothing moves it back out.
        assert gateway.ledger.note_lifecycle("closes-too", "running") == "closed"

    def test_a_queued_run_may_close_without_ever_running(self, gateway):
        """`accepted -> closed`, which the first design would have refused.

        A run can be refused, cancelled or interrupted while it is still in the queue. A lifecycle
        insisting on `running` in between would have had to claim an execution that never happened
        in order to record the ending that did.
        """
        claimed(gateway, "never-left-the-queue")
        assert (gateway.ledger.run_entry("never-left-the-queue") or {}).get("state") == "accepted"
        assert gateway.ledger.note_lifecycle("never-left-the-queue", "closed") == "closed"

    def test_the_lifecycle_does_not_go_backwards(self, gateway):
        claimed(gateway, "forward-only", started=time.time() - 5.0)
        assert gateway.ledger.note_lifecycle("forward-only", "running") == "running"
        assert gateway.ledger.note_lifecycle("forward-only", "accepted") == "running", (
            "a lifecycle position moved backwards")

    def test_the_decision_is_made_against_the_file_not_one_objects_memory(self, gateway):
        """Two `Ledger` objects on one file, which is what defeats an in-memory check.

        LIMITATION, stated rather than implied: this is two objects in one process, not two
        processes. It exercises the reload-inside-the-lock that makes the comparison be against the
        file; it does not measure two gateways contending for the lock itself. That measurement is
        named as owed in `DECISION-0001-ACCEPTED.md`.
        """
        from agentnode_sdk.gateway.ledger import Ledger

        claimed(gateway, "two-readers")
        other = Ledger(gateway.ledger.path)
        # `other` loaded before anything was settled, so its own memory says nothing is set.
        assert gateway.ledger.note_it_settled("two-readers", "interrupted")[1] is True
        assert other.note_it_settled("two-readers", "finished") == ("interrupted", False), (
            "the second object decided from its own stale copy instead of from the file")


# ------------------------------------------------------------------ the sweep


class TestTheSweepSelectsOnTheFact:
    """Visible until cleanup is established, whatever any word says."""

    def test_an_unconfirmed_cleanup_stays_selectable_once_the_run_has_ended(self, gateway):
        claimed(gateway, "ended-but-unswept", started=time.time() - 5.0)
        gateway.ledger.note_it_settled("ended-but-unswept", "finished")
        gateway.ledger.note_cleanup("ended-but-unswept", None)
        assert "ended-but-unswept" in gateway.ledger.runs_left_unswept(), (
            "a run that ended with its cleanup unconfirmed fell out of the only set that would "
            "ever have asked about its container again")

    def test_a_confirmed_cleanup_is_not_selectable(self, gateway):
        claimed(gateway, "ended-and-swept", started=time.time() - 5.0)
        gateway.ledger.note_it_settled("ended-and-swept", "finished")
        gateway.ledger.note_cleanup("ended-and-swept", True)
        assert "ended-and-swept" not in gateway.ledger.runs_left_unswept()

    def test_selection_is_not_gated_on_whether_a_sandbox_was_ever_asked_for(self, gateway):
        """Both `asked_for_a_sandbox` and `started_at` are best-effort writes, and this module's own
        comments say a run can have a live container and still read as never having started.

        Checked against `runs_needing_attention`, which is THE predicate. `runs_left_unswept` and
        `unfinished_runs` are its two disjoint halves -- a start does different work for each -- so
        asking one of them which half a run falls in would be testing the division of labour rather
        than the responsibility.
        """
        claimed(gateway, "looks-like-it-never-started")
        entry = gateway.ledger.run_entry("looks-like-it-never-started") or {}
        assert not entry.get("asked_for_a_sandbox") and not entry.get("started_at"), "premise"
        assert "looks-like-it-never-started" in gateway.ledger.runs_needing_attention()

    def test_the_two_halves_are_disjoint_and_cover_the_predicate(self, gateway):
        """Disjoint because a start works through one of them against a budget: overlapping sets
        spend it twice on one run and can leave the second half of recovery with none."""
        claimed(gateway, "unsettled-and-unswept", started=time.time() - 5.0)
        claimed(gateway, "settled-but-unswept", started=time.time() - 5.0)
        gateway.ledger.note_it_settled("settled-but-unswept", "finished")
        claimed(gateway, "settled-and-swept", started=time.time() - 5.0)
        gateway.ledger.note_it_settled("settled-and-swept", "finished")
        gateway.ledger.note_cleanup("settled-and-swept", True)

        unfinished = set(gateway.ledger.unfinished_runs())
        unswept = set(gateway.ledger.runs_left_unswept())
        needed = set(gateway.ledger.runs_needing_attention())

        assert not (unfinished & unswept), "the two halves overlap: %r" % (unfinished & unswept,)
        assert unfinished | unswept == needed, (
            "the halves do not add up to the predicate: %r against %r"
            % (unfinished | unswept, needed))
        assert "unsettled-and-unswept" in unfinished
        assert "settled-but-unswept" in unswept
        assert "settled-and-swept" not in needed


# ------------------------------------------------------------------ doing it twice


class TestRepeatingItChangesNothing:
    """Idempotent, and byte-stable -- the stronger of the two, and the one that shows a repair is
    finished rather than recurring."""

    def test_a_second_reconciliation_writes_nothing_at_all(self, gateway):
        claimed(gateway, "twice-over", started=time.time() - 5.0)
        a_signed_line(gateway.state.root, "twice-over", state="interrupted", outcome="unverified",
                      queued_at=time.time() - 30.0)
        held = the_quota(gateway.state.root)
        keys = ["dev-1", AN_ACCOUNT]
        held.note_every(keys, "twice-over")
        held.finished_every(keys, "twice-over", 999.0)

        first = gateway.reconcile_every_record_against_the_signed_log()
        assert first["read"] is True
        ledger_then = digest_of(gateway.ledger.path)
        quota_then = digest_of(pathlib.Path(gateway.state.root) / USE_NAME)
        log_then = digest_of(pathlib.Path(gateway.state.root) / meter.METER_NAME)

        second = gateway.reconcile_every_record_against_the_signed_log()
        assert second["settled"] == 0 and second["quota"] == 0, (
            "the second pass found something to repair, so the first did not finish: %r"
            % (second,))
        assert digest_of(gateway.ledger.path) == ledger_then, "the ledger changed on a second pass"
        assert digest_of(pathlib.Path(gateway.state.root) / USE_NAME) == quota_then, (
            "the quota changed on a second pass")
        assert digest_of(pathlib.Path(gateway.state.root) / meter.METER_NAME) == log_then, (
            "the signed log changed, which a reconciliation may never do")

    def test_a_second_restart_changes_nothing_either(self, gateway):
        claimed(gateway, "restart-twice", started=time.time() - 5.0)
        again, state = restarted(gateway)
        try:
            ledger_then = digest_of(again.ledger.path)
            log_then = digest_of(pathlib.Path(state.root) / meter.METER_NAME)
            once_more, state2 = restarted(again)
            try:
                assert len(lines_for(state2.root, "restart-twice")) == 1
                assert digest_of(pathlib.Path(state2.root) / meter.METER_NAME) == log_then
                assert digest_of(once_more.ledger.path) == ledger_then, (
                    "a start with nothing to do still wrote to the ledger")
            finally:
                once_more.close()
                state2.close()
        finally:
            state.close()


# ------------------------------------------------------------------ a document from before


class TestADocumentFromBeforeThisChange:
    """A schema-1 ledger, where `state` could hold an outcome word. The two entries that opened this
    arc are exactly this shape: `state: finished` beside a signed `interrupted` line."""

    def _a_legacy_document(self, root, run_id, state_word, *, cleanup=None, started=True):
        """Written by hand in the old shape: no `schema`, no `settled_as`, an outcome in `state`."""
        where = pathlib.Path(root) / "ledger.json"
        body = {"nonces": {}, "runs": {run_id: {
            "first_seen": time.time() - 30.0,
            "request_sha256": "s" * 64,
            "owner_client_id": "dev-1",
            "owner_account_id": AN_ACCOUNT,
            "state": state_word,
            "admitted": dict(ADMITTED),
        }}}
        if started:
            body["runs"][run_id]["started_at"] = time.time() - 25.0
        if cleanup is not None:
            body["runs"][run_id]["cleanup"] = cleanup
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
        return where

    def test_it_is_read_without_complaint(self, tmp_path):
        from agentnode_sdk.gateway.ledger import Ledger

        root = tmp_path / "state"
        root.mkdir(parents=True)
        self._a_legacy_document(root, "from-before", "finished")
        held = Ledger(root / "ledger.json")
        assert held.run_entry("from-before"), "a document with no schema could not be read"
        assert held.settled_as("from-before") == "", (
            "an outcome was inferred from the old `state` field -- and for the two entries this "
            "arc was opened by, that inference concludes the OPPOSITE of their signed lines")

    def test_the_old_word_is_replaced_by_what_the_signed_line_says(self, gateway):
        """The repair of the measured entries, driven.

        `state: finished` in the ledger, `interrupted` in the signed log. The ledger must end up
        saying `interrupted`, and it must get there by reading the line rather than by trusting
        either word.
        """
        self._a_legacy_document(gateway.state.root, "the-measured-shape", "finished", cleanup=True)
        a_signed_line(gateway.state.root, "the-measured-shape", state="interrupted",
                      outcome="unverified", termination_reason="gateway_stopped",
                      started_at=1010.0, finished_at=1043.422)

        again, state = restarted(gateway)
        try:
            assert again.ledger.settled_as("the-measured-shape") == "interrupted", (
                "the ledger still does not say what the signed record says")
            assert len(lines_for(state.root, "the-measured-shape")) == 1
        finally:
            again.close()
            state.close()

    def test_an_old_entry_is_repaired_however_old_it_is(self, gateway):
        """Not bounded by the sweep's age, which is the reviewer's correction to the first design.

        A contradiction between two of this service's own files does not become acceptable because
        it is a day old, and repairing one asks nothing of a worker.
        """
        from agentnode_sdk.gateway.ledger import SWEEP_AGAIN_WITHIN_SECONDS

        where = self._a_legacy_document(gateway.state.root, "long-ago", "finished", cleanup=True)
        body = json.loads(where.read_text(encoding="utf-8"))
        body["runs"]["long-ago"]["first_seen"] = time.time() - (SWEEP_AGAIN_WITHIN_SECONDS * 3)
        where.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
        a_signed_line(gateway.state.root, "long-ago", state="interrupted", outcome="unverified")

        again, state = restarted(gateway)
        try:
            assert "long-ago" not in again.ledger.runs_left_unswept(), (
                "premise: it is outside the sweep's window, so no worker is contacted about it")
            assert again.ledger.settled_as("long-ago") == "interrupted", (
                "an entry older than the sweep's window kept contradicting its own signed line")
        finally:
            again.close()
            state.close()

    def test_a_legacy_entry_with_no_signed_line_does_not_inherit_its_old_word(self, gateway):
        """The old `state` establishes nothing, so the start settles it honestly instead.

        ## A correction to this test, kept visible

        It first asserted `settled_as == ""` -- that such an entry simply stays unsettled -- and it
        went red with `'interrupted' == ''`. The product was right and the expectation was wrong: a
        run inside the sweep's window with no signed line is a run that is OWED one, and the start
        writes it. `test_a_run_with_no_line_at_all_is_still_closed_by_the_next_start` is the test
        that says so, and the two would have contradicted each other.

        What matters here is narrower and is what "conservatively" meant: the ending it ends up with
        comes from what actually happened, NOT from the word the old document was carrying. It says
        `finished`; the honest answer is `interrupted`. Inheriting the old word would have been the
        defect this whole arc is about, written into a migration.
        """
        self._a_legacy_document(gateway.state.root, "no-authority", "finished", cleanup=True)
        again, state = restarted(gateway)
        try:
            settled = again.ledger.settled_as("no-authority")
            assert settled != "finished", (
                "the old word was inherited, which is the one thing a migration may not do")
            assert settled == "interrupted", (
                "settled as %r; the gateway went away while this run was open, and that is what "
                "its line should say" % settled)
            assert the_one_line_for(state.root, "no-authority")["state"] == "interrupted"
        finally:
            again.close()
            state.close()

    def test_an_old_entry_beyond_the_window_with_no_line_stays_unsettled(self, gateway):
        """And the case that genuinely is left alone: too old for a line to be written now.

        This is the existing, already-reviewed age policy, not a new weakening -- writing a line is
        work with a worker in it. The point is only that it is not settled from the old word either:
        it stays unknown rather than becoming a `finished` nothing ever signed.
        """
        from agentnode_sdk.gateway.ledger import SWEEP_AGAIN_WITHIN_SECONDS

        where = self._a_legacy_document(gateway.state.root, "far-too-old", "finished", cleanup=True)
        body = json.loads(where.read_text(encoding="utf-8"))
        body["runs"]["far-too-old"]["first_seen"] = time.time() - (SWEEP_AGAIN_WITHIN_SECONDS * 3)
        where.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")

        again, state = restarted(gateway)
        try:
            assert not lines_for(state.root, "far-too-old"), (
                "premise: nothing signed exists for it and none is written now")
            assert again.ledger.settled_as("far-too-old") == "", (
                "it was settled from the old word, with no signed line anywhere to support it")
        finally:
            again.close()
            state.close()

    def test_a_document_this_version_wrote_says_which_shape_it_is(self, gateway):
        from agentnode_sdk.gateway.ledger import SCHEMA

        claimed(gateway, "stamped")
        body = json.loads(pathlib.Path(gateway.ledger.path).read_text(encoding="utf-8"))
        assert body.get("schema") == SCHEMA, (
            "a reader cannot tell which shape it is holding without guessing")


# ------------------------------------------------------------------ did it ever run


class TestWhetherItRanIsThreeValued:
    """True, False, and unknown -- and unknown is a real answer rather than a missing one."""

    def test_a_slot_that_was_held_is_evidence_that_it_began(self, gateway):
        claimed(gateway, "held-a-slot", started=time.time() - 5.0)
        assert gateway.ledger.did_it_ever_run("held-a-slot") is True

    def test_a_missing_timestamp_is_unknown_and_not_a_no(self, gateway):
        """`running` is written on a best effort and the failure is swallowed, so its absence is
        nobody having written it down -- which is not the same as nothing having run."""
        claimed(gateway, "nobody-wrote-it-down")
        assert gateway.ledger.did_it_ever_run("nobody-wrote-it-down") is None

    def test_only_a_worker_that_keeps_a_record_establishes_a_no(self, gateway):
        claimed(gateway, "the-worker-said-so")
        assert gateway.ledger.note_execution("the-worker-said-so", False) is False
        assert gateway.ledger.did_it_ever_run("the-worker-said-so") is False

    def test_a_contradicting_answer_is_kept_rather_than_applied(self, gateway):
        claimed(gateway, "two-answers")
        gateway.ledger.note_execution("two-answers", False)
        assert gateway.ledger.note_execution("two-answers", True) is False
        entry = gateway.ledger.run_entry("two-answers") or {}
        assert entry.get("execution_conflicts")

    def test_the_sentence_a_client_reads_does_not_claim_it_never_started(self, gateway):
        """The words, not the value. They have to agree, and the old pair did not: everything that
        was not a positive record of a slot was told, in plain words, that its job never started."""
        from agentnode_sdk.gateway.protocol import GATEWAY_STOPPED

        unknown = gateway.what_to_tell_them_about_an_interruption(GATEWAY_STOPPED, None)
        assert "never started" not in unknown, unknown
        assert "did not finish" in unknown
        assert "not something this gateway can now establish" in unknown
        # And the two it CAN establish still say what they said.
        assert "never started" in gateway.what_to_tell_them_about_an_interruption(
            GATEWAY_STOPPED, False)
        assert "while your job was running" in gateway.what_to_tell_them_about_an_interruption(
            GATEWAY_STOPPED, True)


# ------------------------------------------------------------------ the worker's own word


class TestTheWorkerJournalMeansSomethingElse:
    """`state: finished` in a worker journal record is not the gateway's answer about a customer's
    run, and this says so in one place so nobody has to work it out from two.

    The worker's record for both measured runs read `state: finished` with
    `outcome: {reason: runtime_lost, native_status: 137, exit_code: null}`. The word is the WORKER
    RECORD'S lifecycle -- this record is closed -- and the result is in `outcome`. Nothing in it
    claims success, and renaming a protocol-visible value the worker publishes is a separate arc.
    """

    def test_the_gateways_authority_is_the_signed_line_and_not_a_workers_word(self, gateway):
        """Driven: a run whose signed line says `interrupted` keeps that answer, whatever any other
        record's lifecycle word happens to be."""
        claimed(gateway, "worker-says-finished", started=time.time() - 5.0)
        a_signed_line(gateway.state.root, "worker-says-finished", state="interrupted",
                      outcome="unverified")
        gateway.reconcile_every_record_against_the_signed_log()
        assert gateway.ledger.settled_as("worker-says-finished") == "interrupted"

    def test_the_two_vocabularies_are_written_down_where_they_are_used(self):
        """A reader of the module must be able to find out which word means what."""
        import inspect

        from agentnode_sdk.gateway import ledger as ledger_module

        said = inspect.getdoc(ledger_module) or ""
        assert "LIFECYCLE position" in said
        assert "settled_as" in said and "COPIED FROM THE SIGNED LINE" in said


# ------------------------------------------------------- which path actually does the work


class TestWhichPathDoesTheWork:
    """Watched rather than reasoned about, because three counter-checks stayed green.

    A counter-check that stays green is a signal that the mechanism the author believes is
    load-bearing is not the one carrying the weight. So this watches the two calls that matter while
    the race happens, and says out loud what each one did. It is in the suite rather than in a
    scratch file because "which path does this" is the claim a reader has to be able to check.
    """

    def test_the_handler_asks_the_log_and_publishes_what_it_gets_back(self, waiting_gateway,
                                                                     monkeypatch):
        """The handler's tail IS reached, and the word it publishes is the log's, not its own.

        Measured values, from the run in `raw/S4-02-who-does-the-work.log`:

            write_down_what_it_used   called once, decided `finished`, got back `interrupted`
            move_to                   running, then interrupted (the stop), then interrupted again
            move_to that raised       none
            the record at the end     interrupted

        The third `move_to` is the handler publishing the log's word over the state the stop had
        already set -- allowed because `old == new` is not a move. Nothing here relies on an
        exception, which is what I had assumed when the counter-checks came back green.
        """
        from agentnode_sdk.gateway.server import RunRecord

        base, state, service, backend = waiting_gateway
        seen = {"calls": [], "moved": [], "raised": []}

        real_write = service.write_down_what_it_used

        def watched_write(record, granted, terminal):
            got = real_write(record, granted, terminal)
            seen["calls"].append((terminal, got))
            return got

        monkeypatch.setattr(service, "write_down_what_it_used", watched_write)

        real_move = RunRecord.move_to

        def watched_move(self, new_state):
            try:
                real_move(self, new_state)
            except Exception as exc:                           # noqa: BLE001
                seen["raised"].append((new_state, type(exc).__name__))
                raise
            seen["moved"].append(new_state)

        monkeypatch.setattr(RunRecord, "move_to", watched_move)

        a_running_job(base, state, service, backend, "who-does-it")
        assert service.close_what_is_still_in_flight() == ["who-does-it"]
        backend.let_go.set()
        until_it_has_ended(service, "who-does-it")
        time.sleep(0.5)

        line = the_one_line_for(state.root, "who-does-it")
        assert len(seen["calls"]) == 1, (
            "the handler's metering tail ran %d times, not once: %r"
            % (len(seen["calls"]), seen["calls"]))
        decided, got_back = seen["calls"][0]
        assert got_back == line["state"], (
            "the handler decided %r and was told %r, but the one signed line says %r"
            % (decided, got_back, line["state"]))
        assert decided != got_back, (
            "the handler happened to decide the same word as the line, so this run does not "
            "exercise the deferral at all")
        assert seen["raised"] == [], (
            "a state move raised: %r. The honest answer must not depend on an exception."
            % (seen["raised"],))
        assert seen["moved"][-1] == line["state"]

    def test_two_things_keep_the_client_honest_and_either_one_would_do(self, waiting_gateway):
        """Named because it is why a single-surface counter-check stays green.

        The client is told the signed line's word for two independent reasons:

          1. the handler publishes the word `write_down_what_it_used` hands back, and
          2. the stop has already moved the record to that word, and `move_to` refuses to leave a
             terminal state at all.

        Breaking either one alone leaves the other holding, which is why
        `counter_checks.py` has to break BOTH to make `test_the_client_is_not_told_it_succeeded` go
        red. That is a property of the fix and not of the test, and it is written down here so the
        green single-surface runs in `raw/S4-01` are not mistaken for a test that cannot fail.
        """
        from agentnode_sdk.gateway.protocol import is_terminal, may_move

        base, state, service, backend = waiting_gateway
        a_running_job(base, state, service, backend, "two-reasons")
        assert service.close_what_is_still_in_flight() == ["two-reasons"]
        line = the_one_line_for(state.root, "two-reasons")

        # Mechanism 2, on its own terms: the record is already terminal at the line's word, and the
        # state machine will not let anything move it to a different one.
        record = service.runs["two-reasons"]
        assert record.state == line["state"]
        assert is_terminal(record.state)
        assert not may_move(record.state, "finished"), (
            "a terminal state can still be replaced, so mechanism 2 is not holding")

        backend.let_go.set()
        until_it_has_ended(service, "two-reasons")
        assert service.runs["two-reasons"].state == line["state"]


# --------------------------------------------- a quota entry that is not there at all


class TestAQuotaEntryThatIsMissingAltogether:
    """`STATE-CONSISTENCY-0001`, F-QUOTA-MISSING-NOT-REPAIRED.

    The first version of this repair reconciled a quota figure that DIFFERED from the signed line and
    skipped a scope with no entry at all, and the docstring argued for the skip: that an absent entry
    meant the ceilings were not counting this run. An independent review found that false, and it is:
    `reserve` writes an entry on every scope at admission either way --

        if scopes:  self.use.claim_every(...)      # there ARE window ceilings
        else:       self.use.note_every(...)       # there are not

    -- and the `else` is the point. So an absent entry is one that was LOST, or one the window has
    forgotten, and the first of those is a signed line carrying billed seconds beside a quota carrying
    nothing. Binding answer A5 says "repair a missing OR differing quota entry", and only half of it
    was done.
    """

    def test_a_missing_entry_is_created_from_the_signed_line(self, gateway):
        keys = ["dev-1", AN_ACCOUNT]
        arrived = time.time() - 60.0
        claimed(gateway, "quota-gone", when=arrived, started=arrived + 1.0)
        # `queued_at` is given the real arrival. It defaults to 1000.0 in this helper -- an epoch
        # stamp -- and once the signed line became the authority for the arrival (round two's
        # F-ARRIVAL-CONFLICT-TRUSTS-LEDGER) that default put the run 56 years outside the window, so
        # the correct answer became "do not create" and this test went red against a correct product.
        a_signed_line(gateway.state.root, "quota-gone", state="interrupted", outcome="unverified",
                      queued_at=arrived, started_at=1010.0, finished_at=1021.0)
        assert all(v is None for v in
                   the_quota(gateway.state.root).what_a_run_was_charged(keys, "quota-gone").values()), (
            "premise: no scope holds a figure for this run")

        again, state = restarted(gateway)
        try:
            line = the_one_line_for(state.root, "quota-gone")
            charged = the_quota(state.root).what_a_run_was_charged(keys, "quota-gone")
            for scope, seconds in charged.items():
                assert seconds is not None, (
                    "scope %s still holds no figure for a run whose signed line says %r seconds"
                    % (scope, line["seconds"]))
                assert seconds == pytest.approx(float(line["seconds"]), abs=0.001)
            assert len(lines_for(state.root, "quota-gone")) == 1, (
                "the repair wrote a second usage line")
        finally:
            again.close()
            state.close()

    def test_the_created_entry_is_stamped_with_the_runs_arrival_and_not_with_now(self, gateway):
        """Otherwise the charge lives in the customer's window for another day from the REPAIR.

        The window forgets by `at`. A charge stamped `now` is a charge they did not incur then, and it
        would keep counting against their allowance long after the original would have been forgotten.
        """
        # No scope list here, on purpose: this reads the document itself rather than asking per
        # scope, because what it is about is the `at` on whatever entries exist and not their figures.
        arrived = time.time() - (6 * 60 * 60)          # six hours ago, well inside the window
        claimed(gateway, "stamped-when-it-arrived", when=arrived, started=arrived + 1.0)
        a_signed_line(gateway.state.root, "stamped-when-it-arrived", state="interrupted",
                      outcome="unverified", queued_at=arrived)

        again, state = restarted(gateway)
        try:
            body = json.loads((pathlib.Path(state.root) / USE_NAME).read_text(encoding="utf-8"))
            stamps = [e.get("at") for entries in body.values() if isinstance(entries, list)
                      for e in entries
                      if isinstance(e, dict) and e.get("run_id") == "stamped-when-it-arrived"]
            assert stamps, "nothing was created, so there is no stamp to check"
            for at in stamps:
                assert abs(float(at) - arrived) < 2.0, (
                    "stamped %r; the run arrived at %r, and the difference is how much longer this "
                    "charge would sit in the customer's window than it should"
                    % (at, arrived))
            assert all(float(at) < time.time() - (5 * 60 * 60) for at in stamps), (
                "it was stamped with the repair's own clock")
        finally:
            again.close()
            state.close()

    def test_a_run_the_window_has_forgotten_is_not_given_a_charge_again(self, gateway):
        """The half of the old reasoning that was RIGHT, and is kept.

        Outside the window the absence IS the answer: recreating the entry would resurrect a charge
        the customer's allowance had already forgotten. So nothing is created -- and nothing is
        recorded as repaired either, because an answer that never changes must not be written on every
        start. That is what `test_a_second_reconciliation_writes_nothing_at_all` would catch.
        """
        from agentnode_sdk.gateway.allowance import WINDOW_SECONDS

        keys = ["dev-1", AN_ACCOUNT]
        long_ago = time.time() - (WINDOW_SECONDS * 3)
        claimed(gateway, "window-forgot-it", when=long_ago, started=long_ago + 1.0)
        a_signed_line(gateway.state.root, "window-forgot-it", state="interrupted",
                      outcome="unverified", queued_at=long_ago)

        again, state = restarted(gateway)
        try:
            charged = the_quota(state.root).what_a_run_was_charged(keys, "window-forgot-it")
            assert all(v is None for v in charged.values()), (
                "a charge the window had forgotten was resurrected: %r" % (charged,))
            entry = again.ledger.run_entry("window-forgot-it") or {}
            assert not entry.get("quota_repairs"), (
                "it recorded a repair for an answer that will never change, so every start would "
                "append another one")
            # And doing it again changes nothing at all.
            before = digest_of(again.ledger.path)
            quota_before = digest_of(pathlib.Path(state.root) / USE_NAME)
            again.reconcile_every_record_against_the_signed_log()
            assert digest_of(again.ledger.path) == before
            assert digest_of(pathlib.Path(state.root) / USE_NAME) == quota_before
        finally:
            again.close()
            state.close()

    def test_one_figure_per_scope_and_not_two(self, gateway):
        """Creating must not append beside an entry that is already there."""
        keys = ["dev-1", AN_ACCOUNT]
        arrived = time.time() - 60.0
        claimed(gateway, "exactly-one-figure", when=arrived, started=arrived + 1.0)
        a_signed_line(gateway.state.root, "exactly-one-figure", state="finished",
                      queued_at=arrived)
        held = the_quota(gateway.state.root)
        held.note_every(keys, "exactly-one-figure")
        held.finished_every(keys, "exactly-one-figure", 999.0)

        again, state = restarted(gateway)
        try:
            body = json.loads((pathlib.Path(state.root) / USE_NAME).read_text(encoding="utf-8"))
            for scope in keys:
                mine = [e for e in body.get(scope, [])
                        if isinstance(e, dict) and e.get("run_id") == "exactly-one-figure"]
                assert len(mine) == 1, "scope %s holds %d figures for one run" % (scope, len(mine))
        finally:
            again.close()
            state.close()


# ------------------------------------------- two gateways, in two real processes


class TestTwoLedgersInTwoRealProcesses:
    """A cross-process compare-and-set on the `Ledger` itself. NOT the evidence for SC5.

    Round two accepted this as valid for what it measures and refused it as evidence for SC5, because
    it instantiates `Ledger` directly rather than two gateway services and contends on one persisted
    fact. Both are true. It is kept because the narrower property -- that the write-once decision is
    made against the FILE and not against one object's memory -- is worth a test of its own, and
    `TestTwoGatewayServicesInTwoRealProcesses` below is what SC5 is judged on.

    The review was right to return NOT_ESTABLISHED: two `Ledger` objects in one process exercise the
    reload-inside-the-lock, but they share an interpreter, a thread lock and a page cache. A second
    PROCESS shares none of those, and the cross-process lock is the only thing left.

    So this starts real subprocesses. They are given the same state directory and the same run, and
    each is told to settle it as a DIFFERENT word. Exactly one must win, the file must say that one
    word, and the loser's attempt must be on the record rather than lost.
    """

    PROGRAM = textwrap.dedent("""
        import sys
        from agentnode_sdk.gateway.ledger import Ledger

        import os, time

        path, word, barrier = sys.argv[1], sys.argv[2], sys.argv[3]
        held = Ledger(path)
        # A HANDSHAKE, NOT JUST A BARRIER. Writing the barrier as soon as both children are spawned
        # releases them before either has finished starting, and process startup then serialises
        # them -- which is how this test came to pass with the reload removed from note_it_settled:
        # the second child was CONSTRUCTED after the first had already written, so its snapshot was
        # fresh and the missing reload cost nothing. Measured, in raw/S19-03.
        #
        # So each child says it is ready only once it holds its object, and waits for the parent to
        # see both before any of them writes.
        open(barrier + ".ready." + str(os.getpid()), "w").close()
        until = time.time() + 30
        while not os.path.exists(barrier) and time.time() < until:
            time.sleep(0.002)
        got, wrote = held.note_it_settled("contended", word)
        print("%s|%s" % (got, wrote))
    """)

    def test_exactly_one_word_wins_and_the_other_is_recorded(self, gateway, tmp_path):
        import subprocess

        claimed(gateway, "contended", started=time.time() - 5.0)
        program = tmp_path / "settle.py"
        program.write_text(self.PROGRAM, encoding="utf-8")
        barrier = tmp_path / "go"
        ledger_path = str(gateway.ledger.path)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(pathlib.Path(gateway.state.root).parents[0]) + os.pathsep + \
            str(pathlib.Path(__file__).resolve().parents[1])

        started = [
            subprocess.Popen([sys.executable, str(program), ledger_path, word, str(barrier)],
                             cwd=str(pathlib.Path(__file__).resolve().parents[1]),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            for word in ("interrupted", "finished")
        ]
        release_when_both_are_ready(started, barrier, tmp_path)
        said = []
        for proc in started:
            out, err = proc.communicate(timeout=60)
            assert proc.returncode == 0, "a child failed: %s" % (err[-600:],)
            said.append(out.strip().splitlines()[-1])

        wrote = [s for s in said if s.endswith("|True")]
        assert len(wrote) == 1, (
            "%d of two processes believed it wrote the word: %r. Exactly one may."
            % (len(wrote), said))
        winner = wrote[0].split("|")[0]
        # Both must AGREE about what the file says, whichever of them wrote it.
        assert {s.split("|")[0] for s in said} == {winner}, (
            "the two processes disagree about what the file now says: %r" % (said,))
        entry = gateway.ledger.run_entry("contended") or {}
        assert entry.get("settled_as") == winner
        loser = "finished" if winner == "interrupted" else "interrupted"
        assert entry.get("settled_conflicts"), (
            "the losing process's word left no trace, so nobody can find out two paths read "
            "different things")
        assert any(c.get("offered") == loser for c in entry["settled_conflicts"])

    def test_the_two_children_really_were_two_processes(self, gateway, tmp_path):
        """The control. A test that proved nothing about concurrency because both halves ran in one
        interpreter is exactly what the review just rejected, so this says what ran where."""
        import subprocess

        program = tmp_path / "whoami.py"
        program.write_text("import os, sys; print(os.getpid())\n", encoding="utf-8")
        pids = set()
        for _ in range(2):
            out = subprocess.run([sys.executable, str(program)], capture_output=True, text=True,
                                 timeout=60)
            assert out.returncode == 0, out.stderr
            pids.add(out.stdout.strip())
        assert len(pids) == 2, "the two children shared a process id: %r" % (pids,)
        assert str(os.getpid()) not in pids, "a child ran in this interpreter"


# ------------------------------- what round two found: duplicates, equality, and which clock decides


class TestOneFigurePerScopeAndExactlyTheSignedOne:
    """`STATE-CONSISTENCY-0002`, F-QUOTA-DUPLICATES-SURVIVE and F-QUOTA-APPROXIMATE-NOT-EQUAL.

    The first repair of the quota set every matching entry and left two entries as two, and it treated
    a figure within half a millisecond of the signed one as already right. Both were found by review:
    a scope holding two billing figures does not hold one, whatever the numbers are, and close is not
    the same number.
    """

    def test_two_entries_for_one_run_are_collapsed_to_one(self, gateway):
        keys = ["dev-1", AN_ACCOUNT]
        arrived = time.time() - 120.0
        claimed(gateway, "two-figures", when=arrived, started=arrived + 1.0)
        a_signed_line(gateway.state.root, "two-figures", state="finished", queued_at=arrived)
        held = the_quota(gateway.state.root)
        # Two entries on each scope, which is what two calls to `note_every` leave behind.
        held.note_every(keys, "two-figures", now=arrived)
        held.note_every(keys, "two-figures", now=arrived + 5.0)
        assert all(n == 2 for n in held.how_many_figures_for(keys, "two-figures").values()), "premise"

        again, state = restarted(gateway)
        try:
            counts = the_quota(state.root).how_many_figures_for(keys, "two-figures")
            assert all(n == 1 for n in counts.values()), (
                "a scope still holds more than one billing figure for one run: %r" % (counts,))
            line = the_one_line_for(state.root, "two-figures")
            charged = the_quota(state.root).what_a_run_was_charged(keys, "two-figures")
            for scope, seconds in charged.items():
                assert float(seconds) == float(line["seconds"]), (
                    "scope %s holds %r and the signed line says %r" % (scope, seconds,
                                                                       line["seconds"]))
        finally:
            again.close()
            state.close()

    def test_duplicates_that_already_agree_are_still_collapsed(self, gateway):
        """The case the old code called `already right` and walked away from."""
        keys = ["dev-1", AN_ACCOUNT]
        arrived = time.time() - 120.0
        claimed(gateway, "two-that-agree", when=arrived, started=arrived + 1.0)
        a_signed_line(gateway.state.root, "two-that-agree", state="finished",
                      queued_at=arrived, started_at=1010.0, finished_at=1021.0)
        line = the_one_line_for(gateway.state.root, "two-that-agree")
        held = the_quota(gateway.state.root)
        held.note_every(keys, "two-that-agree", now=arrived)
        held.note_every(keys, "two-that-agree", now=arrived + 5.0)
        # BOTH already carry the right value, so nothing about the numbers is wrong.
        held.the_figure_for(keys, "two-that-agree", float(line["seconds"]), arrived)
        before = the_quota(gateway.state.root).how_many_figures_for(keys, "two-that-agree")

        again, state = restarted(gateway)
        try:
            counts = the_quota(state.root).how_many_figures_for(keys, "two-that-agree")
            assert all(n == 1 for n in counts.values()), (
                "two entries that agreed were left as two: before %r, after %r" % (before, counts))
        finally:
            again.close()
            state.close()

    def test_a_figure_half_a_millisecond_out_is_replaced(self, gateway):
        """Close is not the same number, and equality is what a reader compares."""
        keys = ["dev-1", AN_ACCOUNT]
        arrived = time.time() - 120.0
        claimed(gateway, "nearly-right", when=arrived, started=arrived + 1.0)
        a_signed_line(gateway.state.root, "nearly-right", state="finished", queued_at=arrived)
        line = the_one_line_for(gateway.state.root, "nearly-right")
        held = the_quota(gateway.state.root)
        held.note_every(keys, "nearly-right", now=arrived)
        nearly = float(line["seconds"]) + 0.0004
        held.the_figure_for(keys, "nearly-right", nearly, arrived)
        assert all(float(v) == nearly for v in
                   held.what_a_run_was_charged(keys, "nearly-right").values()), "premise"

        again, state = restarted(gateway)
        try:
            charged = the_quota(state.root).what_a_run_was_charged(keys, "nearly-right")
            for scope, seconds in charged.items():
                assert float(seconds) == float(line["seconds"]), (
                    "scope %s still holds %r against a signed %r -- a difference of %r"
                    % (scope, seconds, line["seconds"], float(seconds) - float(line["seconds"])))
        finally:
            again.close()
            state.close()



class TestTheLineIsTheOnlySourceOfTheArrival:
    """`STATE-CONSISTENCY-0004`, F-SIGNED-ARRIVAL-INVALID-FALLS-BACK-TO-LEDGER.

    Round two moved the arrival from the ledger to the signed line. Round four pointed out that
    ORDERING the two sources was not enough: a line whose `queued_at` was absent, zero or unreadable
    sent the code to the ledger anyway, so the arrival could still be derived from the cache while the
    authority sat right there. And a positive but absurd future stamp was simply believed.

    Every path that reaches the reconciliation has a line, so the ledger is not consulted for this at
    all now, and a value that cannot be a time is refused rather than used.
    """

    def _a_line_with(self, gateway, run_id, queued_at, ledger_first_seen):
        claimed(gateway, run_id, when=ledger_first_seen, started=ledger_first_seen + 1.0)
        a_signed_line(gateway.state.root, run_id, state="finished", queued_at=queued_at)

    def _edit_the_line(self, root, run_id, **changes):
        """The module-level `edit_the_line`, kept as a method because every test here calls it that
        way and because a second class now needs the same thing. One body, two callers."""
        edit_the_line(root, run_id, **changes)

    def test_a_line_that_cannot_say_when_it_arrived_does_not_borrow_the_ledgers_answer(self, gateway):
        """The ledger's own stamp here is recent and perfectly usable. It must still not be used."""
        keys = ["dev-1", AN_ACCOUNT]
        recent = time.time() - 120.0
        self._a_line_with(gateway, "line-cannot-say", queued_at=recent,
                          ledger_first_seen=recent)
        # The meter fills a zero in from `started_at`, so the shape is edited in rather than written.
        self._edit_the_line(gateway.state.root, "line-cannot-say", queued_at=0)
        line = the_one_line_for(gateway.state.root, "line-cannot-say")
        assert float(line["queued_at"]) == 0.0, "premise: the LINE cannot say when it arrived"
        entry = gateway.ledger.run_entry("line-cannot-say") or {}
        assert float(entry.get("first_seen") or 0) > time.time() - 600, (
            "premise: the LEDGER knows when this arrived, and it is well inside the window")

        again, state = restarted(gateway)
        try:
            # THE PROPERTY IS UNCHANGED: the ledger's usable stamp must not become the placement.
            # What changed is that refusing is no longer how it is honoured -- `DECISION-0004` records
            # the figure at the recorder's own instant -- so the assertion moved from "nothing was
            # written" to "what was written is not the ledger's answer". That is the property itself
            # rather than a side effect of it, and it is the stricter of the two.
            held = the_quota(state.root)
            charged = held.what_a_run_was_charged(keys, "line-cannot-say")
            billed = float(line["seconds"])
            assert all(v == billed for v in charged.values()), (
                "the quota and the signed line's %s s disagree: %r" % (billed, charged))
            seen = 0
            for entries in held.snapshot().values():
                for row in entries:
                    if row.get("run_id") != "line-cannot-say":
                        continue
                    seen += 1
                    assert abs(float(row["at"]) - recent) > 30.0, (
                        "the placement IS the ledger's own stamp, to within 30 s: %r" % (row,))
                    assert row.get("arrival") == "substituted", row
            assert seen == len(keys), "the figure is not on every scope: %d" % seen
        finally:
            again.close()
            state.close()

    def test_a_line_with_an_unreadable_stamp_cannot_be_written_in_the_first_place(self, gateway):
        """Where the guard actually is, which is NOT where I first looked.

        I wrote this test expecting to drive a line whose `queued_at` was unreadable through the
        reconciliation. It cannot be driven, because `meter.record` does `float(queued_at or
        started_at)` at line 470 and raises before any line is written. So a line of that shape does
        not exist, and the one that matters is the one the meter refuses.

        The reconciliation keeps its own `try`/`except (TypeError, ValueError)` round that conversion
        anyway. That is not dead weight for the reason this class exists -- the authority is a FILE,
        and a file can be edited by something that is not `meter.record` -- but it is unreachable
        through the writer, and saying it is reachable would be a claim this cannot support.
        """
        with pytest.raises(ValueError):
            a_signed_line(gateway.state.root, "stamp-is-nonsense", state="finished",
                          queued_at="not a time")
        assert not lines_for(gateway.state.root, "stamp-is-nonsense"), (
            "a line was written despite the refusal")

    def test_and_the_reconciliation_survives_one_that_was_edited_in(self, gateway):
        """The case the meter cannot produce and a text editor can: the file is the authority, and a
        file is a file."""
        keys = ["dev-1", AN_ACCOUNT]
        recent = time.time() - 120.0
        self._a_line_with(gateway, "edited-stamp", queued_at=recent, ledger_first_seen=recent)
        self._edit_the_line(gateway.state.root, "edited-stamp", queued_at="not a time")

        again, state = restarted(gateway)
        try:
            held = the_quota(state.root)
            charged = held.what_a_run_was_charged(keys, "edited-stamp")
            # Under `DECISION-0004` the figure is recorded and its PLACEMENT is substituted, so what
            # this test checks is the property it was written for: the unreadable value never becomes
            # a time. Before that decision it checked that nothing at all was written, and
            # `STATE-CONSISTENCY-0005` found that leaving the two records disagreeing.
            # The figure comes from the line, so the expectation is read from the line too rather than
            # written here as a number that could drift away from the fixture.
            signed = [line for line in meter.read(state.root) if line.get("run_id") == "edited-stamp"]
            assert len(signed) == 1, signed
            billed = float(signed[0]["seconds"])
            assert all(v == billed for v in charged.values()), (
                "an unreadable stamp left the quota disagreeing with the signed line's %s s: %r"
                % (billed, charged))
            for entries in held.snapshot().values():
                for entry in entries:
                    if entry.get("run_id") == "edited-stamp":
                        assert isinstance(entry["at"], (int, float)), entry
                        assert entry.get("arrival") == "substituted", entry
            assert again.ledger.runs_with_a_substituted_arrival() == ["edited-stamp"], (
                "the substituted placement is not listed anywhere a reader would find it")
        finally:
            again.close()
            state.close()

    def test_an_arrival_in_the_future_never_becomes_the_figures_placement(self, gateway):
        """A run cannot have arrived after now, so that value is never where the figure sits.

        Round four demanded that the future stamp not be believed, and this test asserted it by
        requiring NOTHING to be written. `STATE-CONSISTENCY-0005` showed what "nothing" left behind,
        and `DECISION-0004` records the figure at the recorder's own instant instead. The property this
        test was written for is unchanged and is checked more strictly now: the future value is read
        back out of the file, and it must not be the stored placement.
        """
        from agentnode_sdk.gateway.allowance import AHEAD_OF_US

        keys = ["dev-1", AN_ACCOUNT]
        held = the_quota(gateway.state.root)
        ahead = time.time() + (AHEAD_OF_US * 10)
        said = held.the_figure_for(keys, "from-the-future", 5.0, ahead)
        assert set(said.values()) == {"created without a usable arrival"}, (
            "a future arrival was answered with %r" % (sorted(set(said.values())),))
        charged = held.what_a_run_was_charged(keys, "from-the-future")
        assert all(v == 5.0 for v in charged.values()), (
            "the signed figure and the quota still disagree: %r" % (charged,))
        assert set(held.how_many_figures_for(keys, "from-the-future").values()) == {1}
        seen = 0
        for scope, entries in held.snapshot().items():
            for entry in entries:
                if entry.get("run_id") != "from-the-future":
                    continue
                seen += 1
                assert float(entry["at"]) < ahead - AHEAD_OF_US, (
                    "the future stamp became the placement on %s, so the charge would sit in the "
                    "customer's window until that time plus a window" % scope)
                assert entry.get("arrival") == "substituted", entry
        assert seen == len(keys), "the figure was not written on every scope: %d" % seen
        # And a stamp inside the jitter allowance is still the real arrival, or ordinary clock noise
        # between two processes would start substituting placements for real charges.
        ok = held.the_figure_for(keys, "a-second-ahead", 5.0, time.time() + 1.0)
        assert set(ok.values()) == {"created"}, ok

    def test_the_three_input_shapes_are_told_apart(self, gateway):
        """No arrival, an arrival ahead of this clock, and an arrival the window has forgotten are
        three different facts. Two of them now place the figure and one still refuses, and collapsing
        any of them would hide which happened."""
        from agentnode_sdk.gateway.allowance import AHEAD_OF_US, WINDOW_SECONDS

        held = the_quota(gateway.state.root)
        assert set(held.the_figure_for(["dev-1"], "none", 1.0, 0.0).values()) == \
            {"created without a usable arrival"}
        assert set(held.the_figure_for(["dev-1"], "ahead", 1.0,
                                       time.time() + AHEAD_OF_US * 5).values()) == \
            {"created without a usable arrival"}
        assert set(held.the_figure_for(["dev-1"], "old", 1.0,
                                       time.time() - WINDOW_SECONDS * 3).values()) == \
            {"outside the window"}
        # The refusal is still a refusal: a USABLE arrival the window has forgotten is not resurrected.
        assert held.what_a_run_was_charged(["dev-1"], "old")["dev-1"] is None
        # And the two that are not refusals left exactly one figure each.
        assert set(held.how_many_figures_for(["dev-1"], "none").values()) == {1}
        assert set(held.how_many_figures_for(["dev-1"], "ahead").values()) == {1}


class TestWhichClockDecidesWhenTheTwoDisagree:
    """`STATE-CONSISTENCY-0002`, F-ARRIVAL-CONFLICT-TRUSTS-LEDGER.

    The first repair read the ledger's `first_seen` and fell back to the signed line, which is this
    arc's own principle inverted in the one place where the two records can disagree. A stale
    `first_seen` suppressed a repair the line supported; a recent one kept a charge alive longer than
    the line said it should.

    The line is the authority for this run's figures, and when it began to wait is one of them.
    """

    def test_a_stale_ledger_stamp_does_not_suppress_a_repair_the_line_supports(self, gateway):
        from agentnode_sdk.gateway.allowance import WINDOW_SECONDS

        keys = ["dev-1", AN_ACCOUNT]
        recent = time.time() - 120.0
        claimed(gateway, "ledger-is-stale", when=time.time() - (WINDOW_SECONDS * 3))
        # The LINE says it arrived two minutes ago; the ledger says three windows ago.
        a_signed_line(gateway.state.root, "ledger-is-stale", state="finished", queued_at=recent)

        again, state = restarted(gateway)
        try:
            charged = the_quota(state.root).what_a_run_was_charged(keys, "ledger-is-stale")
            assert all(v is not None for v in charged.values()), (
                "the ledger's stale stamp suppressed a repair the signed line supports: %r"
                % (charged,))
            body = json.loads((pathlib.Path(state.root) / USE_NAME).read_text(encoding="utf-8"))
            stamps = [e.get("at") for entries in body.values() if isinstance(entries, list)
                      for e in entries
                      if isinstance(e, dict) and e.get("run_id") == "ledger-is-stale"]
            for at in stamps:
                assert abs(float(at) - recent) < 2.0, (
                    "stamped %r, which is the ledger's figure and not the line's %r" % (at, recent))
        finally:
            again.close()
            state.close()

    def test_a_recent_ledger_stamp_does_not_keep_a_charge_the_line_calls_old(self, gateway):
        """The mirror. The ledger says it arrived a minute ago, the line says three windows ago."""
        from agentnode_sdk.gateway.allowance import WINDOW_SECONDS

        keys = ["dev-1", AN_ACCOUNT]
        long_ago = time.time() - (WINDOW_SECONDS * 3)
        claimed(gateway, "ledger-looks-fresh", when=time.time() - 60.0)
        a_signed_line(gateway.state.root, "ledger-looks-fresh", state="finished", queued_at=long_ago)

        again, state = restarted(gateway)
        try:
            charged = the_quota(state.root).what_a_run_was_charged(keys, "ledger-looks-fresh")
            assert all(v is None for v in charged.values()), (
                "a charge the signed line puts outside the window was created because the ledger's "
                "own stamp looked recent: %r" % (charged,))
        finally:
            again.close()
            state.close()

    def test_an_arrival_nobody_knows_is_still_not_the_same_as_one_too_old(self, gateway):
        """`0` is not a time, and "nobody knows when this arrived" is not "this is too old".

        What each one DOES changed with `DECISION-0004` -- the first places the figure and says the
        placement was substituted, the second still refuses -- and that is exactly why they must not
        collapse into one answer.
        """
        keys = ["dev-1", AN_ACCOUNT]
        held = the_quota(gateway.state.root)
        said = held.the_figure_for(keys, "no-arrival-at-all", 5.0, 0.0)
        assert set(said.values()) == {"created without a usable arrival"}, (
            "an unusable arrival was answered with %r" % (sorted(set(said.values())),))
        assert all(v == 5.0 for v in
                   held.what_a_run_was_charged(keys, "no-arrival-at-all").values())
        # And it is NOT the same answer as an arrival the window has passed, which writes nothing.
        from agentnode_sdk.gateway.allowance import WINDOW_SECONDS

        expired = held.the_figure_for(keys, "too-old", 5.0, time.time() - (WINDOW_SECONDS * 3))
        assert set(expired.values()) == {"outside the window"}, expired
        assert all(v is None for v in held.what_a_run_was_charged(keys, "too-old").values())




class TestASignedLineBesideNoFigureAtAll:
    """`STATE-CONSISTENCY-0005`, F-FUTURE-ARRIVAL-REFUSES-RECONCILIATION and
    F-FUTURE-ARRIVAL-BREAKS-EXACTLY-ONE-FIGURE.

    Round four's fix answered an unusable arrival instead of believing it, which was right. This is
    what answering left behind: with **no entry on the scope**, the answer was all that happened, so a
    signed line carrying billed seconds sat beside a scope carrying no figure at all -- for ever, since
    every later start answers the same way -- and `moved` came back empty, so not even the decline was
    recorded. `DECISION-0004` records the figure at the recorder's own instant and names the
    substitution.

    The finding says a future arrival *"prevents all quota repair"*, and that is wider than the code:
    an existing wrong figure and a duplicate pair are repaired without the arrival being consulted at
    all. The last test in this class holds that bound, because a repair claimed where none happened and
    a defect claimed wider than it is are the same kind of mistake.
    """

    def _a_run_with_a_line_and_no_figure(self, gateway, run_id, queued_at):
        """A settled, signed run whose quota entry is gone -- the crash this reconciliation is for."""
        claimed(gateway, run_id, when=time.time() - 120.0, started=time.time() - 119.0)
        a_signed_line(gateway.state.root, run_id, state="finished", queued_at=queued_at)
        held = the_quota(gateway.state.root)
        body = held.snapshot()
        for scope in list(body):
            body[scope] = [e for e in body[scope] if e.get("run_id") != run_id]
        the_quota_file(gateway.state.root).write_text(json.dumps(body), encoding="utf-8")
        assert all(v is None for v in
                   the_quota(gateway.state.root).what_a_run_was_charged(
                       ["dev-1", AN_ACCOUNT], run_id).values())

    def _billed(self, root, run_id) -> float:
        signed = [line for line in meter.read(root) if line.get("run_id") == run_id]
        assert len(signed) == 1, signed
        return float(signed[0]["seconds"])

    def test_a_line_from_a_clock_ahead_of_us_still_ends_with_exactly_one_figure(self, gateway):
        """The reported shape, end to end: a restart must leave the two records agreeing."""
        from agentnode_sdk.gateway.allowance import AHEAD_OF_US

        keys = ["dev-1", AN_ACCOUNT]
        ahead = time.time() + (AHEAD_OF_US * 20)
        self._a_run_with_a_line_and_no_figure(gateway, "ahead-and-missing", ahead)

        again, state = restarted(gateway)
        try:
            billed = self._billed(state.root, "ahead-and-missing")
            held = the_quota(state.root)
            charged = held.what_a_run_was_charged(keys, "ahead-and-missing")
            assert all(v == billed for v in charged.values()), (
                "the signed line bills %s s and the quota says %r" % (billed, charged))
            assert set(held.how_many_figures_for(keys, "ahead-and-missing").values()) == {1}, (
                "a scope does not hold exactly one figure: %r"
                % (held.how_many_figures_for(keys, "ahead-and-missing"),))
            for entries in held.snapshot().values():
                for entry in entries:
                    if entry.get("run_id") == "ahead-and-missing":
                        assert float(entry["at"]) < ahead - AHEAD_OF_US, entry
        finally:
            again.close()
            state.close()

    def test_a_line_that_cannot_say_when_it_arrived_does_too(self, gateway):
        """The same hole through the other unusable shape, which nobody reported and which was there."""
        keys = ["dev-1", AN_ACCOUNT]
        recent = time.time() - 120.0
        self._a_run_with_a_line_and_no_figure(gateway, "zero-and-missing", recent)
        edit_the_line(gateway.state.root, "zero-and-missing", queued_at=0)

        again, state = restarted(gateway)
        try:
            billed = self._billed(state.root, "zero-and-missing")
            charged = the_quota(state.root).what_a_run_was_charged(keys, "zero-and-missing")
            assert all(v == billed for v in charged.values()), (
                "a line that cannot say when it arrived left the quota empty: %r" % (charged,))
        finally:
            again.close()
            state.close()

    def test_the_substitution_is_recorded_and_can_be_listed(self, gateway):
        """A substituted placement nothing can list is one nobody will look at."""
        from agentnode_sdk.gateway.allowance import AHEAD_OF_US

        self._a_run_with_a_line_and_no_figure(gateway, "listed", time.time() + (AHEAD_OF_US * 20))
        again, state = restarted(gateway)
        try:
            assert again.ledger.runs_with_a_substituted_arrival() == ["listed"]
            # It is recorded as a repair, with the word in it rather than beside it.
            entry = again.ledger.run_entry("listed") or {}
            repairs = entry.get("quota_repairs") or []
            assert any("created without a usable arrival" in (r.get("was", {}).get("did") or {}).values()
                       for r in repairs), repairs
            # And it is NOT the sweep's business: the run is settled and the figure is right.
            assert "listed" not in again.ledger.runs_needing_attention(), (
                "a settled run with a right figure was handed back to the recovery sweep")
        finally:
            again.close()
            state.close()

    def test_and_a_second_start_changes_nothing(self, gateway):
        """A5's idempotence, which the refusal could never reach: it answered the same way for ever."""
        from agentnode_sdk.gateway.allowance import AHEAD_OF_US

        self._a_run_with_a_line_and_no_figure(gateway, "twice", time.time() + (AHEAD_OF_US * 20))
        again, state = restarted(gateway)
        try:
            after_one = digest_of(the_quota_file(state.root))
            repairs_one = len((again.ledger.run_entry("twice") or {}).get("quota_repairs") or [])
        finally:
            again.close()
            state.close()
        third, state3 = restarted(gateway)
        try:
            assert digest_of(the_quota_file(state3.root)) == after_one, (
                "the second start rewrote the quota, so the repair is not byte-stable")
            assert len((third.ledger.run_entry("twice") or {}).get("quota_repairs") or []) == \
                repairs_one, "the second start recorded another repair for a run already repaired"
            assert third.ledger.runs_with_a_substituted_arrival() == ["twice"]
        finally:
            third.close()
            state3.close()

    def test_an_existing_wrong_figure_is_repaired_whatever_the_line_says_about_the_arrival(self,
                                                                                          gateway):
        """The bound of the defect, so the submission does not claim a wider one than it fixed.

        With an entry already there, the arrival is never consulted: a wrong value is set and a
        duplicate pair is collapsed, future stamp or not. That was true before this change and stays
        true, and it is the half of the finding's wording that the code does not support.
        """
        from agentnode_sdk.gateway.allowance import AHEAD_OF_US

        keys = ["dev-1", AN_ACCOUNT]
        held = the_quota(gateway.state.root)
        ahead = time.time() + (AHEAD_OF_US * 20)
        here = time.time() - 60.0
        # One wrong figure, and a scope with two.
        held.the_figure_for(keys, "wrong", 1.0, here)
        body = held.snapshot()
        for scope in body:
            for entry in body[scope]:
                if entry.get("run_id") == "wrong":
                    entry["seconds"] = 99.0
            body[scope].append({"run_id": "double", "at": here, "seconds": 1.0})
            body[scope].append({"run_id": "double", "at": here + 1.0, "seconds": 2.0})
        the_quota_file(gateway.state.root).write_text(json.dumps(body), encoding="utf-8")

        said = the_quota(gateway.state.root).the_figure_for(keys, "wrong", 7.0, ahead)
        assert set(said.values()) == {"set"}, said
        assert all(v == 7.0 for v in
                   the_quota(gateway.state.root).what_a_run_was_charged(keys, "wrong").values())
        twice = the_quota(gateway.state.root).the_figure_for(keys, "double", 7.0, ahead)
        assert set(twice.values()) == {"deduplicated"}, twice
        assert set(the_quota(gateway.state.root).how_many_figures_for(keys, "double").values()) == {1}


# ----------------------------------- SC5, with two real gateway services in two real processes


class TestTwoGatewayServicesInTwoRealProcesses:
    """Round two asked for two gateway services; round three said the quota half proved nothing.

    `STATE-CONSISTENCY-0002`, F-SC5-LEDGER-PROCESSES-NOT-GATEWAYS, got this far: real
    `GatewayService` objects in two processes sharing only the state directory and its lock.

    `STATE-CONSISTENCY-0003`, F-SC5-QUOTA-CONTENTION-DOES-NOT-PROVE-MONOTONICITY, was right about what
    came next: the quota half had each child hand `the_figure_for` an INVENTED number, so "either
    offered value may win" was all it could show, and it reported no pre-write value. Two things follow.

    **First, that was the wrong contention to stage.** No product path invents a quota figure. Both
    gateways read the same signed line and pass the seconds it carries, so the property is not an order
    over values -- the quota is a CACHE OF AN AUTHORITY, and a cache has no monotone order of its own.
    The property is **equality to the authority under contention**: whatever the interleaving, the file
    ends with exactly one figure and it equals the signed line's. So the children now contend through
    `reconcile_every_record_against_the_signed_log`, which is the product's own path, and each reports
    the figure it saw before and after.

    **Second, the facts that DO have an order are reported from both sides.** Each child says what it
    saw before writing, so monotonicity is observable per child rather than assumed from an order the
    barrier exists to remove.

    `recover=False` here, and `TestTwoGatewaysRecoveringAtOnce` below is the other half round three
    named as still owed.
    """

    CHILD = textwrap.dedent('''
        import json, os, sys, time

        root, run_id, word, cleanup, barrier, mode = sys.argv[1:7]
        rest = sys.argv[7:]
        # How long this child spends INSIDE the target run's close, so the contended region is long
        # enough that the other child's necessarily falls inside it. Fault injection, deterministic.
        slow_ms = float(rest[0]) if rest else 0.0
        # A file this child touches once its region has ended, and a file it waits for before starting
        # its own. Together they FORCE THE TWO REGIONS APART, which is the control for the overlap
        # measurement: with them set, the measurement must refuse.
        after_me = rest[1] if len(rest) > 1 else ""
        wait_for = rest[2] if len(rest) > 2 else ""
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService
        from tests.test_em3c_gateway import StandInBackend

        state = GatewayState(root, version="test")

        def ready_and_wait():
            # A HANDSHAKE, NOT JUST A BARRIER -- see the note in the Ledger-only child above.
            open(barrier + ".ready." + str(os.getpid()), "w").close()
            until = time.time() + 60
            while not os.path.exists(barrier) and time.time() < until:
                time.sleep(0.002)

        # WHERE THE HANDSHAKE GOES DEPENDS ON WHAT IS BEING CONTENDED, and getting that wrong was
        # STATE-CONSISTENCY-0004's F-RECOVERY-HANDSHAKE-AFTER-RECOVERY: constructing a GatewayService
        # RUNS CRASH RECOVERY, so announcing readiness afterwards leaves the recovery itself outside
        # the barrier and process startup serialises the very thing the test is about. The review
        # called it "the same measurement-shape error the arc explicitly set out to eliminate", and it
        # was right.
        #
        #   recover  the contended work IS the constructor, so the handshake comes FIRST
        #   contend  the contended work is the explicit writes below, so the object is built first and
        #            the handshake comes after it, which is what makes the writes overlap
        # AND THE WINDOW IS RECORDED, because no product property can establish that two processes
        # OVERLAPPED. STATE-CONSISTENCY-0004 doubted the recovery contest was a contest, and measuring
        # it (raw/S19-03, cases C and D) showed the handshake order makes no difference to whether the
        # test catches a second line -- the meter would refuse one even if the two recoveries never
        # met. So overlap is its own measurement: each child says when its contended work began and
        # ended, and the test asserts there was an instant when both were inside their own window.
        #
        # AND THE WINDOW MUST BE THE CONTENDED REGION, NOT THE WHOLE CONSTRUCTOR.
        # STATE-CONSISTENCY-0005, F-RECOVERY-WINDOW-IS-WIDER-THAN-CONTENTION: "those constructor
        # windows can overlap while the actual lock-protected recovery of the target run occurs
        # sequentially". Correct -- a constructor does lifecycle, journal pickup and more besides, so
        # two overlapping constructors say nothing about the one operation this test is about. The
        # window is now the target run's own close: `_close_an_interrupted_run` for THIS run_id, which
        # is where the line is written and the record settled.
        said_began = said_ended = 0.0
        marks = []
        if mode == "recover":
            closing = GatewayService._close_an_interrupted_run

            def timed(service_self, record, entry, *a, **kw):
                rid = str(getattr(record, "run_id", "") or (entry or {}).get("run_id") or "")
                if rid != run_id:
                    return closing(service_self, record, entry, *a, **kw)
                begin = time.time()
                # Spend `slow_ms` INSIDE the region, before doing the work, so the other child's
                # attempt at the same run necessarily happens while this one is still in here. Without
                # it the region is sub-millisecond and whether the two meet is up to the scheduler --
                # which is how a contention test becomes a coin toss that usually passes.
                if slow_ms:
                    time.sleep(slow_ms / 1000.0)
                try:
                    return closing(service_self, record, entry, *a, **kw)
                finally:
                    marks.append([begin, time.time()])

            GatewayService._close_an_interrupted_run = timed
            ready_and_wait()
            # AFTER the handshake, so the parent is never waiting for a child that is waiting for a
            # file the other child writes after the barrier.
            if wait_for:
                until = time.time() + 60
                while not os.path.exists(wait_for) and time.time() < until:
                    time.sleep(0.002)
            service = GatewayService(state, backend=StandInBackend(), recover=True)
            if marks:
                said_began = min(b for b, _ in marks)
                said_ended = max(e for _, e in marks)
            if after_me:
                open(after_me, "w").close()
        else:
            service = GatewayService(state, backend=StandInBackend(), recover=False)
            ready_and_wait()
            said_began = time.time()

        said = {"pid": os.getpid(), "mode": mode}

        def figures():
            return service.use.what_a_run_was_charged(["dev-1"], run_id)

        was = service.ledger.run_entry(run_id) or {}
        said["settled_before"] = was.get("settled_as") or ""
        said["cleanup_before"] = was.get("cleanup")
        said["quota_before"] = figures().get("dev-1")

        if mode != "recover":
            said["lifecycle"] = service.ledger.note_lifecycle(run_id, "running")
            settled, wrote = service.ledger.note_it_settled(run_id, word)
            said["settled_as"] = settled
            said["wrote_the_word"] = wrote
            said["cleanup"] = service.ledger.note_cleanup(run_id, json.loads(cleanup))

        # THE PRODUCT'S OWN PATH for the quota, not an invented figure: it reads the signed log and
        # sets what the line says. Both children run it, so whatever the interleaving the file must
        # end with one figure equal to that line.
        said["reconciled"] = service.reconcile_every_record_against_the_signed_log()
        after = service.ledger.run_entry(run_id) or {}
        said["settled_after"] = after.get("settled_as") or ""
        said["cleanup_after"] = after.get("cleanup")
        said["quota_after"] = figures().get("dev-1")
        if mode != "recover":
            said_ended = time.time()
        said["began"] = said_began
        said["ended"] = said_ended
        said["marks"] = marks
        said["window_is"] = ("the target run's own close" if mode == "recover"
                             else "the explicit contended writes")
        print("RESULT " + json.dumps(said))
    ''')

    def _run_two(self, gateway, tmp_path, run_id, first, second, mode="contend",
                 slow_ms=0.0, force_apart=False, require_overlap=True):
        """Two real processes, released together, each reporting the window of its contended work.

        `slow_ms` is spent inside the FIRST child's contended region, so the second child's falls
        inside it by construction rather than by luck. `force_apart` makes the second child wait until
        the first has left its region, which is the control for the overlap measurement: the windows
        are then disjoint and `_they_overlapped` must refuse them.
        """
        import subprocess

        program = tmp_path / "contend.py"
        program.write_text(self.CHILD, encoding="utf-8")
        barrier = tmp_path / "go"
        done = tmp_path / ("left-the-region-" + run_id)
        here = str(pathlib.Path(__file__).resolve().parents[1])
        env = dict(os.environ)
        env["PYTHONPATH"] = here
        root = str(pathlib.Path(gateway.state.root))

        started = []
        for index, (word, cleanup) in enumerate((first, second)):
            extra = [str(slow_ms if index == 0 else 0.0),
                     str(done) if (force_apart and index == 0) else "",
                     str(done) if (force_apart and index == 1) else ""]
            started.append(subprocess.Popen(
                [sys.executable, str(program), root, run_id, word, json.dumps(cleanup),
                 str(barrier), mode] + extra,
                cwd=here, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env))
        release_when_both_are_ready(started, barrier, tmp_path)
        said = []
        for proc in started:
            out, err = proc.communicate(timeout=180)
            assert proc.returncode == 0, "a child failed:\n%s" % (err[-1500:],)
            line = [x for x in out.splitlines() if x.startswith("RESULT ")]
            assert line, "a child printed no result:\n%s\n%s" % (out[-800:], err[-800:])
            said.append(json.loads(line[-1][len("RESULT "):]))
        assert len({s["pid"] for s in said}) == 2, "the children shared a process: %r" % (said,)
        assert str(os.getpid()) not in {str(s["pid"]) for s in said}, "a child ran in this test"
        if require_overlap:
            self._they_overlapped(said)
        return said

    @staticmethod
    def _they_overlapped(said):
        """There was an instant when both children were inside their own contended window.

        Without this the whole class could be measuring a sequence, and a sequence passing is not the
        same statement as a contention passing. It is checked here rather than in each test so that
        every contest gets it, including the recovery one -- which is where round four doubted it, and
        rightly: no product property can establish overlap, because the meter would refuse a second
        line even if the two recoveries never met.
        """
        windows = [(float(s.get("began") or 0.0), float(s.get("ended") or 0.0)) for s in said]
        # NOTHING MEASURED is a different failure from NO OVERLAP, and in recovery mode it has a
        # specific cause worth naming: a child that never entered the target run's close reports no
        # window at all, and a test that read that as "did not overlap" would be describing the wrong
        # thing. STATE-CONSISTENCY-0005 asked for the instrument to be the contended region itself, so
        # the instrument has to be able to say that it never ran.
        for s, (b, e) in zip(said, windows):
            assert b > 0 and e >= b, (
                "a child measured NOTHING: it never entered %s. said=%r"
                % (s.get("window_is") or "its contended region", s))
        latest_start = max(b for b, _ in windows)
        earliest_end = min(e for _, e in windows)
        assert latest_start <= earliest_end, (
            "the two children did not overlap: windows %r. The later one began %.3fs after the "
            "earlier one had finished, so this measured a sequence and not a contention."
            % (windows, latest_start - earliest_end))

    def test_every_persisted_fact_is_monotone_across_two_gateway_processes(self, gateway, tmp_path):
        arrived = time.time() - 300.0
        claimed(gateway, "contended", when=arrived, started=arrived + 1.0)
        a_signed_line(gateway.state.root, "contended", state="interrupted", outcome="unverified",
                      queued_at=arrived)
        line = the_one_line_for(gateway.state.root, "contended")
        the_quota(gateway.state.root).note_every(["dev-1"], "contended", now=arrived)

        said = self._run_two(gateway, tmp_path, "contended",
                             ("interrupted", True), ("finished", None))

        # THE OUTCOME: exactly one child may have written the word, and both agree afterwards.
        wrote = [s for s in said if s["wrote_the_word"]]
        assert len(wrote) == 1, (
            "%d of two gateway processes believed it wrote the outcome: %r" % (len(wrote), said))
        winner = wrote[0]["settled_as"]
        assert {s["settled_as"] for s in said} == {winner}, (
            "the two processes disagree about the outcome in the file: %r" % (said,))
        entry = gateway.ledger.run_entry("contended") or {}
        assert entry.get("settled_as") == winner
        assert entry.get("state") == "closed"

        # PER CHILD, so no order is assumed: whoever SAW a fact must be told that fact.
        for s in said:
            if s["settled_before"]:
                assert s["settled_as"] == s["settled_before"], (
                    "a process saw %r and was told %r" % (s["settled_before"], s["settled_as"]))
                assert not s["wrote_the_word"]
            if s["cleanup_before"] is True:
                assert s["cleanup"] is True, (
                    "a process saw cleanup already True and its None was applied: %r" % (s,))
            if s["quota_before"] is not None:
                assert float(s["quota_before"]) in (0.0, float(line["seconds"])), (
                    "a process saw a quota figure that is neither the claim nor the signed one: %r"
                    % (s,))

        # THE CLEANUP: True is absorbing, whichever process got there first.
        assert entry.get("cleanup") is True, (
            "an established cleanup was weakened across processes to %r" % (entry.get("cleanup"),))

        # THE QUOTA: one figure, and it EQUALS the signed line -- which is the property a cache of an
        # authority has. Both children went through the product's own reconciliation, so neither
        # invented a number, and both must see the same thing afterwards.
        counts = the_quota(gateway.state.root).how_many_figures_for(["dev-1"], "contended")
        assert counts.get("dev-1") == 1, (
            "the scope holds %r figures for one run after two processes reconciled" % (counts,))
        charged = the_quota(gateway.state.root).what_a_run_was_charged(["dev-1"], "contended")
        assert float(charged["dev-1"]) == float(line["seconds"]), (
            "the quota holds %r and the signed line says %r" % (charged, line["seconds"]))
        for s in said:
            assert s["quota_after"] is not None and \
                float(s["quota_after"]) == float(line["seconds"]), (
                "a process ended seeing %r while the line says %r" % (s["quota_after"],
                                                                     line["seconds"]))

    def test_the_measurement_refuses_two_recoveries_that_were_forced_apart(self, gateway, tmp_path):
        """The control for the instrument, and it is a real serialisation rather than a synthetic one.

        `STATE-CONSISTENCY-0005`, F-RECOVERY-WINDOW-IS-WIDER-THAN-CONTENTION, ended with the thing to
        supply: *"Instrument the actual target-run recovery/lock region and supply a negative control
        that forces those regions apart."* The region moved to the target run's own close; this is the
        control. The second child waits until the first has LEFT that region, so the two cannot have
        met, and the measurement must say so.

        Without this, the overlap assertion could be satisfied by two windows that always overlap for
        a reason that has nothing to do with contention -- and nothing in the suite would notice. The
        synthetic unit control below proves the interval arithmetic; this proves the timestamps
        delimit the operation, which is the half the arithmetic cannot reach.
        """
        arrived = time.time() - 300.0
        claimed(gateway, "forced-apart", when=arrived, started=arrived + 1.0)
        the_quota(gateway.state.root).note_every(["dev-1"], "forced-apart", now=arrived)

        driver = TestTwoGatewayServicesInTwoRealProcesses()
        said = driver._run_two(
            gateway, tmp_path, "forced-apart", ("interrupted", True), ("interrupted", True),
            mode="recover", slow_ms=400.0, force_apart=True, require_overlap=False)

        # WHAT SERIALISATION ACTUALLY LOOKS LIKE HERE, and it is not what I expected when I wrote this
        # control. Forced apart, the second child does not merely enter the region late -- it never
        # enters it at all, because the first has already closed the run and there is nothing left to
        # close. So the two worlds are observably different in the instrument:
        #
        #   released together  BOTH children report a region (the test above asserts it)
        #   forced apart       EXACTLY ONE does, and the other has nothing to recover
        #
        # Either way the measurement must refuse, and it must refuse with the right reason, which is
        # why "measured NOTHING" has a message of its own.
        entered = [s for s in said if s.get("marks")]
        assert len(entered) == 1, (
            "forcing the regions apart left %d of two children inside the region: %r"
            % (len(entered), said))
        assert entered[0]["pid"] == said[0]["pid"], (
            "the child that ran first is not the one that did the work: %r" % (said,))
        with pytest.raises(AssertionError) as refused:
            driver._they_overlapped(said)
        assert "measured NOTHING" in str(refused.value), str(refused.value)
        # And the second child's recovery was not merely silent: it agreed with what it found.
        assert said[1]["settled_after"] == said[0]["settled_after"], said

        # And the product still held while they were apart: one line, one figure, one answer. A
        # control that breaks the thing it is controlling proves nothing about the instrument.
        got = lines_for(gateway.state.root, "forced-apart")
        assert len(got) == 1, "two serialised recoveries wrote %d lines" % len(got)
        counts = the_quota(gateway.state.root).how_many_figures_for(["dev-1"], "forced-apart")
        assert counts.get("dev-1") == 1, counts

    def test_the_overlap_check_refuses_two_windows_that_do_not_meet(self):
        """The control for the overlap assertion, and it needs no real serialisation to be engineered.

        I first tried to control it by running the recovery contest with the old barrier, expecting the
        children to be serialised and the assertion to refuse. It passed, and the passing was CORRECT:
        in recovery mode the contended work comes after the wait in both shapes, so releasing early
        does not serialise it. That expectation is recorded as wrong in `raw/S19-03`.

        So the control is this instead -- hand the check two windows that do not meet and require it to
        refuse, then two that do and require it to accept. An assertion that cannot fail is worth
        nothing, and this is the cheapest honest way to show that it can.
        """
        apart = [{"began": 100.0, "ended": 101.0}, {"began": 102.0, "ended": 103.0}]
        with pytest.raises(AssertionError, match="did not overlap"):
            TestTwoGatewayServicesInTwoRealProcesses._they_overlapped(apart)
        together = [{"began": 100.0, "ended": 103.0}, {"began": 102.0, "ended": 104.0}]
        TestTwoGatewayServicesInTwoRealProcesses._they_overlapped(together)
        # And a child that reported no window at all is refused rather than treated as overlapping --
        # with its own message, because "it never entered the region" and "the regions did not meet"
        # are different findings and reading the first as the second describes the wrong thing.
        with pytest.raises(AssertionError, match="measured NOTHING"):
            TestTwoGatewayServicesInTwoRealProcesses._they_overlapped(
                [{"began": 0.0, "ended": 0.0}, {"began": 102.0, "ended": 104.0}])

    def test_the_same_word_twice_is_a_no_op_in_both_processes(self, gateway, tmp_path):
        """Two processes that both read the one signed line pass the SAME word, and that has to be
        allowed or an idempotent reconciliation could not run twice."""
        arrived = time.time() - 300.0
        claimed(gateway, "agreed", when=arrived, started=arrived + 1.0)
        a_signed_line(gateway.state.root, "agreed", state="interrupted", outcome="unverified",
                      queued_at=arrived)
        said = self._run_two(gateway, tmp_path, "agreed",
                             ("interrupted", True), ("interrupted", True))
        wrote = [s for s in said if s["wrote_the_word"]]
        assert len(wrote) == 1, "the same word was written twice: %r" % (said,)
        assert {s["settled_as"] for s in said} == {"interrupted"}
        entry = gateway.ledger.run_entry("agreed") or {}
        assert entry.get("settled_as") == "interrupted"
        assert not entry.get("settled_conflicts"), (
            "two processes passing the SAME word were recorded as a conflict: %r"
            % (entry.get("settled_conflicts"),))


class TestTwoGatewaysRecoveringAtOnce:
    """The half round three named as still owed: both of them performing startup recovery.

    `STATE-CONSISTENCY-0003`: *"recover=False means it does not exercise two gateways concurrently
    performing startup recovery"*. True, and the reason was given -- two children recovering one
    directory would, in the test above, spend their time destroying each other's state rather than
    contending over it.

    So that contest gets its own test, where destroying each other IS the subject. Two processes both
    construct a gateway with recovery ON, on a directory holding a run that was mid-flight and has no
    signed line. Each will try to close it, ask the worker about its container, write the line and
    settle the record. The invariants are the ones that must hold however that interleaves:

      * exactly ONE signed line for the run -- the meter's property, under real contention
      * both processes agree afterwards about what it says
      * exactly one quota figure, equal to that line
      * an established cleanup is not weakened
    """

    CHILD = TestTwoGatewayServicesInTwoRealProcesses.CHILD

    def test_one_line_one_figure_and_one_answer(self, gateway, tmp_path):
        arrived = time.time() - 300.0
        claimed(gateway, "both-recovering", when=arrived, started=arrived + 1.0)
        the_quota(gateway.state.root).note_every(["dev-1"], "both-recovering", now=arrived)
        assert not lines_for(gateway.state.root, "both-recovering"), (
            "premise: no signed line yet, so both children have one to write")

        said = TestTwoGatewayServicesInTwoRealProcesses._run_two(
            TestTwoGatewayServicesInTwoRealProcesses(), gateway, tmp_path, "both-recovering",
            ("interrupted", True), ("interrupted", True), mode="recover", slow_ms=400.0)

        assert all(s["mode"] == "recover" for s in said), said
        # BOTH children were inside the target run's own close, which is what `_run_two` asserted by
        # overlap and this says in the plainest form: two regions, not one. The control below shows the
        # same instrument reporting exactly one when the two are forced apart.
        assert all(s.get("marks") for s in said), (
            "a child never entered the target run's close, so there was no contention to pass: %r"
            % (said,))
        # ONE line, written by one of two processes that both tried.
        got = lines_for(gateway.state.root, "both-recovering")
        assert len(got) == 1, (
            "two gateways recovering one directory wrote %d signed lines for one run" % len(got))
        line = got[0]
        assert line["state"] == "interrupted"

        entry = gateway.ledger.run_entry("both-recovering") or {}
        assert entry.get("settled_as") == line["state"], (
            "the ledger says %r and the one signed line says %r" % (entry.get("settled_as"),
                                                                    line["state"]))
        assert {s["settled_after"] for s in said} == {line["state"]}, (
            "the two processes ended disagreeing about the outcome: %r" % (said,))

        counts = the_quota(gateway.state.root).how_many_figures_for(["dev-1"], "both-recovering")
        assert counts.get("dev-1") == 1, (
            "the scope holds %r figures after two gateways recovered the same run" % (counts,))
        charged = the_quota(gateway.state.root).what_a_run_was_charged(["dev-1"], "both-recovering")
        assert float(charged["dev-1"]) == float(line["seconds"]), (
            "the quota holds %r and the one signed line says %r" % (charged, line["seconds"]))

        assert entry.get("cleanup") is not False, (
            "recovery left the cleanup saying something is still there: %r" % (entry.get("cleanup"),))

    def test_and_doing_it_a_third_time_changes_nothing(self, gateway, tmp_path):
        """Idempotent across processes as well as within one: a third gateway finds nothing to do."""
        arrived = time.time() - 300.0
        claimed(gateway, "third-time", when=arrived, started=arrived + 1.0)
        the_quota(gateway.state.root).note_every(["dev-1"], "third-time", now=arrived)
        TestTwoGatewayServicesInTwoRealProcesses._run_two(
            TestTwoGatewayServicesInTwoRealProcesses(), gateway, tmp_path, "third-time",
            ("interrupted", True), ("interrupted", True), mode="recover")

        ledger_then = digest_of(gateway.ledger.path)
        log_then = digest_of(pathlib.Path(gateway.state.root) / meter.METER_NAME)
        quota_then = digest_of(pathlib.Path(gateway.state.root) / USE_NAME)

        again, state = restarted(gateway)
        try:
            assert len(lines_for(state.root, "third-time")) == 1
            assert digest_of(pathlib.Path(state.root) / meter.METER_NAME) == log_then, (
                "a third start changed the signed log")
            assert digest_of(again.ledger.path) == ledger_then, (
                "a third start changed the ledger")
            assert digest_of(pathlib.Path(state.root) / USE_NAME) == quota_then, (
                "a third start changed the quota")
        finally:
            again.close()
            state.close()


# ------------------------------------------- every read of the ledger, under the lock


class TestEveryReadOfTheLedgerFileIsUnderTheLock:
    """Structural, because the defect it guards is not deterministic enough to drive.

    `Ledger.__init__` was the one `_load()` that took no cross-process lock, and the two-process test
    round two asked for found it: a second gateway starting while the first wrote raised
    `LedgerUnreadable`, which this gateway treats as a reason to REFUSE TO START, about a file that was
    perfectly intact. A write here is a temporary file and a rename over the target, and on Windows a
    read landing inside that rename fails with a sharing violation.

    Reproducing that needs the read to fall in that window, which is luck. What is not luck is the
    shape of the code, and that is what this asks -- in TWO rules, because one was not enough.

    ## Why two rules, after round three found the first claim overstated

    `STATE-CONSISTENCY-0003`, F-LEDGER-AST-GUARD-SCOPE-IS-OVERSTATED: the first version looked only for
    calls to `self._load`, so a read written as `self.path.read_text(...)` anywhere else in the module
    would have been invisible while the submission claimed every read was guarded. Widening it to every
    file-touching expression then flagged the two inside `_load` itself -- correctly by its own rule and
    wrongly about the world, because `_load` is only ever called from under the lock.

    Lexical containment cannot express that. Two rules can:

      RULE 1  only a NAMED set of methods may touch the file at all
      RULE 2  every call to one of those methods is lexically inside a `with ... ProcessLock(...)`

    Rule 1 catches a new direct `open()` or `read_text()` written anywhere else. Rule 2 catches a call
    to one of them from outside the lock, which is the regression that actually happened. Together they
    say what the first version only claimed.

    What neither can see is a read in a DIFFERENT module that opens this path. That limit is written
    here rather than left for a reader to find.
    """

    #: The only methods in this module that may touch the file, and what each is for. Adding a third is
    #: a deliberate edit here rather than a silent way past rule 1.
    MAY_TOUCH = ("_load", "_write_locked")

    #: Every way a module can reach a file. The point of round three's finding is that a guard which
    #: watches one helper does not watch the file.
    REACHES_A_FILE = ("read_text", "read_bytes", "write_text", "write_bytes", "is_file", "exists",
                      "open", "mkstemp", "fdopen", "replace", "unlink", "chmod", "fsync")

    def _tree(self):
        import ast
        import inspect

        from agentnode_sdk.gateway import ledger as ledger_module

        return ast.parse(inspect.getsource(ledger_module)), ledger_module

    def _lock_spans(self, tree):
        """Every line span lexically inside a `with` that holds a `ProcessLock`."""
        import ast

        spans = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                items = " ".join(ast.unparse(i.context_expr) for i in node.items)
                if "ProcessLock" in items:
                    spans.append((node.body[0].lineno, node.end_lineno or node.body[-1].lineno))
        return spans

    def _methods(self, tree):
        import ast

        return {node.name: node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def test_rule_one_only_the_named_methods_touch_the_file(self):
        import ast

        tree, module = self._tree()
        methods = self._methods(tree)
        inside = {name: (m.lineno, m.end_lineno or m.lineno) for name, m in methods.items()
                  if name in self.MAY_TOUCH}
        assert inside, "the named methods do not exist, so this test is reading the wrong module"

        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = ast.unparse(node.func)
            if not any(called.endswith("." + r) or called == r for r in self.REACHES_A_FILE):
                continue
            if any(lo <= node.lineno <= hi for lo, hi in inside.values()):
                continue
            offenders.append((called, node.lineno))
        assert not offenders, (
            "%s reaches the file outside %r, at %r. Every such place would need its own lock, and "
            "the point of keeping them in one set is that there is one place to check."
            % (module.__name__, list(self.MAY_TOUCH), offenders))

    def test_rule_two_every_call_to_them_is_under_the_lock(self):
        import ast

        tree, module = self._tree()
        spans = self._lock_spans(tree)
        assert spans, "nothing in this module takes a ProcessLock, so the question is wrong"

        calls = [(ast.unparse(node.func), node.lineno) for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and any(ast.unparse(node.func).endswith("." + m) for m in self.MAY_TOUCH)]
        assert calls, "nothing calls them, so this test is reading the wrong thing"

        loose = [(c, n) for c, n in calls if not any(lo <= n <= hi for lo, hi in spans)]
        assert not loose, (
            "the ledger file is reached without the cross-process lock at %r in %s. A read that lands "
            "inside another process's rename fails with a sharing violation, and this module turns "
            "that into LedgerUnreadable -- which refuses to start the gateway, about a file that is "
            "intact." % (loose, module.__file__))

    def test_and_the_constructor_in_particular(self):
        """Named separately because that is the one that was wrong, so a regression there fails with
        its own name rather than as a line number in a list."""
        import ast
        import inspect
        import textwrap

        from agentnode_sdk.gateway.ledger import Ledger

        tree = ast.parse(textwrap.dedent(inspect.getsource(Ledger.__init__)))
        spans = self._lock_spans(tree)
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and any(ast.unparse(node.func).endswith("." + m) for m in self.MAY_TOUCH)]
        assert calls, "the constructor no longer reads the file, so this test needs rewriting"
        for node in calls:
            assert any(lo <= node.lineno <= hi for lo, hi in spans), (
                "Ledger.__init__ reaches the file without the cross-process lock")
