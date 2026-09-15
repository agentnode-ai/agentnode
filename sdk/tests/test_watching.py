"""What an operator can see, and what a health address gives away.

The second question is the one worth testing hardest. A health endpoint is the thing most likely
to be left reachable by accident, so what matters is not that it answers but that answering costs
nothing.
"""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.access import dispatch
from agentnode_sdk.gateway import observability as obs
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


class TestHealthGivesNothingAway:

    def test_it_names_no_account_no_device_no_run_and_no_configuration(self, gateway):
        alice = _a_customer(gateway, "alice")
        run = _a_run_by(gateway, alice)
        said = json.dumps(obs.health(gateway))
        for secret in (alice.account_id, alice.device_id, alice.token, run):
            assert secret not in said, "the health answer names something it should not"
        assert set(obs.health(gateway)) == {"serving", "measured", "taking_work", "because"}

    def test_it_says_when_this_gateway_is_not_taking_work(self, gateway):
        from agentnode_sdk.gateway.allowance import stop_everything

        assert obs.health(gateway)["taking_work"] is True
        stop_everything(gateway.state.root, "upgrading the image")
        after = obs.health(gateway)
        assert after["taking_work"] is False
        assert "upgrading" not in json.dumps(after), (
            "the operator's own words are for customers who ask, not for whoever can reach a "
            "health address")

    def test_an_unmeasured_gateway_says_so_without_explaining_the_machine(self, tmp_path):
        state = GatewayState(str(tmp_path / "bare"), version="test")
        try:
            bare = GatewayService(state, backend=StandInBackend())
            said = obs.health(bare)
            assert said["serving"] is True
            assert said["measured"] is False
            assert said["because"]
        finally:
            state.close()


class TestWhatAnOperatorSees:

    def test_refusals_are_counted_by_reason_and_not_as_one_number(self, gateway):
        alice = _a_customer(gateway, "alice")
        for _ in range(3):
            with pytest.raises(dispatch.Refused):
                dispatch.dispatch("delete_everything", {}, alice, service=gateway)
        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("status", {"run_id": "f" * 32}, alice, service=gateway)
        counts = obs.look(gateway)
        assert counts.refusals_by_reason.get("unknown_operation") == 3
        assert counts.refusals_by_reason.get("no_such_run") == 1, (
            "one error rate hides the difference between a customer needing more and somebody "
            "guessing")

    def test_probes_are_a_pattern_and_the_alert_says_so(self, gateway):
        alice = _a_customer(gateway, "alice")
        for _ in range(25):
            with pytest.raises(dispatch.Refused):
                dispatch.dispatch("delete_everything", {}, alice, service=gateway)
        sink = obs.NowhereSink()
        seen = obs.observe(gateway, sink)
        fired = {alert["rule"] for alert in seen["alerts"]}
        assert "somebody is probing" in fired
        for alert in seen["alerts"]:
            assert alert["what_it_means"], "an alert nobody can act on is noise"

    def test_the_refusals_of_one_account_do_not_appear_under_another(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        for _ in range(4):
            with pytest.raises(dispatch.Refused):
                dispatch.dispatch("delete_everything", {}, alice, service=gateway)
        counts = obs.look(gateway)
        assert counts.refusals_by_account.get(alice.account_id) == 4
        assert bob.account_id not in counts.refusals_by_account

    def test_a_rule_that_raises_cannot_take_the_gateway_down(self, gateway):
        def explodes(_counts):
            raise RuntimeError("a rule with a bug in it")

        rule = obs.Rule("a broken rule", obs.WARNING, "it should not matter", explodes)
        assert rule.check(obs.look(gateway)) is None

    def test_what_is_written_carries_no_secret(self, gateway):
        alice = _a_customer(gateway, "alice")
        _a_run_by(gateway, alice)
        sink = obs.LocalFileSink(gateway.state.root / obs.EVENTS_NAME)
        obs.observe(gateway, sink)
        written = (gateway.state.root / obs.EVENTS_NAME).read_text(encoding="utf-8")
        assert written.strip(), "nothing was written, so this test proved nothing"
        assert alice.token not in written
        for line in written.splitlines():
            assert json.loads(line)["kind"] in obs.KINDS

    def test_an_unwritable_sink_does_not_lose_a_run(self, gateway, tmp_path):
        """Losing a metric must never lose a run."""
        sink = obs.LocalFileSink(tmp_path / "nowhere" / "deeper")
        (tmp_path / "nowhere").write_text("i am a file, not a directory", encoding="utf-8")
        obs.observe(gateway, sink)                            # must not raise
