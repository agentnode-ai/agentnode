"""Which machine each bound fact is about.

`ReportBinding.boot_id` existed to stop a measurement outliving a reboot: a restart can bring a
new kernel, a cgroup controller that is no longer mounted, or a policy that loaded differently,
none of which move the image digest and all of which change what a container actually gets.

It was filled from the GATEWAY's kernel while describing the WORKER's. On one machine those are
the same value and nothing could go wrong. On two they are two different facts, and the field
would have been wrong in both directions at once: a worker reboot would leave a stale
measurement looking current -- the exact failure the field exists to prevent -- and a gateway
reboot would throw away a good measurement while telling an operator that "this machine" had
restarted, about the wrong machine.

`remote-worker-r1` R10 asks that the signed proof binds the situation, and the decision's Q7
answer is to bind both, named. These are that.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

import dataclasses

from agentnode_sdk.gateway.readiness import ReportBinding


class TestBothMachinesAreNamed:

    def test_there_is_no_unqualified_boot_field_left(self):
        """A field called `boot_id` on a two-machine binding is a question nobody can answer by
        reading it. The whole point is that the name says which host."""
        fields = set(ReportBinding.__dataclass_fields__)
        assert "boot_id" not in fields
        assert {"worker_boot_id", "gateway_boot_id"} <= fields

    def test_both_are_carried_into_the_record(self):
        said = ReportBinding(worker_boot_id="w-boot", gateway_boot_id="g-boot").as_dict()
        assert said["worker_boot_id"] == "w-boot"
        assert said["gateway_boot_id"] == "g-boot"
        assert "boot_id" not in said

    def test_they_are_compared_separately(self):
        one = ReportBinding(worker_boot_id="w1", gateway_boot_id="g1")
        worker_rebooted = dataclasses.replace(one, worker_boot_id="w2")
        gateway_rebooted = dataclasses.replace(one, gateway_boot_id="g2")
        assert one.mismatches(worker_rebooted) == ("worker_boot_id",)
        assert one.mismatches(gateway_rebooted) == ("gateway_boot_id",)


class TestTheWorkersBootComesFromTheWorker:

    def test_the_interface_can_be_asked(self):
        """Every worker answers for its OWN machine. A `LocalWorker` is on the same machine as
        the gateway and answers the same value; a remote one answers a different one, and the
        difference is the point."""
        from agentnode_sdk.worker import Worker

        assert hasattr(Worker, "boot_id")

    def test_a_remote_worker_reports_the_boot_it_was_told_over_the_wire(self):
        from agentnode_sdk.worker.remote import SocketWorker

        client = SocketWorker("unix:///nowhere.sock", b"k" * 32)
        client._described = {"boot_id": "the-workers-boot"}
        client._agreed = "agentnode-worker/1"
        assert client.boot_id() == "the-workers-boot"

    def test_a_worker_that_gives_no_boot_says_so_rather_than_guessing(self):
        from agentnode_sdk.worker.remote import SocketWorker

        client = SocketWorker("unix:///nowhere.sock", b"k" * 32)
        client._described = {}
        client._agreed = "agentnode-worker/1"
        assert client.boot_id() == ""

    def test_the_worker_puts_its_own_boot_in_describe(self):
        from agentnode_sdk.worker import service

        assert callable(service._own_boot_id)


class TestWhatAnOperatorIsTold:

    def test_the_reboot_message_names_which_machine(self):
        """"This machine has restarted" is the wrong sentence when it was the other one."""
        import inspect

        from agentnode_sdk.gateway import readiness

        source = inspect.getsource(readiness.ReadinessGate.evaluate_document)
        assert "the machine that runs the sandbox worker" in source
        assert "this gateway's machine" in source
        assert "this machine has restarted since it was last measured" not in source
