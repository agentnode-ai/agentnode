"""EM-3A contract tests.

Each test names the plan section and the review finding it holds down, so a future change that
breaks one can see what it is breaking rather than just seeing a red assertion.
"""
from __future__ import annotations

import pytest

from agentnode_sdk.sandbox.contract import (
    LEARN_MORE,
    LEGACY_HOST_WARNING,
    SHOW_DETAILS,
    USE_MANAGED,
    Action,
    ActionKind,
    AssuranceLevel,
    BackendCapabilities,
    Blocked,
    Capability,
    ConsentBinding,
    Environment,
    Limits,
    NetworkRules,
    Placement,
    Refusal,
    RemoteConsent,
    Retention,
    SandboxPolicy,
    Scope,
    SecretRef,
    Selection,
    LegacyHostIntent,
    merge_policies,
    mint_legacy_host_intent,
    negotiate,
    select_backend,
)


# ---------------------------------------------------------------- E2: no dead ends

class TestEveryRefusalHasAWayOut:
    """§3.3 / F-E2-OPTIONAL-REMEDIATION — cardinality zero must be unrepresentable."""

    def test_a_refusal_without_any_action_cannot_be_built(self):
        with pytest.raises(ValueError, match="at least one executable remediation"):
            Refusal("x", "Something went wrong.", ())

    def test_learn_more_alone_is_not_a_way_out(self):
        with pytest.raises(ValueError, match="do not count"):
            Refusal("x", "Something went wrong.", (LEARN_MORE, SHOW_DETAILS))

    def test_one_remediation_beside_informational_actions_is_enough(self):
        r = Refusal("x", "Secure execution is not set up yet.", (USE_MANAGED, LEARN_MORE))
        assert r.remediations == (USE_MANAGED,)

    def test_a_refusal_must_say_why_in_plain_language(self):
        with pytest.raises(ValueError, match="plain-language reason"):
            Refusal("x", "   ", (USE_MANAGED,))

    def test_an_unmet_capability_must_also_carry_a_way_out(self):
        with pytest.raises(ValueError, match="at least one executable remediation"):
            Capability("net", "Can reach the internet", met=False, reason="No network.",
                       actions=(LEARN_MORE,))

    def test_a_met_capability_needs_no_actions(self):
        assert Capability("net", "Can reach the internet", met=True).actions == ()

    def test_non_remediable_states_still_end_in_something_the_person_can_do(self):
        b = Blocked("region_gone", "That region is not available any more.")
        assert b.safe_exit.kind is ActionKind.REMEDIATION
        assert b.escalation.kind is ActionKind.REMEDIATION

    def test_every_refusal_the_gate_can_produce_carries_a_remediation(self):
        """The property that matters is not that one refusal is fine — it is that no reachable
        refusal is a dead end."""
        environments = [
            Environment(local_runtime_ready=False, managed_available=False, online=True),
            Environment(local_runtime_ready=False, managed_available=True, online=False),
            Environment(local_runtime_ready=False, managed_available=False, online=False),
        ]
        seen = 0
        for env in environments:
            out = select_backend(env)
            assert isinstance(out, Refusal), env
            assert out.remediations, f"dead end for {env}"
            assert out.reason.strip()
            seen += 1
        assert seen == 3


# ---------------------------------------------------------------- E6: legacy host mode

class TestLegacyHostIsUnreachableFromAutomaticPaths:
    """§7.1 / F-E6-HOST-EXCLUSION-BY-CONVENTION — an invariant, not a prose rule."""

    def test_the_token_cannot_be_constructed_directly(self):
        with pytest.raises(PermissionError, match="cannot be constructed directly"):
            LegacyHostIntent(object(), "someone")

    def test_a_config_saying_default_still_never_selects_host(self):
        env = Environment(local_runtime_ready=True, config_host_trust_policy="default")
        out = select_backend(env)
        assert isinstance(out, Selection)
        assert out.placement is Placement.LOCAL

    def test_no_environment_at_all_yields_host_without_a_token(self):
        """Sweep the whole reachable environment space rather than one hand-picked case."""
        for local in (True, False):
            for mobile in (True, False):
                for org in (None, "gateway.example"):
                    for managed in (True, False):
                        for online in (True, False):
                            env = Environment(local_runtime_ready=local, is_mobile=mobile,
                                              organisation_backend=org, managed_available=managed,
                                              online=online, config_host_trust_policy="default")
                            out = select_backend(env)
                            if isinstance(out, Selection):
                                assert out.placement is not Placement.LEGACY_HOST, env

    def test_declining_the_confirmation_mints_nothing(self):
        with pytest.raises(PermissionError, match="not confirmed"):
            mint_legacy_host_intent(lambda _warning: False, confirmed_by="user")

    def test_a_non_interactive_caller_cannot_mint(self):
        with pytest.raises(PermissionError, match="interactive confirmation"):
            mint_legacy_host_intent(None, confirmed_by="automatic")

    def test_the_confirmation_text_names_the_risk_in_plain_language(self):
        assert "elevated risk" in LEGACY_HOST_WARNING
        assert "directly on this computer" in LEGACY_HOST_WARNING

    def test_a_confirmed_token_selects_host_exactly_once(self):
        intent = mint_legacy_host_intent(lambda _w: True, confirmed_by="user")
        first = select_backend(Environment(local_runtime_ready=True), intent=intent)
        assert isinstance(first, Selection) and first.placement is Placement.LEGACY_HOST
        with pytest.raises(PermissionError, match="already used"):
            select_backend(Environment(local_runtime_ready=True), intent=intent)


# ---------------------------------------------------------------- E3: no silent cloud switch

def _binding(**over):
    base = dict(account="a", operator="agentnode", backend_id="managed-eu",
                data_classes=frozenset({"files"}), region="eu",
                retention=Retention(), policy_version="1")
    base.update(over)
    return ConsentBinding(**base)


class TestRemoteConsentIsBoundAndRevocable:
    """§7 / F-E3-STALE-REMEMBERED-CONSENT."""

    def test_a_remote_selection_without_consent_demands_it(self):
        out = select_backend(Environment(managed_available=True))
        assert isinstance(out, Selection) and out.placement is Placement.MANAGED
        assert out.consent_required is True

    def test_a_matching_consent_is_remembered(self):
        want = _binding()
        env = Environment(managed_available=True, wanted_binding=want,
                          consent=RemoteConsent(want, granted_at="t0"))
        out = select_backend(env)
        assert isinstance(out, Selection) and out.consent_required is False

    @pytest.mark.parametrize("changed", [
        {"account": "b"}, {"operator": "someone-else"}, {"backend_id": "managed-us"},
        {"region": "us"}, {"policy_version": "2"},
        {"data_classes": frozenset({"files", "screen"})},
        {"retention": Retention(diagnostics_hours=72)},
    ])
    def test_any_material_change_forces_a_fresh_consent(self, changed):
        stored = RemoteConsent(_binding(), granted_at="t0")
        env = Environment(managed_available=True, wanted_binding=_binding(**changed),
                          consent=stored)
        out = select_backend(env)
        assert isinstance(out, Selection)
        assert out.consent_required is True, f"stale consent silently reused for {changed}"

    def test_shortening_retention_does_not_need_new_consent(self):
        stored = RemoteConsent(_binding(), granted_at="t0")
        want = _binding(retention=Retention(diagnostics_hours=1, audit_metadata_days=1))
        assert stored.covers(want)

    def test_revocation_takes_effect(self):
        want = _binding()
        stored = RemoteConsent(want, granted_at="t0", revoked=True)
        env = Environment(managed_available=True, wanted_binding=want, consent=stored)
        out = select_backend(env)
        assert isinstance(out, Selection) and out.consent_required is True


# ---------------------------------------------------------------- D7: narrowing only

class TestALowerScopeMayOnlyNarrow:
    """§3.4 / D7 — package metadata is untrusted input, not configuration."""

    def test_a_package_cannot_widen_the_network(self):
        user = SandboxPolicy(network=NetworkRules(True, frozenset({"api.example.com"})))
        package = SandboxPolicy(network=NetworkRules(True, frozenset({"api.example.com", "evil.example"})))
        merged = merge_policies({Scope.USER: user, Scope.PACKAGE: package})
        assert merged.network.allowed_destinations == frozenset({"api.example.com"})

    def test_a_package_cannot_turn_the_network_on(self):
        user = SandboxPolicy(network=NetworkRules(enabled=False))
        package = SandboxPolicy(network=NetworkRules(enabled=True, allowed_destinations=frozenset({"x"})))
        assert merge_policies({Scope.USER: user, Scope.PACKAGE: package}).network.enabled is False

    def test_a_package_cannot_raise_limits(self):
        org = SandboxPolicy(limits=Limits(cpu=1, memory_mb=512, wall_clock_s=60))
        package = SandboxPolicy(limits=Limits(cpu=8, memory_mb=8192, wall_clock_s=3600))
        merged = merge_policies({Scope.ORGANISATION: org, Scope.PACKAGE: package})
        assert (merged.limits.cpu, merged.limits.memory_mb, merged.limits.wall_clock_s) == (1, 512, 60)

    def test_a_package_cannot_lower_the_assurance_floor(self):
        org = SandboxPolicy(required_assurance=AssuranceLevel.ATTESTED)
        package = SandboxPolicy(required_assurance=AssuranceLevel.SELF_REPORTED)
        merged = merge_policies({Scope.ORGANISATION: org, Scope.PACKAGE: package})
        assert merged.required_assurance is AssuranceLevel.ATTESTED

    def test_a_lower_scope_may_raise_the_assurance_floor(self):
        user = SandboxPolicy(required_assurance=AssuranceLevel.OBSERVED)
        project = SandboxPolicy(required_assurance=AssuranceLevel.ATTESTED)
        merged = merge_policies({Scope.USER: user, Scope.PROJECT: project})
        assert merged.required_assurance is AssuranceLevel.ATTESTED

    def test_a_package_cannot_lengthen_retention(self):
        user = SandboxPolicy(retention=Retention(diagnostics_hours=24, audit_metadata_days=30))
        package = SandboxPolicy(retention=Retention(diagnostics_hours=999, audit_metadata_days=999))
        merged = merge_policies({Scope.USER: user, Scope.PACKAGE: package})
        assert merged.retention.diagnostics_hours == 24
        assert merged.retention.audit_metadata_days == 30

    def test_injection_targets_cannot_escape_the_egress_allowlist(self):
        """§8 — injectHosts ⊆ allowed destinations, enforced by the merge itself."""
        user = SandboxPolicy(
            network=NetworkRules(True, frozenset({"api.github.com"})),
            secrets=(SecretRef("GH_TOKEN", frozenset({"api.github.com", "exfil.example"})),),
        )
        package = SandboxPolicy(network=NetworkRules(True, frozenset({"api.github.com"})),
                                secrets=(SecretRef("GH_TOKEN"),))
        merged = merge_policies({Scope.USER: user, Scope.PACKAGE: package})
        assert merged.secrets[0].inject_hosts == frozenset({"api.github.com"})

    def test_a_secret_never_carries_a_value(self):
        assert not hasattr(SecretRef("TOKEN"), "value")


# ---------------------------------------------------------------- E1/D6: assurance

class TestAssurance:
    """§4 / D6 — three words, and stronger backends are not levelled down."""

    def test_the_vocabulary_is_closed_and_has_no_certified(self):
        assert {a.value for a in AssuranceLevel} == {"self_reported", "observed", "attested"}
        with pytest.raises(ValueError):
            AssuranceLevel("certified")

    def test_a_backend_below_the_floor_is_refused_with_a_way_out(self):
        policy = SandboxPolicy(required_assurance=AssuranceLevel.ATTESTED)
        caps = BackendCapabilities("local", "On this device", AssuranceLevel.OBSERVED)
        out = negotiate(policy, caps)
        assert isinstance(out, Refusal) and out.remediations

    def test_a_backend_above_the_floor_is_not_levelled_down(self):
        policy = SandboxPolicy(required_assurance=AssuranceLevel.OBSERVED)
        managed = BackendCapabilities("managed", "AgentNode Sandbox", AssuranceLevel.ATTESTED)
        assert negotiate(policy, managed) is None

    def test_an_unmet_capability_refuses_before_start_with_its_own_actions(self):
        caps = BackendCapabilities(
            "local", "On this device", AssuranceLevel.OBSERVED,
            capabilities=(Capability("runtime", "Secure execution on this device", met=False,
                                     reason="Secure execution is not set up on this device yet.",
                                     actions=(USE_MANAGED, LEARN_MORE)),))
        out = negotiate(SandboxPolicy(), caps)
        assert isinstance(out, Refusal)
        assert USE_MANAGED in out.remediations
        assert "not set up" in out.reason

    def test_a_region_the_backend_cannot_serve_refuses(self):
        policy = SandboxPolicy(region="eu")
        caps = BackendCapabilities("managed", "AgentNode Sandbox", AssuranceLevel.OBSERVED,
                                   regions=frozenset({"us"}))
        out = negotiate(policy, caps)
        assert isinstance(out, Refusal) and out.remediations


# ---------------------------------------------------------------- plain language

class TestHumanFacingTextIsPartOfTheContract:
    """§3.3 — the words are contract data, so they can be tested."""

    JARGON = ("docker", "podman", "oci", "microvm", "capability", "egress", "attestation",
              "cap-drop", "namespace", "seccomp", "runc", "tmpfs")

    def test_no_jargon_reaches_the_person(self):
        surfaces = [LEGACY_HOST_WARNING]
        for env in (Environment(), Environment(online=False), Environment(managed_available=False)):
            out = select_backend(env)
            if isinstance(out, Refusal):
                surfaces.append(out.reason)
                surfaces.extend(a.label for a in out.actions)
        for env in (Environment(local_runtime_ready=True), Environment(managed_available=True)):
            out = select_backend(env)
            if isinstance(out, Selection):
                surfaces.append(out.human_name)
        for text in surfaces:
            low = text.lower()
            for word in self.JARGON:
                assert word not in low, f"{word!r} leaked into user-facing text: {text!r}"

    def test_a_refusal_states_that_nothing_ran(self):
        out = select_backend(Environment(managed_available=False))
        assert isinstance(out, Refusal)
        assert "nothing was run" in out.reason.lower()

    def test_every_action_has_a_label_a_person_can_read(self):
        for action in (USE_MANAGED, LEARN_MORE):
            assert action.label and action.label[0].isupper()
        with pytest.raises(ValueError):
            Action("x", "   ", ActionKind.REMEDIATION)
