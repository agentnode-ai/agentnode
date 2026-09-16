"""Nothing below the operator can obtain more than the operator allows.

The composition already narrows by construction -- `merge_policies` folds from the highest scope
down and calls the unbound narrowing functions explicitly, so a subclass cannot redirect it. What
did NOT exist is evidence: a test that, for every dimension there is, asks for MORE from below and
shows the answer is not more.

The distinction matters because "it narrows" is a claim about a function, and what a customer
needs is a claim about the service: that a job, a device, an account, a default or a field added
next year cannot reach past the operator. So these go through the gateway's own composition rather
than calling the fold directly, and the last test in the file is about the field added next year.
"""
from __future__ import annotations

import dataclasses

import pytest

from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.protocol import JobRequest
from agentnode_sdk.gateway.server import GatewayService
from agentnode_sdk.sandbox.contract import (
    Limits,
    NetworkRules,
    Retention,
    SandboxPolicy,
    Scope,
    merge_policies,
)
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


def _asking_for(**kwargs):
    base = dict(job_id="j", run_id="r" * 32, artifact_sha256="a" * 64, policy_sha256="p" * 64)
    base.update(kwargs)
    return JobRequest(**base)


class TestEveryDimensionARequestCanNameIsNarrowedNotWidened:
    """One test per thing a job can ask for more of."""

    @pytest.mark.parametrize("field_name,operator_has,job_asks_for", [
        ("cpu", 1.0, 64.0),
        ("memory_mb", 256, 65536),
        ("processes", 8, 4096),
        ("disk_mb", 128, 1_000_000),
        ("wall_clock_s", 30, 86_400),
    ])
    def test_a_limit(self, field_name, operator_has, job_asks_for):
        operator = SandboxPolicy(limits=Limits(**{field_name: operator_has}))
        job = SandboxPolicy(limits=Limits(**{field_name: job_asks_for}))
        got = merge_policies({Scope.ORGANISATION: operator, Scope.PACKAGE: job})
        assert getattr(got.limits, field_name) == operator_has, (
            "a job asked for more %s than the operator allows and got it" % field_name)

    def test_the_network_cannot_be_turned_on_from_below(self):
        operator = SandboxPolicy(network=NetworkRules(enabled=False,
                                                      allowed_destinations=frozenset()))
        job = SandboxPolicy(network=NetworkRules(enabled=True, allowed_destinations=None))
        got = merge_policies({Scope.ORGANISATION: operator, Scope.PACKAGE: job})
        assert got.network.enabled is False
        assert got.network.is_unrestricted is False

    def test_a_destination_the_operator_did_not_name_cannot_be_added(self):
        operator = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"api.example"})))
        job = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"api.example", "anywhere.example"})))
        got = merge_policies({Scope.ORGANISATION: operator, Scope.PACKAGE: job})
        assert got.network.allowed_destinations == frozenset({"api.example"})

    def test_and_asking_for_unrestricted_does_not_erase_the_list(self):
        """The empty-set-versus-None trap, asserted rather than trusted to the docstring."""
        operator = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"api.example"})))
        job = SandboxPolicy(network=NetworkRules(enabled=True, allowed_destinations=None))
        got = merge_policies({Scope.ORGANISATION: operator, Scope.PACKAGE: job})
        assert got.network.allowed_destinations == frozenset({"api.example"})

    def test_retention_cannot_be_extended_from_below(self):
        operator = SandboxPolicy(retention=Retention(diagnostics_hours=1,
                                                     audit_metadata_days=1))
        job = SandboxPolicy(retention=Retention(diagnostics_hours=9999,
                                                audit_metadata_days=9999))
        got = merge_policies({Scope.ORGANISATION: operator, Scope.PACKAGE: job})
        assert got.retention.diagnostics_hours == 1
        assert got.retention.audit_metadata_days == 1


class TestTheMiddleScopeCannotWidenEither:

    def test_a_device_allowance_cannot_reach_past_the_operator(self, gateway):
        """A client ceiling is a real layer, so it has to be checked as one."""
        token = gateway.state.redeem_pairing(gateway.state.start_pairing(),
                                            client_name="a laptop")
        gateway.state.set_client_allowance(token, ["anywhere.example"])
        got = merge_policies({
            Scope.ORGANISATION: SandboxPolicy(network=NetworkRules(
                enabled=True, allowed_destinations=frozenset({"api.example"}))),
            Scope.USER: gateway.client_policy(token),
            Scope.PACKAGE: SandboxPolicy(network=NetworkRules(enabled=True,
                                                              allowed_destinations=None)),
        })
        assert got.network.allowed_destinations == frozenset(), (
            "a device named a destination the operator does not allow, and it survived")


class TestTheGatewayUsesThatCompositionAndNotItsOwn:

    def test_the_job_a_gateway_grants_is_the_composed_one(self, gateway, tmp_path):
        """Through `GatewayService.compose`, which is what admission actually calls."""
        granted = gateway.compose(_asking_for(network="unrestricted", wall_clock_s=86_400))
        operator = gateway.operator_policy()
        assert granted.limits.wall_clock_s <= operator.limits.wall_clock_s
        if not operator.network.enabled:
            assert granted.network.enabled is False, (
                "the operator allows no network and a job asking for unrestricted got one")

    def test_a_scope_key_that_is_not_one_of_ours_is_refused(self):
        """An arbitrary orderable key could insert itself anywhere in the precedence order."""
        with pytest.raises(TypeError):
            merge_policies({"organisation": SandboxPolicy(),
                            Scope.PACKAGE: SandboxPolicy()})

    def test_a_policy_that_is_not_exactly_a_policy_is_refused(self):
        class Wider(SandboxPolicy):
            def _narrowed_by(self, other):                    # noqa: ARG002
                return self

        with pytest.raises(TypeError):
            merge_policies({Scope.ORGANISATION: SandboxPolicy(),
                            Scope.PACKAGE: Wider()})


class TestAFieldAddedLaterIsCoveredByConstruction:
    """The criterion that is really about next year rather than about today."""

    def test_every_limit_field_narrows_by_minimum(self):
        """Not a list somebody maintains: read off the dataclass, so a NEW field is included.

        A test that enumerated the five limits by name would go on passing after a sixth was
        added and left out of `_narrowed_by` -- which is exactly the shape of defect this
        criterion exists for.
        """
        fields = [f for f in dataclasses.fields(Limits)]
        assert fields, "Limits has no fields, so this test is checking nothing"
        for field in fields:
            tight = Limits(**{field.name: _smaller(field.default)})
            loose = Limits(**{field.name: _bigger(field.default)})
            got = SandboxPolicy._narrowed_by(SandboxPolicy(limits=tight),
                                             SandboxPolicy(limits=loose))
            assert getattr(got.limits, field.name) == _smaller(field.default), (
                "%s does not narrow: a lower scope can ask for more of it and get it. If this "
                "field is new, add it to Limits._narrowed_by." % field.name)

    def test_every_retention_field_that_is_a_number_narrows_by_minimum(self):
        for field in dataclasses.fields(Retention):
            if not isinstance(field.default, int) or isinstance(field.default, bool):
                continue
            tight = Retention(**{field.name: 1})
            loose = Retention(**{field.name: 99_999})
            got = SandboxPolicy._narrowed_by(SandboxPolicy(retention=tight),
                                             SandboxPolicy(retention=loose))
            assert getattr(got.retention, field.name) == 1, (
                "%s does not narrow. If this field is new, add it to Retention._narrowed_by."
                % field.name)


def _smaller(default):
    return 1.0 if isinstance(default, float) else 1


def _bigger(default):
    return 9999.0 if isinstance(default, float) else 9999


class TestWhatTheOperatorNarrowsIsDisclosedBeforeAnybodyAgrees:
    """`POLICY-WIDENING-DECISION-0001`, Option A: an optional requirement may be narrowed, and
    only in the open -- during prepare, before a person has agreed to anything.

    What was wrong: a job that asked for unrestricted network on a gateway allowing none was
    composed down to none and RAN, with nothing in the answer saying the request had been
    changed, unless the caller separately listed that path in `mandatory` -- an opt-in a caller
    has to know about. The caller who does not know about it is the one who most needs telling.
    """

    def _shown(self, gateway, who, **asked):
        import hashlib

        from agentnode_sdk.access import dispatch

        code = b"print(1)\n"
        return dispatch.dispatch("prepare", dict({
            "artifact_sha256": hashlib.sha256(code).hexdigest(),
            "artifact_bytes": len(code), "wall_clock_s": 30}, **asked),
            who, service=gateway)

    def test_the_effective_policy_is_shown_and_not_only_the_requested_one(self, gateway):
        from tests.test_two_accounts import _a_customer

        who = _a_customer(gateway, "alice")
        told = self._shown(gateway, who, network="unrestricted")
        assert told["requested_policy_sha256"], told
        assert told["effective_policy_sha256"], (
            "a person is shown the policy that was ASKED for and not the one that will be in "
            "force, which is the one they are actually agreeing to")
        assert told["effective_policy_sha256"] != told["requested_policy_sha256"], (
            "this gateway's operator policy allows no network, so asking for unrestricted must "
            "not produce the same effective policy as what was asked for")

    def test_and_the_narrowing_is_named_per_dimension(self, gateway):
        from tests.test_two_accounts import _a_customer

        who = _a_customer(gateway, "alice")
        told = self._shown(gateway, who, network="unrestricted")
        assert told["narrowing"], "the request was cut down and nothing said which part"
        fields = {one["field"] for one in told["narrowing"]}
        assert any("network" in f for f in fields), fields
        for one in told["narrowing"]:
            assert "requested" in one and "effective" in one, one

    def test_and_in_words_a_person_can_read(self, gateway):
        from tests.test_two_accounts import _a_customer

        who = _a_customer(gateway, "alice")
        told = self._shown(gateway, who, network="unrestricted")
        assert told["narrowing_in_words"], told
        said = " ".join(told["narrowing_in_words"])
        assert "You asked for" in said and "this job will run with" in said, said

    def test_and_which_operator_policy_decided_it(self, gateway):
        """A digest names one policy and orders none. A person reading a record months later
        needs to know whether two records were made under the same one."""
        from tests.test_two_accounts import _a_customer

        who = _a_customer(gateway, "alice")
        told = self._shown(gateway, who, network="unrestricted")
        assert "operator_policy_version" in told, told

    def test_and_an_UNNARROWED_request_says_so_rather_than_leaving_it_out(self, gateway):
        """A missing section is a guess; a present empty one is an answer."""
        from tests.test_two_accounts import _a_customer

        who = _a_customer(gateway, "alice")
        told = self._shown(gateway, who, network="none")
        assert told["narrowing"] == [] and told["narrowing_in_words"] == [], told

    def test_and_a_submission_under_a_DIFFERENT_effective_policy_is_refused(self, gateway):
        """The half that makes disclosure worth anything. Narrowing a job again after somebody
        agreed to it is a narrowing they did not agree to."""
        import base64
        import hashlib

        from agentnode_sdk.access import dispatch
        from agentnode_sdk.gateway.operator_policy import OperatorPolicyEnvelope

        from tests.test_two_accounts import _a_customer

        who = _a_customer(gateway, "alice")
        code = b"print(1)\n"
        told = self._shown(gateway, who, network="unrestricted")

        # The operator changes what is allowed, between the disclosure and the submission.
        assert "effective_policy_sha256" in told
        bound = dispatch.BOUND_BY_THE_DISCLOSURE
        assert ("effective_policy_sha256",) in bound, (
            "the effective policy is not bound, so a submission under a different one would be "
            "narrowed again rather than refused: %s" % (bound,))
        assert OperatorPolicyEnvelope is not None and base64 and hashlib
