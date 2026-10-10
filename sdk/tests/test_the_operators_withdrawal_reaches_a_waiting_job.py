"""The operator's own `gateway revoke` has to reach a job that is still waiting in the queue.

WHY THIS FILE EXISTS, and it is a measurement rather than a suspicion. On the two-host test stand, with
the machine ceiling at 1 and a queue in front of it, a customer's job stood `accepted` behind somebody
else's running job. The operator then withdrew that device with `agentnode gateway revoke`. The job

    ran, for its full 150.7 seconds, and the usage record billed them --

while its customer was told only *"The job did not come back: This request did not come with a credential
this sandbox recognises."* That is `BETA-4` finding `A-7`, and it is `EG15` of the beta profile failing:
*"An account lock, the kill switch and a device revocation also prevent the later allocation of a job that
is already waiting."*

WHY IT WAS MISSED, which is the interesting part. There are TWO withdrawal paths and only one is complete:

* the contract operation `devices.revoke` (`access/dispatch.py:_devices_revoke`) ends the device's
  sessions, drops its connections and invitations, **drops its queued ticket** with
  `slots.drop(run_id, "revoked")`, asks to stop its running jobs, and removes the credential last. It is
  tested, in `test_capacity_standing.py`, for exactly this property.
* the operator's CLI (`cli/gateway_commands.py:cmd_revoke`) removes the credential and does none of the
  rest -- which is precisely what `_devices_revoke`'s own docstring says is not enough:
  *"Removing the credential stops the NEXT request. That is not the whole of revocation, and a review was
  right to refuse it as such."*

AND THE LAST CHECK DOES NOT CLOSE IT EITHER. A job that is granted its slot goes through
`may_this_caller_proceed` -> `standing_of(account_id, device_id)`, which looks up the **account** only; the
`device_id` is carried into the `Standing` object and never consulted. So a withdrawn device whose account
is in good standing passes the last check before foreign code runs.

The suspension has the same shape -- the operator's CLI cannot reach a ticket from another process -- and
the answer for it is already here: the gateway sweeps every tick
(`drop_queued_work_that_is_no_longer_permitted`) and the standing is re-asked at the slot. A withdrawal
needs both of those too, and has neither.

These tests are written BEFORE the repair and are expected to be red against the unrepaired build, each
for the reason named in its own failure message.
"""
from __future__ import annotations

import pytest

# The fixtures these tests need already exist, and rewriting them would be two slightly different
# definitions of "a job that really had to wait". `capped` is a gateway whose operator allows one run at
# once and lets two wait; `_queued_job` puts a record in the queue behind somebody else's run and
# refuses to pretend if it did not actually have to wait.
from tests.test_capacity_standing import _limits, _line_for, _queued_job  # noqa: F401
from tests.test_capacity_standing import capped  # noqa: F401


def _a_withdrawn_device(service, name="the-withdrawn-machine"):
    """A customer with a waiting job, whose device the OPERATOR then withdraws.

    Withdrawn the way the operator's command withdraws it: by identity, through
    `state.revoke_client`, with no account restriction -- the operator is not an account. Nothing here
    touches the queue, because the operator's command cannot: it runs in a different process.
    """
    from tests.test_two_accounts import _a_customer

    who = _a_customer(service, name)
    record = _queued_job(service, who)
    assert record.slot_ticket is not None and not record.slot_ticket.dropped
    withdrawn = service.state.revoke_client(who.client_id)
    assert withdrawn, "the device was not withdrawn at all, so nothing below tests anything"
    return who, record


class TestTheTwoWithdrawalPathsAreNotTheSame:
    """Stated as a test because it is WHY the mechanism has to be the one it is."""

    def test_1_the_contract_path_exists_and_the_operators_path_cannot_reach_a_ticket(self, capped):
        from agentnode_sdk.access import dispatch as _dispatch

        assert "devices.revoke" in _dispatch.HANDLERS, (
            "there is no in-process withdrawal any more, so this file's premise has changed")

        # The operator's path, done exactly as the CLI does it: the credential goes and nothing
        # else is touched. The ticket is still in the queue afterwards -- which is not the defect,
        # it is the reason the defect has to be caught somewhere else.
        who, record = _a_withdrawn_device(capped, "credential-only")
        assert record.slot_ticket.dropped == "", (
            "removing the credential dropped the ticket by itself; this file is about the case "
            "where it cannot, and that case no longer exists")


class TestTheSweepNoticesADeviceThatIsGone:
    """The gateway's own loop runs this every tick. It asks about suspensions and nothing else."""

    def test_2_the_sweep_takes_a_withdrawn_devices_waiting_job_out_of_the_queue(self, capped):
        who, record = _a_withdrawn_device(capped)
        ticket = record.slot_ticket

        capped.drop_queued_work_that_is_no_longer_permitted()

        # SAFETY FIRST, so a red names the property that broke rather than a precondition.
        assert ticket.dropped == "revoked", (
            "the operator's withdrawal left the waiting job in the queue: dropped=%r"
            % ticket.dropped)
        assert capped.slots.waiting() == 0, (
            "the operator's withdrawal left the waiting job in the queue: %d still waiting"
            % capped.slots.waiting())

    def test_3_and_it_does_not_touch_a_device_that_is_still_recognised(self, capped):
        """The other half of the same guarantee: a sweep that drops everything is not a sweep."""
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "still-here")
        record = _queued_job(capped, who)
        ticket = record.slot_ticket

        capped.drop_queued_work_that_is_no_longer_permitted()

        assert ticket.dropped == "", (
            "the sweep dropped the waiting job of a device that is still recognised: dropped=%r"
            % ticket.dropped)


class TestTheLastCheckBeforeForeignCodeRuns:
    """A sweep is prompt and not instantaneous. The slot could free in between, so the moment the
    slot is granted has to refuse too -- which is exactly how a suspension is enforced."""

    def test_4_a_withdrawn_devices_job_is_refused_at_the_slot_although_no_sweep_ran(self, capped):
        who, record = _a_withdrawn_device(capped)

        # No sweep. The slot simply frees and this job is promoted, which is what happened on the
        # stand: nothing had dropped the ticket, so the promotion went ahead.
        capped.slots.give_back("somebody-elses-run")
        assert record.slot_ticket.granted.wait(2.0), "it never got its slot, so nothing is tested"

        allowed = capped._wait_for_a_slot(record, _limits())

        assert allowed is False, (
            "a withdrawn device's job was started: _wait_for_a_slot said it may run")
        assert record.started_at == 0.0, (
            "a withdrawn device's job was started: the billed clock began at %r"
            % record.started_at)
        assert record.container_name == "", (
            "a withdrawn device's job was started: a sandbox was named %r"
            % record.container_name)

    def test_5_and_the_customer_is_told_it_was_the_withdrawal(self, capped):
        """The theme of this whole arc: a true condition with the WRONG cause is still a defect."""
        who, record = _a_withdrawn_device(capped)
        capped.slots.give_back("somebody-elses-run")
        record.slot_ticket.granted.wait(2.0)
        capped._wait_for_a_slot(record, _limits())

        said = str(getattr(record, "refusal", "") or "")
        assert "withdraw" in said or "revoke" in said, (
            "the withdrawal was not named as the cause; the customer was told %r" % said)
        assert "suspend" not in said, (
            "a withdrawal was reported as a suspension, which is a different operator action: %r"
            % said)

    def test_6_and_nothing_is_billed_for_the_wait(self, capped):
        who, record = _a_withdrawn_device(capped)
        capped.slots.give_back("somebody-elses-run")
        record.slot_ticket.granted.wait(2.0)
        capped._wait_for_a_slot(record, _limits())

        # THE PROPERTY FIRST, AND NAMED. Asking `_line_for` straight away makes the red say "no
        # usage line for 'waiting-job'; there are 0", which is true and is about the absence of a
        # line rather than about the billing -- and the absence has a cause worth naming: there is
        # no line because the job was not refused at all, so it ran. That is the EG19 arc's finding
        # F7 in another place: an assertion order that makes a red name the wrong thing.
        import pathlib
        from agentnode_sdk.gateway import meter

        where = pathlib.Path(capped.state.root) / meter.METER_NAME
        text = where.read_text(encoding="utf-8") if where.is_file() else ""
        assert record.run_id in text, (
            "a job that never ran has no usage line at all, which means it was not refused: it "
            "was allocated the slot and ran, and nothing was written saying it cost nothing")
        line = _line_for(capped.state.root, record.run_id)
        assert line["seconds"] == 0.0, (
            "a job that never ran was billed %s seconds" % line["seconds"])

    def test_7_and_the_machine_does_not_lose_the_slot(self, capped):
        """A refusal that kept the slot would cost the machine one run per withdrawn device."""
        who, record = _a_withdrawn_device(capped)
        capped.slots.give_back("somebody-elses-run")
        record.slot_ticket.granted.wait(2.0)
        capped._wait_for_a_slot(record, _limits())

        assert capped.slots.in_use() == 0, (
            "the refused job kept the slot; %d still held" % capped.slots.in_use())


class TestTheContractPathIsUnchanged:
    """The complete path already worked. A repair that broke it would trade one gap for another."""

    def test_8_the_contract_withdrawal_still_drops_the_ticket_at_once(self, capped):
        from agentnode_sdk.access import dispatch
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "withdrawn-over-the-contract")
        record = _queued_job(capped, who)
        ticket = record.slot_ticket

        answer = dispatch._devices_revoke(capped, who, {"device_id": who.client_id})

        assert record.run_id in answer["runs_stopping"], (
            "the contract withdrawal stopped reporting the waiting job as one it stopped")
        assert ticket.dropped == "revoked", (
            "the contract withdrawal stopped dropping the ticket: dropped=%r" % ticket.dropped)


class TestTheTwoGuaranteesTheFirstEightTestsDidNotCover:
    """Written because designing the counter-check found them, which is what a counter-check is for.

    `M2` of this arc's profile asks that each new guarantee be removable with a predeclared red. Two
    of the guarantees the repair adds had no test able to go red for them: the third answer for a
    token store that cannot be read, and the fact that the sweep's answer is cached per DEVICE rather
    than per account. Neither gap would have shown in a green run.
    """

    def test_9_a_token_store_that_cannot_be_read_refuses_rather_than_starts_the_work(self, capped):
        """Unreadable is not enrolled, and it is not withdrawn either.

        The same reading the accounts record already gets with `cannot_tell`: a gateway that cannot
        tell whether a device was withdrawn is not one to start its work. The alternative is the
        fail-open direction, and a comment defending that would be the whole class of defect this
        project keeps finding.
        """
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "unreadable-store")
        record = _queued_job(capped, who)

        def it_cannot_be_read(_account):
            raise OSError("the token store cannot be read")

        capped.state.devices_in = it_cannot_be_read
        capped.slots.give_back("somebody-elses-run")
        assert record.slot_ticket.granted.wait(2.0), "it never got its slot, so nothing is tested"

        allowed = capped._wait_for_a_slot(record, _limits())

        assert allowed is False, (
            "a job was started although this gateway could not tell whether its device is still "
            "enrolled")
        assert record.started_at == 0.0, (
            "a job was started although this gateway could not tell whether its device is still "
            "enrolled: the billed clock began at %r" % record.started_at)
        said = str(getattr(record, "refusal", "") or "")
        assert "cannot currently tell" in said, (
            "the refusal did not say that this gateway cannot tell; it said %r" % said)

    def test_10_withdrawing_one_device_does_not_drop_its_siblings_waiting_job(self, capped):
        """One customer, two machines, one of them withdrawn.

        The sweep asks whether a ticket's device is still enrolled and remembers the answer. If it
        remembered per ACCOUNT, withdrawing one machine would drop the other machine's waiting job
        too -- a customer losing work they never asked to lose, and an over-reach no green test
        would show.
        """
        from tests.test_two_accounts import _a_customer, _their_second_machine

        who = _a_customer(capped, "two-machines")
        sibling = _their_second_machine(capped, who)
        assert sibling.account_id == who.account_id, "they are not one customer, so this tests nothing"

        # NOT `_queued_job` TWICE. It takes a slot for somebody else on every call, so calling it
        # twice against a ceiling of one and a queue of two fills the queue and the second job is
        # refused with `QueueIsFull` -- which is correct behaviour and not what this test is about.
        # One slot is taken, and both machines' jobs are queued behind it.
        from agentnode_sdk.gateway.server import RunRecord
        import time as _time

        capped.slots.take_or_queue("somebody-elses-run", "acct-somebody-else")
        made = {}
        for label, owner in (("the-withdrawn-machines-job", who),
                             ("the-other-machines-job", sibling)):
            record = RunRecord(run_id=label, job_id="j",
                               owner_client_id=owner.client_id, owner_account_id=owner.account_id)
            record.queued_at = _time.time() - 5.0
            record.slot_ticket = capped.slots.take_or_queue(label, owner.account_id)
            assert record.slot_ticket is not None, "%s did not have to wait" % label
            capped.runs[label] = record
            made[label] = record
        theirs, siblings = made["the-withdrawn-machines-job"], made["the-other-machines-job"]
        assert capped.state.revoke_client(who.client_id), "the first machine was not withdrawn"

        capped.drop_queued_work_that_is_no_longer_permitted()

        assert siblings.slot_ticket.dropped == "", (
            "withdrawing one of a customer's machines dropped the OTHER machine's waiting job: "
            "dropped=%r" % siblings.slot_ticket.dropped)
        assert theirs.slot_ticket.dropped == "revoked", (
            "the withdrawn machine's own waiting job was left in the queue: dropped=%r"
            % theirs.slot_ticket.dropped)
