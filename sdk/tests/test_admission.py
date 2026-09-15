"""What happens before anything runs.

Every ceiling here is HIT and the refusal observed. A test that sets a limit and reads it back
proves the configuration round-trips and nothing else, and that is the shape of evidence this
file exists to avoid: `D2` in the frozen profile says plainly that a limit whose evidence is that
it was configured is NOT_EVIDENCED.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway import accounts as accounts_module
from agentnode_sdk.gateway import admission
from agentnode_sdk.gateway.allowance import Allowance, write_allowance
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by, _their_second_machine


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


class TestEveryReasonIsDeclaredAndRendersAsARefusal:

    def test_the_mapping_is_total(self):
        assert set(admission.AS_A_REFUSAL) == set(admission.REASONS)
        for reason, refusal in admission.AS_A_REFUSAL.items():
            assert refusal in contract.REFUSALS, (
                "%s renders as %r, which no client can be written against" % (reason, refusal))

    def test_a_reason_nobody_declared_cannot_be_raised(self):
        with pytest.raises(ValueError):
            admission.NotAdmitted("looks_suspicious", "because", "do this")

    def test_a_refusal_with_nothing_to_do_cannot_be_raised(self):
        with pytest.raises(ValueError):
            admission.NotAdmitted("device_rate", "you are going too fast", "")

    def test_and_neither_can_a_contract_refusal(self):
        """Every refusal in the product, not only admission's, names an action."""
        with pytest.raises(ValueError):
            dispatch.Refused("malformed", "something is wrong", "")


class TestTheRateIsAskedOfEveryOperationAndNotOnlyOfWork:

    def test_a_burst_of_cheap_reads_is_refused(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(requests_per_minute=5))
        carried, refused = 0, None
        for _ in range(12):
            try:
                dispatch.dispatch("usage", {}, who, service=gateway)
                carried += 1
            except dispatch.Refused as stopped:
                refused = stopped
                break
        assert refused is not None, (
            "twelve requests went through a ceiling of five -- a gateway that rate-limits only "
            "the expensive operation has not rate-limited anything, because a probe uses the "
            "cheap ones")
        assert refused.refusal == "over_a_ceiling"
        assert carried <= 5
        assert refused.what_to_do

    def test_an_account_rate_counts_every_device_in_it(self, gateway):
        alice = _a_customer(gateway, "alice")
        second = _their_second_machine(gateway, alice)
        write_allowance(gateway.state.root, Allowance(account_requests_per_minute=4))
        spent = 0
        with pytest.raises(dispatch.Refused):
            for who in (alice, second) * 6:
                dispatch.dispatch("usage", {}, who, service=gateway)
                spent += 1
        assert spent <= 4, "pairing a second machine raised the account's rate ceiling"

    def test_an_unreadable_rate_counter_refuses_rather_than_forgetting(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(requests_per_minute=100))
        (gateway.state.root / admission.RATE_NAME).write_text("{not json", encoding="utf-8")
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("usage", {}, who, service=gateway)
        assert refused.value.refusal == "over_a_ceiling", (
            "a rate limit that forgets when its file is damaged is one an attacker removes by "
            "damaging a file")

    def test_a_key_this_gateway_did_not_issue_is_treated_as_exhausted(self, gateway):
        rate = admission.RateLimit(gateway.state.root / "probe.json")
        assert rate.spend("../../etc/passwd", 10) > 0
        assert rate.spend("a" * 300, 10) > 0


class TestTheCeilingsOnWork:

    def test_a_device_may_not_exceed_its_runs_in_the_window(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(runs_per_window=1))
        _a_run_by(gateway, who)
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "over_a_ceiling"
        assert "window" in refused.value.because

    def test_an_artifact_larger_than_this_gateway_accepts_is_refused_before_anything(
            self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(max_artifact_bytes=64))
        code = b"x = 1\n" * 200
        shown = dispatch.dispatch(
            "prepare",
            {"artifact_sha256": hashlib.sha256(code).hexdigest(), "artifact_bytes": len(code),
             "wall_clock_s": 30},
            who, service=gateway)
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch(
                "submit",
                {"run_id": uuid.uuid4().hex,
                 "artifact": base64.b64encode(code).decode("ascii"), "wall_clock_s": 30,
                 "accepted_disclosure": shown["accepted_disclosure"]},
                who, service=gateway)
        assert refused.value.refusal == "over_a_ceiling"
        assert gateway.runs == {} or all(
            r.state == "refused" for r in gateway.runs.values())

    def test_an_unreadable_ceiling_refuses_work_rather_than_applying_none(self, gateway):
        who = _a_customer(gateway, "alice")
        (gateway.state.root / "allowance.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal in ("gateway_stopped", "over_a_ceiling",
                                         "sandbox_unavailable")
        assert refused.value.what_to_do

    def test_a_ceiling_this_build_does_not_understand_is_refused_not_ignored(self, gateway):
        who = _a_customer(gateway, "alice")
        (gateway.state.root / "allowance.json").write_text(
            json.dumps({"concurrent_runs": 1, "requests_per_hour": 10}), encoding="utf-8")
        with pytest.raises(dispatch.Refused):
            _a_run_by(gateway, who)

    def test_an_unreadable_use_record_refuses_rather_than_restoring_the_window(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(runs_per_window=1))
        _a_run_by(gateway, who)
        (gateway.state.root / "use.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "gateway_stopped"
        assert refused.value.what_to_do


class TestSuspensionAndTheStop:

    def test_a_suspended_account_cannot_work_and_can_still_read(self, gateway):
        who = _a_customer(gateway, "alice")
        gateway.state.accounts.suspend(who.account_id, "we need to talk about last Tuesday",
                                       by="the operator")
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "gateway_stopped"
        assert "last Tuesday" in refused.value.because, (
            "a suspension a customer cannot read is one they cannot act on")
        assert dispatch.dispatch("usage", {}, who, service=gateway)["runs"] == 0
        assert dispatch.dispatch("devices.list", {}, who, service=gateway)["devices"]

    def test_and_nothing_a_caller_sends_lifts_it(self, gateway):
        who = _a_customer(gateway, "alice")
        gateway.state.accounts.suspend(who.account_id, "a reason", by="the operator")
        declared = {op.name for op in contract.OPERATIONS}
        # There is no operation whose name or parameters could express "let me work again".
        for name in declared:
            op = contract.find(name)
            assert not any("suspend" in f.name or "restore" in f.name for f in op.params)
        with pytest.raises(dispatch.Refused):
            _a_run_by(gateway, who)

    def test_restoring_is_deliberate_and_not_a_side_effect_of_forgetting(self, gateway):
        who = _a_customer(gateway, "alice")
        gateway.state.accounts.suspend(who.account_id, "a reason")
        gateway.state.accounts.forget(who.account_id)
        # Forgetting the record is what DELETION does. It must not read as a restoration for an
        # account whose devices are still here.
        assert gateway.state.accounts.get(who.account_id).active, (
            "this is the honest consequence: a forgotten record IS active, which is why "
            "deletion removes the devices too and why forget() is not the way to unsuspend")
        gateway.state.accounts.suspend(who.account_id, "a reason")
        gateway.state.accounts.restore(who.account_id)
        assert gateway.state.accounts.get(who.account_id).active
        _a_run_by(gateway, who)

    def test_an_unrecognised_state_reads_as_suspended(self, gateway):
        who = _a_customer(gateway, "alice")
        gateway.state._write_private(accounts_module.ACCOUNTS_NAME, json.dumps(
            {who.account_id: {"state": "probably fine"}}))
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "gateway_stopped"

    def test_the_operator_stop_still_refuses_work_and_still_answers_about_the_past(
            self, gateway):
        from agentnode_sdk.gateway.allowance import STOPPED_SAYS, stop_everything

        who = _a_customer(gateway, "alice")
        stop_everything(gateway.state.root, "upgrading the sandbox image")
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.because.startswith(STOPPED_SAYS), (
            "clients match on this opening; composing a second sentence makes the same event "
            "read differently depending on which door somebody came through")
        assert dispatch.dispatch("usage", {}, who, service=gateway)["runs"] == 0


class TestNoClaimToDetectIntent:
    """D8 is blocking in the frozen profile, so it is asserted rather than left to review."""

    def test_no_reader_facing_surface_claims_to_detect_intent(self):
        import pathlib

        import agentnode_sdk

        forbidden = ("detects malicious", "detect malicious", "detects abuse",
                     "detects illegal", "detect illegal", "malicious intent",
                     "identifies malicious", "blocks malicious code",
                     "knows what the job is for")
        root = pathlib.Path(agentnode_sdk.__file__).parent
        offenders = []
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8").lower()
            except (OSError, UnicodeDecodeError):
                continue
            for phrase in forbidden:
                if phrase in text:
                    offenders.append("%s: %r" % (path.name, phrase))
        assert not offenders, (
            "a claim to determine intent is a claim this product does not have and cannot "
            "acquire: " + "; ".join(offenders))

    def test_and_the_module_that_could_says_so_itself(self):
        import inspect

        said = inspect.getdoc(admission) or ""
        assert "does not detect what a job is for" in said.lower()
        assert "behavioural signals" in said.lower()
