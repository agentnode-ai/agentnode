"""Waiting is never billed, and the signed line says so.

`test_capacity_queue.py` establishes who runs and who waits. This establishes what that costs,
against a real `GatewayService` writing its real signed log -- because the promise is about a
number in that log, and a promise about a number has to be read out of the file that carries it.

The sharpest case is the one to read first:
`test_a_job_cancelled_while_waiting_bills_nothing`. A job that waited ten seconds and never ran
must bill zero. Before this work the billed figure was `finished_at - started_at` with
`started_at` set when the record was CONSTRUCTED -- so that job would have been billed for its
entire wait, and a job that never started at all would have been billed the unix epoch.
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest

from agentnode_sdk.gateway import meter


def _lines(root) -> list[dict]:
    where = pathlib.Path(root) / meter.METER_NAME
    if not where.is_file():
        return []
    return [json.loads(x) for x in where.read_text(encoding="utf-8").splitlines() if x.strip()]


def _line_for(root, run_id: str) -> dict:
    for line in _lines(root):
        if line.get("run_id") == run_id:
            return line
    raise AssertionError("no usage line for %r; there are %d lines"
                         % (run_id, len(_lines(root))))


class TestTheShapeOfTheLine:
    """The wait and the billed time are separate values in the signed, chained record."""

    def test_the_line_carries_all_three_times_and_both_durations(self):
        assert "queued_at" in meter.FIELDS
        assert "started_at" in meter.FIELDS
        assert "finished_at" in meter.FIELDS
        assert "seconds" in meter.FIELDS
        assert "waited_s" in meter.FIELDS

    def test_and_the_chain_still_covers_them(self):
        """Adding fields must not put anything outside the signature. `SEALED` is what binds a
        line to the one before it; everything else is what the line SAYS, and the signature is
        over all of it."""
        assert set(meter.SEALED) == {"seq", "prev", "signature"}
        assert not set(meter.SEALED) & set(meter.FIELDS)

    def test_neither_duration_can_be_dictated_by_a_caller(self, tmp_path):
        """Both are derived from the three times. A meter that accepts a billed figure is a
        meter whose bills cannot be checked against anything."""
        import inspect

        taken = set(inspect.signature(meter.record).parameters)
        assert "seconds" not in taken
        assert "waited_s" not in taken


class TestWhatIsBilledAndWhatIsNot:

    def _write(self, tmp_path, *, queued, started, finished):
        meter.record(tmp_path, run_id="r", client_id="c", account_id="acct-" + "0" * 16,
                     queued_at=queued, started_at=started, finished_at=finished,
                     cpu=1.0, memory_mb=512, wall_clock_s=60, state="finished",
                     outcome="succeeded", bytes_out=0, worker_topology="x",
                     allowance_sha256="a" * 64, worker_id="w",
                     operator_policy_sha256="p" * 64, operator_policy_version=1)
        return _line_for(tmp_path, "r")

    def test_a_job_that_waited_is_billed_only_from_its_slot(self, tmp_path):
        line = self._write(tmp_path, queued=1000.0, started=1010.0, finished=1015.0)
        assert line["seconds"] == 5.0, "the wait was billed"
        assert line["waited_s"] == 10.0

    def test_a_job_that_never_started_is_billed_nothing(self, tmp_path):
        """`started_at` of zero means no slot was ever held. There is nothing to subtract from,
        which is the mechanism -- not a rule applied to the number afterwards."""
        line = self._write(tmp_path, queued=1000.0, started=0.0, finished=1010.0)
        assert line["seconds"] == 0.0
        assert line["waited_s"] == 10.0, "the wait was not recorded either"

    def test_and_it_is_not_billed_the_unix_epoch(self, tmp_path):
        """What the old arithmetic would have produced: `finished_at - 0.0`. Named as its own
        test because it is the failure that would have been noticed last -- an invoice for
        fifty-six billion seconds is absurd enough to be caught, and one for ten seconds of
        waiting is not."""
        line = self._write(tmp_path, queued=0.0, started=0.0, finished=time.time())
        assert line["seconds"] == 0.0
        assert line["seconds"] < 1.0

    def test_a_job_that_never_waited_has_no_wait_to_show(self, tmp_path):
        line = self._write(tmp_path, queued=1000.0, started=1000.0, finished=1002.0)
        assert line["seconds"] == 2.0 and line["waited_s"] == 0.0

    def test_neither_number_can_go_negative(self, tmp_path):
        """Clocks disagree, files are edited, and a negative duration on an invoice is a number
        somebody has to explain."""
        line = self._write(tmp_path, queued=2000.0, started=1010.0, finished=1000.0)
        assert line["seconds"] >= 0.0 and line["waited_s"] >= 0.0


class TestAgainstARealGateway:
    """The same promises, through a service that really admits, really queues and really writes.

    `StandInBackend` stands in for the container runtime -- these tests are about the gateway's
    accounting, and a real container would add minutes and establish nothing extra about it.
    """

    @pytest.fixture()
    def capped(self, tmp_path):
        """A gateway whose operator allows ONE run at once and lets ONE wait."""
        import json as _json

        from tests.test_em3c_gateway import StandInBackend, _store_measurement
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService

        root = tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        # THE NUMBER LIVES IN THE OPERATOR'S FILE. Setting it here the way an operator would is
        # the point: if this could only be done by editing the product, Q1 would already be lost.
        (root / "allowance.json").write_text(
            _json.dumps({"machine_concurrent_runs": 1, "queue_depth": 1}), encoding="utf-8")
        state = GatewayState(str(root), version="test")
        service = GatewayService(state, backend=StandInBackend())
        _store_measurement(service)
        try:
            yield service
        finally:
            service.close()
            state.close()

    def test_the_operator_file_is_where_the_ceiling_comes_from(self, capped):
        assert capped.allowance().machine_concurrent_runs == 1
        assert capped.allowance().queue_depth == 1
        assert capped.slots.ceiling == 1 and capped.slots.queue_depth == 1

    def test_lowering_the_ceiling_does_not_drop_what_is_already_waiting(self, capped, tmp_path):
        """An operator changing a limit must not be a way to lose work. The object is changed in
        place rather than replaced, so tickets already waiting are still waiting on the one that
        will promote them."""
        import json as _json

        first = capped.slots
        capped.slots.take_or_queue("holding", "A")
        ticket = capped.slots.take_or_queue("waiting", "B")
        (pathlib.Path(capped.state.root) / "allowance.json").write_text(
            _json.dumps({"machine_concurrent_runs": 2, "queue_depth": 4}), encoding="utf-8")
        again = capped.slots
        assert again is first, "the waiting job was left on an object nobody will promote from"
        assert again.ceiling == 2
        capped.slots.give_back("holding")
        assert ticket.granted.is_set()

    def test_a_job_cancelled_while_waiting_bills_nothing(self, capped):
        """THE SHARPEST CASE. It waited, it never ran, and the invoice is zero."""
        from agentnode_sdk.gateway.server import RunRecord

        capped.slots.take_or_queue("holding-the-slot", "acct-other")
        record = RunRecord(run_id="waited-then-cancelled", job_id="j",
                           owner_client_id="dev", owner_account_id="acct-mine")
        record.queued_at = time.time() - 10.0
        record.slot_ticket = capped.slots.take_or_queue(record.run_id, "acct-mine")
        assert record.slot_ticket is not None, "it should have had to wait"
        capped.runs[record.run_id] = record

        granted = capped.allowance()
        capped.slots.drop(record.run_id, "cancelled")
        assert capped._wait_for_a_slot(record, _limits()) is False

        assert record.started_at == 0.0, "the billed clock started for a job that never ran"
        line = _line_for(capped.state.root, record.run_id)
        assert line["seconds"] == 0.0, "a job that never ran was billed %s" % line["seconds"]
        assert line["waited_s"] >= 9.0, "the wait was not recorded"
        assert line["state"] == "cancelled"
        assert granted is not None

    def test_and_it_left_nothing_behind_to_clean_up(self, capped):
        from agentnode_sdk.gateway.server import RunRecord

        capped.slots.take_or_queue("holding-the-slot", "acct-other")
        record = RunRecord(run_id="nothing-to-clean", job_id="j",
                           owner_client_id="dev", owner_account_id="acct-mine")
        record.slot_ticket = capped.slots.take_or_queue(record.run_id, "acct-mine")
        capped.runs[record.run_id] = record
        capped.slots.drop(record.run_id, "cancelled")
        capped._wait_for_a_slot(record, _limits())
        assert record.container_name == "", "a container was named for a job that never ran"
        assert record.cleanup_verified is True

    def test_a_job_that_ran_is_billed_from_its_slot_not_its_arrival(self, capped):
        """A REAL customer, because the standing is re-checked at the slot and an account nobody
        ever created is rightly refused there. An earlier version of this test invented one and
        read the refusal as a bug in the clock -- it was the suspension check doing its job."""
        from agentnode_sdk.gateway.server import RunRecord
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "somebody-real")
        record = RunRecord(run_id="ran-after-waiting", job_id="j",
                           owner_client_id=who.client_id, owner_account_id=who.account_id)
        record.queued_at = time.time() - 8.0
        record.slot_ticket = None                  # a free machine: no wait
        assert capped._wait_for_a_slot(record, _limits()) is True
        assert record.started_at > 0.0
        record.finished_at = record.started_at + 2.0
        record.move_to("running")
        capped.write_down_what_it_used(record, _limits(), "finished")

        line = _line_for(capped.state.root, record.run_id)
        assert line["seconds"] == 2.0, "billed %s -- the arrival was counted" % line["seconds"]
        assert line["waited_s"] >= 7.0

    def test_the_quota_is_charged_the_billed_seconds_and_not_the_wait(self, capped):
        """Charging a customer's window quota for our queue would charge them twice for one
        ceiling -- once in money and once in what they are allowed to use.

        Read through the syntax tree rather than by splitting the text. A first version of this
        cut the source on the first `)` and landed inside a list comprehension, so it reported a
        failure about code that was correct -- a test that cannot read what it is judging is
        worse than none.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(capped.write_down_what_it_used)))
        charged = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Call)
                   and getattr(node.func, "attr", "") == "finished_every"]
        assert charged, "nothing charges the window quota any more"
        for call in charged:
            assert call.args, "finished_every was called with no duration at all"
            duration = ast.unparse(call.args[-1])
            assert duration == "billed", (
                "the window quota is charged %r rather than the billed figure" % duration)


def _limits():
    """The shape `write_down_what_it_used` reads limits out of. Built from the production
    dataclasses rather than a stand-in, so a change to either is a failure here."""
    from agentnode_sdk.sandbox.contract import Limits, SandboxPolicy

    return SandboxPolicy(limits=Limits(cpu=1.0, memory_mb=512, wall_clock_s=60))
