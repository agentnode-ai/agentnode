"""A run is shown as ended only once its durable record says so.

`server.py` states the rule twice. Above the place `_run` publishes a terminal state: *"A reader that
sees a terminal state must be seeing a complete record."* And above the usage line: it is written
*"BEFORE the terminal state is published, for the same reason cleanup is"*. Two endings broke it:

* `_run` wrote the signed line, PUBLISHED the terminal state, and only then settled the ledger. A client
  polling the run could be told `finished` while the ledger entry still said nothing -- and a gateway
  that died in that gap left an ended run for the next start to close. The settlement was also the one
  durable write with no guard, so a ledger that could not be written killed the run's thread after its
  client had been told it ended, and the gateway went on taking work it could not account for.
* `_end_without_running`, the ending of a job that never got a slot, published FIRST and wrote the
  signed line and the ledger afterwards -- so there even the signed line followed the answer.

Found as a Windows teardown failure (`WinError 32` on `.ledger-*` in CI, observation `O3` of the F12
arc): a test fixture that waits for every run to be terminal and then removes the directory met the
ledger's temporary file, because "terminal" did not yet mean "written".

WHAT EACH TEST GOES RED WITH on the unrepaired build, predeclared here before the run:

* `test_1`, `test_2`, `test_3`, `test_7` -- "was already shown as"
* `test_4`, `test_8`, `test_10`, `test_11` -- "went on taking work"

`test_5`, `test_6` and `test_9` must be GREEN BEFORE AND AFTER. They are the guards on the repair: the
word a client is told still comes from the signed log, and a run whose records cannot be written still
ends rather than leaving its client waiting on something nobody will finish.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time

import pytest

from tests import consent
from tests.test_capacity_standing import _limits, _queued_job  # noqa: F401
from tests.test_capacity_standing import capped  # noqa: F401
from tests.test_em3c_gateway import StandInBackend, _granted, _paired, _store_measurement
from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.protocol import is_terminal
from agentnode_sdk.gateway.server import GatewayService, make_server

PHRASE = "was already shown as"


@pytest.fixture()
def gateway(tmp_path):
    """A real gateway on a real HTTP door, whose sandbox returns at once.

    It CLOSES the service on the way out, which joins every run thread. That is deliberately not
    what `test_allowance.a_gateway` does -- the fixture this defect was found through -- because these
    tests must not depend on the race they are about.
    """
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    service.CONTAINER_APPEAR_SECONDS = 0.5
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state, service
    finally:
        server.shutdown()
        thread.join(timeout=10)
        service.close()
        state.close()


def _submit(conn, service, run_id):
    return consent.submit(conn, b"print('x')", granted=_granted(service), run_id=run_id,
                          wall_clock_s=60)


def _wait_until_terminal(service, run_id, seconds=20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        record = service.runs.get(run_id)
        if record is not None and is_terminal(record.state):
            return record
        time.sleep(0.02)
    raise AssertionError("run %r never reached a terminal state" % run_id)


def _entry(service, run_id) -> dict:
    data = json.loads(pathlib.Path(service.ledger.path).read_text(encoding="utf-8"))
    return (data.get("runs") or {}).get(run_id) or {}


class _WhatTheClientWasToldWhenTheLedgerWasWritten:
    """Wraps `note_it_settled` and, at the moment the durable write BEGINS, asks the gateway -- through
    its own HTTP door, the way a client would -- what state the run is in.

    Asked before the real write is called, never inside it, so the ledger's lock is not held while
    the door is asked.
    """

    def __init__(self, service, conn, run_id):
        self.service, self.conn, self.run_id = service, conn, run_id
        self.told: list = []
        self._real = service.ledger.note_it_settled
        service.ledger.note_it_settled = self

    def __call__(self, run_id, *args, **kwargs):
        if str(run_id) == self.run_id:
            self.told.append(str(gc.status_of(self.conn, run_id).get("state") or ""))
        return self._real(run_id, *args, **kwargs)


class TestARunThatRanIsWrittenDownBeforeItIsShown:
    """`_run`: the ledger is settled before the terminal state is published."""

    def test_1_a_client_is_not_told_it_ended_before_the_ledger_says_so(self, gateway):
        base, state, service = gateway
        conn = _paired(base, state)
        spy = _WhatTheClientWasToldWhenTheLedgerWasWritten(service, conn, "ran-and-recorded")

        _submit(conn, service, "ran-and-recorded")
        _wait_until_terminal(service, "ran-and-recorded")
        service.close()                       # every run thread joined: the ledger write is over

        assert spy.told, "the ledger was never settled for this run, so nothing was measured"
        shown = [s for s in spy.told if is_terminal(s)]
        assert not shown, (
            "the run %s %r through the client's own door when the ledger began to record it -- "
            "so a client could be told it ended while the durable record said nothing"
            % (PHRASE, shown[0]))

    def test_2_and_once_it_is_shown_the_ledger_already_has_it(self, gateway):
        """The reader's half of the same rule, without any spy: the first moment the run is terminal,
        the ledger entry is settled with the same word. The ledger write is slowed so the gap, if
        there is one, is wide enough to land in every time rather than by luck."""
        base, state, service = gateway
        conn = _paired(base, state)
        real = service.ledger.note_it_settled

        def slowly(run_id, *args, **kwargs):
            time.sleep(0.5)
            return real(run_id, *args, **kwargs)

        service.ledger.note_it_settled = slowly

        _submit(conn, service, "seen-then-read")
        record = _wait_until_terminal(service, "seen-then-read")
        seen = record.state
        settled = str(_entry(service, "seen-then-read").get("settled_as") or "")

        assert settled == seen, (
            "the run %s %r while the ledger's settled_as was %r" % (PHRASE, seen, settled or "(none)"))


class TestAJobThatNeverRanIsWrittenDownBeforeItIsShown:
    """`_end_without_running`: the signed line and the ledger come before the terminal state."""

    @staticmethod
    def _a_withdrawn_waiting_job(service, name):
        from tests.test_two_accounts import _a_customer

        who = _a_customer(service, name)
        record = _queued_job(service, who, run_id=name)
        assert record.slot_ticket is not None and not record.slot_ticket.dropped, (
            "this job did not have to wait, so nothing below is about a waiting job")
        assert service.ledger.claim(record.run_id, request_sha256="d" * 64, nonce="n-" + name,
                                    owner_client_id=who.client_id,
                                    owner_account_id=who.account_id)
        assert service.state.revoke_client(who.client_id), "the device was not withdrawn at all"
        return record

    @staticmethod
    def _let_it_through(service, record):
        service.slots.give_back("somebody-elses-run")
        assert record.slot_ticket.granted.wait(2.0), "it never got its slot"
        return service._wait_for_a_slot(record, _limits())

    def test_3_the_line_and_the_ledger_come_first(self, capped):
        record = self._a_withdrawn_waiting_job(capped, "withdrawn-while-waiting")
        seen = {}
        real_line = capped.write_down_what_it_used
        real_ledger = capped.ledger.note_it_settled

        def the_line(rec, *args, **kwargs):
            seen.setdefault("line", rec.state)
            return real_line(rec, *args, **kwargs)

        def the_ledger(run_id, *args, **kwargs):
            seen.setdefault("ledger", record.state)
            return real_ledger(run_id, *args, **kwargs)

        capped.write_down_what_it_used = the_line
        capped.ledger.note_it_settled = the_ledger

        assert self._let_it_through(capped, record) is False, "a withdrawn device's job started"

        assert set(seen) == {"line", "ledger"}, "a durable write never happened: %r" % seen
        shown = {what: state for what, state in seen.items() if is_terminal(state)}
        assert not shown, (
            "the job %s %r before its durable record was written (%s)"
            % (PHRASE, record.state, ", ".join(sorted(shown))))
        assert record.state == "refused"
        assert _entry(capped, record.run_id).get("settled_as") == "refused"


class TestALedgerThatCannotBeWritten:
    """The settlement had no guard. With one, a failed write stops the gateway -- durably and visibly,
    as a failed usage line already does -- and the run still ends, because a client waiting forever on
    a run nobody will finish is the worse of the two."""

    def test_4_stops_the_gateway(self, gateway):
        from agentnode_sdk.gateway.allowance import why_it_is_stopped

        base, state, service = gateway
        conn = _paired(base, state)

        def it_cannot_be_written(run_id, *args, **kwargs):
            raise OSError("the disk is full")

        service.ledger.note_it_settled = it_cannot_be_written

        _submit(conn, service, "unrecordable")
        _wait_until_terminal(service, "unrecordable")
        service.close()

        assert why_it_is_stopped(service.state.root), (
            "a gateway whose ledger could not record an ended run went on taking work")

    def test_5_and_the_run_still_ends(self, gateway):
        base, state, service = gateway
        conn = _paired(base, state)

        def it_cannot_be_written(run_id, *args, **kwargs):
            raise OSError("the disk is full")

        service.ledger.note_it_settled = it_cannot_be_written

        _submit(conn, service, "unrecordable-but-ended")
        record = _wait_until_terminal(service, "unrecordable-but-ended")
        assert is_terminal(record.state)


class TestASignedLineThatCannotBeWritten:
    """`write_down_what_it_used` caught `OSError` itself and returned nothing, so the guard around it
    -- whose comment says a gateway that cannot write down what it ran "must not go on running
    things" -- never saw the commonest way a write fails. Found by the independent consultation
    before this change (`Q0001`), not by me."""

    @staticmethod
    def _the_meter_cannot_write(monkeypatch):
        from agentnode_sdk.gateway import meter

        def full(*args, **kwargs):
            raise OSError("the disk is full")

        monkeypatch.setattr(meter, "record", full)

    def test_8_after_a_run_stops_the_gateway(self, gateway, monkeypatch):
        from agentnode_sdk.gateway.allowance import why_it_is_stopped

        base, state, service = gateway
        conn = _paired(base, state)
        self._the_meter_cannot_write(monkeypatch)

        _submit(conn, service, "no-line-written")
        _wait_until_terminal(service, "no-line-written")
        service.close()

        assert why_it_is_stopped(service.state.root), (
            "a gateway that could not write the signed line for a run went on taking work")

    def test_9_and_the_run_still_ends(self, gateway, monkeypatch):
        base, state, service = gateway
        conn = _paired(base, state)
        self._the_meter_cannot_write(monkeypatch)

        _submit(conn, service, "no-line-but-ended")
        assert is_terminal(_wait_until_terminal(service, "no-line-but-ended").state)

    def test_10_for_a_job_that_never_ran_stops_the_gateway_too(self, capped, monkeypatch):
        from agentnode_sdk.gateway.allowance import why_it_is_stopped

        record = TestAJobThatNeverRanIsWrittenDownBeforeItIsShown._a_withdrawn_waiting_job(
            capped, "no-line-for-the-waiting-job")
        self._the_meter_cannot_write(monkeypatch)

        TestAJobThatNeverRanIsWrittenDownBeforeItIsShown._let_it_through(capped, record)

        assert is_terminal(record.state), "the job that never ran was left without an ending"
        assert why_it_is_stopped(capped.state.root), (
            "a gateway that could not write the signed line for a job that never ran went on "
            "taking work")


class TestALedgerThatAlreadySaysSomethingElse:
    """`note_it_settled` keeps the first word and returns it. The ledger copies the signed log, so a
    different word already there means two paths read two different things -- and `_run` called it
    and went on as though it had established its own word."""

    def test_11_stops_the_gateway_and_tells_the_client_the_signed_word(self, gateway):
        from agentnode_sdk.gateway.allowance import why_it_is_stopped

        base, state, service = gateway
        conn = _paired(base, state)
        real_line = service.write_down_what_it_used

        def somebody_settled_it_already(rec, granted, terminal):
            service.ledger.note_it_settled(rec.run_id, "interrupted")
            return real_line(rec, granted, terminal)

        service.write_down_what_it_used = somebody_settled_it_already

        _submit(conn, service, "two-words")
        record = _wait_until_terminal(service, "two-words")
        service.close()

        assert record.state == "finished", (
            "the client was told %r; the signed line says 'finished'" % record.state)
        assert why_it_is_stopped(service.state.root), (
            "a ledger that contradicts the signed line was left as it was, and the gateway went on "
            "taking work")


class TestTheWordStillComesFromTheSignedLog:
    """Guard: reordering must not let the handler publish its own word over the log's."""

    def test_6_a_line_already_written_by_someone_else_is_what_is_published(self, capped):
        """A stop that already wrote this run's line wins: the job that never ran publishes the
        log's word and the ledger carries it, whichever path got there second."""
        record = TestAJobThatNeverRanIsWrittenDownBeforeItIsShown._a_withdrawn_waiting_job(
            capped, "already-closed-by-a-stop")
        real_line = capped.write_down_what_it_used

        def a_stop_got_there_first(rec, granted, terminal):
            real_line(rec, granted, "cancelled")
            return real_line(rec, granted, terminal)

        capped.write_down_what_it_used = a_stop_got_there_first

        TestAJobThatNeverRanIsWrittenDownBeforeItIsShown._let_it_through(capped, record)

        settled = _entry(capped, record.run_id).get("settled_as")
        assert settled == "cancelled", "the ledger says %r, the log says 'cancelled'" % settled

    def test_7_and_the_client_is_told_that_word_too(self, capped):
        """The same race, from the client's side. Publishing first meant the job that never ran told
        its client its OWN word even when the log already held another one -- the disagreement
        `_run` was repaired for in `state-consistency-r1`, still open on this path."""
        record = TestAJobThatNeverRanIsWrittenDownBeforeItIsShown._a_withdrawn_waiting_job(
            capped, "told-what-the-log-says")
        real_line = capped.write_down_what_it_used

        def a_stop_got_there_first(rec, granted, terminal):
            real_line(rec, granted, "cancelled")
            return real_line(rec, granted, terminal)

        capped.write_down_what_it_used = a_stop_got_there_first

        TestAJobThatNeverRanIsWrittenDownBeforeItIsShown._let_it_through(capped, record)

        assert record.state == "cancelled", (
            "the job %s %r while the signed log says 'cancelled'" % (PHRASE, record.state))
