"""The one place an operation is carried out, and the one place it is refused.

Every door ends here. So the questions worth asking of it are not "does submit work" -- that is
the gateway's job and has its own tests -- but whether anything can reach a handler without having
been through the checks, in the right order, and whether a refusal says the same thing whichever
way somebody arrived.
"""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


@pytest.fixture()
def paired(gateway):
    """A real device, paired the way a real one is."""
    token = gateway.state.redeem_pairing(gateway.state.start_pairing(), client_name="a laptop")
    return dispatch.identify(gateway, token)


class TestNothingReachesAHandlerWithoutPassingTheChecks:

    def test_an_operation_nobody_declared_is_refused_by_name(self, gateway, paired):
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("delete_everything", {}, paired, service=gateway)
        assert refused.value.refusal == "unknown_operation"
        assert refused.value.what_to_do, "a refusal with nothing to do about it leaves somebody stuck"

    def test_a_client_older_than_an_operation_is_told_so(self, gateway, paired):
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("capabilities", {}, paired, service=gateway, speaks="0")
        assert refused.value.refusal == "unknown_operation"
        assert "protocol" in refused.value.because

    def test_nobody_is_refused_before_anything_else_happens(self, gateway):
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("submit", {"run_id": "r" * 32}, dispatch.NOBODY, service=gateway)
        assert refused.value.refusal == "not_authenticated"
        assert gateway.runs == {}, "an unauthenticated request reached the gateway"

    def test_a_device_that_was_withdrawn_is_not_a_caller(self, gateway, paired):
        gateway.state.revoke(paired.token)
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("status", {"run_id": "x" * 32}, paired, service=gateway)
        assert refused.value.refusal in ("not_authenticated", "device_revoked")

    def test_authenticated_is_not_the_same_as_allowed(self, gateway, paired):
        read_only = dispatch.Principal(token=paired.token, device_id=paired.device_id,
                                       client_id=paired.client_id,
                                       capabilities=(contract.READ,))
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("submit", {"run_id": "r" * 32}, read_only, service=gateway)
        assert refused.value.refusal == "not_permitted"
        assert gateway.runs == {}, "a device without RUN reached the gateway"

    def test_a_parameter_nobody_declared_is_refused_rather_than_ignored(self, gateway, paired):
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("status", {"run_id": "r" * 32, "as_root": True}, paired,
                              service=gateway)
        assert refused.value.refusal == "malformed"
        assert "as_root" in refused.value.because

    def test_a_missing_required_parameter_says_which(self, gateway, paired):
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("status", {}, paired, service=gateway)
        assert refused.value.refusal == "malformed"
        assert "run_id" in refused.value.because

    def test_and_a_value_outside_what_is_allowed(self, gateway, paired):
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("prepare", {"command": ["true"], "artifact_sha256": "a" * 64,
                                          "artifact_bytes": 1, "network": "everything"},
                              paired, service=gateway)
        assert refused.value.refusal == "malformed"


class TestTheOperatorsStopReachesEveryDoor:

    def test_work_is_refused_while_the_gateway_is_stopped(self, gateway, paired, tmp_path):
        from agentnode_sdk.gateway.allowance import stop_everything

        stop_everything(gateway.state.root, "the image is being replaced")
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("submit", {"run_id": "r" * 32}, paired, service=gateway)
        assert refused.value.refusal == "gateway_stopped"
        assert "image is being replaced" in refused.value.because, (
            "the operator's own words did not reach the person who was refused")

    def test_but_asking_what_happened_still_works(self, gateway, paired):
        """A stopped gateway still answers about the past. Somebody has to be able to see why."""
        from agentnode_sdk.gateway.allowance import stop_everything

        stop_everything(gateway.state.root, "stopped")
        answer = dispatch.dispatch("usage", {}, paired, service=gateway)
        assert "runs" in answer


class TestEveryAttemptIsRecorded:

    def _audit(self, gateway):
        path = gateway.state.root / "audit.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_what_was_carried_out(self, gateway, paired):
        dispatch.dispatch("usage", {}, paired, service=gateway)
        assert [l for l in self._audit(gateway)
                if l["operation"] == "usage" and l["outcome"] == "carried_out"]

    def test_and_what_was_refused(self, gateway, paired):
        """A record of refusals is as much the point: an account being probed looks like
        refusals, and a log that kept only successes would not show it."""
        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("status", {}, paired, service=gateway)
        assert [l for l in self._audit(gateway) if l["outcome"] == "malformed"]

    def test_and_the_record_carries_no_secret(self, gateway, paired):
        dispatch.dispatch("usage", {}, paired, service=gateway)
        written = (gateway.state.root / "audit.jsonl").read_text(encoding="utf-8")
        assert paired.token not in written, "the audit log carries the device's token"


class TestTheContractAndTheDispatcherAgree:

    def test_every_declared_operation_can_actually_be_carried_out(self):
        """A declaration with nothing behind it is a door that opens onto a wall."""
        missing = [op.name for op in contract.OPERATIONS if op.name not in dispatch.HANDLERS]
        assert not missing, missing

    def test_and_nothing_can_be_carried_out_that_was_not_declared(self):
        """The other direction, which is the one that matters for security: a handler nobody
        declared would be reachable by name while appearing in no schema."""
        undeclared = [name for name in dispatch.HANDLERS if contract.find(name) is None]
        assert not undeclared, undeclared

    def test_a_refusal_can_only_be_one_of_the_declared_ones(self):
        with pytest.raises(ValueError):
            dispatch.Refused("because_i_said_so", "no")

    def test_every_refusal_renders_the_same_way(self, gateway, paired):
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("nonsense", {}, paired, service=gateway)
        answer = refused.value.as_answer()
        assert set(answer) <= {"refused", "because", "what_to_do"}
        assert answer["refused"] in contract.REFUSALS


class TestWhatAPrincipalIs:

    def test_capabilities_come_from_what_the_gateway_recorded(self, gateway, paired):
        """Not from anything the caller sends. A caller that could name its own capabilities
        would be deciding what it is allowed to do."""
        assert contract.RUN in paired.capabilities
        assert paired.client_id, "a paired device has an identity the server assigned"

    def test_an_unknown_token_is_nobody(self, gateway):
        assert dispatch.identify(gateway, "not-a-token") == dispatch.NOBODY
        assert dispatch.identify(gateway, "") == dispatch.NOBODY

    def test_and_nobody_is_not_authenticated(self):
        assert not dispatch.NOBODY.authenticated


class TestTheAuditCannotBeWrittenTo:
    """A log somebody can plant a string in is a log nobody can read.

    A review found both fields caller-controlled, and then found that filtering characters was
    not enough either: ordinary text passes any character filter, so a line of job output would
    have survived intact. Nothing caller-supplied is written now.
    """

    def _lines(self, gateway):
        path = gateway.state.root / "audit.jsonl"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_ordinary_looking_output_does_not_survive(self, gateway, paired):
        """The case that defeated the character filter."""
        looks_harmless = "hello world from inside the sandbox"
        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("status", {"run_id": "r" * 32, looks_harmless: 1}, paired,
                              service=gateway)
        assert looks_harmless not in self._lines(gateway)

    def test_nor_does_anything_shaped_like_a_credential(self, gateway, paired):
        for planted in ("sk-live-4242424242", paired.token, "BEGIN PRIVATE KEY"):
            with pytest.raises(dispatch.Refused):
                dispatch.dispatch("status", {"run_id": "r" * 32, planted: 1}, paired,
                                  service=gateway)
            assert planted not in self._lines(gateway)

    def test_an_operation_name_nobody_declared_is_not_written_either(self, gateway, paired):
        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("drop-all-tables; sk-live-9999", {}, paired, service=gateway)
        written = self._lines(gateway)
        assert "sk-live-9999" not in written
        assert "(undeclared)" in written

    def test_but_it_still_says_enough_to_read_back(self, gateway, paired):
        """A log that records nothing useful is as bad as one that records too much."""
        import json as _json

        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("status", {}, paired, service=gateway)
        lines = [_json.loads(l) for l in self._lines(gateway).splitlines() if l.strip()]
        malformed = [l for l in lines if l["outcome"] == "malformed"]
        assert malformed, lines
        assert malformed[-1]["about"] == ["run_id"], malformed[-1]
        assert malformed[-1]["operation"] == "status"
