"""EM-3A contract tests.

Each test names the plan section and the review finding it holds down, so a future change that
breaks one can see what it is breaking rather than just seeing a red assertion.
"""
from __future__ import annotations

import pytest

from agentnode_sdk.sandbox.contract import (
    LEARN_MORE,
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
    merge_policies,
    negotiate,
    select_backend,
)


# ---------------------------------------------------------------- E2: no dead ends

class TestEveryRefusalHasAWayOut:
    """§3.3 / F-E2-OPTIONAL-REMEDIATION — cardinality zero must be unrepresentable."""

    def test_a_refusal_cannot_be_subclassed_around(self):
        """EM3A-IMPL-0003 / F-A1: __post_init__ is virtual, so a subclass was the way around it."""
        with pytest.raises(TypeError, match="Refusal is final"):
            class Sneaky(Refusal):
                def __post_init__(self):
                    pass

    def test_blocked_cannot_be_subclassed_around_either(self):
        with pytest.raises(TypeError, match="Blocked is final"):
            class SneakyBlocked(Blocked):
                def __post_init__(self):
                    pass

    def test_a_home_made_action_labelled_remediation_does_not_count(self):
        """EM3A-IMPL-0002 / F-A1: the invariant proved a label, not a way out."""
        pretend = Action("teleport", "Do something nobody implemented", ActionKind.REMEDIATION)
        with pytest.raises(ValueError, match="not a registered one"):
            Refusal("x", "Something went wrong.", (pretend,))

    def test_an_impostor_reusing_a_real_id_does_not_count_either(self):
        impostor = Action("use_managed", "Use the AgentNode Sandbox", ActionKind.REMEDIATION)
        with pytest.raises(ValueError, match="not a registered one"):
            Refusal("x", "Something went wrong.", (impostor,))

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

class TestThereIsNoHostPlacement:
    """§7.1 v1.6 / Option b. Three token designs were taken apart in review; the property is now
    structural instead of guarded, and these are the six acceptance obligations the plan names."""

    def test_the_placement_enum_has_no_host_value(self):
        assert {p.value for p in Placement} == {"local", "self_hosted", "managed"}
        with pytest.raises(ValueError):
            Placement("legacy_host")
        with pytest.raises(ValueError):
            Placement("host")

    def test_the_module_exposes_no_host_symbol_at_all(self):
        """Bound 3: not part of the interface — checked by name, not by intention."""
        from agentnode_sdk.sandbox import contract as C
        leaked = [n for n in dir(C)
                  if any(k in n.lower() for k in ("host", "legacy", "intent", "mint", "authority"))]
        assert leaked == [], f"host-shaped names survive in the contract: {leaked}"

    def test_automatic_selection_is_total_over_the_environment_space(self):
        """Bound 1: enumerate the WHOLE product space, not a sample, and assert every outcome is a
        protected placement or a refusal with a way out."""
        seen_placements, seen_refusals = set(), 0
        combos = 0
        for local in (True, False):
            for mobile in (True, False):
                for org in (None, "gateway.example"):
                    for managed in (True, False):
                        for online in (True, False):
                            for policy in ("curated_only", "default", "none", "anything-else"):
                                combos += 1
                                env = Environment(local_runtime_ready=local, is_mobile=mobile,
                                                  organisation_backend=org,
                                                  managed_available=managed, online=online,
                                                  config_host_trust_policy=policy)
                                out = select_backend(env)
                                if isinstance(out, Selection):
                                    assert out.placement in (Placement.LOCAL, Placement.SELF_HOSTED,
                                                             Placement.MANAGED), env
                                    seen_placements.add(out.placement)
                                else:
                                    assert out.remediations, env
                                    seen_refusals += 1
        assert combos == 128
        assert seen_placements == {Placement.LOCAL, Placement.SELF_HOSTED, Placement.MANAGED}
        assert seen_refusals > 0

    def test_no_extra_argument_can_ask_for_host(self):
        """The gate takes exactly one parameter, so there is nothing to smuggle a capability in."""
        import inspect
        params = list(inspect.signature(select_backend).parameters)
        assert params == ["env"], params
        for weapon in (object(), "legacy_host", Placement.LOCAL, {"placement": "host"}, None):
            with pytest.raises(TypeError):
                select_backend(Environment(), weapon)          # positional
            with pytest.raises(TypeError):
                select_backend(Environment(), intent=weapon)   # the old keyword

    def test_no_remediation_leads_to_the_host(self):
        """Bound 2: every catalogue entry ends in a protected placement or in stopping."""
        from agentnode_sdk.sandbox.contract import _REMEDIATION_CATALOGUE
        allowed = {"use_managed", "install_local_runtime", "connect_own_server",
                   "retry_when_online", "contact_support", "abandon_safely"}
        assert set(_REMEDIATION_CATALOGUE) == allowed
        for action in _REMEDIATION_CATALOGUE.values():
            blob = (action.id + " " + action.label).lower()
            for word in ("host", "unprotected", "unsafe", "legacy", "directly on"):
                assert word not in blob, f"{action.id} points at host execution: {action.label!r}"

    def test_every_catalogue_entry_resolves_to_a_protected_placement_or_to_stopping(self):
        """Bound 2, as behaviour rather than wording: each entry DECLARES where it leads, and the
        only two destinations are a protected placement or stopping. Nothing resolves to a host."""
        from agentnode_sdk.sandbox.contract import (_REMEDIATION_CATALOGUE, _REMEDIATION_OUTCOME,
                                                    LEADS_TO_PROTECTED, LEADS_TO_STOPPING)
        assert set(_REMEDIATION_OUTCOME) == set(_REMEDIATION_CATALOGUE)
        for aid, outcome in _REMEDIATION_OUTCOME.items():
            assert outcome in (LEADS_TO_PROTECTED, LEADS_TO_STOPPING), aid
        # and the protected ones name placements that exist in this contract
        protected = {a for a, o in _REMEDIATION_OUTCOME.items() if o == LEADS_TO_PROTECTED}
        assert protected == {"use_managed", "install_local_runtime", "connect_own_server",
                             "retry_when_online"}

    def test_the_catalogue_is_sealed_after_import(self):
        """Bound 2: a caller cannot invent a way out that nothing implements."""
        from agentnode_sdk.sandbox import contract as C
        with pytest.raises(RuntimeError, match="sealed"):
            C._register_remediation(Action("run_on_host", "Run it on this computer anyway",
                                           ActionKind.REMEDIATION), C.LEADS_TO_PROTECTED)

    def test_no_concrete_backend_reports_a_host_placement(self):
        """Bound 3, as behaviour: walk the real SandboxBackend implementations in the package."""
        import inspect
        from agentnode_sdk.sandbox.backend import SandboxBackend
        import agentnode_sdk.sandbox.container_backend as cb
        found = [obj for _n, obj in inspect.getmembers(cb, inspect.isclass)
                 if issubclass(obj, SandboxBackend) and obj is not SandboxBackend]
        assert found, "no concrete backend found to inspect"
        for cls in found:
            src = inspect.getsource(cls).lower()
            for word in ("legacy_host", "legacyhostintent", "placement.legacy"):
                assert word not in src, f"{cls.__name__} mentions {word}"
            assert not hasattr(cls, "placement"), f"{cls.__name__} declares a placement"

    def test_the_shipped_policy_is_documented_as_what_it_is(self):
        """Bound 5, read from the config description rather than trusted as prose."""
        from agentnode_sdk.config import CONFIG_DESCRIPTIONS
        text = CONFIG_DESCRIPTIONS["sandbox.host_trust_policy"].lower()
        assert "host" in text
        assert "sandboxed" in text or "fail-closed" in text
        # it must not advertise host execution as safe or protected
        assert "safe" not in text

    def test_no_user_facing_string_promises_safety_for_a_legacy_path(self):
        """Bound 4: safety words must never co-occur with a legacy or host marker — in the contract
        AND in the prototype, because the person reads the prototype."""
        surfaces = []
        for env in (Environment(), Environment(online=False), Environment(managed_available=False),
                    Environment(local_runtime_ready=True), Environment(organisation_backend="g")):
            out = select_backend(env)
            surfaces.append(out.human_name if isinstance(out, Selection) else out.reason)
            if isinstance(out, Refusal):
                surfaces.extend(a.label for a in out.actions)
        from agentnode_sdk.sandbox.contract import _REMEDIATION_CATALOGUE
        surfaces.extend(a.label for a in _REMEDIATION_CATALOGUE.values())
        safety = ("safe", "protected", "sandbox")
        legacy = ("host", "legacy", "unprotected", "directly on this computer")
        # the prototype's user-facing strings too: every quoted string the script can show
        from pathlib import Path
        import re as _re
        proto = Path(__file__).resolve().parents[2] / "docs" / "em3" / "onboarding-prototype.html"
        assert proto.is_file(), f"the prototype must be reviewable from the tests: {proto}"
        html = proto.read_text(encoding="utf-8")
        checked_prototype = 0
        for line in html.splitlines():
            low = line.lower()
            if any(w in low for w in legacy) and "//" not in low.split("<")[0][:4]:
                checked_prototype += 1
                # a line may name the legacy mode only to say it is NOT part of this flow
                if any(w in low for w in safety):
                    assert ("not part of" in low or "no host placement" in low
                            or "does not exist" in low or "unprotected execution" in low), (
                        f"the prototype claims safety near a legacy marker: {line.strip()[:120]!r}")
        assert checked_prototype > 0, "the prototype scan found nothing to check"

        for text in surfaces:
            low = text.lower()
            if any(w in low for w in legacy):
                assert not any(w in low for w in safety), f"safety claimed for a legacy path: {text!r}"


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
        {"retention": Retention(workspace="keep_until_deleted_by_user")},
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

    def test_a_subclass_cannot_redefine_narrowing(self):
        """EM3A-IMPL-0001 / F-A1: virtual dispatch let a subclass widen instead of narrow."""
        class Widening(SandboxPolicy):
            def _narrowed_by(self, lower):
                return lower                      # would hand control to the untrusted side
        user = SandboxPolicy(network=NetworkRules(True, frozenset({"api.example.com"})))
        with pytest.raises(TypeError, match="not exactly SandboxPolicy"):
            merge_policies({Scope.USER: Widening(), Scope.PACKAGE: user})

    def test_a_forged_part_is_rejected_too(self):
        class WideningLimits(Limits):
            def _narrowed_by(self, other):
                return self
        bad = SandboxPolicy(limits=WideningLimits(cpu=64))
        with pytest.raises(TypeError, match="where Limits is required"):
            merge_policies({Scope.USER: SandboxPolicy(), Scope.PACKAGE: bad})

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
        surfaces = []
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

    def test_every_catalogue_label_is_plain_language_too(self):
        """The jargon rule covers what the person can click, not only what they read."""
        from agentnode_sdk.sandbox.contract import _REMEDIATION_CATALOGUE
        for action in _REMEDIATION_CATALOGUE.values():
            low = action.label.lower()
            for word in self.JARGON:
                assert word not in low, f"{word!r} in a button label: {action.label!r}"

    def test_a_refusal_states_that_nothing_ran(self):
        out = select_backend(Environment(managed_available=False))
        assert isinstance(out, Refusal)
        assert "nothing was run" in out.reason.lower()

    def test_every_action_has_a_label_a_person_can_read(self):
        for action in (USE_MANAGED, LEARN_MORE):
            assert action.label and action.label[0].isupper()
        with pytest.raises(ValueError):
            Action("x", "   ", ActionKind.REMEDIATION)
