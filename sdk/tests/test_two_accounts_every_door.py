"""The same isolation questions, asked through the DOORS rather than through the dispatcher.

`test_two_accounts.py` drives `dispatch.dispatch` directly, which is where the decision is made.
A reviewer was right that this establishes the decision and not the doors: the claim "every
transport ends in the same place" is a claim about the architecture, and a claim is not evidence.

So each cross-account question is asked again over REST and over MCP, with two real accounts, a
real HTTP server and real credentials on the wire. What is asserted is what the SECOND account
gets back, on the wire, not what a helper returned.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from agentnode_sdk.access import contract, dispatch, mcp, rest, schemas
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by


@pytest.fixture()
def serving(tmp_path):
    """A real gateway on a real socket, with two real customers."""
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    server = make_server(service, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        yield base, service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        state.close()


def _over_rest(base, token, operation, params):
    """What a REST client sends, and what comes back -- status included.

    The METHOD comes from the contract rather than being hardcoded: an operation that changes
    nothing is a GET here, and sending it as a POST is answered 405. Reading the method off the
    declaration is also the point -- a client written against the contract gets this right, and
    a test that hardcoded one would be testing a different client from the one the product
    documents.
    """
    declared = contract.find(operation)
    # The same rule the adapter applies, read off the declaration: an operation that changes
    # something OR takes any parameter is a POST, because parameters travel in the body and
    # never in a path or a query -- a URL ends up in logs, history and referrers.
    posting = bool(declared and (declared.changes or declared.params))
    where = base + rest.NAMESPACE + operation.replace(".", "/")
    request = urllib.request.Request(
        where, data=json.dumps(params).encode("utf-8") if posting else None,
        method="POST" if posting else "GET")
    request.add_header("Content-Type", "application/json")
    request.add_header("X-AgentNode-Token", token)
    request.add_header("X-AgentNode-Protocol", contract.PROTOCOL_VERSION)
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:
            return answer.status, json.loads(answer.read().decode("utf-8"))
    except urllib.error.HTTPError as refused:
        return refused.code, json.loads(refused.read().decode("utf-8"))


def _over_mcp(base, token, operation, arguments):
    """What an MCP client sends. A refusal is a RESULT here, not a transport error."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": schemas.tool_name_for(operation),
                                  "arguments": arguments}}).encode("utf-8")
    request = urllib.request.Request(base + rest.MCP_PATH, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("X-AgentNode-Token", token)
    request.add_header("X-AgentNode-Protocol", contract.PROTOCOL_VERSION)
    with urllib.request.urlopen(request, timeout=30) as answer:
        return json.loads(answer.read().decode("utf-8"))


class TestACrossAccountReadIsRefusedOnEveryDoor:

    def test_the_device_list_over_rest_is_the_askers_own(self, serving):
        base, service = serving
        alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
        status, answer = _over_rest(base, bob.token, "devices.list", {})
        assert status == 200
        seen = {d["device_id"] for d in answer["devices"]}
        assert seen == {bob.device_id}
        assert alice.device_id not in json.dumps(answer)

    def test_and_over_mcp(self, serving):
        base, service = serving
        alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
        answer = _over_mcp(base, bob.token, "devices.list", {})
        said = json.dumps(answer)
        assert alice.device_id not in said
        assert bob.device_id in said

    def test_another_accounts_run_does_not_exist_over_rest(self, serving):
        base, service = serving
        alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
        run = _a_run_by(service, alice)
        for operation in ("status", "result", "cancel"):
            status, answer = _over_rest(base, bob.token, operation, {"run_id": run})
            assert status >= 400, "%s answered 200 about another account's run" % operation
            assert answer.get("refused") == "no_such_run"
            assert answer.get("what_to_do")

    def test_and_over_mcp_it_is_a_refusal_rather_than_a_result(self, serving):
        base, service = serving
        alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
        run = _a_run_by(service, alice)
        answer = _over_mcp(base, bob.token, "status", {"run_id": run})
        assert answer["result"]["isError"] is True
        assert answer["result"]["structuredContent"]["refused"] == "no_such_run"

    def test_and_over_the_older_door_that_predates_the_contract(self, serving):
        """`/v1/jobs/<run>` hands back the whole signed record, so it is the one to check."""
        base, service = serving
        alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
        run = _a_run_by(service, alice)
        request = urllib.request.Request(base + "/v1/jobs/" + run)
        request.add_header("X-AgentNode-Token", bob.token)
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                raise AssertionError("the older door handed over another account's run: %s"
                                     % answer.read()[:200])
        except urllib.error.HTTPError as refused:
            assert refused.code == 404
            assert alice.device_id not in refused.read().decode("utf-8")

    def test_usage_over_rest_counts_only_the_asker(self, serving):
        base, service = serving
        alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
        _a_run_by(service, alice)
        _a_run_by(service, alice)
        status, answer = _over_rest(base, bob.token, "usage", {})
        assert status == 200
        assert answer["runs"] == 0 and answer["account_runs"] == 0

    def test_and_a_customer_is_shown_their_own_account_figures(self, serving):
        """Not another account's -- their own, which is what the account ceiling refuses on."""
        base, service = serving
        from tests.test_two_accounts import _their_second_machine

        alice = _a_customer(service, "alice")
        second = _their_second_machine(service, alice)
        _a_run_by(service, alice)
        status, answer = _over_rest(base, second.token, "usage", {})
        assert status == 200
        assert answer["runs"] == 0, "this machine has started nothing"
        assert answer["account_runs"] == 1, (
            "the account ceiling is one of the two that can refuse this device, and a customer "
            "shown only their own machine's figures cannot tell why they were refused")

    def test_sessions_of_another_account_are_not_listed_over_rest(self, serving):
        base, service = serving
        alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
        service.sessions.open(alice.device_id, label="alice's browser")
        status, answer = _over_rest(base, bob.token, "sessions.list", {})
        assert status == 200
        assert answer["sessions"] == []

    def test_an_enrolment_of_another_account_does_not_exist_over_rest(self, serving):
        base, service = serving
        alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
        begun = dispatch.dispatch("connections.enrol",
                                  {"way_in": contract.MCP, "label": "alice's AI"},
                                  alice, service=service)
        status, answer = _over_rest(base, bob.token, "connections.check",
                                    {"challenge": begun["challenge"]})
        assert status >= 400
        assert answer.get("refused") == "no_such_run"


class TestACrossAccountWriteIsRefusedOnEveryDoor:

    def test_withdrawing_another_accounts_device_over_rest_does_nothing(self, serving):
        base, service = serving
        alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
        session, _csrf = service.sessions.open(alice.device_id, label="alice's browser")
        run = _a_run_by(service, alice)

        status, answer = _over_rest(base, bob.token, "devices.revoke",
                                    {"device_id": alice.device_id})
        assert status == 200, "the answer is the same shape as for a device that is not there"
        assert answer["withdrawn"] is False and answer["runs_stopping"] == []
        assert dispatch.identify(service, alice.token).authenticated
        assert service.sessions.whose(session) is not None
        assert not service.runs[run].cancel_requested.is_set()

    def test_and_over_mcp_the_operation_is_not_even_offered(self, serving):
        base, service = serving
        alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
        for name in ("devices.revoke", "devices.rotate", "sessions.end"):
            answer = _over_mcp(base, bob.token, name, {"device_id": alice.device_id})
            assert "error" in answer, "%s was carried out over MCP" % name
            assert "no tool called" in json.dumps(answer).lower()
        assert dispatch.identify(service, alice.token).authenticated


class TestTheOperatorSurfaceIsNotOnAnyDoor:

    def test_no_declared_operation_reaches_a_ceiling_a_policy_or_a_suspension(self):
        """Not "it is not offered to models" -- not declared AT ALL, so no door addresses it."""
        reachable = {op.name for op in contract.OPERATIONS}
        for forbidden in ("accounts.list", "accounts.suspend", "accounts.restore",
                          "limits.set", "policy.set", "stop", "resume", "retention.set",
                          "accounts.delete", "accounts.export", "metrics", "watch"):
            assert forbidden not in reachable, (
                "%s is a contract operation, so it is addressable at /v1/op/ and reachable by "
                "whoever holds a capability -- and a capability is something a customer holds"
                % forbidden)

    def test_and_the_addresses_this_gateway_answers_on_carry_no_operator_surface(self, serving):
        base, service = serving
        alice = _a_customer(service, "alice")
        for path in ("/v1/accounts", "/v1/limits", "/v1/metrics", "/v1/admin",
                     "/v1/op/accounts/suspend", "/v1/op/limits/set"):
            request = urllib.request.Request(base + path, data=b"{}", method="POST")
            request.add_header("Content-Type", "application/json")
            request.add_header("X-AgentNode-Token", alice.token)
            try:
                with urllib.request.urlopen(request, timeout=30) as answer:
                    body = answer.read().decode("utf-8")
                    raise AssertionError("%s answered %d: %s" % (path, answer.status, body[:200]))
            except urllib.error.HTTPError as refused:
                assert refused.code in (404, 400, 403), "%s answered %d" % (path, refused.code)

    def test_what_is_reachable_without_a_credential_gives_nothing_away(self, serving):
        base, service = serving
        alice = _a_customer(service, "alice")
        _a_run_by(service, alice)
        with urllib.request.urlopen(base + "/v1/health", timeout=30) as answer:
            said = answer.read().decode("utf-8")
        assert alice.account_id not in said and alice.device_id not in said
        assert set(json.loads(said)) <= {"serving", "measured", "taking_work", "because",
                                         "gateway", "fingerprint", "protocol"}
