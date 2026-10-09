"""A job that is only waiting has no network, and a lock that arrives while it waits still reaches it.

These are the last two of the fifteen egress properties, and they are the two that cannot be
satisfied by the egress code at all: by the time `start_egress_proxy` runs, the job is running. What
they are about is everything BEFORE that -- whether a queued job ever gets as far as a network being
built for it, and whether a suspension, a stop or a withdrawal that arrives while it waits can be
outrun by the slot being granted.

So nothing here asserts on what the egress module does. Each test watches the ONE seam where a
network can come into existence, and the question is whether that seam is reached at all.

THE POSITIVE CONTROL IS NOT OPTIONAL. "The seam was not reached" is what a broken harness says too:
a mis-spelled patch target, a service that refuses for an unrelated reason, a record that was never
queued. `TestTheWatchCanSeeTheSeam` runs a job that really does run, through the same watch, and
fails if the watch stays silent. Without it every assertion below would pass against a product that
had no egress at all.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time

import pytest

from agentnode_sdk.gateway import meter


def _limits():
    from agentnode_sdk.sandbox.contract import Limits, SandboxPolicy

    return SandboxPolicy(limits=Limits(cpu=1.0, memory_mb=512, wall_clock_s=60))


def _line_for(root, run_id: str) -> dict:
    where = pathlib.Path(root) / meter.METER_NAME
    lines = [json.loads(x) for x in where.read_text(encoding="utf-8").splitlines() if x.strip()] \
        if where.is_file() else []
    for line in lines:
        if line.get("run_id") == run_id:
            return line
    raise AssertionError("no usage line for %r; there are %d" % (run_id, len(lines)))


class TheWatch:
    """Everything that would have to happen for a job to have a network, recorded.

    It patches the two functions that between them are the whole of "this job got a network": the
    one that builds it and the one that measures it. A job that reached neither has none -- not
    because it was denied one, but because nothing ever asked.
    """

    def __init__(self):
        self.built = []
        self.verified = []

    def install(self, monkeypatch, *, allow=True):
        from agentnode_sdk.sandbox import egress as _egress
        from agentnode_sdk.sandbox import egress_verify as _verify
        from agentnode_sdk.worker import local as _local

        def _build(*argv, **kwargs):
            self.built.append({"argv": argv, "kwargs": sorted(kwargs)})
            if not allow:
                raise AssertionError("a network was built for a job that must not have one")
            return _NOTHING_REAL

        def _measure(handle):
            self.verified.append(handle)
            return {"watched": True}

        for where in (_egress, _local):
            if hasattr(where, "start_egress_proxy"):
                monkeypatch.setattr(where, "start_egress_proxy", _build)
        for where in (_verify, _local):
            if hasattr(where, "verify_the_boundary"):
                monkeypatch.setattr(where, "verify_the_boundary", _measure)
        return self

    @property
    def quiet(self) -> bool:
        return not self.built and not self.verified


class _NothingReal:
    """A handle that is not a network. If anything tried to USE what the watch returned, it would
    fail loudly here rather than quietly behaving like a working boundary."""

    network_name = "the-watch-never-made-a-network"
    proxy_url = "http://the-watch-never-made-a-proxy:0"

    def as_record(self):
        return {"watched": True}

    def __getattr__(self, name):
        raise AssertionError("something used the watch's handle as if it were a real network: %r"
                            % name)


_NOTHING_REAL = _NothingReal()


@pytest.fixture()
def capped(tmp_path):
    """One run at once, two may wait. The same shape the other capacity tests use."""
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


def _queued_job(service, who, run_id="the-waiting-job"):
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


class TestTheWatchCanSeeTheSeam:
    """THE CONTROL. If this fails, nothing else in this file means anything.

    It does not go through the gateway: it calls the worker's own run, which is where a network is
    built, so the watch is proved against the shortest path to the seam it claims to watch.
    """

    def test_a_job_that_really_runs_builds_a_network_and_has_it_measured(self, monkeypatch,
                                                                        tmp_path):
        from agentnode_sdk.sandbox.contract import Limits
        from agentnode_sdk.worker import Job
        from agentnode_sdk.worker.local import LocalWorker

        watch = TheWatch().install(monkeypatch)

        class _Backend:
            """Not a sandbox. Enough of one that the run reaches the seam and comes back."""

            name = "watched"
            runtime = "podman"

            def run_process(self, *argv, **kwargs):
                # (exit code, stdout, stderr) -- what the real backend returns.
                return (0, "", "")

            def __getattr__(self, name):
                return lambda *a, **k: None

        # network="egress" is what makes the worker build one. That string is the job's own word
        # for a restricted network; the destinations travel beside it.
        job = Job(run_id="r" * 32, container_name="agentnode-control-abcd",
                  command=("true",), artifact=b"", stdin="", network="egress",
                  allowed_domains=("example.invalid",), limits=Limits(),
                  owner_label="c0n7r01ab1e1abe1", epoch="e1")
        try:
            LocalWorker(_Backend()).run(job)
        except Exception as exc:                                       # noqa: BLE001
            # A stand-in backend is not a sandbox, so the run may still fail further on. What
            # must not happen is the seam being silent.
            if watch.quiet:
                pytest.fail("the watch saw nothing even on a job that ran: it is watching the "
                            "wrong thing, and every other test in this file is vacuous (%s: %s)"
                            % (type(exc).__name__, exc))
        assert watch.built, (
            "no network was built for a job whose policy asked for a restricted one. The watch "
            "is patched into the wrong place and the rest of this file proves nothing.")
        assert watch.verified, "the boundary was never measured for a job that ran"


class TestAJobThatIsOnlyWaitingHasNoNetwork:
    """EG14. Not "its network is empty" -- it has none, because nothing was ever built."""

    def test_a_queued_job_has_built_nothing_while_it_waits(self, capped, monkeypatch):
        from tests.test_two_accounts import _a_customer

        watch = TheWatch().install(monkeypatch, allow=False)
        who = _a_customer(capped, "the-waiting-machine")
        record = _queued_job(capped, who)

        # It is waiting, and it stays waiting: the slot is held by somebody else and nothing
        # gives it back.
        assert capped.slots.waiting() == 1
        assert watch.quiet, "something was built for a job that has not been given a slot"
        assert record.started_at == 0.0
        assert record.container_name == "", "a sandbox was named for a job that is still waiting"
        assert record.route_out == {}, (
            "the record already carries a route out for a job that is only waiting: %r"
            % (record.route_out,))

    def test_a_waiting_job_that_is_then_dropped_never_builds_one(self, capped, monkeypatch):
        """The whole point: between being dropped and being told, there is no window in which a
        network exists. `allow=False` makes building one an immediate failure rather than something
        a later assertion has to notice."""
        from tests.test_two_accounts import _a_customer

        watch = TheWatch().install(monkeypatch, allow=False)
        who = _a_customer(capped, "the-waiting-machine")
        record = _queued_job(capped, who)

        assert capped.slots.drop(record.run_id, "stopped") is True
        assert capped._wait_for_a_slot(record, _limits()) is False
        assert watch.quiet
        line = _line_for(capped.state.root, record.run_id)
        assert line["seconds"] == 0.0
        # AND THE USAGE LINE SAYS IT HAD NO ROUTE OUT. A blank is the honest answer for a job
        # that never ran; a word there would describe a boundary that never existed.
        assert line.get("egress", "") == "", (
            "the usage line claims a route out of %r for a job that never ran" % line.get("egress"))
        assert line.get("egress_sha256", "") == ""

    def test_the_queue_itself_holds_nothing_that_could_become_a_network(self, capped):
        """A ticket is a run id, an account, an arrival number and a time. Were it to carry the
        policy, something could act on it while the job was still waiting."""
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "the-waiting-machine")
        record = _queued_job(capped, who)
        ticket = record.slot_ticket
        carried = {name for name in dir(ticket) if not name.startswith("_")}
        for forbidden in ("policy", "egress", "egress_allow", "network", "argv", "image"):
            assert forbidden not in carried, (
                "a waiting ticket carries %r, so something could build a network from a queue "
                "entry" % forbidden)


class TestALockThatArrivesWhileAJobWaitsStillReachesIt:
    """EG15. A suspension, a stop and a withdrawal each have to prevent the LATER allocation of a
    job that was already waiting when they arrived -- and each reaches it by a different route,
    which is why each is asked separately rather than once."""

    def test_a_withdrawn_device_prevents_the_allocation(self, capped, monkeypatch):
        from agentnode_sdk.access import dispatch
        from tests.test_two_accounts import _a_customer

        watch = TheWatch().install(monkeypatch, allow=False)
        who = _a_customer(capped, "the-withdrawn-machine")
        record = _queued_job(capped, who)

        dispatch._devices_revoke(capped, who, {"device_id": who.client_id})

        assert capped._wait_for_a_slot(record, _limits()) is False
        assert watch.quiet
        assert record.slot_ticket.dropped == "revoked"

    def test_the_operator_stopping_the_machine_prevents_the_allocation(self, capped, monkeypatch):
        watch = TheWatch().install(monkeypatch, allow=False)
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "the-waiting-machine")
        record = _queued_job(capped, who)

        gone = capped.slots.drop_every(lambda ticket: True, "stopped")

        assert record.run_id in gone
        assert capped._wait_for_a_slot(record, _limits()) is False
        assert watch.quiet

    def test_a_suspension_is_asked_again_when_the_slot_is_granted(self, capped, monkeypatch):
        """The suspension cannot drop a ticket -- it is applied by another process entirely -- so
        it is enforced at the last moment before foreign code runs. That moment is what this test
        is about, and a network must not be built on the way to the refusal."""
        watch = TheWatch().install(monkeypatch, allow=False)
        from tests.test_two_accounts import _a_customer

        who = _a_customer(capped, "the-suspended-account")
        record = _queued_job(capped, who)

        # The slot becomes free while the account is no longer in good standing.
        suspended = {"why": ""}

        def _refuse_now(*argv, **kwargs):
            from agentnode_sdk.gateway.admission import Refusal

            raise Refusal("account_suspended", suspended["why"] or "suspended", "")

        suspended["why"] = "unpaid"
        monkeypatch.setattr(capped, "may_this_caller_proceed", _refuse_now, raising=False)
        capped.slots.give_back("somebody-elses-run")

        assert capped._wait_for_a_slot(record, _limits()) is False, (
            "the slot was granted to a suspended account")
        assert watch.quiet, "a network was built for an account that is not allowed to run"

    def test_the_wait_is_woken_rather_than_held_until_a_slot_frees(self, capped, monkeypatch):
        """A lock that only takes effect when the machine empties is not a lock. It is here as an
        egress property because a job left in the queue keeps a place that a job entitled to run
        would otherwise get."""
        from agentnode_sdk.access import dispatch
        from tests.test_two_accounts import _a_customer

        TheWatch().install(monkeypatch, allow=False)
        who = _a_customer(capped, "the-withdrawn-machine")
        record = _queued_job(capped, who)

        woke = threading.Event()
        threading.Thread(target=lambda: (capped.slots.wait_for_slot(record.slot_ticket),
                                         woke.set()), daemon=True).start()
        assert not woke.wait(0.2), "it was not waiting in the first place"

        dispatch._devices_revoke(capped, who, {"device_id": who.client_id})

        assert woke.wait(5.0), "the withdrawal did not wake the job that was waiting"
        assert capped.slots.in_use() == 1, (
            "it woke because the machine emptied, which would prove nothing about the withdrawal")


class TestTwoWaitingJobsOfDifferentAccountsShareNothing:
    """Parallel accounts cannot observe each other's network state -- and a queue is the one place
    where two accounts' jobs are held side by side in one process, so it is where that could leak."""

    def test_neither_waiting_job_can_see_anything_of_the_others(self, capped, monkeypatch):
        from tests.test_two_accounts import _a_customer

        TheWatch().install(monkeypatch, allow=False)
        one = _a_customer(capped, "machine-one")
        two = _a_customer(capped, "machine-two")
        first = _queued_job(capped, one, run_id="run-of-account-one")
        second = capped.slots.take_or_queue("run-of-account-two", two.account_id)
        assert second is not None, "the second job did not have to wait, so this tests nothing"

        assert first.slot_ticket.account_id != second.account_id
        assert first.slot_ticket.run_id != second.run_id
        # Dropping one must not touch the other, in either direction.
        assert capped.slots.drop("run-of-account-one", "stopped") is True
        assert second.dropped == "", (
            "dropping one account's waiting job dropped another account's: %r" % second.dropped)
        assert capped.slots.waiting() == 1
