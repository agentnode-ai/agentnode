"""How many front doors this gateway has, established rather than asserted.

Authentication has to start somewhere. Two operations exist for callers who hold nothing yet: one
says which gateway you have reached, the other is how you come to hold a credential at all. The
question worth being able to answer is whether there are exactly two, and these tests answer it by
reading the code rather than by reading the comments.

The other half is what those two may say and do. `hello` answers anybody who can reach the port,
so what it discloses is a decision and not an afterthought; `pair` hands out credentials, so every
guard on it -- expiry, one use, a rate limit, an atomic claim -- is load-bearing.
"""
from __future__ import annotations

import ast
import inspect
import io
import json
import os
import threading

import pytest

from agentnode_sdk.access import contract, dispatch, routes, schemas
from agentnode_sdk.gateway import server as gateway_server
from agentnode_sdk.gateway.identity import PAIRING_TTL_SECONDS, GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement


@pytest.fixture()
def sandbox(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        service.close()
        state.close()


def audit_lines(service):
    path = os.path.join(str(service.state.root), "audit.jsonl")
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TestThereAreExactlyTwo:

    def test_the_list_is_the_list(self):
        assert dispatch.BOOTSTRAP == ("hello", "pair", "open_session")

    @pytest.mark.parametrize("operation",
                             [op.name for op in contract.OPERATIONS] + ["", "anything", "submit "])
    def test_nothing_else_can_be_reached_without_a_credential(self, sandbox, operation):
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.before_anyone(operation, {}, service=sandbox)
        assert refused.value.refusal == "not_authenticated"

    def test_and_an_attempt_at_one_is_recorded(self, sandbox):
        """An account being probed looks like this, and a log that kept only the successes
        would not show it."""
        with pytest.raises(dispatch.Refused):
            dispatch.before_anyone("submit", {}, service=sandbox)
        assert [line for line in audit_lines(sandbox)
                if line["outcome"] == "not_authenticated"], audit_lines(sandbox)

    def test_the_register_names_the_same_two(self):
        anonymous = {r.path for r in routes.REGISTER
                     if r.kind == routes.BEFORE_ANYONE_IS_ANYBODY}
        assert anonymous == {"/v1/hello", "/v1/pair", "/v1/session",
                             "/console/setup", "/console/confirm"}

    def test_and_every_route_is_one_of_the_four_kinds(self):
        allowed = {routes.THROUGH_THE_DISPATCHER, routes.TRANSLATES,
                   routes.BEFORE_ANYONE_IS_ANYBODY, routes.SERVES_A_PAGE}
        for route in routes.REGISTER:
            assert route.kind in allowed, (route.path, route.kind)


class TestNeitherIsAThingAModelCanCall:
    """True by construction: they are not declared operations, so no rendering can contain them.

    That is stronger than filtering them out. A filter is a list somebody maintains; this is the
    absence of anything to filter.
    """

    def test_they_are_not_declared_operations(self):
        for name in dispatch.BOOTSTRAP:
            assert contract.find(name) is None

    @pytest.mark.parametrize("which", ["openapi", "mcp", "tool_calling"])
    def test_and_appear_in_no_rendering(self, which):
        offered = schemas.operations_named_by(schemas.every_rendering()[which], which)
        for name in dispatch.BOOTSTRAP:
            assert name not in offered

    def test_nor_anywhere_in_the_text_a_model_is_handed(self):
        said = json.dumps(schemas.mcp_tools()) + json.dumps(schemas.tool_calling_schema())
        for word in ("pairing", "invitation", "/v1/pair", "session"):
            assert word not in said.lower(), word


class TestWhatHelloWillTellAStranger:

    def test_enough_to_decide_whether_to_pair(self, sandbox):
        said = dispatch.before_anyone("hello", {}, service=sandbox)
        for expected in ("protocol", "gateway", "fingerprint", "ready", "properties",
                         "pairing_open"):
            assert expected in said, expected

    def test_it_says_exactly_what_it_was_decided_to_say(self, sandbox):
        """Whatever the gateway's own view of itself grows next, it does not become public by
        growing. A field reaches a stranger by being put on the list."""
        said = dispatch.before_anyone("hello", {}, service=sandbox)
        assert set(said) == set(dispatch.WHAT_A_STRANGER_IS_TOLD)

    def test_a_field_added_to_the_gateways_own_view_is_not_published_by_accident(
            self, sandbox, monkeypatch):
        monkeypatch.setattr(sandbox, "hello",
                            lambda: dict(sandbox.__class__.hello(sandbox),
                                         operator_email="someone@example.invalid",
                                         state_root="/var/lib/agentnode"))
        said = dispatch.before_anyone("hello", {}, service=sandbox)
        assert "operator_email" not in said and "state_root" not in said

    def test_and_none_of_it_names_the_machine_it_runs_on(self, sandbox):
        """The property, checked rather than asserted: nothing here points at a path, a file or
        an account on the operator's machine."""
        written = json.dumps(dispatch.before_anyone("hello", {}, service=sandbox))
        for leak in (str(sandbox.state.root), os.path.expanduser("~"), "/root", "/etc"):
            assert leak not in written, leak

    def test_a_gateway_that_is_not_ready_still_names_a_way_through(self, sandbox, monkeypatch):
        """A refusal that names no next step is the thing this project has spent months
        removing everywhere else, so `hello` keeps its."""
        monkeypatch.setattr(sandbox, "hello",
                            lambda: dict(sandbox.__class__.hello(sandbox), ready=False,
                                         reason="this gateway has not been measured",
                                         next_steps=["measure it, then try again"]))
        said = dispatch.before_anyone("hello", {}, service=sandbox)
        assert said["ready"] is False
        assert said["reason"] and said["next_steps"]


class TestWhatPairingEnforces:

    def test_an_invitation_works_once(self, sandbox):
        code = sandbox.state.start_pairing()
        assert dispatch.before_anyone("pair", {"code": code, "client_name": "a laptop"},
                                      service=sandbox)["token"]
        with pytest.raises(dispatch.Refused):
            dispatch.before_anyone("pair", {"code": code, "client_name": "again"},
                                   service=sandbox)

    def test_an_expired_one_does_not(self, sandbox):
        import time

        code = sandbox.state.start_pairing(now=time.time() - PAIRING_TTL_SECONDS - 60)
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.before_anyone("pair", {"code": code}, service=sandbox)
        assert "expired" in refused.value.because

    def test_a_mistyped_one_does_not(self, sandbox):
        real = sandbox.state.start_pairing()
        letters = [c for c in real if c != "-"]
        letters[-1] = "K" if letters[-1] != "K" else "M"
        wrong = "-".join("".join(letters[i:i + 4]) for i in range(0, 12, 4))
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.before_anyone("pair", {"code": wrong}, service=sandbox)
        assert "does not match" in refused.value.because

        # And the invitation is SPENT, right guess or wrong. The claim is made before the code is
        # compared, so an invitation is good for exactly one attempt -- which is what makes
        # guessing structurally impossible rather than merely slow.
        #
        # It is a real trade: anybody who can reach the port can burn an invitation the operator
        # has opened. That is bounded by the throttle and by the window, and it costs an operator
        # one button press, where the other arrangement would cost a credential. Stated here
        # because it is a choice and not an accident.
        with pytest.raises(dispatch.Refused) as after:
            dispatch.before_anyone("pair", {"code": real}, service=sandbox)
        assert "not accepting pairings" in after.value.because

    def test_guessing_is_rate_limited(self, sandbox):
        sandbox.state.start_pairing()
        refusals = []
        for _ in range(40):
            try:
                dispatch.before_anyone("pair", {"code": "AAAA-BBBB-CCCC"}, service=sandbox)
            except dispatch.Refused as no:
                refusals.append(no.because)
        assert any("too many" in r.lower() or "wait" in r.lower() for r in refusals), refusals[-2:]

    def test_two_callers_racing_on_one_invitation_do_not_both_get_in(self, sandbox):
        """The claim is made and removed in one step. Without that, an invitation handed to two
        people at once would admit both, which is the opposite of single use."""
        code = sandbox.state.start_pairing()
        got, ready = [], threading.Barrier(6)

        def redeem():
            ready.wait(timeout=10)
            try:
                got.append(dispatch.before_anyone(
                    "pair", {"code": code, "client_name": "racer"}, service=sandbox)["token"])
            except dispatch.Refused:
                pass

        racers = [threading.Thread(target=redeem, daemon=True) for _ in range(6)]
        for r in racers:
            r.start()
        for r in racers:
            r.join(timeout=10)
        assert len(got) == 1, got

    def test_every_attempt_is_recorded_and_nothing_a_caller_sent_is_written_down(self, sandbox):
        sandbox.state.start_pairing()
        with pytest.raises(dispatch.Refused):
            dispatch.before_anyone("pair", {"code": "AAAA-BBBB-CCCC"}, service=sandbox)
        # A fresh one, because the guess above spent the first: one attempt per invitation.
        code = sandbox.state.start_pairing()
        dispatch.before_anyone("pair", {"code": code, "client_name": "a laptop"}, service=sandbox)

        said = [line for line in audit_lines(sandbox) if line["operation"] == "pair"]
        assert len(said) >= 2
        assert {line["outcome"] for line in said} >= {"carried_out", "not_authenticated"}
        # The code somebody guessed is not written down, and neither is the token they were
        # given. A log a caller can put text into is a log a caller can put a token into.
        written = json.dumps(said)
        assert "AAAA" not in written and "a laptop" not in written


class TestTheDoorsDecideNothing:
    """Read off the request handler itself, so it cannot drift back."""

    def handler_source(self):
        return io.open(inspect.getsourcefile(gateway_server), encoding="utf-8").read()

    def test_no_route_makes_a_decision_of_its_own(self):
        """What a route may do to the service is render and sign. Everything that DECIDES --
        who is asking, whether they may, what the policy allows, whether anybody agreed -- lives
        in the dispatcher, and this is what keeps it there."""
        tree = ast.parse(self.handler_source())
        reached = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith(("do_", "_older",
                                                                           "_the")):
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Attribute)
                            and isinstance(inner.value, ast.Attribute)
                            and inner.value.attr == "service"):
                        reached.add(inner.attr)
        may_render = {"sign_answer", "stamp", "state"}
        assert reached <= may_render, (
            "the request handler reaches %s on the service; only %s render an answer"
            % (sorted(reached - may_render), sorted(may_render)))

    @pytest.mark.parametrize("decision", ["require_client", "owned_run", "authenticate",
                                          "submit", "cancel", "redeem_pairing", "rotate_token",
                                          "require_private_state", "hello"])
    def test_and_none_of_the_old_decision_calls_survives_in_a_route(self, decision):
        assert ("self.service.%s(" % decision) not in self.handler_source(), decision
