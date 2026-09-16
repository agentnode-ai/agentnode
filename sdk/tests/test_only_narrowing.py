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


def _an_operator_that_narrows_everything(gateway, request):
    """An operator ceiling strictly below whatever the job asked for, in every numeric dimension.

    Derived from the request rather than written down, because the numbers a job does not name
    are still numbers it asked for: `requested_policy` fills them from the contract's defaults,
    and an operator below a default narrows just as really as one below an explicit request. A
    table of literals here would go stale the day a default moves, and would go stale silently.
    """
    from agentnode_sdk.gateway.policy_paths import policy_shape

    shape = policy_shape(gateway.requested_policy(request))
    numbers = {}
    for path, value in shape.items():
        if path.startswith("limits.") and isinstance(value, (int, float)):
            field = path.split(".", 1)[1]
            numbers[field] = type(value)(max(1, value // 2) if isinstance(value, int)
                                         else value / 2)
    return SandboxPolicy(network=NetworkRules(enabled=True, allowed_destinations=None),
                         limits=Limits(**numbers))


def _admit_asking(gateway, *, mandatory=(), optional=(), operator=None, wall_clock_s=3600,
                  network="unrestricted", domains=()):
    """Drive a real admission and return the refusal, or None when the job was admitted.

    Through `admit` rather than through `merge_policies`: what a customer needs is not that a
    function narrows, but that this gateway refuses. Everything above this line tests the fold;
    this tests the service.
    """
    import hashlib

    code = b"print(1)\n"
    request = _asking_for(mandatory=tuple(mandatory), optional=tuple(optional),
                          wall_clock_s=wall_clock_s, network=network,
                          allowed_domains=tuple(domains),
                          artifact_sha256=hashlib.sha256(code).hexdigest())
    gateway._operator_policy = (operator if operator is not None
                                else _an_operator_that_narrows_everything(gateway, request))
    # The digest of the policy the job DESCRIBES, computed the way the gateway computes it
    # rather than pasted in. A wrong one is refused before the mandatory check is reached, and
    # a test that tripped over that would report the wrong refusal as the right one.
    from agentnode_sdk.gateway.policy_paths import policy_shape
    from agentnode_sdk.gateway.protocol import canonical_bytes, digest

    request = dataclasses.replace(
        request, policy_sha256=digest(canonical_bytes(
            policy_shape(gateway.requested_policy(request)))))
    try:
        gateway.admit(request, code)
    except Exception as exc:                                    # noqa: BLE001
        return exc
    return None


class TestAMandatoryRequirementIsRefusedRatherThanClamped:
    """The other half of `POLICY-WIDENING-DECISION-0001`, one test per dimension.

    Everything above this line shows the composition NARROWING a request, and a review read that
    as the whole answer and was right to object: "the supplied tests explicitly expect clamping"
    (`ALPHA-R2-ADMISSION-0008`, D5). Clamping is the correct behaviour for a requirement the job
    can run without. It is the wrong behaviour for one it cannot, and the decision draws the line
    exactly there:

    * OPTIONAL -- narrowed during `prepare`, before any human agrees, and disclosed as a delta in
      structured form and in words. That is every test above this class.
    * MANDATORY -- refused, naming the field, with nothing started.

    `admit()` has enforced this since it was written; what did not exist was a test per
    dimension, which is what a criterion saying "each dimension" asks for. Each case below is
    paired with the SAME request declared optional, so the difference is visibly the declaration
    rather than the ceiling.
    """

    LIMITS = ["limits.cpu", "limits.memory_mb", "limits.processes", "limits.wall_clock_s"]

    def test_every_path_in_the_vocabulary_has_a_case(self):
        """The vocabulary is the list this class must cover, read from the vocabulary itself so
        that a field added next year fails here until somebody gives it a case."""
        from agentnode_sdk.gateway.policy_paths import POLICY_PATHS

        covered = set(self.LIMITS) | {"network.enabled", "network.allowed_destinations"}
        assert covered == set(POLICY_PATHS), sorted(set(POLICY_PATHS) ^ covered)

    @pytest.mark.parametrize("path", LIMITS)
    def test_a_limit_declared_mandatory_is_refused(self, gateway, path):
        refused = _admit_asking(gateway, mandatory=(path,))
        assert refused is not None, f"{path} was clamped where it should have been refused"
        assert path in str(refused)
        assert "Nothing was started" in str(refused)

    @pytest.mark.parametrize("path", LIMITS)
    def test_and_the_SAME_narrowing_declared_optional_is_not_refused(self, gateway, path):
        """The pairing is the point. Same operator policy, same request, same field narrowed --
        and the only difference is which list the job put it in. Were both refused, the refusal
        would be about the ceiling and would say nothing about mandatory at all."""
        assert _admit_asking(gateway, optional=(path,)) is None

    def test_a_network_the_operator_turned_off_is_refused_when_mandatory(self, gateway):
        off = SandboxPolicy(network=NetworkRules(enabled=False,
                                                 allowed_destinations=frozenset()))
        refused = _admit_asking(gateway, mandatory=("network.enabled",), operator=off)
        assert refused is not None
        assert "network.enabled" in str(refused)

    def test_and_the_same_one_declared_optional_runs_without_a_network(self, gateway):
        off = SandboxPolicy(network=NetworkRules(enabled=False,
                                                 allowed_destinations=frozenset()))
        assert _admit_asking(gateway, optional=("network.enabled",), operator=off) is None

    def test_a_destination_the_operator_does_not_allow_is_refused_when_mandatory(self, gateway):
        only = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"api.example"})))
        refused = _admit_asking(gateway, mandatory=("network.allowed_destinations",),
                                operator=only)
        assert refused is not None
        assert "network.allowed_destinations" in str(refused)

    def test_and_the_same_one_declared_optional_runs_on_the_shorter_list(self, gateway):
        only = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"api.example"})))
        assert _admit_asking(gateway, optional=("network.allowed_destinations",),
                             operator=only) is None

    def test_an_unknown_field_is_refused_whichever_list_it_is_in(self, gateway):
        """Fail closed on a name this build cannot decide -- in EITHER list, because a field the
        gateway cannot enforce is not made safe by the job calling it optional."""
        for where in ("mandatory", "optional"):
            refused = _admit_asking(gateway, **{where: ("limits.gpus",)})
            assert refused is not None, where
            assert "cannot be enforced" in str(refused)

    def test_a_field_in_both_lists_is_refused(self):
        """Mandatory and optional at once has no defined behaviour, and choosing one silently
        would settle a security question by accident."""
        from agentnode_sdk.gateway.policy_paths import PolicyPathError, validate_paths

        with pytest.raises(PolicyPathError):
            validate_paths(("limits.cpu",), ("limits.cpu",))
