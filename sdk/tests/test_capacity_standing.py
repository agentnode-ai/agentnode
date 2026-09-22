"""A job that is only WAITING is still somebody's job: standing reaches it, and a restart answers it.

`test_capacity_queue.py` establishes who waits. `test_capacity_billing.py` establishes what waiting
costs. This is the third question the queue raises and the one that is easiest to leave out: what
happens to a job that has not started when the world changes underneath it.

Three things were wrong when the queue was first built, and each is a test here:

* **A withdrawn device's waiting job was not woken.** Revocation set `cancel_requested` and asked
  the worker to stop a container -- but a job still in the queue has no container, and the thread
  holding its ticket was not watching that flag. It stayed in the queue, taking up a place, until
  a slot happened to free.
* **A restart told every unfinished job the same thing.** The ledger held `accepted` from
  submission until a terminal state and nothing ever wrote anything between, so a job that never
  left the queue was told "the gateway restarted while this job was running" -- and the recovery
  then asked the worker to clean up a sandbox that had never been created.
* **A suspension has no way to reach a ticket at all**, because it is applied by the operator's
  CLI in a different process. It is enforced where it can be: the standing is asked again at the
  moment the slot is granted. That path had no test.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time

import pytest

from agentnode_sdk.gateway import meter


def _line_for(root, run_id: str) -> dict:
    where = pathlib.Path(root) / meter.METER_NAME
    lines = [json.loads(x) for x in where.read_text(encoding="utf-8").splitlines() if x.strip()] \
        if where.is_file() else []
    for line in lines:
        if line.get("run_id") == run_id:
            return line
    raise AssertionError("no usage line for %r; there are %d" % (run_id, len(lines)))


def _limits():
    from agentnode_sdk.sandbox.contract import Limits, SandboxPolicy

    return SandboxPolicy(limits=Limits(cpu=1.0, memory_mb=512, wall_clock_s=60))


@pytest.fixture()
def capped(tmp_path):
    """A gateway whose operator allows ONE run at once and lets ONE wait.

    The same shape `test_capacity_billing.py` uses, and for the same reason: the number has to
    come out of the operator's file, or Q1 is lost before any of this is asked.
    """
    from tests.test_em3c_gateway import StandInBackend, _store_measurement
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService

    root = tmp_path / "state"
    root.mkdir(parents=True, exist_ok=True)
    (root / "allowance.json").write_text(
        json.dumps({"machine_concurrent_runs": 1, "queue_depth": 2}), encoding="utf-8")
    state = GatewayState(str(root), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        service.close()
        state.close()


def _queued_job(service, who, run_id="waiting-job"):
    """A record that really had to wait, because somebody else really holds the only slot."""
    from agentnode_sdk.gateway.server import RunRecord

    service.slots.take_or_queue("somebody-elses-run", "acct-somebody-else")
    record = RunRecord(run_id=run_id, job_id="j",
                       owner_client_id=who.client_id, owner_account_id=who.account_id)
    record.queued_at = time.time() - 5.0
    record.slot_ticket = service.slots.take_or_queue(run_id, who.account_id)
    assert record.slot_ticket is not None, "it did not have to wait, so this tests nothing"
    service.runs[run_id] = record
    return record


class TestAWithdrawnDeviceLetsGoOfWhatItLeftWaiting:
    """Revocation has to reach a job that has not started, and reach it AT ONCE."""

    def test_withdrawing_a_device_takes_its_waiting_job_out_of_the_queue(self, capped):
        from agentnode_sdk.access import dispatch
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "the-withdrawn-machine")
        record = _queued_job(capped, who)
        ticket = record.slot_ticket

        answer = dispatch._devices_revoke(capped, who, {"device_id": who.client_id})

        assert record.run_id in answer["runs_stopping"], (
            "the withdrawal did not report the waiting job as one it stopped")
        assert ticket.dropped == "revoked", (
            "the waiting job was left in the queue with dropped=%r" % ticket.dropped)
        assert capped.slots.waiting() == 0, "it is still occupying a place in the queue"

    def test_and_it_is_woken_rather_than_left_until_a_slot_frees(self, capped):
        """THE POINT OF THE FIX. The thread holding the ticket is blocked with no timeout.

        Before, nothing woke it: `cancel_requested` is not what a ticket watches. It would have
        sat there -- and the only thing that would eventually move it was somebody else's job
        finishing, which on a busy machine is not soon and on an idle one never comes.
        """
        from agentnode_sdk.access import dispatch
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "the-withdrawn-machine")
        record = _queued_job(capped, who)

        woke = threading.Event()

        def wait_like_the_run_thread_does():
            capped.slots.wait_for_slot(record.slot_ticket)
            woke.set()

        waiter = threading.Thread(target=wait_like_the_run_thread_does, daemon=True)
        waiter.start()
        assert not woke.wait(0.2), "it was not waiting in the first place"

        dispatch._devices_revoke(capped, who, {"device_id": who.client_id})

        assert woke.wait(2.0), "the withdrawal did not wake the job that was waiting"
        # AND NOBODY'S SLOT WAS FREED to make that happen -- the run holding the only slot is
        # still holding it. Named because a wake-up that only arrived because the machine
        # emptied would prove nothing about the withdrawal.
        assert capped.slots.in_use() == 1

    def test_the_withdrawn_job_never_runs_and_bills_nothing(self, capped):
        from agentnode_sdk.access import dispatch
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "the-withdrawn-machine")
        record = _queued_job(capped, who)
        dispatch._devices_revoke(capped, who, {"device_id": who.client_id})

        assert capped._wait_for_a_slot(record, _limits()) is False
        assert record.started_at == 0.0, "the billed clock started for a withdrawn job"
        assert record.container_name == "", "a sandbox was named for a job that never ran"

        line = _line_for(capped.state.root, record.run_id)
        assert line["seconds"] == 0.0, "billed %s for a job that never ran" % line["seconds"]
        assert line["waited_s"] >= 4.0, "the wait it really had was not recorded"

    def test_and_it_is_told_the_device_was_withdrawn(self, capped):
        """Not "cancelled", which is something the customer does. This was done TO them, and a
        refusal that names the wrong cause sends somebody to look in the wrong place."""
        from agentnode_sdk.access import dispatch
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "the-withdrawn-machine")
        record = _queued_job(capped, who)
        dispatch._devices_revoke(capped, who, {"device_id": who.client_id})
        capped._wait_for_a_slot(record, _limits())

        assert "withdrawn" in record.refusal, record.refusal
        assert record.state == "refused"

    def test_a_withdrawal_leaves_another_account_waiting(self, capped):
        """It takes out THAT device's job and nobody else's. A revocation that emptied the queue
        would be a way for one customer to clear the machine."""
        from agentnode_sdk.access import dispatch
        from tests.test_two_accounts import _a_customer

        mine = _a_customer(capped, "mine")
        theirs = _a_customer(capped, "theirs")
        record = _queued_job(capped, mine, run_id="mine")
        other = capped.slots.take_or_queue("theirs", theirs.account_id)

        dispatch._devices_revoke(capped, mine, {"device_id": mine.client_id})

        assert record.slot_ticket.dropped == "revoked"
        assert other.dropped == "", "somebody else's waiting job was dropped too"


class TestASuspensionReachesAJobThatIsAlreadyWaiting:
    """It cannot reach the ticket, so it is enforced at the slot. That path needs a test."""

    def test_the_suspension_is_applied_from_outside_this_process(self, capped):
        """Stated as a test because it is WHY the mechanism is the one it is.

        The operator's CLI writes to the accounts file; the queue is objects in the gateway's
        memory. There is no route through which a suspension could drop a ticket, so the only
        honest place to enforce it is the moment the slot is granted.
        """
        from agentnode_sdk.access import dispatch as _dispatch

        assert "accounts.suspend" not in _dispatch.HANDLERS
        assert not [name for name in _dispatch.HANDLERS if "suspend" in name], (
            "there is now an in-process suspension, so a waiting job should be dropped by it "
            "rather than left to be caught at the slot")

    def test_a_suspended_account_gets_its_slot_and_is_then_refused(self, capped):
        """It is promoted -- there is no way not to promote it -- and stopped before the work."""
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "about-to-be-suspended")
        record = _queued_job(capped, who)

        capped.state.accounts.suspend(who.account_id, "we need to talk", by="the operator")
        capped.slots.give_back("somebody-elses-run")
        assert record.slot_ticket.granted.wait(2.0), "it never got its slot"

        assert capped._wait_for_a_slot(record, _limits()) is False
        assert record.started_at == 0.0, "a suspended account's job started the billed clock"
        assert record.container_name == "", "a sandbox was named for a suspended account"

    def test_and_it_bills_nothing_although_it_held_a_slot_briefly(self, capped):
        """It did occupy a slot for the length of one standing check. The customer is not
        charged for that: the billed clock starts AFTER the check, not before it."""
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "about-to-be-suspended")
        record = _queued_job(capped, who)
        capped.state.accounts.suspend(who.account_id, "a reason", by="the operator")
        capped.slots.give_back("somebody-elses-run")
        record.slot_ticket.granted.wait(2.0)
        capped._wait_for_a_slot(record, _limits())

        line = _line_for(capped.state.root, record.run_id)
        assert line["seconds"] == 0.0, "billed %s" % line["seconds"]

    def test_and_the_slot_goes_back_so_the_machine_does_not_lose_it(self, capped):
        """A refusal that kept the slot would cost the machine one run for every suspended job."""
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "about-to-be-suspended")
        record = _queued_job(capped, who)
        capped.state.accounts.suspend(who.account_id, "a reason", by="the operator")
        capped.slots.give_back("somebody-elses-run")
        record.slot_ticket.granted.wait(2.0)
        capped._wait_for_a_slot(record, _limits())

        assert capped.slots.in_use() == 0, (
            "the refused job kept the slot; %d still held" % capped.slots.in_use())


class TestARestartTellsAWaitingJobApartFromARunningOne:
    """The ledger has to carry the difference, or the answer after a restart is a guess."""

    def _a_claimed_run(self, service, run_id, *, nonce):
        assert service.ledger.claim(run_id, nonce, "s" * 64, "dev", owner_account_id="acct-x")

    def _restarted(self, service, tmp_path):
        """A SECOND service on the same directory, which is what a restart is."""
        from tests.test_em3c_gateway import StandInBackend
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService

        service.close()
        state = GatewayState(str(pathlib.Path(service.state.root)), version="test")
        again = GatewayService(state, backend=StandInBackend())
        return again, state

    def test_the_ledger_records_that_a_run_started(self, capped):
        """WITHOUT THIS THERE IS NOTHING TO TELL THEM APART. The ledger held `accepted` from
        submission to a terminal state; nothing wrote anything in between."""
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(capped._run)))
        noted = [ast.unparse(node).replace(" ", "") for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and getattr(node.func, "attr", "") == "note_state"]
        assert any("'running'" in call for call in noted), (
            "nothing marks a run as started, so a restart cannot tell a job that ran from one "
            "that only waited: %r" % noted)

    def test_a_job_that_only_waited_is_told_it_never_started(self, capped, tmp_path):
        self._a_claimed_run(capped, "only-ever-waited", nonce="n1")
        again, state = self._restarted(capped, tmp_path)
        try:
            record = again.runs["only-ever-waited"]
            assert record.state == "interrupted"
            assert "waiting" in record.refusal, record.refusal
            assert "while your job was running" not in record.refusal, (
                "a job that never left the queue was told it was running")
            assert "nothing was charged" in record.refusal
        finally:
            again.close()
            state.close()

    def test_it_is_still_swept_like_every_other_interrupted_run(self, capped, tmp_path):
        """AND THAT IS ON PURPOSE, after a first version skipped the sweep for these.

        Skipping it looks right: the job never started, so no container exists to remove. But
        `running` is noted on a best effort -- a ledger that cannot be written must not stop a
        job that already holds a slot -- so a run CAN have a live container and still read as
        `accepted`. Skipping the sweep leaks that container.

        `test_reachable.py::test_the_sandbox_a_cut_short_run_left_is_removed` caught it, in those
        words. What the two cases differ in is the SENTENCE, not the sweeping.
        """
        self._a_claimed_run(capped, "only-ever-waited", nonce="n1")
        again, state = self._restarted(capped, tmp_path)
        try:
            record = again.runs["only-ever-waited"]
            assert record.container_name, (
                "nothing names the sandbox this run might have left, so nothing can remove it")
        finally:
            again.close()
            state.close()

    def test_a_job_that_was_running_still_gets_the_answer_it_had(self, capped, tmp_path):
        """The fix must not make the case it did not break any quieter."""
        self._a_claimed_run(capped, "was-really-running", nonce="n2")
        capped.ledger.note_state("was-really-running", "running")
        again, state = self._restarted(capped, tmp_path)
        try:
            record = again.runs["was-really-running"]
            assert record.state == "interrupted"
            # The wording moved when the sentence had to start saying WHICH interruption it
            # was -- "the gateway restarted" was a specific claim, and wrong for a planned
            # stop. What this test is about is unchanged: a job that ran is told it ran.
            assert "while your job was running" in record.refusal, record.refusal
            assert record.container_name, "the sandbox it may have left was not named"
        finally:
            again.close()
            state.close()

    def test_neither_is_started_a_second_time(self, capped, tmp_path):
        """The client asked once. A gateway that re-ran what it found is a gateway that bills
        twice for one request and runs somebody's code without being asked to."""
        self._a_claimed_run(capped, "only-ever-waited", nonce="n1")
        self._a_claimed_run(capped, "was-really-running", nonce="n2")
        capped.ledger.note_state("was-really-running", "running")
        again, state = self._restarted(capped, tmp_path)
        try:
            for run_id in ("only-ever-waited", "was-really-running"):
                assert again.runs[run_id].state == "interrupted"
            assert again.slots.in_use() == 0, "a recovered run took a slot"
            assert again.slots.waiting() == 0, "a recovered run was put back in the queue"
        finally:
            again.close()
            state.close()

    def test_and_neither_vanishes_without_an_answer(self, capped, tmp_path):
        """Q10's other half: the submitter must be able to learn what became of it."""
        self._a_claimed_run(capped, "only-ever-waited", nonce="n1")
        again, state = self._restarted(capped, tmp_path)
        try:
            assert "only-ever-waited" in again.runs
            public = again.runs["only-ever-waited"].public()
            assert public["state"] == "interrupted"
            assert public["started_at"] == 0.0, "it was given a start it never had"
        finally:
            again.close()
            state.close()


class TestAnOperatorCanSetTheCeilingWithoutEditingAFile:
    """Q1 asks for a configuration value an operator sets. It was one -- in a file nothing in
    the product would write.

    Every other ceiling this gateway has is a flag on `agentnode gateway limits`. The machine
    ceiling and its queue were reachable only by hand-editing `allowance.json`, which is how an
    operator ends up with a JSON file they are afraid to touch and a number nobody can explain.
    """

    def _root(self, tmp_path):
        root = tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_the_command_line_sets_both(self, tmp_path):
        from agentnode_sdk.cli.main import main
        from agentnode_sdk.gateway.allowance import read_allowance

        root = self._root(tmp_path)
        assert main(["gateway", "limits", "--dir", str(root),
                     "--machine-concurrent-runs", "2", "--queue-depth", "4"]) == 0
        allowed = read_allowance(root)
        assert allowed.machine_concurrent_runs == 2
        assert allowed.queue_depth == 4

    def test_setting_one_ceiling_does_not_clear_another(self, tmp_path):
        """`limits` rebuilds the whole allowance from what it read plus what was asked. A new
        field left out of that read would be silently reset to zero by any unrelated change --
        and for `machine_concurrent_runs` zero is no ceiling at all."""
        from agentnode_sdk.cli.main import main
        from agentnode_sdk.gateway.allowance import read_allowance

        root = self._root(tmp_path)
        main(["gateway", "limits", "--dir", str(root),
              "--machine-concurrent-runs", "2", "--queue-depth", "4"])
        main(["gateway", "limits", "--dir", str(root), "--runs-per-window", "500"])

        allowed = read_allowance(root)
        assert allowed.runs_per_window == 500
        assert allowed.machine_concurrent_runs == 2, (
            "setting an unrelated ceiling removed the machine ceiling")
        assert allowed.queue_depth == 4

    def test_it_shows_them_and_says_what_a_zero_queue_means(self, tmp_path, capsys):
        """The number alone reads backwards: everywhere else on that screen zero means no
        limit, and here it means nobody waits."""
        from agentnode_sdk.cli.main import main

        root = self._root(tmp_path)
        main(["gateway", "limits", "--dir", str(root), "--machine-concurrent-runs", "2"])
        capsys.readouterr()
        main(["gateway", "limits", "--dir", str(root)])
        shown = capsys.readouterr().out

        assert "machine_concurrent_runs" in shown
        assert "nobody waits" in shown, shown
        assert "no limit" not in shown.split("queue_depth")[1].splitlines()[0], (
            "a queue depth of zero was shown as 'no limit', which is the opposite of true")


class TestAFullMachineSaysWhatToDoAboutIt:
    """Q6 asks for a refusal that names the condition AND carries a next step.

    It named the condition. The step was built, travelled the whole way to the client, and was
    dropped by the one surface a person reads. Exercised on the closed alpha, seven jobs against
    a ceiling of two and a queue of four, the seventh was told:

        Refused, and nothing was run.
        This sandbox could not carry that out: this sandbox is already running as many jobs as
        it will run at once, and its queue is full. Nothing was started and nothing will be
        charged for.

    -- and nothing else. Two things were wrong, and they are separate.
    """

    def test_a_full_queue_is_a_ceiling_and_not_an_unavailable_sandbox(self):
        """`sandbox_unavailable` means "nothing could run it, and this is not the caller's
        fault". The sandbox is fine. It is busy, which is a ceiling and has its own name."""
        from agentnode_sdk.access.dispatch import _translate
        from agentnode_sdk.gateway.capacity import QueueIsFull

        refused = _translate(QueueIsFull("it is full", "send it again in a moment", 5.0))
        assert refused.refusal == "over_a_ceiling", (
            "a full machine was reported as %r" % refused.refusal)

    def test_and_it_keeps_its_own_remedy_rather_than_the_generic_one(self):
        """The generic branch substituted "tell whoever runs it", which is advice to complain.
        `QueueIsFull` knows the operator's actual numbers and says to try again shortly."""
        from agentnode_sdk.access.dispatch import _translate
        from agentnode_sdk.gateway.capacity import QueueIsFull

        real = QueueIsFull("it is full", "Send it again in a moment. It runs 2 at once.", 5.0)
        assert _translate(real).what_to_do == real.remedy

    def test_the_client_prints_the_step_and_not_only_the_reason(self):
        """Read out of the source of the one function that renders this, because the failure was
        not that the step was missing from the answer -- it was in the answer -- but that this
        printed the reason and returned."""
        import ast
        import inspect
        import textwrap

        from agentnode_sdk.cli import remote_commands

        source = textwrap.dedent(inspect.getsource(remote_commands.cmd_run))
        tree = ast.parse(source)
        printed = {ast.unparse(node) for node in ast.walk(tree)
                   if isinstance(node, ast.Call)
                   and getattr(node.func, "id", "") == "print"}
        assert any("what_to_do" in one for one in printed), (
            "the refusal's next step is never printed")

    def test_every_refusal_still_has_a_step_to_print(self):
        """The guard the above depends on: nothing in the contract may refuse without one."""
        from agentnode_sdk.access.dispatch import Refused

        with pytest.raises(Exception):
            Refused("over_a_ceiling", "because", "")


class TestFairnessThroughARealGateway:
    """The same rule as `test_capacity_queue.py`, but through the service rather than a bare
    `Slots`, and stated as what a CUSTOMER experiences rather than as an ordering.

    It exists as its own test because a counter-check has to be able to remove the fairness rule
    and fail something OTHER than the test that spells the rule out. Two tests that fail for the
    same reason are one test.

    Exercised for real on the closed alpha too: A submitted four, B submitted one afterwards, and
    B started ahead of two of A's -- one of which had waited 8.8 s.
    """

    def test_a_customer_who_flooded_the_machine_does_not_get_served_first(self, capped):
        who_floods, who_waits = "acct-floods", "acct-waits"
        # Both slots taken by the flooder, and two more of theirs queued behind it.
        capped.slots.take_or_queue("flood-1", who_floods)
        mine = [capped.slots.take_or_queue("flood-2", who_floods)]
        # The other customer arrives LAST.
        theirs = capped.slots.take_or_queue("the-other-customer", who_waits)
        assert theirs is not None, "it should have had to wait"

        capped.slots.give_back("flood-1")

        assert theirs.granted.is_set(), (
            "the freed slot went to the account that already had the machine; the customer who "
            "arrived last and holds nothing is still waiting")
        assert not any(t.granted.is_set() for t in mine), (
            "one of the flooder's own queued jobs was promoted ahead of a customer holding none")


class TestACeilingBiggerThanTheMachine:
    """The third of the three values the ceiling can have, and the one that was unanswered.

    Absent and zero mean no ceiling, and that was deliberate and written down. A ceiling LARGER
    than the machine has cores for was simply accepted: an operator who typed 200 instead of 2
    got 200, and the mechanism that exists to stop the machine being oversold was switched off
    by the number meant to configure it.

    `ALPHA-CAPACITY-QUEUE-0001`, finding F1: "no normative or frozen evidence establishes
    deliberate validation, refusal, clamping, or warning when it exceeds what the machine can
    serve. The profile expressly requires that case."

    The answer is: allowed, and said. Neither of the other two is right --
    `capacity.more_than_this_machine_can_serve` carries the reasoning, and these tests hold it
    to it.
    """

    def _root(self, tmp_path):
        root = tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_what_the_machine_can_serve_is_the_number_of_cores(self):
        """Because each sandbox is allotted one. The comparison is only true while that is."""
        from agentnode_sdk.gateway.capacity import what_this_machine_can_serve
        from agentnode_sdk.sandbox.contract import Limits

        assert Limits().cpu == 1.0, (
            "a sandbox no longer gets one core by default, so comparing the ceiling against the "
            "core count is no longer the right comparison")
        assert what_this_machine_can_serve() >= 1

    def test_a_ceiling_within_the_machine_says_nothing(self):
        from agentnode_sdk.gateway.capacity import (
            more_than_this_machine_can_serve, what_this_machine_can_serve,
        )

        assert more_than_this_machine_can_serve(what_this_machine_can_serve()) == 0
        assert more_than_this_machine_can_serve(1) == 0

    def test_no_ceiling_is_not_an_oversized_one(self):
        """Zero means no machine ceiling. Reporting it as 'more than the machine has' would be
        the inverted reading that `queue_depth` already has to warn about."""
        from agentnode_sdk.gateway.capacity import more_than_this_machine_can_serve

        assert more_than_this_machine_can_serve(0) == 0

    def test_a_ceiling_beyond_the_machine_is_measured_not_guessed(self):
        from agentnode_sdk.gateway.capacity import (
            more_than_this_machine_can_serve, what_this_machine_can_serve,
        )

        have = what_this_machine_can_serve()
        assert more_than_this_machine_can_serve(have + 7) == 7

    def test_an_unknowable_capacity_says_nothing_rather_than_everything(self, monkeypatch):
        """Zero cores means 'not established'. Treating it as 'nothing can run' would warn about
        every ceiling on a machine that simply would not answer."""
        from agentnode_sdk.gateway import capacity

        monkeypatch.setattr(capacity, "what_this_machine_can_serve", lambda: 0)
        assert capacity.more_than_this_machine_can_serve(500) == 0

    def test_it_is_not_clamped(self, tmp_path):
        """THE POINT. The operator asked for a number and gets that number.

        Serving fewer than the figure reported would hide the overbooking behind a value that
        looks obeyed -- which is the defect this whole arc exists to remove, reintroduced by the
        fix for it.
        """
        from agentnode_sdk.cli.main import main
        from agentnode_sdk.gateway.allowance import read_allowance
        from agentnode_sdk.gateway.capacity import Slots, what_this_machine_can_serve

        root = self._root(tmp_path)
        asked = what_this_machine_can_serve() + 50
        assert main(["gateway", "limits", "--dir", str(root),
                     "--machine-concurrent-runs", str(asked), "--queue-depth", "4"]) == 0

        allowed = read_allowance(root)
        assert allowed.machine_concurrent_runs == asked, (
            "the ceiling was changed to %s behind the operator's back" 
            % allowed.machine_concurrent_runs)
        assert Slots(ceiling=allowed.machine_concurrent_runs).ceiling == asked

    def test_it_is_not_refused(self, tmp_path):
        from agentnode_sdk.cli.main import main

        root = self._root(tmp_path)
        assert main(["gateway", "limits", "--dir", str(root),
                     "--machine-concurrent-runs", "500"]) == 0, (
            "a configuration choice was turned into a failure")

    def test_but_it_is_said_when_it_is_set(self, tmp_path, capsys):
        from agentnode_sdk.cli.main import main
        from agentnode_sdk.gateway.capacity import what_this_machine_can_serve

        root = self._root(tmp_path)
        have = what_this_machine_can_serve()
        main(["gateway", "limits", "--dir", str(root),
              "--machine-concurrent-runs", str(have + 50)])
        said = capsys.readouterr().out

        assert "more than this machine has cores for" in said.lower(), said
        assert str(have) in said, "it does not say what the machine actually has"
        assert str(have + 50) in said, "it does not say what was asked for"

    def test_and_said_again_every_time_it_is_shown(self, tmp_path, capsys):
        """A warning that appeared once, at a moment nobody was reading, was not given."""
        from agentnode_sdk.cli.main import main
        from agentnode_sdk.gateway.capacity import what_this_machine_can_serve

        root = self._root(tmp_path)
        main(["gateway", "limits", "--dir", str(root),
              "--machine-concurrent-runs", str(what_this_machine_can_serve() + 50)])
        capsys.readouterr()

        main(["gateway", "limits", "--dir", str(root)])
        shown = capsys.readouterr().out
        assert "more than this machine has cores for" in shown.lower(), shown

    def test_and_a_sensible_ceiling_is_not_nagged_about(self, tmp_path, capsys):
        from agentnode_sdk.cli.main import main

        root = self._root(tmp_path)
        main(["gateway", "limits", "--dir", str(root), "--machine-concurrent-runs", "1"])
        capsys.readouterr()
        main(["gateway", "limits", "--dir", str(root)])
        assert "more than this machine" not in capsys.readouterr().out.lower()


class TestAnInterruptedRunIsStillInTheRecord:
    """Q4 asks that the wait and the billed time BOTH appear in the signed, chained log.

    They did -- for every run that reached an ending. A run interrupted by a restart produced no
    line at all. Nothing was billed for it and the gateway could still say what became of it, but
    the signed record, which is the thing a customer would be handed as proof of what this
    service did, did not contain the job.

    `ALPHA-CAPACITY-QUEUE-0002`, F1: "their waited and billed values are absent from the signed
    chain ... it directly defeats the recording criterion." It does. The queue also makes the case
    ordinary rather than rare: a job waiting when a restart happens is now normal.

    These do NOT close `interrupted-audit-record-r1`, which asks for more -- every kind of
    interruption, and whether 'exactly one' is enforced rather than observed. They establish the
    one thing Q4 asks for.
    """

    ADMITTED = {"cpu": 2.0, "memory_mb": 1024, "wall_clock_s": 99,
                "allowance_sha256": "a" * 64, "operator_policy_sha256": "p" * 64,
                "operator_policy_version": 7, "worker_topology": "single-host-development"}

    def _claimed(self, service, run_id, *, nonce, when, started=None):
        assert service.ledger.claim(run_id, nonce, "s" * 64, "dev",
                                    now=when, owner_account_id="acct-" + "1" * 16,
                                    admitted=self.ADMITTED)
        if started is not None:
            service.ledger.note_state(run_id, "running", at=started)

    def _restarted(self, service):
        from tests.test_em3c_gateway import StandInBackend
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService

        root = str(pathlib.Path(service.state.root))
        service.close()
        state = GatewayState(root, version="test")
        return GatewayService(state, backend=StandInBackend()), state

    def test_a_job_that_only_waited_gets_its_line(self, capped):
        began = time.time() - 30.0
        self._claimed(capped, "only-waited", nonce="n1", when=began)
        again, state = self._restarted(capped)
        try:
            line = _line_for(again.state.root, "only-waited")
            assert line["state"] == "interrupted"
            assert line["seconds"] == 0.0, (
                "a job that never started was billed %s" % line["seconds"])
            assert line["waited_s"] >= 29.0, (
                "the wait was %s; it was not read from the ledger" % line["waited_s"])
        finally:
            again.close()
            state.close()

    def test_and_the_times_come_from_the_ledger_not_from_the_restart(self, capped):
        """THE POINT. A restored record is CONSTRUCTED now, so its own `queued_at` is the moment
        of the restart. A line built from that would say the job waited no time at all, which is
        a false statement about somebody's bill rather than a missing one."""
        began = time.time() - 300.0
        self._claimed(capped, "waited-five-minutes", nonce="n1", when=began)
        again, state = self._restarted(capped)
        try:
            line = _line_for(again.state.root, "waited-five-minutes")
            assert abs(line["queued_at"] - began) < 1.0, (
                "queued_at is %s, not the ledger's first_seen %s" % (line["queued_at"], began))
            assert line["waited_s"] > 290.0
        finally:
            again.close()
            state.close()

    def test_a_job_that_was_running_is_billed_from_when_it_started(self, capped):
        began = time.time() - 100.0
        self._claimed(capped, "was-running", nonce="n2", when=began, started=began + 60.0)
        again, state = self._restarted(capped)
        try:
            line = _line_for(again.state.root, "was-running")
            assert line["seconds"] >= 35.0, (
                "billed %s; it should be from the slot to now" % line["seconds"])
            assert 55.0 <= line["waited_s"] <= 65.0, (
                "waited %s; it should be arrival to slot" % line["waited_s"])
        finally:
            again.close()
            state.close()

    def test_the_line_says_what_the_run_was_admitted_under(self, capped):
        """Not what is configured by the time the line is written. A limit changed while a
        gateway was down must not rewrite what an interrupted run is recorded as."""
        self._claimed(capped, "with-its-own-limits", nonce="n3", when=time.time() - 5.0)
        again, state = self._restarted(capped)
        try:
            line = _line_for(again.state.root, "with-its-own-limits")
            assert line["cpu"] == 2.0 and line["memory_mb"] == 1024
            assert line["wall_clock_s"] == 99
            assert line["allowance_sha256"] == "a" * 64
            assert line["operator_policy_version"] == 7
        finally:
            again.close()
            state.close()

    def test_a_second_restart_does_not_write_a_second_line(self, capped):
        """Enforced, not hoped: `unfinished_runs` selects `accepted` and `running`, and the entry
        says `interrupted` once the line is written. A second restart cannot see it again."""
        self._claimed(capped, "only-once", nonce="n1", when=time.time() - 10.0)
        again, state = self._restarted(capped)
        once, state2 = self._restarted(again)
        try:
            lines = [json.loads(x) for x
                     in (pathlib.Path(once.state.root) / meter.METER_NAME)
                     .read_text(encoding="utf-8").splitlines() if x.strip()]
            mine = [x for x in lines if x.get("run_id") == "only-once"]
            assert len(mine) == 1, "closed %d times" % len(mine)
        finally:
            once.close()
            state2.close()
            state.close()

    def test_and_the_chain_still_verifies(self, capped):
        self._claimed(capped, "in-the-chain", nonce="n1", when=time.time() - 10.0)
        again, state = self._restarted(capped)
        try:
            checked = meter.verify(again.state.root)
            assert checked["ok"], checked
            assert checked["unchecked"] == 0
        finally:
            again.close()
            state.close()


class TestAnOperatorCommandIsNotAGatewayTakingOver:
    """Building a service to READ something used to end the jobs a live gateway was running.

    `GatewayService.__init__` ran crash recovery. Eleven operator commands build one -- four of
    them only to reach the state object beside it -- so each was performing recovery against the
    directory a serving gateway was using: marking its running jobs interrupted, asking the
    worker to remove their containers, and killing them.

    Measured on the closed alpha, not reasoned about: `agentnode gateway accounts`, which only
    lists customers, took 10.6 s and ended a job eight seconds into its work. The client got
    `-9`; the signed line said `interrupted`, billed 8.29 s.

    This is what was behind `EARLY-ENDING-HOLDERS.md`: holders died at ~11 s in every exercise
    that ran an operator command, and survived in every control that did not. The ~11 s was how
    long the command took to start.
    """

    def _an_unfinished_run(self, service, run_id="left-in-flight"):
        assert service.ledger.claim(run_id, "nonce-" + run_id, "s" * 64, "dev",
                                    owner_account_id="acct-" + "2" * 16)
        service.ledger.note_state(run_id, "running")
        return run_id

    def _another_service(self, service, recover):
        from tests.test_em3c_gateway import StandInBackend
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService

        state = GatewayState(str(pathlib.Path(service.state.root)), version="test")
        return GatewayService(state, backend=StandInBackend(), recover=recover), state

    def test_a_visitor_leaves_a_running_job_alone(self, capped):
        run_id = self._an_unfinished_run(capped)
        visitor, state = self._another_service(capped, recover=False)
        try:
            assert run_id not in visitor.runs, "a visitor took over a live run"
            entry = capped.ledger.run_entry(run_id) or {}
            assert entry.get("state") == "running", (
                "a visitor moved a live run to %r" % entry.get("state"))
        finally:
            visitor.close()
            state.close()

    def test_and_writes_no_usage_line_for_it(self, capped):
        """The sharpest form: a signed record saying a job was interrupted, written about a job
        that is running perfectly well, is a false statement in the one document a customer
        would be handed as proof."""
        run_id = self._an_unfinished_run(capped, "still-going")
        visitor, state = self._another_service(capped, recover=False)
        try:
            where = pathlib.Path(capped.state.root) / meter.METER_NAME
            lines = [json.loads(x) for x in where.read_text(encoding="utf-8").splitlines()
                     if x.strip()] if where.is_file() else []
            assert not [x for x in lines if x.get("run_id") == run_id], (
                "a visitor wrote a usage line about a job that was still running")
        finally:
            visitor.close()
            state.close()

    def test_a_gateway_taking_over_still_recovers(self, capped):
        """The other half. Turning recovery off everywhere would lose interrupted runs instead
        of killing live ones, which is the same kind of mistake pointing the other way."""
        run_id = self._an_unfinished_run(capped, "really-interrupted")
        taking_over, state = self._another_service(capped, recover=True)
        try:
            assert run_id in taking_over.runs
            assert taking_over.runs[run_id].state == "interrupted"
        finally:
            taking_over.close()
            state.close()

    def test_only_the_start_command_asks_to_recover(self):
        """Read out of the CLI's own source, so a twelfth command cannot quietly join the other
        eleven. `_service` defaults to not recovering; exactly one caller overrides it."""
        import ast
        import inspect
        import textwrap

        from agentnode_sdk.cli import gateway_commands

        source = textwrap.dedent(inspect.getsource(gateway_commands))
        tree = ast.parse(source)
        asked = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_service":
                asked.append({k.arg: ast.unparse(k.value) for k in node.keywords})
        assert asked, "nothing builds a service any more; this test is watching the wrong thing"
        recovering = [x for x in asked if x.get("recover") == "True"]
        assert len(recovering) == 1, (
            "%d commands ask to recover; only `start` is a gateway taking over" % len(recovering))

        made = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_service")
        default = made.args.defaults[-1]
        assert ast.unparse(default) == "False", (
            "_service recovers by default again, so a command that forgets the flag kills runs")
