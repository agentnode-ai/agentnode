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
from agentnode_sdk.gateway.protocol import TIMED_OUT
from agentnode_sdk.sandbox.backend import Outcome
from agentnode_sdk.gateway.server import GatewayService, make_server

from tests.test_em3c_gateway import StandInBackend, _granted, _store_measurement
from tests import reliability


class Backend(StandInBackend):
    """The stand-in, able to report a run its limit ended.

    It builds that report with the production `Outcome`, the same class the container backend
    returns, so what the gateway records is what the gateway would record. Nothing here writes
    a run record or an answer.
    """

    def __init__(self):
        super().__init__()
        self.stop_next_at_the_limit = False
        #: What this sandbox prints, when something wants it to print something in particular.
        #: A crossing is established from what the GATEWAY'S SIGNED RECORD carries, so a test
        #: about one needs the record to carry what the payload would have printed. Left unset,
        #: this behaves as it always did.
        self.answers = None

    def run_process(self, spec, input_text=None, timeout=120.0):
        if self.answers is not None and not self.stop_next_at_the_limit:
            self.specs.append(spec)
            return self.answers(spec, input_text)
        result = super().run_process(spec, input_text=input_text, timeout=timeout)
        if self.stop_next_at_the_limit:
            self.stop_next_at_the_limit = False
            return Outcome(None, "", f"[sandbox timed out after {timeout}s]",
                           reason=TIMED_OUT, native_status=137, platform="linux-container")
        return result


class RealGateway:
    """One gateway, one paired client, and the answers it really gives."""

    def __init__(self, root):
        self.state = GatewayState(str(root), version="test")
        self.backend = Backend()
        self.service = GatewayService(self.state, backend=self.backend)
        _store_measurement(self.service)
        self.server = make_server(self.service, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.connection = gc.pair(self.base, self.state.start_pairing(), client_name="evidence")

    def close(self):
        self.server.shutdown()

    def saying_what_it_was_doing(self, what: str):
        """Turn a timeout here into a description of why it timed out.

        Every failure of this kind in CI has been a request to this gateway not coming back
        within thirty seconds, and a bare `TimeoutError: timed out` cannot be told apart from a
        machine that was simply busy. The stacks say which: a server thread blocked on something
        names it, and no blocked thread at all says the opposite. Re-running until it passes
        would answer neither.
        """
        import contextlib
        import time as _time

        @contextlib.contextmanager
        def watching():
            started = _time.monotonic()
            try:
                yield
            except TimeoutError as exc:
                raise TimeoutError(str(exc) + reliability.what_was_it_doing(
                    what, _time.monotonic() - started)) from exc

        return watching()

    def a_finished_run(self, artifact: bytes = b"print('EXT-OK')") -> dict:
        """Submit a job, wait for it, and return the answer the client verified."""
        with self.saying_what_it_was_doing("a submission to the session gateway"):
            answer = gc.submit(self.connection, artifact,
                               granted=_granted(self.service, token=self.connection.token))
        run_id = answer["run_id"]
        with self.saying_what_it_was_doing("waiting for run " + str(run_id)):
            return gc.wait_for(self.connection, run_id, timeout=30.0)

    def a_run_its_limit_ended(self, artifact: bytes = b"print('never mind')") -> dict:
        """A real submission the sandbox stops at its limit, answered by the real gateway."""
        self.backend.stop_next_at_the_limit = True
        return self.a_finished_run(artifact)

    def resigned(self, answer: dict, **changes) -> dict:
        """A real answer with something changed, signed and stamped by the production code.

        `EM3C-EVIDENCE-0013`: an earlier version recomputed the binding by hand and left the
        signature belonging to the answer before the change, so the object was not one the
        gateway would ever have sent. This goes through `sign_answer` and `stamp`, which are the
        two functions that turn a run record into an answer -- so what comes out is what that
        gateway would send if the run had been this one.

        For tests about something OTHER than the tie between an answer's outside and its inside.
        A test about the tie changes a field WITHOUT re-signing, and must go red.
        """
        inner = {k: v for k, v in answer.items()
                 if k not in ("binding", "signature", "gateway", "fingerprint", "protocol")}
        inner.update(changes)
        return self.service.stamp(
            self.service.sign_answer(inner, self.connection.token))

    def accepted(self, answer: dict) -> dict:
        """What the production client says about an answer: it takes it, or it refuses it."""
        gc.assert_same_gateway(self.connection, answer)
        return gc.verify_answer(self.connection, answer)

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
