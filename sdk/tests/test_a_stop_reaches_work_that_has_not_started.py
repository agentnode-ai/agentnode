"""A suspension that reaches the queue, not only what is already running.

`reserve` checks standing when a job is admitted, and `_wait_for_a_slot` checks it again when a
slot is granted. Between those two there is a job sitting in the queue, and for a while the
gateway's own comment said nothing could be done about it: the suspension is applied by an
operator's command, in a different process, which cannot reach these tickets.

That is true of the suspension and not of the queue, which is in this process. R9 asks that a
stop reach work that has not started.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

from types import SimpleNamespace

from agentnode_sdk.gateway.capacity import Slots
from agentnode_sdk.gateway.server import GatewayService


class TestASuspensionReachesTheQueue:

    def _queued(self, *accounts):
        slots = Slots(ceiling=1, queue_depth=10)
        held = slots.take_or_queue("running", "someone-else")
        tickets = [slots.take_or_queue("run-%d" % i, account)
                   for i, account in enumerate(accounts)]
        return slots, held, tickets

    def test_a_waiting_job_for_a_suspended_account_is_dropped(self):
        slots, _held, _tickets = self._queued("alice")
        # `runs` AS WELL AS `_slots`. The sweep gained a second pass for a device the operator
        # withdrew, and that pass reads the run record to learn whose device a ticket belongs
        # to. This stand-in has no run records, so that pass drops nothing and these four
        # tests keep asking exactly what they asked before -- which is the point: the
        # alternative was a defensive `getattr(self, "runs", {})` in the product, and a
        # sweep that silently skips its own check when an attribute is missing is fail-open.
        service = SimpleNamespace(_slots=slots, runs={})
        service.standing_of = lambda account, _c: SimpleNamespace(
            suspended_because="by the operator" if account == "alice" else "")

        gone = GatewayService.drop_queued_work_that_is_no_longer_permitted(service)
        assert gone == ["run-0"]

    def test_a_waiting_job_for_an_account_in_good_standing_is_left_alone(self):
        slots, _held, _tickets = self._queued("bob")
        # `runs` AS WELL AS `_slots`. The sweep gained a second pass for a device the operator
        # withdrew, and that pass reads the run record to learn whose device a ticket belongs
        # to. This stand-in has no run records, so that pass drops nothing and these four
        # tests keep asking exactly what they asked before -- which is the point: the
        # alternative was a defensive `getattr(self, "runs", {})` in the product, and a
        # sweep that silently skips its own check when an attribute is missing is fail-open.
        service = SimpleNamespace(_slots=slots, runs={})
        service.standing_of = lambda account, _c: SimpleNamespace(suspended_because="")

        assert GatewayService.drop_queued_work_that_is_no_longer_permitted(service) == []

    def test_only_the_suspended_accounts_work_goes(self):
        slots, _held, _tickets = self._queued("alice", "bob", "alice")
        # `runs` AS WELL AS `_slots`. The sweep gained a second pass for a device the operator
        # withdrew, and that pass reads the run record to learn whose device a ticket belongs
        # to. This stand-in has no run records, so that pass drops nothing and these four
        # tests keep asking exactly what they asked before -- which is the point: the
        # alternative was a defensive `getattr(self, "runs", {})` in the product, and a
        # sweep that silently skips its own check when an attribute is missing is fail-open.
        service = SimpleNamespace(_slots=slots, runs={})
        service.standing_of = lambda account, _c: SimpleNamespace(
            suspended_because="by the operator" if account == "alice" else "")

        assert sorted(GatewayService.drop_queued_work_that_is_no_longer_permitted(service)) == [
            "run-0", "run-2"]

    def test_standing_that_cannot_be_read_does_not_throw_a_job_away(self):
        """Unreadable is not permission -- the admission path already refuses on it, with a
        reason. It is also not a reason for a job to vanish out of a queue on a guess."""
        slots, _held, _tickets = self._queued("alice")
        # `runs` AS WELL AS `_slots`. The sweep gained a second pass for a device the operator
        # withdrew, and that pass reads the run record to learn whose device a ticket belongs
        # to. This stand-in has no run records, so that pass drops nothing and these four
        # tests keep asking exactly what they asked before -- which is the point: the
        # alternative was a defensive `getattr(self, "runs", {})` in the product, and a
        # sweep that silently skips its own check when an attribute is missing is fail-open.
        service = SimpleNamespace(_slots=slots, runs={})

        def unreadable(_account, _client):
            raise RuntimeError("the account file could not be read")

        service.standing_of = unreadable
        assert GatewayService.drop_queued_work_that_is_no_longer_permitted(service) == []

    def test_a_gateway_with_no_queue_yet_does_nothing(self):
        assert GatewayService.drop_queued_work_that_is_no_longer_permitted(
            SimpleNamespace(_slots=None)) == []

    def test_the_watcher_calls_it(self):
        """The loop that already reads the stop file, so a suspension costs one more check on
        a pass that was happening anyway."""
        import inspect

        from agentnode_sdk.gateway import server

        source = inspect.getsource(server)
        watcher = source[source.index("def _watch_the_stop"):]
        assert "drop_queued_work_that_is_no_longer_permitted" in watcher[:4000]
