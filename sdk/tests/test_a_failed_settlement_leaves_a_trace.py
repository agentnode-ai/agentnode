"""O7: a ledger that could not be settled from the signed line is TOLD, never swallowed in silence.

The signed usage line is the authority on how a run ended, and the ledger caches it. Settling that cache
can fail -- a full disk, a permission, a lock -- and every place that settles it caught the failure and
said nothing (`except Exception: pass`). So an operator had a ledger that disagreed with its own log and no
way to learn why or since when. Measured on main: five situations, all silent (bundle
`o7-a-failed-settlement-leaves-a-trace`, E0003 and E0004).

What is told, and what is not changed (DECISION-0001, from consultation Q0001):

* one stderr line per failed attempt, naming the run by the same 12-character prefix `could_not_record`
  uses, and the failure by its class and a safe one-line detail;
* the paths go on exactly as before: the caller still gets the line's word, a start still serves, a pass
  still settles the next run. A stale cache is not a reason to stop taking work;
* the entry stays unsettled, so the next start settles it. There is no retry in this process and no marker
  beyond stderr.
"""
from __future__ import annotations

import errno
import time

import pytest

from tests.test_interrupted_record import claim, restart, the_one_line_for

SETTLE = "the ledger could not be settled from the signed line"
DID_NOT_RUN = "the start-up reconciliation did not run"
CALLERS_PATH = "C:/a/path/the/caller/wrote/run-0123456789"


def _disk_full():
    # With a filename, because `str(OSError)` appends it -- and the operator's line must never carry it.
    return OSError(errno.ENOSPC, "No space left on device", CALLERS_PATH)


@pytest.fixture(autouse=True)
def a_boot_identity(monkeypatch):
    from agentnode_sdk.gateway import lifecycle

    monkeypatch.setattr(lifecycle, "this_boot", lambda: "the-boot-this-test-runs-under")


@pytest.fixture()
def gateway(tmp_path):
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService
    from tests.test_em3c_gateway import StandInBackend, _store_measurement

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


def _settling_fails_for(monkeypatch, *run_ids, exc=None):
    """Settling these runs from their line fails; every other run settles as it would. Counted."""
    from agentnode_sdk.gateway.ledger import Ledger

    real = Ledger.settle_from_the_signed_line
    calls = []

    def maybe(self, run_id, settled_as, because, at=None):
        if run_id in run_ids:
            calls.append(run_id)
            raise exc if exc is not None else _disk_full()
        return real(self, run_id, settled_as, because, at=at)

    monkeypatch.setattr(Ledger, "settle_from_the_signed_line", maybe)
    return calls


def _lines(err: str, phrase: str) -> list:
    return [x.strip() for x in err.splitlines() if phrase in x]


def _closing(gateway, run_id):
    from agentnode_sdk.gateway.server import RunRecord

    now = time.time()
    claim(gateway, run_id, when=now - 30.0, started=now - 20.0)
    record = RunRecord(run_id=run_id, job_id="j", state="interrupted")
    record.finished_at = time.time()
    return record


def _a_settled_line(gateway, run_id, reason):
    from agentnode_sdk.gateway.protocol import outcome_of
    from tests.test_interrupted_record import a_line

    now = time.time()
    claim(gateway, run_id, when=now - 30.0, started=now - 20.0)
    a_line(gateway.state.root, run_id, state="finished",
           outcome=outcome_of("finished", reason, None), termination_reason=reason)


def test_1_the_interrupted_run_closer_tells_the_operator(gateway, monkeypatch, capsys):
    from agentnode_sdk.gateway.protocol import GATEWAY_LOST

    record = _closing(gateway, "o7-closer-000001")
    capsys.readouterr()
    calls = _settling_fails_for(monkeypatch, "o7-closer-000001")
    said = gateway._close_an_interrupted_run(record, gateway.ledger.run_entry("o7-closer-000001") or {},
                                             reason=GATEWAY_LOST)
    err = capsys.readouterr().err
    told = _lines(err, SETTLE)
    assert calls == ["o7-closer-000001"]
    assert told == ["run o7-closer-00: the ledger could not be settled from the signed line "
                    "(OSError: No space left on device); it will be settled again at the next start"], (
        "the operator was told %r for one failed settlement" % told)
    # And the caller is answered exactly as before: with what the signed line says.
    assert said == the_one_line_for(gateway.state.root, "o7-closer-000001")["state"] == "interrupted"


def test_2_the_handler_that_lost_the_race_tells_the_operator(gateway, monkeypatch, capsys):
    from types import SimpleNamespace

    from agentnode_sdk.gateway.protocol import CANCELLED, EXITED, outcome_of
    from agentnode_sdk.gateway.server import RunRecord
    from tests.test_interrupted_record import a_line

    now = time.time()
    claim(gateway, "o7-raced-0000001", when=now - 30.0, started=now - 20.0)
    a_line(gateway.state.root, "o7-raced-0000001", state="cancelled",
           outcome=outcome_of("cancelled", CANCELLED, None), termination_reason=CANCELLED)
    record = RunRecord(run_id="o7-raced-0000001", job_id="j", state="running")
    record.started_at, record.finished_at = now - 20.0, now
    record.termination_reason, record.exit_code = EXITED, 0
    granted = SimpleNamespace(limits=SimpleNamespace(cpu=1.0, memory_mb=512, wall_clock_s=60))
    capsys.readouterr()
    calls = _settling_fails_for(monkeypatch, "o7-raced-0000001")
    said = gateway.write_down_what_it_used(record, granted, "finished")
    err = capsys.readouterr().err
    told = _lines(err, SETTLE)
    assert calls == ["o7-raced-0000001"]
    assert len(told) == 1 and told[0].startswith("run o7-raced-000: "), (
        "the operator was told %r for one failed settlement" % told)
    assert said == "cancelled", "the handler that lost the race no longer answers with the line's word"


def test_3_the_start_up_reconciliation_tells_once_per_attempt_and_goes_on(gateway, monkeypatch, capsys):
    from agentnode_sdk.gateway.protocol import TIMED_OUT

    _a_settled_line(gateway, "o7-unsettled-001", TIMED_OUT)
    _a_settled_line(gateway, "o7-neighbour-001", TIMED_OUT)
    capsys.readouterr()
    calls = _settling_fails_for(monkeypatch, "o7-unsettled-001")
    again, state = restart(gateway, cleanly=True)
    try:
        err = capsys.readouterr().err
        told = _lines(err, SETTLE)
        assert calls, "the injected failure was never reached"
        assert len(told) == len(calls), (
            "%d failed settlement attempt(s), and the operator was told %d time(s): %r"
            % (len(calls), len(told), told))
        assert all(x.startswith("run o7-unsettled: ") for x in told), told
        # The pass went on to the next run, and the gateway is serving.
        neighbour = again.ledger.run_entry("o7-neighbour-001") or {}
        assert (neighbour.get("settled_as"), neighbour.get("settled_because")) == ("finished", TIMED_OUT)
        assert "o7-unsettled-001" in again.ledger.runs_needing_attention()
    finally:
        again.close()
        state.close()


def test_4_a_reconciliation_without_a_ledger_snapshot_says_so(gateway, monkeypatch, capsys):
    from agentnode_sdk.gateway.ledger import Ledger
    from agentnode_sdk.gateway.protocol import TIMED_OUT

    _a_settled_line(gateway, "o7-snapshot-0001", TIMED_OUT)

    def refuse(self):
        raise _disk_full()

    capsys.readouterr()
    monkeypatch.setattr(Ledger, "snapshot", refuse)
    again, state = restart(gateway, cleanly=True)
    try:
        err = capsys.readouterr().err
        told = _lines(err, DID_NOT_RUN)
        assert told == ["the start-up reconciliation did not run: the ledger could not be read "
                        "(OSError: No space left on device)"], (
            "a start-up reconciliation that could not take the ledger's snapshot told %r" % told)
    finally:
        again.close()
        state.close()


def test_5_a_reconciliation_without_the_signed_log_says_so(gateway, monkeypatch, capsys):
    from agentnode_sdk.gateway import meter

    def refuse(root, *a, **k):
        raise _disk_full()

    capsys.readouterr()
    monkeypatch.setattr(meter, "read", refuse)
    said = gateway.reconcile_every_record_against_the_signed_log()
    err = capsys.readouterr().err
    told = _lines(err, DID_NOT_RUN)
    assert told == ["the start-up reconciliation did not run: the signed log could not be read "
                    "(OSError: No space left on device)"], (
        "a reconciliation that could not read the signed log told %r" % told)
    assert said == {"read": False, "settled": 0, "quota": 0}


@pytest.mark.parametrize("exc, detail", [
    (OSError(errno.EACCES, "Permission\r\ndenied", CALLERS_PATH, CALLERS_PATH + "-2"), "PermissionError: Permission  denied"),
    (ValueError("x" * 400 + "\n"), "ValueError: " + "x" * 160),
], ids=["an-oserror-with-two-filenames", "a-long-message-with-a-newline"])
def test_6_the_detail_is_one_bounded_line_and_never_a_filename(gateway, monkeypatch, capsys, exc, detail):
    from agentnode_sdk.gateway.protocol import GATEWAY_LOST

    record = _closing(gateway, "o7-detail-000001")
    capsys.readouterr()
    _settling_fails_for(monkeypatch, "o7-detail-000001", exc=exc)
    gateway._close_an_interrupted_run(record, gateway.ledger.run_entry("o7-detail-000001") or {},
                                      reason=GATEWAY_LOST)
    err = capsys.readouterr().err
    told = _lines(err, SETTLE)
    assert len(told) == 1, "one failed attempt, told as %r" % told
    assert "(" + detail + ")" in told[0], "the detail was %r" % told[0]
    assert "run-0123456789" not in err and "wrote" not in err, (
        "the operator's line carried a filename the exception held: %r" % err)


def test_7_the_run_stays_findable_and_the_next_start_settles_it(gateway, monkeypatch):
    from agentnode_sdk.gateway.protocol import GATEWAY_LOST

    record = _closing(gateway, "o7-later-0000001")
    _settling_fails_for(monkeypatch, "o7-later-0000001")
    gateway._close_an_interrupted_run(record, gateway.ledger.run_entry("o7-later-0000001") or {},
                                      reason=GATEWAY_LOST)
    assert "o7-later-0000001" in gateway.ledger.runs_needing_attention()
    monkeypatch.undo()
    from agentnode_sdk.gateway import lifecycle

    monkeypatch.setattr(lifecycle, "this_boot", lambda: "the-boot-this-test-runs-under")
    again, state = restart(gateway, cleanly=True)
    try:
        entry = again.ledger.run_entry("o7-later-0000001") or {}
        line = the_one_line_for(state.root, "o7-later-0000001")
        assert (entry.get("settled_as"), entry.get("settled_because")) == (
            line["state"], line["termination_reason"])
    finally:
        again.close()
        state.close()


def test_8_any_other_failure_of_the_pass_is_told_without_guessing_its_part(gateway, monkeypatch, capsys):
    """The catch around the whole pass receives what the pass does not name itself -- here the quota's
    snapshot. It says the pass did not finish, and does not claim it was the ledger or the log."""
    from agentnode_sdk.gateway.allowance import Use
    from agentnode_sdk.gateway.protocol import TIMED_OUT

    _a_settled_line(gateway, "o7-the-quota-001", TIMED_OUT)

    def refuse(self):
        raise _disk_full()

    capsys.readouterr()
    monkeypatch.setattr(Use, "snapshot", refuse)
    again, state = restart(gateway, cleanly=True)
    try:
        err = capsys.readouterr().err
        told = _lines(err, "the start-up reconciliation did not")
        assert told == ["the start-up reconciliation did not finish (OSError: No space left on device)"], (
            "a pass that failed somewhere it does not name told %r" % told)
    finally:
        again.close()
        state.close()
