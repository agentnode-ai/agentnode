"""A job stopped before it ever ran has a reason, and the durable record has to carry it.

WHY THIS FILE EXISTS, and it is a correction rather than an extension.

`D1` of this arc's profile asks that *"the durable, signed and chained closing record carries the actual
terminal reason. A timeout must not appear merely as `finished`."* The first repair of this arc made the
timeout half true: `settled_because=timeout` where before there was nothing. Measured on the two-host
stand (`E0231`), the rest of that ledger then read:

    cancelled    cancelled    1
    finished     exited       8
    finished     timeout      1
    refused      (none)       3

The three `refused` entries are the jobs stopped **while they were waiting** -- one account suspended,
one device withdrawn, one gateway told to stop taking work. Each customer was told the true cause in
words. The durable record carries **no reason at all** for any of them.

I argued in `writing/THE-FINDINGS.md` (`D-3`) that this was the refusal vocabulary rather than `D1`, on
the grounds that `TERMINATION_REASONS` answers *why a run that was running stopped* and that inventing a
word would make the record unreadable, because `what_disagrees` refuses any reason outside that tuple.
The independent review of round one rejected that reading, and it is right:

    "A timeout is now distinguished, but D1's first sentence requires the durable closing record to
     carry the actual terminal reason generally. Three measured terminal refusals have no reason at
     all. The timeout sentence is an example, not a limitation of the criterion."

So the vocabulary gains the words it is missing. The constraint my own argument identified is real and
is what shapes the repair: a word that is not in `TERMINATION_REASONS` does not record a cause, it makes
the record **unreadable** -- `what_disagrees` reports it and every evidence bundle carrying it raises an
`EVIDENCE_ERROR`. `test_5` is that constraint, as a test.

WHAT EACH TEST GOES RED WITH on the unrepaired build, predeclared here before the run:

* `test_1` -- "a withdrawn device's waiting job settled with no terminal reason at all"
* `test_2` -- "an unreadable enrolment left the record with no terminal reason"
* `test_3` -- "a suspended account's waiting job settled with no terminal reason at all"
* `test_4` -- "a gateway that stopped taking work left the record with no terminal reason"
* `test_5` -- "is not a reason this build knows", raised by the product's own rule against the words
  this file pins
* `test_6` -- "the ledger did not carry the reason". Its first version went red with
  `FileNotFoundError` instead, because its own fixture never claimed the run in the ledger; see
  the comment in the test and finding `D-5`.
* `test_8` -- "two different stops wrote the same word"
* `test_9` -- "a refusal with no specific word wrote nothing at all"
* `test_10` -- "an invented word was stored, so the terminal record cannot be read back"

`test_7` must be **GREEN BEFORE AND AFTER**. It is not about the repair; it is the guard on it. Adding a
reason to these endings must not change the OUTCOME a customer is told, because `outcome` is a field
clients branch on and this repair is about `termination_reason`. A repair that quietly moved every
refusal from `failed` to something else would be a client-visible change smuggled in under `D1`.

THE WORDS ARE WRITTEN AS LITERALS HERE, not imported from the protocol. Two reasons. A test that imports
a constant which does not exist yet goes red with `ImportError`, which is a red about this file rather
than about the product. And these words go into a signed record that clients read, so pinning them as
text is the point: if somebody renames one, this file says so.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tests.test_capacity_standing import _limits, _line_for, _queued_job  # noqa: F401
from tests.test_capacity_standing import capped  # noqa: F401

#: The word each stop has to leave behind, as it must appear in the record. Pinned as text.
WITHDRAWN = "device_withdrawn"
UNREADABLE = "enrolment_unreadable"
SUSPENDED = "account_suspended"
NOT_TAKING_WORK = "not_taking_work"
#: The honest coarse word, for a refusal at the slot whose specific cause has no word of its own. It is
#: not a guess: it is a true statement at a coarser grain, with the sentence beside it carrying the rest.
GENERIC = "refused_before_it_ran"


def _a_waiting_job(service, name):
    """A real customer with a job that really had to wait, behind somebody else's run."""
    from tests.test_two_accounts import _a_customer

    who = _a_customer(service, name)
    record = _queued_job(service, who)
    assert record.slot_ticket is not None and not record.slot_ticket.dropped, (
        "this job did not have to wait, so nothing below is about a waiting job")
    return who, record


def _let_it_through(service, record):
    """Free the machine slot and let this job be granted it."""
    service.slots.give_back("somebody-elses-run")
    assert record.slot_ticket.granted.wait(2.0), "it never got its slot"
    return service._wait_for_a_slot(record, _limits())


def _reason(record) -> str:
    return str(getattr(record, "termination_reason", "") or "")


def _entry(service, run_id) -> dict:
    data = json.loads(pathlib.Path(service.ledger.path).read_text(encoding="utf-8"))
    return (data.get("runs") or {}).get(run_id) or {}


class TestEachStopLeavesItsOwnWord:
    """`D1`: the durable record carries the actual terminal reason, for every ending."""

    def test_1_a_withdrawn_device(self, capped):
        who, record = _a_waiting_job(capped, "withdrawn-and-waiting")
        assert capped.state.revoke_client(who.client_id), "the device was not withdrawn at all"

        allowed = _let_it_through(capped, record)

        assert allowed is False, "a withdrawn device's waiting job was allowed to start"
        assert _reason(record) == WITHDRAWN, (
            "a withdrawn device's waiting job settled with no terminal reason at all; the record "
            "says %r where it should say %r" % (_reason(record), WITHDRAWN))

    def test_2_an_enrolment_this_gateway_cannot_read(self, capped):
        """The fail-closed branch. It refuses already; what it does not do is say why."""
        who, record = _a_waiting_job(capped, "unreadable-and-waiting")

        def it_cannot_be_read(_account_id):
            raise OSError("the token store cannot be read")

        capped.state.devices_in = it_cannot_be_read

        allowed = _let_it_through(capped, record)

        assert allowed is False, "an unreadable enrolment let the work start"
        assert _reason(record) == UNREADABLE, (
            "an unreadable enrolment left the record with no terminal reason; it says %r where it "
            "should say %r" % (_reason(record), UNREADABLE))

    def test_3_a_suspended_account(self, capped):
        who, record = _a_waiting_job(capped, "suspended-and-waiting")
        capped.state.accounts.suspend(who.account_id, "we need to talk", by="the operator")

        allowed = _let_it_through(capped, record)

        assert allowed is False, "a suspended account's waiting job was allowed to start"
        assert _reason(record) == SUSPENDED, (
            "a suspended account's waiting job settled with no terminal reason at all; the record "
            "says %r where it should say %r" % (_reason(record), SUSPENDED))

    def test_4_a_gateway_that_stopped_taking_work(self, capped):
        from agentnode_sdk.gateway import allowance

        who, record = _a_waiting_job(capped, "stopped-and-waiting")
        allowance.stop_everything(capped.state.root, "the operator pulled the switch",
                                  by="the operator")

        allowed = _let_it_through(capped, record)

        assert allowed is False, "a stopped gateway started a waiting job"
        assert _reason(record) == NOT_TAKING_WORK, (
            "a gateway that stopped taking work left the record with no terminal reason; it says "
            "%r where it should say %r" % (_reason(record), NOT_TAKING_WORK))


class TestTheQueuesOwnDropReasonsKeepTheirWords:
    """The OTHER way a stop reaches a waiting job, which the first four tests do not exercise.

    There are two. A stop can be noticed by the gateway's sweep, which takes the ticket out of the
    queue with a reason on it; or it can be noticed when the slot is granted, by re-asking admission.
    `test_3` and `test_4` take the second path -- they apply the stop and then free the slot, with no
    sweep running -- so the mapping on the FIRST path was carried by nothing at all.

    HOW THAT WAS FOUND, because it is the useful part: counter-check `27` removes the drop-reason
    mapping and came back **green**. A counter-check that is not red removed nothing, which here meant
    the guarantee had no test rather than that the mutation was wrong. These two tests are what the
    green one asked for, and they were written after the code they cover -- the counter-check is the
    discriminating evidence, exactly as in finding `B-3`.
    """

    def test_11_a_ticket_dropped_because_the_gateway_stopped(self, capped):
        who, record = _a_waiting_job(capped, "dropped-because-stopped")
        capped.slots.drop(record.run_id, "stopped")

        assert capped._wait_for_a_slot(record, _limits()) is False, (
            "a job whose ticket was dropped was allowed to start")
        assert _reason(record) == NOT_TAKING_WORK, (
            "a ticket dropped because the gateway stopped taking work recorded %r where it should "
            "record %r" % (_reason(record), NOT_TAKING_WORK))

    def test_12_a_ticket_dropped_because_the_device_was_withdrawn(self, capped):
        who, record = _a_waiting_job(capped, "dropped-because-revoked")
        capped.slots.drop(record.run_id, "revoked")

        assert capped._wait_for_a_slot(record, _limits()) is False, (
            "a job whose ticket was dropped was allowed to start")
        assert _reason(record) == WITHDRAWN, (
            "a ticket dropped because the device was withdrawn recorded %r where it should record "
            "%r" % (_reason(record), WITHDRAWN))


class TestTheWordsAreWordsTheRecordCanHold:
    """The constraint that shapes this repair: an unknown reason makes the record UNREADABLE."""

    def test_5_every_word_these_paths_write_is_one_the_product_can_read_back(self):
        """`what_disagrees` is the product's own rule. A word outside its vocabulary is not a
        cause recorded, it is a record nobody can read -- an `EVIDENCE_ERROR` in every bundle
        that carries it (`tools/evidence.py`). This is why the words go INTO the protocol rather
        than being written by the path that knows the cause."""
        from agentnode_sdk.gateway.protocol import what_disagrees

        for word in (WITHDRAWN, UNREADABLE, SUSPENDED, NOT_TAKING_WORK, GENERIC):
            says = what_disagrees("refused", word, None, None, "")
            assert says == "", (
                "the record cannot hold %r: the product's own rule says %r" % (word, says))


class TestItReachesTheDurableRecord:
    """`D1` is about the DURABLE record, not about a field on an object in memory."""

    def test_6_the_ledger_carries_the_reason_too(self, capped):
        who, record = _a_waiting_job(capped, "withdrawn-and-recorded")
        # THE RUN HAS TO BE CLAIMED IN THE LEDGER FIRST, the way admission claims it. The first
        # version of this test did not, and it went red with `FileNotFoundError: ledger.json` --
        # no ledger file existed at all, because `_queued_job` builds a `RunRecord` directly and
        # skips the claim the real path makes. That red was not the predeclared one, and the tool
        # that compares the two refused it; without that refusal the test would have been a test
        # that could not pass even with the repair in, which is `A-9` of this arc in another
        # place. It is finding `D-5`.
        assert capped.ledger.claim(record.run_id, request_sha256="d" * 64, nonce="n-recorded",
                                  owner_client_id=who.client_id,
                                  owner_account_id=who.account_id), (
            "the run was not claimed in the ledger, so there is no entry for a reason to reach")
        assert capped.state.revoke_client(who.client_id)
        _let_it_through(capped, record)

        entry = _entry(capped, record.run_id)
        assert entry.get("settled_as"), (
            "the run has no closing word in the ledger at all, so there is nothing for a reason "
            "to sit beside: the entry is %r" % entry)
        assert entry.get("settled_because") == WITHDRAWN, (
            "the ledger did not carry the reason: settled_because is %r where it should be %r"
            % (entry.get("settled_because"), WITHDRAWN))


class TestWhatMustNotChange:
    """Green before the repair and green after it. The repair is not allowed to break these."""

    def test_7_the_outcome_a_customer_is_told_is_unchanged(self):
        """`outcome` is a field clients branch on, and this repair is about `termination_reason`.

        A refusal's outcome before this repair -- state `refused`, no reason -- and its outcome
        after it, with each of the new words, have to be the same value. If they are not, the
        repair has changed what a customer is told about every refused job under cover of a
        criterion about the reason field.
        """
        from agentnode_sdk.gateway.protocol import outcome_of

        before = outcome_of("refused", "", None)
        for word in (WITHDRAWN, UNREADABLE, SUSPENDED, NOT_TAKING_WORK, GENERIC):
            assert outcome_of("refused", word, None) == before, (
                "adding the reason %r changed the outcome a customer is told from %r to %r"
                % (word, before, outcome_of("refused", word, None)))

    def test_8_the_four_stops_do_not_collapse_into_one_word(self):
        """`D2`: they have to stay DISTINGUISHABLE. Four causes behind one word would satisfy
        `D1`'s letter and lose exactly what `D2` asks for."""
        words = [WITHDRAWN, UNREADABLE, SUSPENDED, NOT_TAKING_WORK]
        assert len(set(words)) == len(words), (
            "two different stops wrote the same word: %r" % words)
        assert GENERIC not in words, (
            "the coarse fallback is also one of the specific words, so a specific cause and an "
            "unnamed one cannot be told apart: %r" % GENERIC)


class TestTheFallbackIsAWordAndNotAnAbsence:
    """The case this repair must not reintroduce: a refusal that records nothing."""

    def test_9_a_refusal_whose_cause_has_no_word_still_carries_one(self, capped):
        """Driven through `_end_without_running` directly, because the point is about the path
        that writes the record rather than about any particular way of reaching it. An absence
        here is the original defect: a terminal record with nothing saying why."""
        who, record = _a_waiting_job(capped, "refused-for-some-other-reason")
        capped.slots.give_back("somebody-elses-run")
        record.slot_ticket.granted.wait(2.0)
        record.slot_ticket = None

        capped._end_without_running(record, _limits(), "refused",
                                    "something this build has no word for")

        assert _reason(record), (
            "a refusal with no specific word wrote nothing at all, which is the defect this file "
            "exists about: the record is terminal and says nothing about why")
        assert _reason(record) == GENERIC, (
            "the fallback word is %r where it should be %r" % (_reason(record), GENERIC))

    def test_10_a_word_from_outside_the_vocabulary_is_not_stored(self, capped):
        """The hazard my own argument identified, as a test rather than as a comment.

        `what_disagrees` refuses any reason outside `TERMINATION_REASONS`, so a word invented at
        a call site does not record a cause -- it makes the terminal record UNREADABLE, an
        `EVIDENCE_ERROR` in every bundle that carries it. That turns one defect, a missing reason,
        into a worse one. So a caller passing something outside the vocabulary must not have it
        stored: the record keeps a word the product can read back.

        This test was written while that guard was NOT in the code, and it is red without it.
        """
        from agentnode_sdk.gateway.protocol import what_disagrees

        who, record = _a_waiting_job(capped, "refused-with-an-invented-word")
        capped.slots.give_back("somebody-elses-run")
        record.slot_ticket.granted.wait(2.0)
        record.slot_ticket = None

        capped._end_without_running(record, _limits(), "refused",
                                    "a sentence", because_word="a_word_nobody_declared")

        says = what_disagrees("refused", _reason(record), None, None, "")
        assert says == "", (
            "an invented word was stored, so the terminal record cannot be read back: %s" % says)
        assert _reason(record) == GENERIC, (
            "an unknown word should fall back to %r; the record says %r"
            % (GENERIC, _reason(record)))
