"""A timeout is not a finished run, and a job stopped while waiting is not a job with no ending.

WHAT WAS MEASURED, twice.

`EG12` of the beta profile -- *"Cancel, timeout, error, cleanup and restart remove the rules, or
reconstruct them deterministically. Nothing of a finished run is left behind"* -- was left at WARN by the
third acceptance with this reason:

    "All enumerated cleanup paths were driven. The ledger nevertheless labels a timeout as finished,
     making timeout indistinguishable from normal completion to an operator."

And this arc's own `A` track found the same gap in a second place, on the two-host stand: a job stopped
while it was still waiting in the queue got a **signed usage line** and **no terminal word in the ledger
at all**. Its entry stayed `state=accepted settled_as=(none)` permanently, after the gateway had already
told the customer it did not finish. That is finding `A-1`, and it violates the invariant the product
states in its own words at `ledger.unfinished_runs`:

    "After reconciliation every run that has a signed line has `settled_as`, so what is left here is
     exactly the set that has no line -- which is the set a start owes one."

WHERE THE INFORMATION IS, which is the useful part: it is not missing, it is dropped. The signed usage
line already carries `termination_reason` (`gateway/meter.py`), and `protocol.TIMED_OUT` has existed as
long as the protocol. The ledger simply has no field for it: `note_it_settled` takes a word and nothing
else, and `termination_reason` appears in `gateway/ledger.py` exactly zero times.

WHAT THIS FILE ASKS FOR -- `D1`, `D2` and `D3` of this arc's profile: the durable record carries the real
terminal reason; success, error, cancellation and timeout stay distinguishable through the ledger, the
evidence and the billing record, not one of the three only; and there is still exactly one closing line
per run, with cleanup still a precondition for terminal closure. Neither old property is traded for the
new one.

WHAT THE FIRST SIX TESTS GO RED WITH, before the repair, and it is worth being plain about: they ask
`note_it_settled` for a reason and get `TypeError: unexpected keyword argument 'because'`. That is a red
about the API's shape rather than about a stored value -- and here the two are the same statement, because
the defect IS that the durable record has no way to carry the reason. `termination_reason` appears in
`gateway/ledger.py` zero times. The last four tests do not use that keyword: two of them are about the
properties the ledger already has and must keep, and two drive a real gateway.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from agentnode_sdk.gateway import meter
from agentnode_sdk.gateway.ledger import Ledger
from agentnode_sdk.gateway.protocol import CANCELLED, EXITED, TIMED_OUT


def _a_ledger(tmp_path):
    return Ledger(tmp_path / "ledger.json")


def _a_run(ledger, run_id="r", nonce="n"):
    assert ledger.claim(run_id, request_sha256="d" * 64, nonce=nonce,
                        owner_client_id="c", owner_account_id="acct-1"), "the run was not claimed"
    return run_id


def _entry(ledger, run_id):
    data = json.loads(pathlib.Path(ledger.path).read_text(encoding="utf-8"))
    return (data.get("runs") or {}).get(run_id) or {}


class TestTheDurableRecordCarriesTheReason:
    """`D1`: a timeout does not appear merely as `finished`."""

    def test_1_a_timeout_is_not_recorded_as_a_plain_finished_run(self, tmp_path):
        ledger = _a_ledger(tmp_path)
        run = _a_run(ledger)

        ledger.note_it_settled(run, "finished", because=TIMED_OUT)

        entry = _entry(ledger, run)
        assert entry.get("settled_as") == "finished", entry
        assert entry.get("settled_because") == TIMED_OUT, (
            "the durable record does not say a timeout ended this run; it holds %r" % entry)

    def test_2_and_an_ordinary_ending_is_told_apart_from_it(self, tmp_path):
        ledger = _a_ledger(tmp_path)
        run = _a_run(ledger)

        ledger.note_it_settled(run, "finished", because=EXITED)

        entry = _entry(ledger, run)
        assert entry.get("settled_because") == EXITED, (
            "an ordinary ending and a timeout are not told apart: %r" % entry)
        assert entry.get("settled_because") != TIMED_OUT

    def test_3_the_four_endings_are_four_different_readings(self, tmp_path):
        """`D2`, in the ledger: success, error, cancellation and timeout."""
        seen = {}
        for n, (word, because) in enumerate((
                ("finished", EXITED), ("finished", TIMED_OUT),
                ("cancelled", CANCELLED), ("unverified", "runtime_lost"))):
            ledger = _a_ledger(tmp_path / ("l%d" % n))
            pathlib.Path(tmp_path / ("l%d" % n)).mkdir(parents=True, exist_ok=True)
            run = _a_run(ledger, run_id="r%d" % n, nonce="n%d" % n)
            ledger.note_it_settled(run, word, because=because)
            entry = _entry(ledger, run)
            seen[(word, because)] = (entry.get("settled_as"), entry.get("settled_because"))
        assert len(set(seen.values())) == 4, (
            "four different endings produced %d different durable readings: %r"
            % (len(set(seen.values())), seen))


class TestTheReasonObeysTheSameRuleAsTheWord:
    """The ledger's own rule: the word comes from the signed line, never from the caller."""

    def test_4_a_second_different_reason_does_not_overwrite_the_first(self, tmp_path):
        ledger = _a_ledger(tmp_path)
        run = _a_run(ledger)
        ledger.note_it_settled(run, "finished", because=TIMED_OUT)

        ledger.note_it_settled(run, "finished", because=EXITED)

        entry = _entry(ledger, run)
        assert entry.get("settled_because") == TIMED_OUT, (
            "a second caller overwrote the reason the signed line gave: %r" % entry)

    def test_5_and_the_disagreement_is_kept_rather_than_resolved(self, tmp_path):
        ledger = _a_ledger(tmp_path)
        run = _a_run(ledger)
        ledger.note_it_settled(run, "finished", because=TIMED_OUT)
        ledger.note_it_settled(run, "finished", because=EXITED)

        entry = _entry(ledger, run)
        conflicts = entry.get("settled_conflicts") or []
        assert any(str(c.get("offered_because") or "") == EXITED for c in conflicts), (
            "a conflicting reason was dropped instead of recorded: %r" % entry)

    def test_6_and_the_same_reason_twice_is_not_a_conflict(self, tmp_path):
        """Idempotent, like the word: two paths that both read the one line must both be allowed."""
        ledger = _a_ledger(tmp_path)
        run = _a_run(ledger)
        ledger.note_it_settled(run, "finished", because=TIMED_OUT)
        ledger.note_it_settled(run, "finished", because=TIMED_OUT)

        entry = _entry(ledger, run)
        assert not (entry.get("settled_conflicts") or []), (
            "writing the same reason twice was recorded as a disagreement: %r" % entry)


class TestAJobStoppedWhileWaitingGetsAnEndingToo:
    """Finding `A-1` of this arc, which is `D1` in a second place."""

    def test_7_the_ledger_is_settled_for_a_run_that_never_started(self, capped):
        """Measured on the stand before this existed: the entry stayed `accepted` for ever."""
        from tests.test_capacity_standing import _limits, _queued_job
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "stopped-while-waiting")
        record = _queued_job(capped, who)
        # The gateway's ledger has to know about this run before it can settle it.
        assert capped.ledger.claim(record.run_id, request_sha256="d" * 64, nonce="nonce-7",
                                   owner_client_id=who.client_id,
                                   owner_account_id=who.account_id)

        capped.state.accounts.suspend(who.account_id, "we need to talk", by="the operator")
        capped.slots.give_back("somebody-elses-run")
        assert record.slot_ticket.granted.wait(2.0), "it never got its slot"
        assert capped._wait_for_a_slot(record, _limits()) is False

        entry = _entry(capped.ledger, record.run_id)
        assert str(entry.get("settled_as") or ""), (
            "a run that was stopped while waiting has no ending in the durable record at all, "
            "although a signed usage line was written for it: %r" % entry)
        assert str(entry.get("state") or "") != "accepted", (
            "the durable record still calls this run waiting: %r" % entry)

    def test_8_and_the_signed_line_and_the_ledger_agree_about_it(self, capped):
        """`D2` asks for all three to agree, not for two of them."""
        from tests.test_capacity_standing import _limits, _line_for, _queued_job
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "stopped-while-waiting-2")
        record = _queued_job(capped, who)
        assert capped.ledger.claim(record.run_id, request_sha256="d" * 64, nonce="nonce-8",
                                   owner_client_id=who.client_id,
                                   owner_account_id=who.account_id)
        capped.state.accounts.suspend(who.account_id, "a reason", by="the operator")
        capped.slots.give_back("somebody-elses-run")
        record.slot_ticket.granted.wait(2.0)
        capped._wait_for_a_slot(record, _limits())

        line = _line_for(capped.state.root, record.run_id)
        entry = _entry(capped.ledger, record.run_id)
        assert str(entry.get("settled_as") or "") == str(line.get("state") or ""), (
            "the ledger and the signed line disagree about what this run became: ledger %r, "
            "line %r" % (entry.get("settled_as"), line.get("state")))


class TestNeitherOldPropertyIsTraded:
    """`D3`. Green before the repair as well as after."""

    def test_9_there_is_still_exactly_one_closing_line_per_run(self, tmp_path):
        ledger = _a_ledger(tmp_path)
        run = _a_run(ledger)
        # THE OLD SIGNATURE ON PURPOSE. These two tests assert properties the ledger ALREADY has,
        # and must be green before the repair as well as after -- so they must not fail merely
        # because a keyword the repair adds does not exist yet. The reason is what the repair adds;
        # one closing line per run and a recorded disagreement are what it must not trade away.
        first = ledger.note_it_settled(run, "finished")
        second = ledger.note_it_settled(run, "cancelled")

        assert first[1] is True and second[1] is False, (
            "the second call wrote a second ending: %r then %r" % (first, second))
        assert _entry(ledger, run).get("settled_as") == "finished"

    def test_10_and_a_word_that_disagrees_is_still_refused_and_recorded(self, tmp_path):
        ledger = _a_ledger(tmp_path)
        run = _a_run(ledger)
        ledger.note_it_settled(run, "finished")
        ledger.note_it_settled(run, "cancelled")

        conflicts = _entry(ledger, run).get("settled_conflicts") or []
        assert any(str(c.get("offered") or "") == "cancelled" for c in conflicts), (
            "a conflicting WORD stopped being recorded: %r" % conflicts)


# The capped gateway fixture, reused rather than redefined.
from tests.test_capacity_standing import capped  # noqa: E402,F401


class TestARealTimeoutThroughTheWholePath:
    """The product-level half of `D1`, and the reason it needs its own tests.

    Everything above either drives the ledger directly or drives a job that never ran. Neither
    reaches the ORDINARY ending, where the gateway hands the signed line's word and the record's
    reason to the ledger together -- so without these two, the claim "a timeout does not appear
    merely as finished" would rest on a unit test of the ledger's API and on nothing that timed out.

    Designing the counter-check table is what showed that: the guarantee had no test able to go red
    for it.
    """

    def test_11_a_run_the_runtime_reports_as_timed_out_says_so_in_the_ledger(self, gateway):
        from agentnode_sdk.gateway import client as gc
        from tests import consent

        base, state, service, _backend = gateway

        class ATimingOutRun:
            """What a backend returns for a run that hit its wall clock.

            `backend.why_it_stopped` reads `.reason` off whatever a backend returned -- one place,
            so that a backend saying nothing is read as an ordinary exit in exactly one way. This
            says something.
            """

            returncode = 124
            stdout = ""
            stderr = ""
            reason = TIMED_OUT
            native_status = 124
            platform = "linux"

            def __iter__(self):
                # The older bare-tuple shape, so whichever way the caller unpacks it works.
                return iter((self.returncode, self.stdout, self.stderr))

        # PATCHED ON THE STAND-IN ITSELF, not on the product. `service.backend` is a read-only
        # property and nothing in the gateway may use it anyway -- every question about a runtime
        # goes through `worker`, which holds this same object. So the one method that answers for a
        # run is replaced on the object the worker already has.
        service.backend.run_process = (
            lambda spec, input_text=None, timeout=120.0: ATimingOutRun())

        conn = _paired(base, state)
        answer = consent.submit(conn, b"x", network="none", run_id="timed-out-run")
        final = gc.wait_for(conn, answer["run_id"], timeout=20)

        entry = _entry(service.ledger, "timed-out-run")
        assert str(entry.get("settled_as") or "") == final["state"], (
            "the ledger and the client were told different words: %r vs %r"
            % (entry.get("settled_as"), final["state"]))
        assert entry.get("settled_because") == TIMED_OUT, (
            "a run the runtime reported as timed out is recorded with reason %r, so an operator "
            "reading the durable record cannot tell it from an ordinary completion"
            % entry.get("settled_because"))

    def test_12_and_the_signed_line_says_the_same_about_it(self, gateway):
        """`D2`: the ledger, the evidence and the billing record, not one of the three."""
        from agentnode_sdk.gateway import client as gc
        from tests import consent
        from tests.test_capacity_standing import _line_for

        base, state, service, _backend = gateway

        class ATimingOutRun:
            returncode = 124
            stdout = ""
            stderr = ""
            reason = TIMED_OUT
            native_status = 124
            platform = "linux"

            def __iter__(self):
                return iter((self.returncode, self.stdout, self.stderr))

        # PATCHED ON THE STAND-IN ITSELF, not on the product. `service.backend` is a read-only
        # property and nothing in the gateway may use it anyway -- every question about a runtime
        # goes through `worker`, which holds this same object. So the one method that answers for a
        # run is replaced on the object the worker already has.
        service.backend.run_process = (
            lambda spec, input_text=None, timeout=120.0: ATimingOutRun())
        conn = _paired(base, state)
        consent.submit(conn, b"x", network="none", run_id="timed-out-run-2")
        gc.wait_for(conn, "timed-out-run-2", timeout=20)

        line = _line_for(service.state.root, "timed-out-run-2")
        entry = _entry(service.ledger, "timed-out-run-2")
        assert str(line.get("termination_reason") or "") == TIMED_OUT, (
            "the signed line does not say this run timed out: %r" % line)
        assert entry.get("settled_because") == str(line.get("termination_reason") or ""), (
            "the ledger and the signed line disagree about WHY this run ended: %r vs %r"
            % (entry.get("settled_because"), line.get("termination_reason")))


from tests.test_em3c_gateway import _paired, gateway  # noqa: E402,F401
