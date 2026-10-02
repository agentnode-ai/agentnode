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
        a_signed_line(gateway.state.root, "twice-over", state="interrupted", outcome="unverified")
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
        a_signed_line(gateway.state.root, "quota-gone", state="interrupted", outcome="unverified",
                      started_at=1010.0, finished_at=1021.0)
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
        a_signed_line(gateway.state.root, "exactly-one-figure", state="finished")
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


class TestTwoGatewaysInTwoRealProcesses:
    """`SC5` names a second gateway, and the first submission could not establish it.

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

        path, word, barrier = sys.argv[1], sys.argv[2], sys.argv[3]
        held = Ledger(path)
        # Both processes wait for the same file to appear, so they reach the write together rather
        # than one after the other. Without it this would be a sequence and not a contention.
        import os, time
        until = time.time() + 20
        while not os.path.exists(barrier) and time.time() < until:
            time.sleep(0.005)
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
        barrier.write_text("go", encoding="utf-8")
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
