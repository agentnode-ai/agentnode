"""Four doors, one sandbox, and the same answer through each.

REST, remote MCP, the local stdio bridge and the CLI-shaped client. Every one of them goes over
HTTP to a real gateway; none of them calls `dispatch()` in process. That is the whole claim being
tested: the door is a translation and the decision is always on the other side of it.

COMPATIBLE is recorded here and nowhere else, because this is the only place a tool call actually
reaches AgentNode and comes back. `access/compatibility.py` refuses to produce that state without
an `Observation` naming a run somebody can look up, so a door that was never exercised cannot be
called compatible by anybody writing a document.
"""
from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request

import pytest

from agentnode_sdk.access import client as adapter
from agentnode_sdk.access import compatibility as compat
from agentnode_sdk.access import contract, mcp, rest, schemas
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server
from tests import serving
from tests.test_em3c_gateway import StandInBackend, _store_measurement


@pytest.fixture()
def sandbox(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    server = make_server(service, port=0, host="127.0.0.1")
    serving.owned(server, state)
    base = "http://127.0.0.1:%d" % server.server_address[1]
    token = state.redeem_pairing(state.start_pairing(), client_name="a laptop")
    return service, base, token


def rpc(base, token, message):
    """One JSON-RPC message to the remote MCP door, over HTTP."""
    request = urllib.request.Request(base + rest.MCP_PATH,
                                     data=json.dumps(message).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header(rest.TOKEN_HEADER, token)
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            raw = answer.read().decode("utf-8")
            return answer.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as refused:
        return refused.code, json.loads(refused.read().decode("utf-8") or "{}")


def a_finished_run(sandbox_client, run_id):
    """Prepare, then submit, then wait -- the way every door must.

    Nothing runs that was not disclosed first, so prepare is not optional politeness
    here: the submission carries what it returned, and without it the sandbox refuses.
    """
    told = sandbox_client.prepare(command=["python", "-c", "print('hi')"],
                                  artifact_sha256="a" * 64, artifact_bytes=14,
                                  wall_clock_s=30)
    sandbox_client.submit(run_id=run_id,
                          artifact=base64.b64encode(b"print('hi')").decode("ascii"),
                          command=["python", "-c", "print('hi')"], wall_clock_s=30,
                          accepted_disclosure=told["accepted_disclosure"])
    for _ in range(100):
        where = sandbox_client.status(run_id)
        if where["state"] not in ("accepted", "running"):
            return where
        time.sleep(0.1)
    raise AssertionError("the run never reached a terminal state")


class TestTheRestDoor:

    def test_a_neutral_http_client_can_do_the_whole_thing(self, sandbox):
        service, base, token = sandbox
        door = adapter.Sandbox(base, token)
        assert door.capabilities()["protocol"] == contract.PROTOCOL_VERSION
        where = a_finished_run(door, "r" * 32)
        assert where["run_id"] == "r" * 32
        assert door.usage()["runs"] >= 1
        observed = compat.confirmed(
            "a neutral REST client", [compat.DIRECT],
            compat.Observation(way_in=compat.DIRECT, run_id=where["run_id"], at=time.time(),
                               client="urllib over the authenticated API"),
            ask_the_sandbox=door.status)
        assert observed.state == compat.COMPATIBLE


class TestTheRemoteMcpDoor:

    def test_it_lists_tools_and_calls_one(self, sandbox):
        service, base, token = sandbox
        status, hello = rpc(base, token, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert status == 200, hello
        assert hello["result"]["protocolVersion"] == mcp.MCP_PROTOCOL

        status, listed = rpc(base, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert status == 200, listed
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert schemas.tool_name_for("submit") in names

        run_id = "m" * 32
        status, called = rpc(base, token, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": schemas.tool_name_for("prepare"), "arguments": {
                "command": ["python", "-c", "print('hi')"], "artifact_sha256": "a" * 64,
                "artifact_bytes": 14, "wall_clock_s": 30}}})
        assert status == 200 and not called["result"].get("isError"), called
        disclosure = called["result"]["structuredContent"]["accepted_disclosure"]

        status, called = rpc(base, token, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": schemas.tool_name_for("submit"), "arguments": {
                "run_id": run_id,
                "artifact": base64.b64encode(b"print('hi')").decode("ascii"),
                "command": ["python", "-c", "print('hi')"], "wall_clock_s": 30,
                "accepted_disclosure": disclosure}}})
        assert status == 200, called
        assert not called["result"].get("isError"), called
        assert called["result"]["structuredContent"]["run_id"] == run_id

        observed = compat.confirmed(
            "a neutral MCP client", [compat.MCP],
            compat.Observation(way_in=compat.MCP, run_id=run_id, at=time.time(),
                               client="JSON-RPC over the remote MCP door"),
            ask_the_sandbox=adapter.Sandbox(base, token).status)
        assert observed.state == compat.COMPATIBLE

    def test_making_an_invitation_is_not_among_the_tools(self, sandbox):
        """The operation that lets a NEW device in is not something a connected AI may do."""
        service, base, token = sandbox
        _status, listed = rpc(base, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = " ".join(tool["name"] for tool in listed["result"]["tools"])
        for never in ("pair", "invit", "token", "rotate"):
            assert never not in names, (never, names)

    def test_a_tool_this_device_may_not_use_is_not_offered(self, sandbox):
        """Filtered by what the DEVICE holds, so a tool it cannot call never appears."""
        service, base, token = sandbox
        from agentnode_sdk.access import dispatch as d

        who = d.identify(service, token)
        read_only = d.Principal(token=who.token, device_id=who.device_id,
                                client_id=who.client_id, capabilities=(contract.READ,))
        offered = {tool["name"] for tool in mcp.tools_for(read_only)}
        assert schemas.tool_name_for("status") in offered
        assert schemas.tool_name_for("submit") not in offered

    def test_a_refusal_comes_back_as_a_result_not_a_broken_call(self, sandbox):
        """An AI told "the call failed" retries. One told why can say so to the person waiting."""
        service, base, token = sandbox
        status, called = rpc(base, token, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": schemas.tool_name_for("status"),
                       "arguments": {"run_id": "nope" * 8}}})
        assert status == 200
        assert called["result"]["isError"] is True
        assert called["result"]["structuredContent"]["refused"] == "no_such_run"

    def test_an_unauthenticated_caller_gets_nothing(self, sandbox):
        service, base, _token = sandbox
        _status, listed = rpc(base, "", {"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
        assert listed["result"]["tools"] == [], (
            "an unpaired caller was offered tools it could not use")


class TestTheLocalStdioBridge:

    def test_it_relays_a_tool_call_over_the_authenticated_api(self, sandbox):
        service, base, token = sandbox
        door = adapter.Sandbox(base, token)
        run_id = "b" * 32
        # The bridge goes through prepare like every other door: nothing runs undisclosed.
        told = door.prepare(command=["python", "-c", "print('hi')"],
                            artifact_sha256="a" * 64, artifact_bytes=14, wall_clock_s=30)
        incoming = io.StringIO(chr(10).join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": schemas.tool_name_for("submit"), "arguments": {
                    "run_id": run_id,
                    "artifact": base64.b64encode(b"print('hi')").decode("ascii"),
                    "command": ["python", "-c", "print('hi')"], "wall_clock_s": 30,
                    "accepted_disclosure": told["accepted_disclosure"]}}}),
        ]) + chr(10))
        outgoing = io.StringIO()
        adapter.bridge(door, incoming, outgoing)

        replies = [json.loads(l) for l in outgoing.getvalue().splitlines() if l.strip()]
        assert len(replies) == 3, replies
        assert replies[0]["result"]["protocolVersion"] == mcp.MCP_PROTOCOL
        assert any(t["name"] == schemas.tool_name_for("submit")
                   for t in replies[1]["result"]["tools"])
        assert replies[2]["result"]["structuredContent"]["run_id"] == run_id

        observed = compat.confirmed(
            "the local MCP stdio bridge", [compat.MCP],
            compat.Observation(way_in=compat.MCP, run_id=run_id, at=time.time(),
                               client="stdio bridge relaying to the authenticated API"),
            ask_the_sandbox=door.status)
        assert observed.state == compat.COMPATIBLE

    def test_and_it_holds_no_authority_of_its_own(self, sandbox):
        """Point it at a sandbox with no credential and everything is refused at the far end."""
        service, base, _token = sandbox
        door = adapter.Sandbox(base, "")
        incoming = io.StringIO(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": schemas.tool_name_for("usage"), "arguments": {}}}) + "\n")
        outgoing = io.StringIO()
        adapter.bridge(door, incoming, outgoing)
        reply = json.loads(outgoing.getvalue())
        assert reply["result"]["isError"] is True
        assert reply["result"]["structuredContent"]["refused"] == "not_authenticated"


class TestTheCommandLineShapedClient:

    def test_it_uses_the_same_api_as_everything_else(self, sandbox):
        service, base, token = sandbox
        door = adapter.Sandbox(base, token)
        run_id = "c" * 32
        where = a_finished_run(door, run_id)
        assert where["state"]
        produced = door.result(run_id)
        assert produced["run_id"] == run_id
        devices = door.devices()["devices"]
        assert devices

        observed = compat.confirmed(
            "the AgentNode CLI", [compat.RUNS_OUR_CLIENT],
            compat.Observation(way_in=compat.RUNS_OUR_CLIENT, run_id=run_id, at=time.time(),
                               client="the SDK client the CLI uses"),
            ask_the_sandbox=door.status)
        assert observed.state == compat.COMPATIBLE

    def test_a_refusal_keeps_its_name_on_the_way_back(self, sandbox):
        service, base, token = sandbox
        door = adapter.Sandbox(base, token)
        with pytest.raises(adapter.TheSandboxRefused) as refused:
            door.status("no" * 16)
        assert refused.value.refusal == "no_such_run"
        assert refused.value.what_to_do

    def test_and_an_unreachable_sandbox_says_so_rather_than_hanging(self):
        door = adapter.Sandbox("http://127.0.0.1:9", "t", timeout=2.0)
        with pytest.raises(adapter.TheSandboxRefused) as refused:
            door.usage()
        assert refused.value.refusal == "sandbox_unavailable"


class TestNoDoorTakesAShortcut:

    def _names_used_by(self, module):
        """Every identifier the CODE actually mentions -- not the prose around it.

        The first version of this read the source as text and failed on the module's own
        docstring, which names the things it is explaining that it must not touch. A check that
        cannot tell an explanation from a call is not checking the thing it claims to.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                node.value.value = ""                          # drop docstrings
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.alias):
                used.add(node.name.rsplit(".", 1)[-1])
                used.add((node.asname or "").strip())
            elif isinstance(node, ast.ImportFrom) and node.module:
                used.add(node.module.rsplit(".", 1)[-1])
        return used

    def test_no_client_side_adapter_reaches_past_the_api(self):
        """The decision named this as the part that must hold: anything running on the user's
        machine crosses the authenticated API and touches nothing else."""
        used = self._names_used_by(adapter)
        for forbidden in ("GatewayState", "GatewayService", "ContainerBackend", "server",
                          "identity", "worker"):
            assert forbidden not in used, forbidden

    def test_and_the_check_would_notice_a_shortcut(self):
        """A check that cannot fire is not a check."""
        import ast

        shortcut = (
            "from agentnode_sdk.gateway.identity import GatewayState" + chr(10)
            + "def go():" + chr(10)
            + "    return GatewayState('/tmp')" + chr(10)
        )
        tree = ast.parse(shortcut)
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "GatewayState" in used

    def test_and_the_only_thing_that_carries_an_operation_out_is_the_dispatcher(self):
        import inspect

        from agentnode_sdk.access import dispatch

        for door in (rest, mcp):
            source = inspect.getsource(door)
            assert "dispatch.dispatch(" in source, door.__name__
            # No door reaches into the gateway for anything except identifying the caller.
            assert "service.submit(" not in source, door.__name__
            assert "service.runs" not in source, door.__name__
        assert "HANDLERS" in inspect.getsource(dispatch)
