"""The whole journey, over the wire, as a stranger would do it.

Invitation → pairing → capabilities → disclosure → submit → status → result → cancel → usage →
devices → revoke → and then nothing works any more.

Every request here goes over real HTTP to a real server. There is no in-process shortcut, and
that is deliberate rather than incidental: a test that called `dispatch()` directly would prove
that the dispatcher works and nothing at all about whether the door in front of it does. The
whole point of the arrangement is that the door cannot decide anything, and the only way to
establish that is to go through it.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from agentnode_sdk.access import contract, rest
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server
from tests import serving
from tests.test_em3c_gateway import StandInBackend, _store_measurement


class ADoor:
    """A neutral client. It knows HTTP and the contract, and nothing about our internals."""

    #: Longer than the gateway's own cancel settle window (45s). A cancel is synchronous and
    #: waits for the sandbox to actually be gone, so a client that gave up sooner would be
    #: measuring its own impatience rather than the operation. That the wait can be that long is
    #: a real property worth knowing about -- see the note in the cancel test.
    patience = 60.0

    def __init__(self, base, token=""):
        self.base = base
        self.token = token

    def ask(self, operation, params=None, token=None, speaks=None):
        """Returns (status, answer). Never raises for a refusal: a refusal is an answer."""
        path = self.base + rest.NAMESPACE + operation.replace(".", "/")
        op = contract.find(operation)
        body = None
        method = "GET"
        if op is None or op.changes or op.params:
            method = "POST"
            body = json.dumps(params or {}).encode("utf-8")
        request = urllib.request.Request(path, data=body, method=method)
        request.add_header("Content-Type", "application/json")
        presented = self.token if token is None else token
        if presented:
            request.add_header(rest.TOKEN_HEADER, presented)
        if speaks:
            request.add_header(rest.SPEAKS_HEADER, speaks)
        try:
            with urllib.request.urlopen(request, timeout=self.patience) as answer:
                return answer.status, json.loads(answer.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as refused:
            return refused.code, json.loads(refused.read().decode("utf-8") or "{}")


@pytest.fixture()
def sandbox(tmp_path):
    """A gateway serving on loopback, and the operator's side of it."""
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    server = make_server(service, port=0, host="127.0.0.1")
    serving.owned(server, state)
    yield service, "http://127.0.0.1:%d" % server.server_address[1]


class TestTheWholeJourney:

    def test_a_stranger_gets_from_an_invitation_to_a_finished_job_and_out_again(self, sandbox):
        service, base = sandbox

        # --- the operator makes one invitation. One time, and it expires.
        code = service.state.start_pairing()
        assert code, "the operator got nothing to hand over"

        # --- a device takes it. No ssh, no YAML, no certificate digest typed by anybody.
        token = service.state.redeem_pairing(code, client_name="a laptop")
        assert token
        door = ADoor(base, token)

        # --- what can this sandbox do, and which version is each operation?
        status, capabilities = door.ask("capabilities")
        assert status == 200, capabilities
        assert capabilities["protocol"] == contract.PROTOCOL_VERSION
        offered = {o["name"] for o in capabilities["operations"]}
        assert {"submit", "status", "result", "cancel", "usage"} <= offered
        assert all(o["since"] for o in capabilities["operations"]), (
            "an operation with no version leaves a client guessing whether it exists")

        # --- what would happen, before anything happens.
        status, told = door.ask("prepare", {
            "command": ["python", "-c", "print('hello')"],
            "artifact_sha256": "a" * 64, "artifact_bytes": 21, "wall_clock_s": 30})
        assert status == 200, told
        for must_say in ("runs_at", "transfers", "network", "limits", "expected_use",
                         "what_this_does_not_establish"):
            assert must_say in told, must_say
        assert told["network"]["everything_else"] == "refused"
        assert told["what_this_does_not_establish"], (
            "the disclosure says where code runs without saying what that does not protect against")

        # --- one confirmation, for the whole job. Not one per shell command.
        import base64

        artifact = b"print('hello')"
        status, started = door.ask("submit", {
            "run_id": "e" * 32, "artifact": base64.b64encode(artifact).decode("ascii"),
            "command": ["python", "-c", "print('hello')"], "wall_clock_s": 30,
            "accepted_disclosure": told["accepted_disclosure"]})
        assert status == 200, started
        run_id = started["run_id"]

        # --- where has it got to, and what did it produce?
        import time

        for _ in range(100):
            status, where = door.ask("status", {"run_id": run_id})
            assert status == 200, where
            if where["state"] not in ("accepted", "running"):
                break
            time.sleep(0.1)
        status, produced = door.ask("result", {"run_id": run_id})
        assert status == 200, produced
        assert produced["run_id"] == run_id

        # --- what has it used?
        status, used = door.ask("usage")
        assert status == 200, used
        assert used["runs"] >= 1, used

        # --- which devices can reach this sandbox as me?
        status, devices = door.ask("devices.list")
        assert status == 200, devices
        mine = [d for d in devices["devices"] if d["name"] == "a laptop"]
        assert mine, devices

        # --- and withdrawing this device stops everything, at once.
        status, gone = door.ask("devices.revoke", {"device_id": mine[0]["device_id"]})
        assert status == 200, gone
        assert gone["withdrawn"] is True

        status, refused = door.ask("usage")
        assert status == 401, refused
        assert refused["refused"] == "not_authenticated"
        assert refused.get("what_to_do"), "a refusal with nothing to do about it leaves somebody stuck"

    def test_cancelling_says_what_became_of_the_sandbox(self, sandbox):
        import base64

        service, base = sandbox
        token = service.state.redeem_pairing(service.state.start_pairing(), client_name="a laptop")
        door = ADoor(base, token)
        _s, told = door.ask("prepare", {"command": ["python", "-c", "pass"],
                                        "artifact_sha256": "b" * 64,
                                        "artifact_bytes": 1, "wall_clock_s": 30})
        status, started = door.ask("submit", {
            "run_id": "c" * 32, "artifact": base64.b64encode(b"x").decode("ascii"),
            "command": ["python", "-c", "pass"], "wall_clock_s": 30,
            "accepted_disclosure": told["accepted_disclosure"]})
        assert status == 200, started
        # A cancel is SYNCHRONOUS: the gateway waits for the sandbox to be confirmed gone, up to
        # its settle window of forty-five seconds. That is right for the answer it gives -- it can
        # say whether cleanup was verified -- and it is a long time to hold a caller. Worth
        # naming: a managed service will want this to be startable and pollable rather than
        # blocking, and that is an access-layer change, not a change to the sandbox.
        status, stopped = door.ask("cancel", {"run_id": started["run_id"]})
        # 200 whether it was still going or had already finished. Racing a short job is not an
        # error, and a caller that was slightly too late must be able to tell that from a cancel
        # that did not work -- which is what the full suite, under load, found.
        assert status == 200, stopped
        assert "cleanup_verified" in stopped, (
            "a cancel that does not say what became of the sandbox leaves it unaccounted for")
        assert stopped["state"], stopped


class TestTheDoorDecidesNothing:

    def test_an_unknown_operation_is_not_reachable_over_http(self, sandbox):
        service, base = sandbox
        token = service.state.redeem_pairing(service.state.start_pairing(), client_name="x")
        status, refused = ADoor(base, token).ask("delete_everything", {})
        assert status == 404
        assert refused["refused"] == "unknown_operation"

    def test_no_token_is_refused_before_anything_runs(self, sandbox):
        service, base = sandbox
        status, refused = ADoor(base, "").ask("submit", {"run_id": "n" * 32})
        assert status == 401
        assert refused["refused"] == "not_authenticated"
        assert service.runs == {}, "an unauthenticated request reached the gateway"

    def test_a_made_up_token_is_refused_too(self, sandbox):
        service, base = sandbox
        status, refused = ADoor(base, "not-a-real-token").ask("usage")
        assert status == 401, refused

    def test_a_parameter_nobody_declared_is_refused_over_the_wire(self, sandbox):
        service, base = sandbox
        token = service.state.redeem_pairing(service.state.start_pairing(), client_name="x")
        status, refused = ADoor(base, token).ask("status", {"run_id": "r" * 32,
                                                            "as_root": True})
        assert status == 400
        assert refused["refused"] == "malformed"
        assert "as_root" in refused["because"]

    def test_a_client_older_than_an_operation_is_told_which_version_it_needs(self, sandbox):
        service, base = sandbox
        token = service.state.redeem_pairing(service.state.start_pairing(), client_name="x")
        status, refused = ADoor(base, token).ask("capabilities", speaks="0")
        assert status == 404
        assert "protocol" in refused["because"]

    def test_the_schema_is_fetchable_before_anybody_has_a_token(self, sandbox):
        """A client cannot write the request that gets it a token without knowing the shape."""
        _service, base = sandbox
        with urllib.request.urlopen(base + rest.SCHEMA_PATH, timeout=20) as answer:
            document = json.loads(answer.read().decode("utf-8"))
        assert document["openapi"].startswith("3.")
        offered = {entry[m]["operationId"] for entry in document["paths"].values() for m in entry}
        assert offered == {op.name for op in contract.OPERATIONS}

    def test_and_the_token_never_travels_in_the_url(self):
        """A URL ends up in logs, history and proxies. A header does not."""
        import inspect

        source = inspect.getsource(rest)
        assert "TOKEN_HEADER" in source
        assert "?token=" not in source and "token=" not in source.split("TOKEN_HEADER", 1)[1][:400]
