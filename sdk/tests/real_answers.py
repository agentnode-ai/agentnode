"""Gateway answers for the tests, produced by a real gateway over its real transport.

`EM3C-E3-CLASSIFY-0001`: the one authorised external run died because the evidence reader had
been given a hand-written list of an *inner* object's fields, while a client receives that object
inside an envelope the gateway stamps and signs. Every test passed, because every test built the
inner object by hand too. A double that does not produce what the real thing produces tests the
double.

So nothing here writes an answer. A real `GatewayService` is started on loopback, a real client
pairs with it, a real job is submitted and waited for, and the answer is fetched and verified by
the production client. What that returns is what the tests use.

The backend is a stand-in, because this machine has no container runtime and the answer's SHAPE
does not depend on whether a container really ran -- that is the container lane's job. Everything
between the run record and the bytes on the wire is the product's own.
"""
from __future__ import annotations

import threading

import pytest

from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server

from tests.test_em3c_gateway import StandInBackend, _granted, _store_measurement


class RealGateway:
    """One gateway, one paired client, and the answers it really gives."""

    def __init__(self, root):
        self.state = GatewayState(str(root), version="test")
        self.backend = StandInBackend()
        self.service = GatewayService(self.state, backend=self.backend)
        _store_measurement(self.service)
        self.server = make_server(self.service, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.connection = gc.pair(self.base, self.state.start_pairing(), client_name="evidence")

    def close(self):
        self.server.shutdown()

    def a_finished_run(self, artifact: bytes = b"print('EXT-OK')") -> dict:
        """Submit a job, wait for it, and return the answer the client verified."""
        answer = gc.submit(self.connection, artifact,
                           granted=_granted(self.service, token=self.connection.token))
        run_id = answer["run_id"]
        return gc.wait_for(self.connection, run_id, timeout=30.0)

    def an_absent_run(self, run_id: str = "0" * 32):
        """What the gateway says about a run it does not know. Returned raw: `status_of` refuses
        it, and refusing it is the point."""
        return gc._get(f"{self.base}/v1/jobs/{run_id}", token=self.connection.token)


@pytest.fixture(scope="session")
def real_gateway(tmp_path_factory):
    gateway = RealGateway(tmp_path_factory.mktemp("real-gateway"))
    try:
        yield gateway
    finally:
        gateway.close()


@pytest.fixture(scope="session")
def real_answer(real_gateway) -> dict:
    """One real, verified answer about one real run. The tests build from this and never
    from a literal."""
    return real_gateway.a_finished_run()
