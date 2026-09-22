"""mtls-loopback-identity-r1, decision stage 4: the same guarantees either way, and a record bound
to who the connection proved.

**Parity.** The same job through a real gateway twice: once to a worker behind the unix socket,
once to the same kind of worker behind the TLS door. Compared field by field: the policy it was
admitted under, the quota it consumed, how it ended, what became of its sandbox, what it was
billed, what the audit and event logs say. They must agree everywhere except in HOW the worker was
reached and WHO that connection proved -- which is what `worker_transport` and `worker_identity`
are for, and the only place they may differ. Billed seconds are compared by the rule that produces
them (`finished_at - started_at` of the slot), not by equality of two wall clocks.

The socket half needs a unix socket, so the comparison runs on the Linux lanes and on the alpha,
and on a machine without one it says so rather than passing.

**Binding (M14).** Two real worker instances, w1 and w2, both regularly issued and both accepted
by the gateway. The job goes to w2, whose self-report has been substituted to say "w1" -- AFTER
the connection was authorised, because that is the only place a substitution can live without
an identity check refusing it first. The signed line must say w2. If it said w1, the record
would be believing the worker's word about itself.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway import meter
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server
from agentnode_sdk.worker.remote import SocketWorker, TlsWorker
from agentnode_sdk.worker.service import Bench
from tests import consent
from tests.test_em3c_gateway import StandInBackend, _granted, _paired, _store_measurement
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import KEY, Door, World
from tests.test_socket_worker import AWorkerThatAnswers

TERMINAL = ("finished", "failed", "cancelled", "interrupted", "timed_out", "killed",
            "refused", "unverified")


class Gw:
    """A real gateway over HTTP whose worker is the one handed in."""

    def __init__(self, root: Path, worker) -> None:
        self.state = GatewayState(str(root), version="test")
        self.service = GatewayService(self.state, backend=StandInBackend())
        self.service._worker = worker
        _store_measurement(self.service)
        self.service.CONTAINER_APPEAR_SECONDS = 0.5
        self.server = make_server(self.service, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.conn = _paired(self.base, self.state)

    def close(self) -> None:
        try:
            self.server.shutdown()
        finally:
            try:
                self.service.close()
            except Exception:                                  # noqa: BLE001
                pass


def a_gateway_over(root: Path, worker) -> Gw:
    return Gw(Path(root), worker)


def submit_and_wait(gw: Gw, run_id: str) -> dict:
    try:
        consent.submit(gw.conn, b"print('x')", granted=_granted(gw.service), run_id=run_id)
    except Exception:                                          # noqa: BLE001 - a refusal is read below
        pass
    record: dict = {}
    for _ in range(600):
        try:
            record = gc.status_of(gw.conn, run_id)
        except Exception:                                      # noqa: BLE001
            record = {}
        if record.get("state") in TERMINAL:
            return record
        time.sleep(0.05)
    return record


def the_line(gw: Gw, run_id: str) -> dict:
    lines = [l for l in meter.read(gw.state.root) if l.get("run_id") == run_id]
    assert len(lines) == 1, lines
    return lines[0]


def _log(gw: Gw, name: str) -> list[dict]:
    path = Path(gw.state.root) / name
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


#: What the two lines must agree on. Everything a line says, except identifiers and wall-clock
#: times, which differ by construction, and the two fields whose whole job is to differ.
SAME = ("cpu", "memory_mb", "wall_clock_s", "state", "outcome", "termination_reason",
        "exit_code", "sandbox", "bytes_out", "ever_started", "worker_topology",
        "worker_topology_means", "worker_id", "allowance_sha256", "allowance_admitted_under",
        "operator_policy_sha256", "operator_policy_version")
DIFFER = ("worker_transport", "worker_identity")


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or not hasattr(os, "getuid"),
                    reason="the socket half of the comparison needs a unix socket; it runs on "
                           "the Linux lanes and on the alpha")
class TestTheSameJobEitherWay:

    def test_the_same_job_gets_the_same_guarantees_either_way(self, tmp_path):
        world = World(tmp_path / "pki")
        gateway_dir = world.service("gateway", "g1")
        worker_dir = world.service("worker", "w1")

        socket_dir = tmp_path / "s"
        bench = Bench(AWorkerThatAnswers(), "unix://" + str(socket_dir / "w.sock"), KEY,
                      only_uid=os.getuid())
        bench.label = "w1"
        bench.open()
        threading.Thread(target=bench.serve_forever, daemon=True).start()
        door = Door(world, worker_dir, {"g1"}, label="w1")

        over_socket = a_gateway_over(tmp_path / "gw-socket", SocketWorker(bench.address, KEY))
        over_tls = a_gateway_over(tmp_path / "gw-tls",
                                  TlsWorker(door.address, KEY,
                                            world.settings(gateway_dir, {"w1"})))
        try:
            a = submit_and_wait(over_socket, "same-job")
            b = submit_and_wait(over_tls, "same-job")
            assert a.get("state") == b.get("state") == "finished", (a, b)

            one, two = the_line(over_socket, "same-job"), the_line(over_tls, "same-job")
            for name in SAME:
                assert one[name] == two[name], (name, one[name], two[name])
            # Billed by the same rule on both, from each run's own slot: the signed line carries
            # the slot's length rounded to the millisecond (`meter.record`), exactly.
            for line in (one, two):
                slot = max(0.0, line["finished_at"] - line["started_at"])
                assert line["seconds"] == round(slot, 3), (line["seconds"], slot)
            # And distinguishable where, and only where, they should be.
            assert one["worker_transport"] == "unix"
            assert two["worker_transport"] == "mtls"
            assert one["worker_identity"] == bench.address
            assert two["worker_identity"] == "agentnode://alpha/worker/w1"

            # The quota each consumed: one run apiece, charged the slot's length unrounded.
            for gw, line in ((over_socket, one), (over_tls, two)):
                runs, seconds = gw.service.use.so_far(line["account_id"])
                assert runs == 1
                assert seconds == pytest.approx(
                    max(0.0, line["finished_at"] - line["started_at"]), abs=1e-5)

            # The audit and event logs say the same things in the same order. The audit log is
            # per OPERATION, not per run, and how many status polls a client makes depends on
            # timing -- so what is compared is the ordered set of distinct (operation, via,
            # outcome), which is what each path did and how each ended.
            def shape(entries, keep):
                seen: list = []
                for e in entries:
                    one_ = tuple(e.get(k) for k in keep)
                    if one_ not in seen:
                        seen.append(one_)
                return seen

            assert shape(_log(over_socket, "audit.jsonl"), ("operation", "via", "outcome")) == \
                shape(_log(over_tls, "audit.jsonl"), ("operation", "via", "outcome"))
            assert [e.get("what") for e in _log(over_socket, "events.jsonl")] == \
                [e.get("what") for e in _log(over_tls, "events.jsonl")]

            for gw in (over_socket, over_tls):
                assert meter.verify(gw.state.root)["ok"]
        finally:
            over_socket.close()
            over_tls.close()
            door.close()
            bench.stop_serving()


class TestTheRecordIsBoundToWhoTheConnectionProved:

    def test_the_signed_line_names_the_instance_the_handshake_checked(self, tmp_path):
        world = World(tmp_path / "pki")
        gateway_dir = world.service("gateway", "g1")
        world.service("worker", "w1")
        w2 = world.service("worker", "w2")
        # w2 answers the describe calling itself "w1". Both are accepted by the gateway, so no
        # identity check refuses anything: the ONLY thing that can put w2 in the record is the
        # binding to the connection.
        door = Door(world, w2, {"g1"}, label="w1")
        client = TlsWorker(door.address, KEY, world.settings(gateway_dir, {"w1", "w2"}))
        gw = a_gateway_over(tmp_path / "gw", client)
        try:
            assert submit_and_wait(gw, "who-ran").get("state") == "finished"
            assert client._describe()["instance_label"] == "w1", "the self-report was substituted"
            line = the_line(gw, "who-ran")
            assert line["worker_id"] == "w2"
            assert line["worker_identity"] == "agentnode://alpha/worker/w2"
            assert line["worker_transport"] == "mtls"
            assert meter.verify(gw.state.root)["ok"]
        finally:
            gw.close()
            door.close()

    def test_and_w1_is_recorded_as_w1(self, tmp_path):
        """The control: the other regularly issued instance, reached directly, is w1."""
        world = World(tmp_path / "pki")
        gateway_dir = world.service("gateway", "g1")
        w1 = world.service("worker", "w1")
        world.service("worker", "w2")
        door = Door(world, w1, {"g1"}, label="w1")
        client = TlsWorker(door.address, KEY, world.settings(gateway_dir, {"w1", "w2"}))
        gw = a_gateway_over(tmp_path / "gw", client)
        try:
            assert submit_and_wait(gw, "w1-ran").get("state") == "finished"
            line = the_line(gw, "w1-ran")
            assert (line["worker_id"], line["worker_identity"]) == \
                ("w1", "agentnode://alpha/worker/w1")
        finally:
            gw.close()
            door.close()
