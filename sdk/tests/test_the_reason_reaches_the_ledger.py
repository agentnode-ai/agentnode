"""The reason the signed line gives reaches the ledger, on every path that settles the ledger FROM the line.

`O4`. `_settle_the_caches_from` handed the ledger the line's WORD and not its REASON, and
`note_it_settled` keeps the first word it is given -- writing an empty reason beside it, which a later
caller offering the real one does not count as a disagreement. So every run whose ledger was settled from
its line had `settled_because == ""` for good, although the line carries `termination_reason`: a run a
restart closed, a run that died between its line and its ledger, and the run whose own handler lost the
race to the meter. Measured before the change: `a1e_review/o4-the-reason-reaches-the-ledger/evidence/E0001`,
`E0002`. The startup pass that reconciles every run with the log also settled the word alone, and only
when no word was there, so an entry the defect had already written was never repaired (consultation
`Q0001`).

What holds now: the reason comes from the same line as the word; an empty reason beside an agreeing word
is filled from the line, and only from the line; a reason that differs is kept and the disagreement
recorded; a word that differs is never given the line's reason.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agentnode_sdk.gateway.protocol import (
    CANCELLED,
    EXITED,
    GATEWAY_LOST,
    TIMED_OUT,
    outcome_of,
)
from tests.test_interrupted_record import a_line, claim, lines_for, restart, the_one_line_for


@pytest.fixture(autouse=True)
def a_boot_identity(monkeypatch):
    """As in `test_interrupted_record.py`: the logic is portable, the boot identity is not."""
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


def _the_ledger_says_what_the_line_says(label, entry, line):
    said = str(line.get("termination_reason") or "")
    held = str((entry or {}).get("settled_because") or "")
    assert str((entry or {}).get("settled_as") or "") == line.get("state"), (
        "%s: the ledger's word %r is not the line's %r"
        % (label, (entry or {}).get("settled_as"), line.get("state")))
    assert held == said, "%s: the line gives the reason %r and the ledger holds %r" % (
        label, said, held)


def _a_finished_line(root, run_id, reason):
    return a_line(root, run_id, state="finished", outcome=outcome_of("finished", reason, 0),
                  termination_reason=reason, exit_code=0)


# ------------------------------------------------------------------- the discriminating tests

def test_1_a_run_a_restart_closes_keeps_its_reason(gateway):
    now = time.time()
    claim(gateway, "t1", when=now - 30.0, started=now - 20.0)
    again, state = restart(gateway, cleanly=True)
    try:
        _the_ledger_says_what_the_line_says(
            "a run a restart closed", again.ledger.run_entry("t1"), the_one_line_for(state.root, "t1"))
    finally:
        again.close()
        state.close()


def test_2_an_interrupted_run_closed_by_its_own_line_keeps_its_reason(gateway):
    from agentnode_sdk.gateway.server import RunRecord

    now = time.time()
    claim(gateway, "t2", when=now - 30.0, started=now - 20.0)
    record = RunRecord(run_id="t2", job_id="j", state="interrupted")
    record.finished_at = time.time()
    gateway._close_an_interrupted_run(record, gateway.ledger.run_entry("t2") or {},
                                      reason=GATEWAY_LOST)
    _the_ledger_says_what_the_line_says(
        "an interrupted run", gateway.ledger.run_entry("t2"),
        the_one_line_for(gateway.state.root, "t2"))


def test_3_a_death_between_the_line_and_the_ledger_is_repaired_with_its_reason(gateway):
    now = time.time()
    claim(gateway, "t3", when=now - 30.0, started=now - 20.0)
    # The line, and nothing in the ledger: what a process that died between the two left behind.
    _a_finished_line(gateway.state.root, "t3", TIMED_OUT)
    again, state = restart(gateway)
    try:
        _the_ledger_says_what_the_line_says(
            "a run taken over after a death between its two writes",
            again.ledger.run_entry("t3"), the_one_line_for(state.root, "t3"))
    finally:
        again.close()
        state.close()


def test_4_the_handler_that_lost_the_race_takes_the_winners_word_and_reason(gateway):
    from agentnode_sdk.gateway.server import RunRecord

    now = time.time()
    claim(gateway, "t4", when=now - 30.0, started=now - 20.0)
    a_line(gateway.state.root, "t4", state="cancelled",
           outcome=outcome_of("cancelled", CANCELLED, None), termination_reason=CANCELLED)
    record = RunRecord(run_id="t4", job_id="j", state="running")
    record.started_at, record.finished_at = now - 20.0, now
    record.termination_reason, record.exit_code = EXITED, 0
    granted = SimpleNamespace(limits=SimpleNamespace(cpu=1.0, memory_mb=512, wall_clock_s=60))
    said = gateway.write_down_what_it_used(record, granted, "finished")
    assert said == "cancelled", "the losing handler was told %r, not the winning line's word" % said
    _the_ledger_says_what_the_line_says(
        "the handler that lost the race", gateway.ledger.run_entry("t4"),
        the_one_line_for(gateway.state.root, "t4"))


def test_5_an_entry_the_defect_already_wrote_is_repaired_at_the_next_reconciliation(gateway):
    now = time.time()
    claim(gateway, "t5", when=now - 30.0, started=now - 20.0)
    _a_finished_line(gateway.state.root, "t5", TIMED_OUT)
    # What the defect left: the right word and no reason.
    gateway.ledger.note_it_settled("t5", "finished", at=now - 5.0)
    before = gateway.ledger.run_entry("t5")
    gateway.reconcile_every_record_against_the_signed_log()
    after = gateway.ledger.run_entry("t5")
    assert after.get("settled_because") == TIMED_OUT, (
        "the reconciliation left the reason out: the line says %r and the ledger still holds %r"
        % (TIMED_OUT, after.get("settled_because")))
    assert after.get("settled_as") == before.get("settled_as") and (
        after.get("settled_at") == before.get("settled_at")), (
        "the reconciliation changed more than the missing reason")
    assert not after.get("settled_conflicts"), "filling an empty reason was recorded as a conflict"


def test_6_a_different_reason_already_held_is_kept_and_the_disagreement_recorded(gateway):
    now = time.time()
    claim(gateway, "t6", when=now - 30.0, started=now - 20.0)
    _a_finished_line(gateway.state.root, "t6", TIMED_OUT)
    gateway.ledger.note_it_settled("t6", "finished", at=now - 5.0, because=EXITED)
    gateway._settle_the_caches_from_the_signed_line("t6")
    entry = gateway.ledger.run_entry("t6")
    assert entry.get("settled_because") == EXITED, "the first reason was overwritten"
    conflicts = entry.get("settled_conflicts") or []
    assert any(c.get("offered_because") == TIMED_OUT and c.get("already_because") == EXITED
               for c in conflicts), (
        "the line gives %r, the ledger holds %r, and no disagreement was recorded: %r"
        % (TIMED_OUT, EXITED, conflicts))


# ------------------------------------------------------------------------------- the controls

def test_7_control_a_different_word_is_never_given_the_lines_reason(gateway):
    now = time.time()
    claim(gateway, "t7", when=now - 30.0, started=now - 20.0)
    a_line(gateway.state.root, "t7", state="cancelled",
           outcome=outcome_of("cancelled", CANCELLED, None), termination_reason=CANCELLED)
    gateway.ledger.note_it_settled("t7", "finished", at=now - 5.0)
    gateway._settle_the_caches_from_the_signed_line("t7")
    gateway.reconcile_every_record_against_the_signed_log()
    entry = gateway.ledger.run_entry("t7")
    assert entry.get("settled_as") == "finished" and entry.get("settled_because") == "", entry
    assert entry.get("settled_conflicts"), "a disagreeing word was not recorded"


def test_8_control_a_run_with_no_line_is_not_settled_by_reconciliation(gateway):
    now = time.time()
    claim(gateway, "t8", when=now - 30.0, started=now - 20.0)
    gateway.reconcile_every_record_against_the_signed_log()
    assert not gateway.ledger.settled_as("t8")


def test_9_control_reconciling_an_agreeing_entry_twice_changes_nothing(gateway):
    now = time.time()
    claim(gateway, "t9", when=now - 30.0, started=now - 20.0)
    _a_finished_line(gateway.state.root, "t9", TIMED_OUT)
    gateway.ledger.note_it_settled("t9", "finished", at=now - 5.0, because=TIMED_OUT)
    gateway.reconcile_every_record_against_the_signed_log()
    first = gateway.ledger.path.read_bytes()
    gateway.reconcile_every_record_against_the_signed_log()
    assert gateway.ledger.path.read_bytes() == first, "a second reconciliation changed the ledger"
    assert not gateway.ledger.run_entry("t9").get("settled_conflicts")


def test_11_a_contradicting_word_is_not_rewritten_at_every_start(gateway):
    """Review round one, F001: an entry whose word contradicts the line, with no reason, was visited
    by every reconciliation, and each visit appended the same conflict and rewrote the ledger."""
    now = time.time()
    claim(gateway, "t11", when=now - 30.0, started=now - 20.0)
    a_line(gateway.state.root, "t11", state="cancelled",
           outcome=outcome_of("cancelled", CANCELLED, None), termination_reason=CANCELLED)
    gateway.ledger.note_it_settled("t11", "finished", at=now - 5.0)
    gateway.reconcile_every_record_against_the_signed_log()
    first = gateway.ledger.path.read_bytes()
    conflicts = len(gateway.ledger.run_entry("t11").get("settled_conflicts") or [])
    gateway.reconcile_every_record_against_the_signed_log()
    again = len(gateway.ledger.run_entry("t11").get("settled_conflicts") or [])
    assert gateway.ledger.path.read_bytes() == first and again == conflicts, (
        "a second reconciliation rewrote the ledger: %d conflict(s), then %d" % (conflicts, again))


def test_10_control_still_exactly_one_closing_line(gateway):
    now = time.time()
    claim(gateway, "t10", when=now - 30.0, started=now - 20.0)
    again, state = restart(gateway, cleanly=True)
    try:
        assert len(lines_for(state.root, "t10")) == 1
    finally:
        again.close()
        state.close()
