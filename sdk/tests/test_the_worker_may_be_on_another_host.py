"""Declaring that the worker is on another machine, and what that declaration forecloses.

`remote-worker-r1` R1 asks whether a configured `remote` topology speaks mutual TLS and nothing
else, and whether the absence of a fallback is enforced by construction or merely unexercised.
These are about construction: the point of most of them is that something is REFUSED, and refused
before any connection is attempted.

The rule being tested is in `worker/topology.py`. Its shape is a pair -- a declaration and an
address -- because either one alone is the wrong thing to hang a machine boundary on. An address
can be mistyped. A declaration with no matching address reaches nothing. Requiring both means a
typo is a refusal rather than a crossing.

NONE OF THIS IS A HOST-ISOLATION TEST. Everything here runs in one process on one kernel. What it
establishes is that the code would address a worker elsewhere and would refuse to address one
anywhere else; whether two machines actually isolate anything is R16, and no machine here is a
second one.
"""
from __future__ import annotations

import pytest

from tests.test_mtls_transport import world  # noqa: F401  (a pytest fixture)

from agentnode_sdk.worker import (SEPARATE_WORKER_HOST, SINGLE_HOST_DEVELOPMENT,
                                  WorkerUnreachable)
from agentnode_sdk.worker import topology as T
from agentnode_sdk.worker.remote import SocketWorker, from_address
from agentnode_sdk.worker.tls import NotLoopback, endpoint

KEY = b"k" * 32

A_REMOTE_ADDRESS = "tcps://10.0.1.5:8443"
A_LOOPBACK_ADDRESS = "tcps://127.0.0.1:8443"
A_SOCKET = "unix:///run/agentnode/worker.sock"


class TestTheDeclarationAndTheAddressMustAgree:
    """Neither one alone decides where the worker is."""

    def test_a_remote_address_with_a_remote_declaration_is_accepted(self):
        assert T.check(SEPARATE_WORKER_HOST, A_REMOTE_ADDRESS) == T.REMOTE_TCPS

    def test_a_remote_address_with_no_declaration_at_all_is_refused(self):
        """The case a mistyped address produces, and the reason the pair exists."""
        with pytest.raises(T.TopologyRefused) as refused:
            T.check("", A_REMOTE_ADDRESS)
        assert refused.value.cause == T.NOT_DECLARED
        assert T.DECLARED_KEY in refused.value.what_to_do

    def test_a_remote_address_declared_local_is_refused(self):
        with pytest.raises(T.TopologyRefused) as refused:
            T.check(SINGLE_HOST_DEVELOPMENT, A_REMOTE_ADDRESS)
        assert refused.value.cause == T.DISAGREES

    def test_a_loopback_address_declared_remote_is_refused(self):
        """The other direction, which matters for a different reason: a record that said the
        worker was on its own machine while it was not would be a false record, and every
        signed report carries the topology."""
        with pytest.raises(T.TopologyRefused) as refused:
            T.check(SEPARATE_WORKER_HOST, A_LOOPBACK_ADDRESS)
        assert refused.value.cause == T.DISAGREES

    def test_a_topology_this_build_does_not_have_is_refused(self):
        with pytest.raises(T.TopologyRefused) as refused:
            T.check("somewhere-else", A_REMOTE_ADDRESS)
        assert refused.value.cause == T.UNKNOWN_TOPOLOGY

    def test_the_in_process_worker_still_needs_no_declaration(self):
        """The default build has no transport and no boundary. Requiring a declaration for it
        would break every existing installation for nothing."""
        assert T.check("", "") == T.IN_PROCESS


class TestThereIsNothingToFallBackTo:
    """R1's real question: is the absence of a fallback a fact, or merely untested?"""

    def test_a_unix_socket_is_not_among_the_arrangements_a_remote_declaration_permits(self):
        assert T.UNIX not in T.PERMITTED[SEPARATE_WORKER_HOST]
        assert T.IN_PROCESS not in T.PERMITTED[SEPARATE_WORKER_HOST]
        assert T.LOOPBACK_TCPS not in T.PERMITTED[SEPARATE_WORKER_HOST]

    def test_and_asking_for_one_under_a_remote_declaration_is_refused(self):
        for address in (A_SOCKET, "unix+stream:///run/agentnode/worker.sock", A_LOOPBACK_ADDRESS):
            with pytest.raises(T.TopologyRefused) as refused:
                T.check(SEPARATE_WORKER_HOST, address)
            assert refused.value.cause == T.DISAGREES

    def test_the_only_arrangement_a_remote_declaration_permits_is_mutual_tls(self):
        """Stated as the whole permitted set rather than as "tls is allowed", so that adding a
        transport later has to be a deliberate edit to this list and cannot be something that
        starts working by itself."""
        assert T.PERMITTED[SEPARATE_WORKER_HOST] == (T.REMOTE_TCPS,)

    def test_from_address_refuses_before_it_chooses_a_transport(self):
        """Not "it fails when it tries to connect" -- it does not get as far as choosing."""
        with pytest.raises(WorkerUnreachable) as refused:
            from_address(A_SOCKET, KEY, topology=SEPARATE_WORKER_HOST)
        assert getattr(refused.value, "cause", "") == T.DISAGREES

    def test_and_a_remote_address_with_no_certificate_settings_is_refused_not_downgraded(self):
        with pytest.raises(WorkerUnreachable) as refused:
            from_address(A_REMOTE_ADDRESS, KEY, tls=None, topology=SEPARATE_WORKER_HOST)
        assert "refused, not reached another way" in str(refused.value)


class TestTheAddressItself:
    def test_a_name_is_refused_in_both_arrangements(self):
        """A name resolves to wherever its owner points it, which can change without anybody
        touching this deployment."""
        for declared in (SINGLE_HOST_DEVELOPMENT, SEPARATE_WORKER_HOST):
            with pytest.raises(T.TopologyRefused) as refused:
                T.check(declared, "tcps://worker.internal:8443")
            assert refused.value.cause == T.ADDRESS_UNUSABLE

    @pytest.mark.parametrize("address", ["tcps://0.0.0.0:8443", "tcps://[::]:8443"])
    def test_every_interface_is_refused_in_both_arrangements(self, address):
        """The one bind that would undo the whole arrangement: a worker reachable from wherever
        the machine is reachable from, which on a cloud host means the internet."""
        for declared in (SINGLE_HOST_DEVELOPMENT, SEPARATE_WORKER_HOST):
            with pytest.raises(T.TopologyRefused) as refused:
                T.check(declared, address)
            assert refused.value.cause == T.WILDCARD

    def test_an_address_without_a_port_is_refused(self):
        with pytest.raises(T.TopologyRefused) as refused:
            T.check(SEPARATE_WORKER_HOST, "tcps://10.0.1.5")
        assert refused.value.cause == T.ADDRESS_UNUSABLE

    def test_a_scheme_this_build_does_not_speak_is_refused(self):
        for address in ("tcp://10.0.0.5:9000", "https://elsewhere.example", "ssh://box/sock"):
            with pytest.raises(T.TopologyRefused) as refused:
                T.check(SEPARATE_WORKER_HOST, address)
            assert refused.value.cause == T.ADDRESS_UNUSABLE


class TestTheGateStaysShutUnlessItIsOpened:
    """`endpoint()` is the function that used to settle this alone. Its default must not have
    moved: a caller that says nothing gets the loopback-only rule it always got."""

    @pytest.mark.parametrize("address", ["tcps://127.0.0.1:8443", "tcps://[::1]:8443"])
    def test_loopback_still_works_with_no_topology_given(self, address):
        host, port = endpoint(address)
        assert port == 8443

    @pytest.mark.parametrize("address", [
        "tcps://10.0.0.5:8443", "tcps://0.0.0.0:8443", "tcps://192.168.1.2:8443",
        "tcps://localhost:8443", "tcps://worker.example:8443", "tcps://[::]:8443",
    ])
    def test_and_everything_else_is_still_refused_with_no_topology_given(self, address):
        with pytest.raises(NotLoopback):
            endpoint(address)

    def test_the_gate_opens_only_for_an_explicit_declaration(self):
        with pytest.raises(NotLoopback):
            endpoint(A_REMOTE_ADDRESS)
        assert endpoint(A_REMOTE_ADDRESS, topology=SEPARATE_WORKER_HOST) == ("10.0.1.5", 8443)

    def test_the_refusal_carries_its_cause_and_a_step(self):
        """R12: a refusal that cannot be told from another refusal without reading the English
        is not structured, and one with nothing to do about it is an obstacle."""
        with pytest.raises(NotLoopback) as refused:
            endpoint(A_REMOTE_ADDRESS)
        assert refused.value.cause == T.DISAGREES
        assert refused.value.what_to_do


class TestWhatTheRecordSays:
    def test_the_worker_reports_the_arrangement_it_was_told_not_one_it_worked_out(self):
        """Every signed record carries the topology. It has to be the declared one: an address
        can be changed without anybody deciding anything, and a record should say which
        arrangement somebody chose."""
        local = SocketWorker(A_SOCKET, KEY, topology=SINGLE_HOST_DEVELOPMENT)
        assert local.topology == SINGLE_HOST_DEVELOPMENT
        elsewhere = SocketWorker(A_SOCKET, KEY, topology=SEPARATE_WORKER_HOST)
        assert elsewhere.topology == SEPARATE_WORKER_HOST

    def test_and_a_topology_carries_what_it_does_not_establish(self):
        from agentnode_sdk.worker import what_it_does_not_establish

        said = what_it_does_not_establish(SEPARATE_WORKER_HOST)
        assert said, "a record made under an arrangement must say what the arrangement leaves open"


class TestBothSidesJudgeIndependently:
    """A worker told it is on its own machine must not bind a loopback address because whoever
    dials it believes otherwise. Each side checks its own configuration, with real certificate
    settings, so what is being tested is the listener and not a missing argument."""

    def _a_listener(self, world, at, topology):
        from agentnode_sdk.worker.service import Bench
        from agentnode_sdk.worker.tls import TlsListener
        from tests.test_mtls_transport import DEPLOYMENT
        from tests.test_socket_worker import AWorkerThatAnswers

        worker = world.service("worker", "w1")
        bench = Bench(AWorkerThatAnswers(), "unix:///nowhere.sock", KEY, only_uid=None)
        return TlsListener(bench, at, world.settings(worker, {"g1"}, role="worker",
                                                     deployment=DEPLOYMENT),
                           topology=topology)

    def test_the_listener_refuses_an_address_its_own_declaration_forbids(self, world):
        # Declared local, told to listen somewhere that is not local.
        listener = self._a_listener(world, A_REMOTE_ADDRESS, SINGLE_HOST_DEVELOPMENT)
        with pytest.raises(NotLoopback) as refused:
            listener.open()
        assert refused.value.cause == T.DISAGREES

    def test_and_refuses_a_wildcard_even_when_it_is_declared_remote(self, world):
        listener = self._a_listener(world, "tcps://0.0.0.0:0", SEPARATE_WORKER_HOST)
        with pytest.raises(NotLoopback) as refused:
            listener.open()
        assert refused.value.cause == T.WILDCARD

    def test_and_a_remote_declaration_lets_it_bind_a_real_address(self, world):
        """The other half: the gate does open. Bound to a loopback alias so nothing leaves this
        machine -- which is exactly why this is a transport test and not an isolation one."""
        listener = self._a_listener(world, "tcps://127.0.0.2:0", SINGLE_HOST_DEVELOPMENT)
        host, _port = listener.open()
        try:
            assert host == "127.0.0.2"
        finally:
            listener.stop_serving()
