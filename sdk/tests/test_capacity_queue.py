"""The machine ceiling and the queue in front of it.

Measured on the alpha before any of this existed: four identical jobs on two cores each took
7.76 s of wall clock for work that took 3.44 s alone, while each used the same 3.37 s of CPU. A
customer billed by elapsed time pays 2.25x for the same job because the gateway admitted more
work than it could run -- **the gateway's arithmetic on the customer's invoice.**

These tests are about the thing that stops that. They exercise `Slots` directly rather than
through a gateway, because what is being established here is the decision -- who runs, who waits,
who is refused, who is dropped -- and a container in the middle would only make the same
assertions slower and less certain.

What a gateway adds on top -- the billed clock, the signed line, the standing re-check -- is in
`test_capacity_billing.py`, against a real service.
"""
from __future__ import annotations

import threading
import time

import pytest

from agentnode_sdk.gateway.capacity import QueueIsFull, Slots


class TestTheCeilingHoldsWhateverArrives:

    def test_up_to_the_ceiling_runs_at_once(self):
        slots = Slots(ceiling=2, queue_depth=4)
        assert slots.take_or_queue("a", "A") is None
        assert slots.take_or_queue("b", "B") is None
        assert slots.in_use() == 2

    def test_and_the_one_after_it_waits_rather_than_being_refused(self):
        """The difference between this ceiling and every other one. A device limit is the
        customer's own doing and refusing is the right answer; a machine limit is ours."""
        slots = Slots(ceiling=1, queue_depth=1)
        assert slots.take_or_queue("a", "A") is None
        ticket = slots.take_or_queue("b", "B")
        assert ticket is not None
        assert slots.waiting() == 1
        assert not ticket.granted.is_set()

    def test_no_ceiling_means_no_queue_and_no_waiting(self):
        """The control. Without it "the ceiling holds" would also be true of a gateway that
        refuses everything, including one where the operator set no ceiling at all."""
        slots = Slots(ceiling=0, queue_depth=0)
        for i in range(25):
            assert slots.take_or_queue("r%d" % i, "A") is None
        assert slots.in_use() == 25 and slots.waiting() == 0

    def test_a_ceiling_of_one_really_admits_one_under_a_hundred_threads(self):
        """The mistake this class exists to prevent: reading the count, doing something else, and
        then taking a slot. A hundred threads arriving together is how a ceiling of one admits
        two, and it is the reason every decision here is made under one lock."""
        slots = Slots(ceiling=1, queue_depth=200)
        got_a_slot: list[str] = []
        start = threading.Barrier(40)

        def arrive(i):
            start.wait()
            if slots.take_or_queue("r%d" % i, "acct-%d" % i) is None:
                got_a_slot.append("r%d" % i)

        threads = [threading.Thread(target=arrive, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(got_a_slot) == 1, "the ceiling admitted %d" % len(got_a_slot)
        assert slots.waiting() == 39


class TestTheQueueIsBoundedAndSaysSo:

    def test_a_full_queue_refuses(self):
        slots = Slots(ceiling=1, queue_depth=2)
        slots.take_or_queue("a", "A")
        slots.take_or_queue("b", "B")
        slots.take_or_queue("c", "C")
        with pytest.raises(QueueIsFull):
            slots.take_or_queue("d", "D")

    def test_and_the_refusal_carries_something_to_do(self):
        """A refusal nobody can act on gets worked around rather than fixed."""
        slots = Slots(ceiling=1, queue_depth=0)
        slots.take_or_queue("a", "A")
        with pytest.raises(QueueIsFull) as refused:
            slots.take_or_queue("b", "B")
        assert refused.value.remedy
        assert refused.value.retry_after_s > 0
        assert "nothing was started" in str(refused.value).lower()
        assert "charged" in str(refused.value).lower()

    def test_a_refused_job_took_no_slot_and_left_no_trace(self):
        slots = Slots(ceiling=1, queue_depth=0)
        slots.take_or_queue("a", "A")
        with pytest.raises(QueueIsFull):
            slots.take_or_queue("b", "B")
        assert slots.in_use() == 1 and slots.waiting() == 0

    def test_the_refusal_says_nothing_about_who_else_is_here(self):
        """The bound and the ceiling are this machine's own settings. What must not appear is
        anybody's identity, and neither must a count of jobs that would let one be inferred."""
        slots = Slots(ceiling=1, queue_depth=1)
        slots.take_or_queue("a", "a-very-distinctive-account")
        slots.take_or_queue("b", "another-distinctive-account")
        with pytest.raises(QueueIsFull) as refused:
            slots.take_or_queue("c", "C")
        said = str(refused.value) + refused.value.remedy
        assert "a-very-distinctive-account" not in said
        assert "another-distinctive-account" not in said
        assert "run_id" not in said and "\"a\"" not in said


class TestOneAccountCannotStarveAnother:

    def test_a_freed_slot_goes_to_the_account_holding_fewest(self):
        """Arrival order alone is not fairness between accounts: one account sending fifty jobs
        would put fifty ahead of everybody else's first."""
        slots = Slots(ceiling=2, queue_depth=10)
        slots.take_or_queue("a1", "A")
        slots.take_or_queue("a2", "A")
        # A queues first and B after it.
        a3 = slots.take_or_queue("a3", "A")
        b1 = slots.take_or_queue("b1", "B")
        slots.give_back("a1")
        assert b1.granted.is_set(), "the slot went to the account that already had two"
        assert not a3.granted.is_set()

    def test_a_flood_from_one_account_does_not_delay_another(self):
        """Fifty from A, one from B. B must not be fiftieth."""
        slots = Slots(ceiling=1, queue_depth=100)
        slots.take_or_queue("holder", "A")
        flood = [slots.take_or_queue("a%d" % i, "A") for i in range(50)]
        late = slots.take_or_queue("b", "B")
        slots.give_back("holder")
        assert late.granted.is_set(), "B waited behind A's flood"
        assert not any(t.granted.is_set() for t in flood)

    def test_within_one_account_the_first_to_arrive_goes_first(self):
        """Fairness BETWEEN accounts is not licence to reorder WITHIN one."""
        slots = Slots(ceiling=1, queue_depth=10)
        slots.take_or_queue("holder", "A")
        first = slots.take_or_queue("a1", "A")
        second = slots.take_or_queue("a2", "A")
        slots.give_back("holder")
        assert first.granted.is_set() and not second.granted.is_set()

    def test_and_the_order_survives_a_clock_that_moves_backwards(self):
        """Order comes from a counter, not from the clock.

        A system clock stepping backwards -- an ntp correction, a VM resuming -- would otherwise
        reorder a queue, and the reordering would look exactly like correct behaviour. Here the
        clock runs backwards on purpose: the job that arrived FIRST is stamped with the LATEST
        time, so anything sorting by `arrived_at` would serve them the wrong way round.
        """
        ticking = iter([500.0, 400.0, 300.0, 200.0, 100.0])
        slots = Slots(ceiling=1, queue_depth=10, now=lambda: next(ticking))
        slots.take_or_queue("holder", "A")
        first = slots.take_or_queue("a1", "A")     # stamped 500.0
        second = slots.take_or_queue("a2", "A")    # stamped 400.0 -- looks earlier, is not
        assert first.arrived_at > second.arrived_at, "the clock did not run backwards"
        assert first.seq < second.seq, "the counter did not run forwards"
        slots.give_back("holder")
        assert first.granted.is_set(), "the later stamp was served first"
        assert not second.granted.is_set()


class TestAWaitingJobCanBeTakenOut:

    def test_a_waiting_job_can_be_cancelled(self):
        slots = Slots(ceiling=1, queue_depth=4)
        slots.take_or_queue("holder", "A")
        ticket = slots.take_or_queue("waiting", "B")
        assert slots.drop("waiting", "cancelled") is True
        assert ticket.dropped == "cancelled"
        assert slots.wait_for_slot(ticket, timeout=0) is False

    def test_a_dropped_job_is_woken_rather_than_left_to_time_out(self):
        """A waiter that sits until a timeout is a thread nobody freed and a client nobody
        answered."""
        slots = Slots(ceiling=1, queue_depth=4)
        slots.take_or_queue("holder", "A")
        ticket = slots.take_or_queue("waiting", "B")
        woke: list[bool] = []

        def wait():
            woke.append(slots.wait_for_slot(ticket, timeout=10))

        t = threading.Thread(target=wait)
        t.start()
        time.sleep(0.05)
        slots.drop("waiting", "cancelled")
        t.join(timeout=5)
        assert woke == [False], "the waiter was not woken by the drop"

    def test_a_dropped_job_never_takes_the_slot_it_was_waiting_for(self):
        slots = Slots(ceiling=1, queue_depth=4)
        slots.take_or_queue("holder", "A")
        ticket = slots.take_or_queue("waiting", "B")
        slots.drop("waiting", "cancelled")
        slots.give_back("holder")
        assert slots.in_use() == 0, "a dropped job was promoted anyway"
        assert not ticket.dropped == ""

    def test_dropping_everything_at_once_leaves_nothing_to_promote(self):
        """What a kill switch needs. Dropping one at a time lets a job slip from waiting into a
        slot freed by the drop before it -- which is the case a stop has to prevent."""
        slots = Slots(ceiling=2, queue_depth=10)
        slots.take_or_queue("h1", "A")
        slots.take_or_queue("h2", "B")
        tickets = [slots.take_or_queue("w%d" % i, "C%d" % i) for i in range(6)]
        gone = slots.drop_every(lambda t: True, "stopped")
        assert len(gone) == 6 and slots.waiting() == 0
        slots.give_back("h1")
        slots.give_back("h2")
        assert slots.in_use() == 0
        assert all(t.dropped == "stopped" for t in tickets)

    def test_dropping_one_account_leaves_the_others_waiting(self):
        """A suspension is about one customer. It must not empty the queue."""
        slots = Slots(ceiling=1, queue_depth=10)
        slots.take_or_queue("holder", "Z")
        mine = [slots.take_or_queue("a%d" % i, "A") for i in range(3)]
        theirs = slots.take_or_queue("b", "B")
        gone = slots.drop_every(lambda t: t.account_id == "A", "suspended")
        assert sorted(gone) == ["a0", "a1", "a2"]
        assert all(t.dropped == "suspended" for t in mine)
        assert theirs.dropped == "" and slots.waiting() == 1


class TestSlotsComeBack:

    def test_a_finished_run_frees_its_slot(self):
        slots = Slots(ceiling=1, queue_depth=1)
        slots.take_or_queue("a", "A")
        slots.give_back("a")
        assert slots.in_use() == 0
        assert slots.take_or_queue("b", "B") is None

    def test_giving_back_a_slot_nobody_held_changes_nothing(self):
        """A caller that cannot tell whether it got as far as holding one would have to guess,
        and guessing wrong either leaks a slot forever or hands out one still in use."""
        slots = Slots(ceiling=1, queue_depth=1)
        slots.take_or_queue("a", "A")
        slots.give_back("never-existed")
        assert slots.in_use() == 1

    def test_one_freed_slot_promotes_exactly_one(self):
        slots = Slots(ceiling=1, queue_depth=5)
        slots.take_or_queue("holder", "A")
        waiting = [slots.take_or_queue("w%d" % i, "B%d" % i) for i in range(4)]
        slots.give_back("holder")
        assert sum(1 for t in waiting if t.granted.is_set()) == 1
        assert slots.in_use() == 1

    def test_a_run_of_jobs_through_a_ceiling_of_two_never_exceeds_it(self):
        """The property over time rather than at one instant: whatever the interleaving, the
        number holding a slot is never more than the ceiling."""
        slots = Slots(ceiling=2, queue_depth=50)
        seen_at_once: list[int] = []
        done = threading.Event()

        def watch():
            while not done.is_set():
                seen_at_once.append(slots.in_use())
                time.sleep(0.001)

        watcher = threading.Thread(target=watch)
        watcher.start()

        def work(i):
            name = "r%d" % i
            ticket = slots.take_or_queue(name, "acct-%d" % (i % 3))
            if ticket is not None:
                slots.wait_for_slot(ticket, timeout=30)
            time.sleep(0.01)
            slots.give_back(name)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        done.set()
        watcher.join(timeout=5)
        assert seen_at_once, "the watcher saw nothing, so this proved nothing"
        assert max(seen_at_once) <= 2, "saw %d at once" % max(seen_at_once)
        assert slots.in_use() == 0 and slots.waiting() == 0


class TestWhatAWaitingCustomerCanSee:

    def test_there_is_no_position_to_read(self):
        """A position is by construction a tally of other people's jobs. There is no way to
        report one that does not disclose them, so there is none to report."""
        slots = Slots(ceiling=1, queue_depth=5)
        slots.take_or_queue("holder", "A")
        ticket = slots.take_or_queue("mine", "B")
        for name in vars(ticket):
            assert "position" not in name and "index" not in name and "ahead" not in name
        assert not hasattr(ticket, "position")

    def test_a_ticket_names_only_its_own_job_and_account(self):
        slots = Slots(ceiling=1, queue_depth=5)
        slots.take_or_queue("holder", "somebody-else")
        ticket = slots.take_or_queue("mine", "me")
        said = repr(vars(ticket))
        assert "somebody-else" not in said
        assert "mine" in said and "me" in said
